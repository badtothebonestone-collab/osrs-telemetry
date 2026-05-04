import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


SCHEMA_VERSION_INDEX = "visual_perception.index.v1"
SCHEMA_VERSION_TICK_RECORD = "visual_perception.tick_record.v1"
MISSING_PERCEPTION_MESSAGE = (
    "Required perception files not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)
CROP_GENERATION_REASON = "metadata-only mode; rerun with --generate-crops to attempt crops"
PILLOW_UNAVAILABLE_REASON = "Pillow not available"
NO_CROPS_CREATED_REASON = "no crops generated; selected frame files missing or region boxes unavailable"
NO_CROP_ELIGIBLE_RECORDS_REASON = "no selected records with currently existing frame files or valid region boxes"
APPROXIMATE_REGIONS_WARNING = (
    "screen_regions.json appears approximate; if crops look off, run "
    "python telemetry-viewer\\calibrate_screen_regions.py --latest-existing-frame"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def perception_paths(session: Path) -> dict[str, Path]:
    perception_dir = session / "perception"
    return {
        "perceptionDir": perception_dir,
        "tickBundles": perception_dir / "tick_bundles.jsonl",
        "screenRegions": perception_dir / "screen_regions.json",
        "visualIndex": perception_dir / "visual_perception_index.json",
        "visualTickRecords": perception_dir / "visual_tick_records.jsonl",
    }


def session_relative(session: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except (OSError, ValueError):
        return str(path)


def safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in ("-", "_") else "_" for character in value)
    return cleaned.strip("_") or "region"


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def signal_filters(args) -> list[str]:
    filters = []

    if args.combat:
        filters.append("hasCombatSignal")

    if args.inventory:
        filters.append("hasInventorySignal")

    if args.ui:
        filters.append("hasUiSignal")

    if args.frame_issues:
        filters.append("hasFrameIssue")

    return filters


def selected_by_tick_args(bundle: dict, args) -> bool:
    tick_id = bundle.get("tickId")

    if args.tick is not None:
        return tick_id == args.tick

    if args.tick_range is not None:
        start, end = args.tick_range
        return isinstance(tick_id, int) and start <= tick_id <= end

    return True


def selected_by_signal_filters(bundle: dict, filters: list[str]) -> bool:
    if not filters:
        return True

    derived = bundle.get("derived")

    if not isinstance(derived, dict):
        return False

    return any(bool(derived.get(key)) for key in filters)


def approximate_regions_warning(screen_regions: dict) -> str | None:
    note = screen_regions.get("note")

    if screen_regions.get("approximate") is True:
        return APPROXIMATE_REGIONS_WARNING

    if isinstance(note, str) and "approximate" in note.lower():
        return APPROXIMATE_REGIONS_WARNING

    return None


def frame_exists_for_selection(session: Path, bundle: dict) -> bool:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    exists = frame.get("exists")
    path_value = frame.get("absolutePath") or frame.get("path")

    if not path_value:
        return exists if isinstance(exists, bool) else False

    frame_path = Path(path_value)

    if not frame_path.is_absolute():
        frame_path = session / frame_path

    try:
        frame_path.resolve().relative_to(session.resolve())
    except (OSError, ValueError):
        return exists if isinstance(exists, bool) else False

    return frame_path.exists()


def collect_candidate_bundles(session: Path, tick_bundle_path: Path, args, filters: list[str]) -> list[dict]:
    candidates = []

    for bundle in iter_jsonl_records(tick_bundle_path):
        if not selected_by_signal_filters(bundle, filters):
            continue

        if not selected_by_tick_args(bundle, args):
            continue

        bundle["_visualFrameExists"] = frame_exists_for_selection(session, bundle)
        candidates.append(bundle)

    return candidates


def should_prefer_existing_frames(args) -> bool:
    if args.tick is not None:
        return False

    if args.only_existing_frames:
        return False

    if args.prefer_existing_frames:
        return True

    return bool(args.generate_crops)


def newest_from_group(group: list[dict], count: int) -> list[dict]:
    if count <= 0:
        return []

    return group[-count:]


def select_candidate_bundles(candidates: list[dict], args) -> tuple[list[dict], bool]:
    if args.tick is None and args.only_existing_frames:
        candidates = [candidate for candidate in candidates if candidate.get("_visualFrameExists") is True]

    prefer_existing = should_prefer_existing_frames(args)

    if prefer_existing:
        existing = [candidate for candidate in candidates if candidate.get("_visualFrameExists") is True]
        missing = [candidate for candidate in candidates if candidate.get("_visualFrameExists") is not True]
        preferred_candidates = existing + missing
    else:
        existing = []
        missing = []
        preferred_candidates = candidates

    if args.tick is not None:
        return candidates[:1], prefer_existing

    if args.latest is not None:
        if args.latest <= 0:
            return [], prefer_existing

        if prefer_existing:
            selected = newest_from_group(existing, args.latest)
            remaining = args.latest - len(selected)

            if remaining > 0:
                selected.extend(newest_from_group(missing, remaining))

            return selected, prefer_existing

        return newest_from_group(candidates, args.latest), prefer_existing

    if args.limit is not None:
        if args.limit <= 0:
            return [], prefer_existing

        return preferred_candidates[: args.limit], prefer_existing

    return preferred_candidates, prefer_existing


def iter_jsonl_records(path: Path):
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


def numeric_value(value):
    return value if isinstance(value, (int, float)) else None


def int_count(value) -> int:
    return value if isinstance(value, int) else 0


def format_pair(values: dict | None) -> str:
    if not isinstance(values, dict):
        return "?/?"

    current = values.get("boosted")
    maximum = values.get("real")
    return f"{current if current is not None else '?'}/{maximum if maximum is not None else '?'}"


def format_position(position: dict | None) -> str:
    if not isinstance(position, dict):
        return "?,?,?"

    return ",".join(
        str(position.get(key) if position.get(key) is not None else "?")
        for key in ("worldX", "worldY", "plane")
    )


def event_preview(events: dict | None, *, limit: int = 8) -> str:
    if not isinstance(events, dict):
        return "none"

    event_types = events.get("onTickEventTypes")

    if not isinstance(event_types, list) or not event_types:
        return "none"

    labels = [str(event_type) for event_type in event_types if event_type is not None]

    if len(labels) <= limit:
        return ", ".join(labels)

    return ", ".join(labels[:limit]) + f", +{len(labels) - limit}"


NORMALIZED_PRECISION = 6


def clamped_normalized_box(box: dict) -> dict:
    try:
        x = float(box["x"])
        y = float(box["y"])
        w = float(box["w"])
        h = float(box["h"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("region boxes must contain numeric x, y, w, and h") from error

    minimum_size = 0.000001
    x = max(0.0, min(1.0 - minimum_size, x))
    y = max(0.0, min(1.0 - minimum_size, y))
    w = max(minimum_size, min(1.0 - x, w))
    h = max(minimum_size, min(1.0 - y, h))
    return {
        "x": round(x, NORMALIZED_PRECISION),
        "y": round(y, NORMALIZED_PRECISION),
        "w": round(w, NORMALIZED_PRECISION),
        "h": round(h, NORMALIZED_PRECISION),
    }


def clamped_point(point: dict) -> dict:
    try:
        x = float(point["x"])
        y = float(point["y"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("region points must contain numeric x and y") from error

    return {
        "x": round(max(0.0, min(1.0, x)), NORMALIZED_PRECISION),
        "y": round(max(0.0, min(1.0, y)), NORMALIZED_PRECISION),
    }


def region_tags(raw_region: dict) -> list[str]:
    tags = raw_region.get("tags")

    if not isinstance(tags, list):
        return []

    return [str(tag) for tag in tags if tag is not None]


def positive_float(raw_value, field_name: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"region {field_name} must be numeric") from error

    return max(0.000001, value)


def normalize_region_record(name: str, raw_region) -> dict:
    if not isinstance(raw_region, dict):
        raise ValueError(f"region {name} must be an object")

    if all(key in raw_region for key in ("x", "y", "w", "h")):
        return {
            "type": "rect",
            "box": clamped_normalized_box(raw_region),
            "tags": region_tags(raw_region),
        }

    region_type = raw_region.get("type") or "rect"

    if region_type in ("rect", "grid"):
        box = raw_region.get("box") if isinstance(raw_region.get("box"), dict) else raw_region
        output = {
            "type": region_type,
            "box": clamped_normalized_box(box),
            "tags": region_tags(raw_region),
        }

        if region_type == "grid":
            try:
                rows = int(raw_region.get("rows", 7))
                cols = int(raw_region.get("cols", 4))
                slot_count = int(raw_region.get("slotCount", rows * cols))
            except (TypeError, ValueError) as error:
                raise ValueError(f"grid region {name} rows, cols, and slotCount must be integers") from error

            rows = max(1, rows)
            cols = max(1, cols)
            output["rows"] = rows
            output["cols"] = cols
            output["slotCount"] = max(0, min(rows * cols, slot_count))

        return output

    if region_type == "circle":
        center = clamped_point(raw_region.get("center") if isinstance(raw_region.get("center"), dict) else {})
        max_radius = max(0.000001, min(center["x"], 1.0 - center["x"], center["y"], 1.0 - center["y"]))
        return {
            "type": "circle",
            "center": center,
            "radius": round(min(positive_float(raw_region.get("radius"), "radius"), max_radius), NORMALIZED_PRECISION),
            "tags": region_tags(raw_region),
        }

    if region_type == "ellipse":
        center = clamped_point(raw_region.get("center") if isinstance(raw_region.get("center"), dict) else {})
        max_radius_x = max(0.000001, min(center["x"], 1.0 - center["x"]))
        max_radius_y = max(0.000001, min(center["y"], 1.0 - center["y"]))

        try:
            rotation = float(raw_region.get("rotation", 0))
        except (TypeError, ValueError):
            rotation = 0.0

        return {
            "type": "ellipse",
            "center": center,
            "radiusX": round(min(positive_float(raw_region.get("radiusX"), "radiusX"), max_radius_x), NORMALIZED_PRECISION),
            "radiusY": round(min(positive_float(raw_region.get("radiusY"), "radiusY"), max_radius_y), NORMALIZED_PRECISION),
            "rotation": round(rotation, NORMALIZED_PRECISION),
            "tags": region_tags(raw_region),
        }

    raise ValueError(f"unsupported region type for {name}: {region_type}")


def serialize_region_for_save(region: dict) -> dict:
    normalized = normalize_region_record("region", region)
    region_type = normalized["type"]
    output = {"type": region_type}

    if region_type in ("rect", "grid"):
        output["box"] = normalized["box"]
    elif region_type == "circle":
        output["center"] = normalized["center"]
        output["radius"] = normalized["radius"]
    elif region_type == "ellipse":
        output["center"] = normalized["center"]
        output["radiusX"] = normalized["radiusX"]
        output["radiusY"] = normalized["radiusY"]
        output["rotation"] = normalized.get("rotation", 0)

    if region_type == "grid":
        output["rows"] = normalized["rows"]
        output["cols"] = normalized["cols"]
        output["slotCount"] = normalized["slotCount"]

    output["tags"] = normalized.get("tags", [])
    return output


def clamp_region(region: dict) -> dict:
    return serialize_region_for_save(region)


def bounding_box_for_region(region: dict) -> dict:
    normalized = normalize_region_record("region", region)

    if normalized["type"] in ("rect", "grid"):
        return normalized["box"]

    if normalized["type"] == "circle":
        center = normalized["center"]
        radius = normalized["radius"]
        return clamped_normalized_box(
            {
                "x": center["x"] - radius,
                "y": center["y"] - radius,
                "w": radius * 2,
                "h": radius * 2,
            }
        )

    center = normalized["center"]
    return clamped_normalized_box(
        {
            "x": center["x"] - normalized["radiusX"],
            "y": center["y"] - normalized["radiusY"],
            "w": normalized["radiusX"] * 2,
            "h": normalized["radiusY"] * 2,
        }
    )


def pixel_box_from_normalized_box(normalized_box: dict, width, height) -> dict | None:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None

    try:
        normalized_x = float(normalized_box["x"])
        normalized_y = float(normalized_box["y"])
        normalized_w = float(normalized_box["w"])
        normalized_h = float(normalized_box["h"])
    except (KeyError, TypeError, ValueError):
        return None

    left = max(0, min(width, round(normalized_x * width)))
    upper = max(0, min(height, round(normalized_y * height)))
    right = max(0, min(width, round((normalized_x + normalized_w) * width)))
    lower = max(0, min(height, round((normalized_y + normalized_h) * height)))

    if right <= left:
        right = max(0, min(width, left + 1))

    if lower <= upper:
        lower = max(0, min(height, upper + 1))

    if right <= left or lower <= upper:
        return None

    return {
        "x": left,
        "y": upper,
        "w": right - left,
        "h": lower - upper,
    }


def grid_slot_boxes(region: dict) -> list[dict]:
    normalized = normalize_region_record("region", region)

    if normalized["type"] != "grid":
        return []

    box = normalized["box"]
    rows = normalized["rows"]
    cols = normalized["cols"]
    slot_count = normalized["slotCount"]
    slot_w = box["w"] / cols
    slot_h = box["h"] / rows
    slots = []

    for index in range(slot_count):
        row = index // cols
        col = index % cols
        slots.append(
            {
                "slot": index,
                "row": row,
                "col": col,
                "box": clamped_normalized_box(
                    {
                        "x": box["x"] + col * slot_w,
                        "y": box["y"] + row * slot_h,
                        "w": slot_w,
                        "h": slot_h,
                    }
                ),
            }
        )

    return slots


def region_to_pixel_geometry(region: dict, frame_width, frame_height) -> dict | None:
    if not isinstance(frame_width, int) or not isinstance(frame_height, int) or frame_width <= 0 or frame_height <= 0:
        return None

    normalized = normalize_region_record("region", region)
    bounding_box = bounding_box_for_region(normalized)
    pixel = pixel_box_from_normalized_box(bounding_box, frame_width, frame_height)

    if pixel is None:
        return None

    output = {
        "type": normalized["type"],
        "boundingBox": pixel,
        "pixelBox": pixel,
    }

    if normalized["type"] == "circle":
        min_dimension = min(frame_width, frame_height)
        output["center"] = {
            "x": round(normalized["center"]["x"] * frame_width),
            "y": round(normalized["center"]["y"] * frame_height),
        }
        output["radius"] = round(normalized["radius"] * min_dimension)
    elif normalized["type"] == "ellipse":
        output["center"] = {
            "x": round(normalized["center"]["x"] * frame_width),
            "y": round(normalized["center"]["y"] * frame_height),
        }
        output["radiusX"] = round(normalized["radiusX"] * frame_width)
        output["radiusY"] = round(normalized["radiusY"] * frame_height)
        output["rotation"] = normalized.get("rotation", 0)
    elif normalized["type"] == "grid":
        output["rows"] = normalized["rows"]
        output["cols"] = normalized["cols"]
        output["slotCount"] = normalized["slotCount"]
        output["slotBoxes"] = [
            {
                "slot": slot["slot"],
                "row": slot["row"],
                "col": slot["col"],
                "box": slot["box"],
                "pixelBox": pixel_box_from_normalized_box(slot["box"], frame_width, frame_height),
            }
            for slot in grid_slot_boxes(normalized)
        ]

    return output


def pixel_box(normalized_box: dict, width, height) -> dict | None:
    geometry = region_to_pixel_geometry(normalized_box, width, height)

    if geometry is None:
        return None

    return geometry.get("boundingBox")


def region_records(regions: dict, frame: dict) -> tuple[list[dict], bool]:
    width = frame.get("width")
    height = frame.get("height")
    records = []
    missing_pixel_boxes = False

    for name, raw_region in regions.items():
        try:
            region = serialize_region_for_save(normalize_region_record(name, raw_region))
            normalized_box = bounding_box_for_region(region)
            geometry = region_to_pixel_geometry(region, width, height)
        except ValueError:
            region = None
            normalized_box = None
            geometry = None

        calculated_box = geometry.get("boundingBox") if isinstance(geometry, dict) else None

        if calculated_box is None:
            missing_pixel_boxes = True

        slots = geometry.get("slotBoxes") if isinstance(geometry, dict) else None
        slot_payload = []

        if isinstance(slots, list):
            for slot in slots:
                if not isinstance(slot, dict):
                    continue

                slot_payload.append(
                    {
                        "slot": slot.get("slot"),
                        "row": slot.get("row"),
                        "col": slot.get("col"),
                        "normalizedBox": slot.get("box"),
                        "pixelBox": slot.get("pixelBox"),
                        "cropPath": None,
                        "exists": False,
                    }
                )

        records.append(
            {
                "name": name,
                "type": region.get("type") if isinstance(region, dict) else None,
                "tags": region.get("tags", []) if isinstance(region, dict) else [],
                "geometry": region,
                "normalizedBox": normalized_box,
                "pixelBox": calculated_box,
                "pixelGeometry": geometry,
                "shapeMask": shape_mask_metadata(region),
                "gridSlots": slot_payload,
                "cropPath": None,
                "exists": False,
            }
        )

    return records, missing_pixel_boxes


def shape_mask_metadata(region: dict | None) -> dict | None:
    if not isinstance(region, dict):
        return None

    region_type = region.get("type")

    if region_type not in ("circle", "ellipse"):
        return None

    return {
        "shape": region_type,
        "maskGenerated": False,
        "cropMode": "boundingBoxOnly",
        "outside": "included",
        "rotation": region.get("rotation", 0) if region_type == "ellipse" else 0,
        "rotationApplied": False,
    }


def crop_box(pixel: dict) -> tuple[int, int, int, int] | None:
    if not isinstance(pixel, dict):
        return None

    x = pixel.get("x")
    y = pixel.get("y")
    w = pixel.get("w")
    h = pixel.get("h")

    if not all(isinstance(value, int) for value in (x, y, w, h)):
        return None

    if w <= 0 or h <= 0:
        return None

    return x, y, x + w, y + h


def draw_alpha_shape_mask(image_module, image_draw_module, region: dict, size: tuple[int, int]):
    mask = image_module.new("L", size, 0)
    draw = image_draw_module.Draw(mask)
    draw.ellipse((0, 0, max(0, size[0] - 1), max(0, size[1] - 1)), fill=255)
    return mask


def crop_region_image(image, region: dict, box: tuple[int, int, int, int], image_module, image_draw_module):
    region_type = region.get("type")

    if region_type in ("circle", "ellipse"):
        shape_mask = region.get("shapeMask") if isinstance(region.get("shapeMask"), dict) else {}
        rotation = shape_mask.get("rotation", 0)

        if region_type == "ellipse" and rotation not in (0, 0.0, None):
            shape_mask.update(
                {
                    "maskGenerated": False,
                    "cropMode": "boundingBoxOnly",
                    "outside": "included",
                    "reason": "rotated ellipse alpha masks are not generated yet",
                }
            )
            return image.crop(box).convert("RGB"), "JPEG", ".jpg"

        if image_draw_module is not None:
            crop = image.crop(box).convert("RGBA")
            mask = draw_alpha_shape_mask(image_module, image_draw_module, region, crop.size)
            crop.putalpha(mask)
            shape_mask.update(
                {
                    "maskGenerated": True,
                    "cropMode": "alphaPng",
                    "outside": "transparent",
                    "rotationApplied": False,
                }
            )
            return crop, "PNG", ".png"

        shape_mask.update(
            {
                "maskGenerated": False,
                "cropMode": "boundingBoxOnly",
                "outside": "included",
                "reason": "Pillow ImageDraw unavailable",
            }
        )

    return image.crop(box).convert("RGB"), "JPEG", ".jpg"


def slot_crop_name(region_name: str, slot_number) -> str:
    try:
        slot = int(slot_number)
    except (TypeError, ValueError):
        slot = 0

    return f"{safe_name(region_name)}Slot{slot:02d}.jpg"


def generate_region_crops(
    session: Path,
    bundle_frame: dict,
    visual_regions: list[dict],
    tick_id,
    temp_crops_dir: Path,
    image_module,
    *,
    generate_grid_slots: bool = False,
) -> tuple[int, list[str], int]:
    warnings = []

    if not isinstance(tick_id, int):
        return 0, ["cannot generate crops for record without integer tickId"], 0

    frame_path_value = bundle_frame.get("absolutePath") or bundle_frame.get("path")
    frame_path = Path(frame_path_value) if frame_path_value else None

    if frame_path is None:
        return 0, [f"tick {tick_id} frame file not found; crops skipped"], 0

    if not frame_path.is_absolute():
        frame_path = session / frame_path

    if not frame_path.exists():
        return 0, [f"tick {tick_id} frame file missing; crops skipped"], 0

    try:
        frame_path.resolve().relative_to(session.resolve())
    except (OSError, ValueError):
        return 0, [f"tick {tick_id} frame path escapes session; crops skipped"], 0

    crop_dir = temp_crops_dir / f"tick-{tick_id:08d}"
    published_crop_dir = Path("perception") / "crops" / f"tick-{tick_id:08d}"
    crop_dir.mkdir(parents=True, exist_ok=True)
    crop_count = 0
    slot_crop_count = 0

    try:
        from PIL import ImageDraw
    except ImportError:
        ImageDraw = None

    try:
        with image_module.open(frame_path) as image:
            for region in visual_regions:
                box = crop_box(region.get("pixelBox"))

                if box is None:
                    warnings.append(f"tick {tick_id} region {region.get('name')} has no valid pixel box; crop skipped")
                    continue

                region_name = safe_name(str(region.get("name") or "region"))
                crop_image, crop_format, extension = crop_region_image(image, region, box, image_module, ImageDraw)
                crop_path = crop_dir / f"{region_name}{extension}"
                crop_image.save(crop_path, crop_format)
                region["cropPath"] = str(published_crop_dir / crop_path.name)
                region["exists"] = True
                crop_count += 1

                if generate_grid_slots and region.get("type") == "grid":
                    for slot in region.get("gridSlots") or []:
                        slot_box = crop_box(slot.get("pixelBox"))

                        if slot_box is None:
                            warnings.append(
                                f"tick {tick_id} region {region.get('name')} slot {slot.get('slot')} has no valid pixel box; crop skipped"
                            )
                            continue

                        slot_path = crop_dir / slot_crop_name(region.get("name") or "grid", slot.get("slot"))
                        image.crop(slot_box).convert("RGB").save(slot_path, "JPEG")
                        slot["cropPath"] = str(published_crop_dir / slot_path.name)
                        slot["exists"] = True
                        crop_count += 1
                        slot_crop_count += 1
    except OSError as error:
        return crop_count, [f"tick {tick_id} unable to crop frame: {error}"], slot_crop_count

    return crop_count, warnings, slot_crop_count


def state_summary(state: dict) -> dict:
    position = state.get("position") if isinstance(state.get("position"), dict) else {}

    return {
        "gameState": state.get("gameState"),
        "position": {
            "worldX": position.get("worldX"),
            "worldY": position.get("worldY"),
            "plane": position.get("plane"),
        },
        "hp": state.get("hp") if isinstance(state.get("hp"), dict) else {},
        "prayer": state.get("prayer") if isinstance(state.get("prayer"), dict) else {},
        "runEnergyPercent": state.get("runEnergyPercent"),
        "interacting": state.get("interacting"),
        "inventoryCount": int_count(state.get("inventoryCount")),
        "equipmentCount": int_count(state.get("equipmentCount")),
        "npcCount": int_count(state.get("npcCount")),
        "playerCount": int_count(state.get("playerCount")),
    }


def signal_payload(derived: dict) -> dict:
    return {
        "hasCombatSignal": bool(derived.get("hasCombatSignal")),
        "hasInventorySignal": bool(derived.get("hasInventorySignal")),
        "hasUiSignal": bool(derived.get("hasUiSignal")),
        "hasVarSignal": bool(derived.get("hasVarSignal")),
        "hasFrameIssue": bool(derived.get("hasFrameIssue")),
        "hasCaptureError": bool(derived.get("hasCaptureError")),
    }


def prompt_context(bundle: dict, frame: dict, summary: dict, events: dict) -> str:
    tick_id = bundle.get("tickId")
    position = format_position(summary.get("position"))
    hp = format_pair(summary.get("hp"))
    prayer = format_pair(summary.get("prayer"))
    run = summary.get("runEnergyPercent")
    run_text = f"{run}%" if run is not None else "?%"
    inventory = summary.get("inventoryCount")
    event_text = event_preview(events)
    status = frame.get("frameIndexStatus") or frame.get("status") or "unknown"
    latency = frame.get("totalLatencyMs")
    latency_text = f"{latency:.0f}ms" if isinstance(latency, (int, float)) else "unknown"

    return (
        f"Tick {tick_id}, {summary.get('gameState') or 'UNKNOWN'} at {position}. "
        f"HP {hp}, prayer {prayer}, run {run_text}. "
        f"Inventory {inventory}/28. "
        f"Events: {event_text}. "
        f"Frame status {status}, latency {latency_text}."
    )


def recommended_review(signals: dict) -> dict:
    reasons = []

    if signals["hasFrameIssue"]:
        reasons.append("frameIssue")

    if signals["hasCaptureError"]:
        reasons.append("captureError")

    if signals["hasCombatSignal"]:
        reasons.append("combatSignal")

    if signals["hasInventorySignal"]:
        reasons.append("inventorySignal")

    if signals["hasUiSignal"]:
        reasons.append("uiSignal")

    if signals["hasVarSignal"]:
        reasons.append("varSignal")

    if signals["hasFrameIssue"] or signals["hasCaptureError"]:
        priority = "HIGH"
    elif signals["hasCombatSignal"] or signals["hasInventorySignal"] or signals["hasUiSignal"]:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    return {
        "priority": priority,
        "reasons": reasons,
    }


def build_visual_record(
    session: Path,
    bundle: dict,
    regions: dict,
    *,
    crop_enabled: bool = False,
    generate_grid_slots: bool = False,
    temp_crops_dir: Path | None = None,
    image_module=None,
) -> tuple[dict, list[str], int, int]:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    state = bundle.get("state") if isinstance(bundle.get("state"), dict) else {}
    events = bundle.get("events") if isinstance(bundle.get("events"), dict) else {}
    derived = bundle.get("derived") if isinstance(bundle.get("derived"), dict) else {}
    frame_exists = bundle.get("_visualFrameExists")

    if not isinstance(frame_exists, bool):
        frame_exists = frame_exists_for_selection(session, bundle)

    crop_frame = dict(frame)
    crop_frame["exists"] = frame_exists
    summary = state_summary(state)
    signals = signal_payload(derived)
    regions_payload, missing_pixel_boxes = region_records(regions, frame)
    warnings = []

    if missing_pixel_boxes:
        warnings.append(f"tick {bundle.get('tickId')} missing frame width/height for pixel boxes")

    crop_count = 0
    slot_crop_count = 0

    if crop_enabled and temp_crops_dir is not None and image_module is not None:
        crop_count, crop_warnings, slot_crop_count = generate_region_crops(
            session,
            crop_frame,
            regions_payload,
            bundle.get("tickId"),
            temp_crops_dir,
            image_module,
            generate_grid_slots=generate_grid_slots,
        )
        warnings.extend(crop_warnings)

    return (
        {
            "schemaVersion": SCHEMA_VERSION_TICK_RECORD,
            "sessionId": bundle.get("sessionId"),
            "tickId": bundle.get("tickId"),
            "timestampUtc": bundle.get("timestampUtc"),
            "frame": {
                "path": frame.get("path"),
                "exists": frame_exists,
                "width": frame.get("width"),
                "height": frame.get("height"),
                "status": frame.get("frameIndexStatus") or frame.get("captureStatus"),
                "captureSource": frame.get("captureSource"),
                "writeDelayMs": numeric_value(frame.get("writeDelayMs")),
                "totalLatencyMs": numeric_value(frame.get("totalLatencyMs")),
            },
            "stateSummary": summary,
            "signals": signals,
            "regions": regions_payload,
            "promptContext": prompt_context(bundle, frame, summary, events),
            "recommendedReview": recommended_review(signals),
        },
        warnings,
        crop_count,
        slot_crop_count,
    )


def write_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        file.write(json_dump_compact(data))
        file.write("\n")


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json_dump_compact(record))
            file.write("\n")


def atomic_publish(perception_dir: Path, temp_dir: Path, filenames: tuple[str, ...], *, publish_crops: bool = False) -> None:
    perception_dir.mkdir(parents=True, exist_ok=True)

    for filename in filenames:
        os.replace(temp_dir / filename, perception_dir / filename)

    crop_root = perception_dir / "crops"
    temp_crop_root = temp_dir / "crops"

    if crop_root.exists():
        shutil.rmtree(crop_root)

    if publish_crops and temp_crop_root.exists():
        shutil.move(str(temp_crop_root), str(crop_root))


def load_pillow_image():
    try:
        from PIL import Image
    except ImportError:
        return None

    return Image


def build_visual_perception(session: Path, args) -> dict:
    paths = perception_paths(session)
    perception_dir = paths["perceptionDir"]
    tick_bundle_path = paths["tickBundles"]
    screen_region_path = paths["screenRegions"]
    warnings = []

    if not tick_bundle_path.exists() or not screen_region_path.exists():
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)

    screen_regions = safe_read_json(screen_region_path)

    if not isinstance(screen_regions, dict):
        raise ValueError(f"Unable to read screen regions: {screen_region_path}")

    region_warning = approximate_regions_warning(screen_regions)

    if region_warning:
        warnings.append(region_warning)

    regions = screen_regions.get("regions")

    if not isinstance(regions, dict):
        raise ValueError(f"screen_regions.json does not contain a regions object: {screen_region_path}")

    regions = {
        name: serialize_region_for_save(normalize_region_record(name, region))
        for name, region in regions.items()
    }

    filters = signal_filters(args)
    records = []
    signal_counts = Counter()
    review_priority_counts = Counter()
    selected_tick_ids = []
    missing_pixel_box_ticks = 0
    crop_count = 0
    slot_boxes_generated_count = 0
    slot_crops_generated_count = 0
    selected_frame_exists_count = 0
    selected_frame_missing_count = 0
    crop_eligible_count = 0
    crop_skipped_missing_frame_count = 0
    image_module = None
    crop_generation_reason = CROP_GENERATION_REASON

    if args.generate_crops:
        image_module = load_pillow_image()

        if image_module is None:
            crop_generation_reason = PILLOW_UNAVAILABLE_REASON
            warnings.append(PILLOW_UNAVAILABLE_REASON)

    temp_dir = perception_dir / f".tmp-visual-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"
    temp_crops_dir = temp_dir / "crops"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    candidates = collect_candidate_bundles(session, tick_bundle_path, args, filters)
    selected_bundles, effective_prefer_existing = select_candidate_bundles(candidates, args)

    for bundle in selected_bundles:
        visual_record, record_warnings, record_crop_count, record_slot_crop_count = build_visual_record(
            session,
            bundle,
            regions,
            crop_enabled=bool(args.generate_crops and image_module is not None),
            generate_grid_slots=bool(args.generate_grid_slots),
            temp_crops_dir=temp_crops_dir,
            image_module=image_module,
        )
        records.append(visual_record)
        warnings.extend(record_warnings)
        selected_tick_ids.append(visual_record.get("tickId"))
        crop_count += record_crop_count
        slot_crops_generated_count += record_slot_crop_count
        slot_boxes_generated_count += sum(
            len(region.get("gridSlots") or [])
            for region in visual_record["regions"]
            if isinstance(region, dict)
        )

        if any("missing frame width/height" in warning for warning in record_warnings):
            missing_pixel_box_ticks += 1

        frame_exists = visual_record["frame"].get("exists") is True
        has_crop_box = any(region.get("pixelBox") is not None for region in visual_record["regions"])

        if frame_exists:
            selected_frame_exists_count += 1
        else:
            selected_frame_missing_count += 1

        if frame_exists and has_crop_box:
            crop_eligible_count += 1

        if args.generate_crops and not frame_exists:
            crop_skipped_missing_frame_count += 1

        signals = visual_record["signals"]

        for key, value in signals.items():
            if value:
                signal_counts[key] += 1

        review_priority_counts[visual_record["recommendedReview"]["priority"]] += 1

    if not records and args.only_existing_frames and candidates:
        warnings.append(
            "no matching tick bundles currently reference existing frame files; "
            "frame retention may have removed them or the perception dataset may need rebuilding"
        )

    if missing_pixel_box_ticks:
        warnings.append(
            f"pixel boxes unavailable for {missing_pixel_box_ticks} selected ticks because frame width/height was missing"
        )

    if args.generate_crops and image_module is not None:
        if crop_count:
            crop_generation_reason = None
        elif not records or crop_eligible_count == 0:
            crop_generation_reason = NO_CROP_ELIGIBLE_RECORDS_REASON
        else:
            crop_generation_reason = NO_CROPS_CREATED_REASON

    valid_tick_ids = [tick_id for tick_id in selected_tick_ids if isinstance(tick_id, int)]
    output_paths = {
        "visualPerceptionIndex": "perception/visual_perception_index.json",
        "visualTickRecords": "perception/visual_tick_records.jsonl",
        "crops": "perception/crops",
    }
    filters_used = {
        "tick": args.tick,
        "range": list(args.tick_range) if args.tick_range else None,
        "limit": args.limit,
        "latest": args.latest,
        "signals": filters,
        "signalCombination": "OR",
        "onlyExistingFrames": bool(args.only_existing_frames),
        "preferExistingFrames": bool(args.prefer_existing_frames),
        "effectivePreferExistingFrames": bool(effective_prefer_existing),
        "generateGridSlots": bool(args.generate_grid_slots),
    }
    region_type_counts = Counter(region.get("type") or "unknown" for region in regions.values())
    index = {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "sessionPath": str(session),
        "sessionId": records[0].get("sessionId") if records else None,
        "generatedAtUtc": utc_now(),
        "selectedTickCount": len(records),
        "firstTickId": valid_tick_ids[0] if valid_tick_ids else None,
        "lastTickId": valid_tick_ids[-1] if valid_tick_ids else None,
        "selectedFrameExistsCount": selected_frame_exists_count,
        "selectedFrameMissingCount": selected_frame_missing_count,
        "cropEligibleCount": crop_eligible_count,
        "cropSkippedMissingFrameCount": crop_skipped_missing_frame_count,
        "cropsGenerated": bool(crop_count),
        "cropGenerationReason": crop_generation_reason,
        "cropCount": crop_count,
        "regionTypeCounts": dict(region_type_counts.most_common()),
        "gridRegionCount": region_type_counts.get("grid", 0),
        "slotBoxesGeneratedCount": slot_boxes_generated_count,
        "slotCropsGeneratedCount": slot_crops_generated_count,
        "regionNames": list(regions.keys()),
        "signalCounts": {
            key: signal_counts.get(key, 0)
            for key in (
                "hasCombatSignal",
                "hasInventorySignal",
                "hasUiSignal",
                "hasVarSignal",
                "hasFrameIssue",
                "hasCaptureError",
            )
        },
        "reviewPriorityCounts": {
            key: review_priority_counts.get(key, 0)
            for key in ("LOW", "MEDIUM", "HIGH")
        },
        "frameIssueCount": signal_counts.get("hasFrameIssue", 0),
        "captureErrorCount": signal_counts.get("hasCaptureError", 0),
        "filters": filters_used,
        "paths": output_paths,
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    filenames = ("visual_perception_index.json", "visual_tick_records.jsonl")

    try:
        write_json(temp_dir / "visual_perception_index.json", index)
        write_jsonl(temp_dir / "visual_tick_records.jsonl", records)
        atomic_publish(perception_dir, temp_dir, filenames, publish_crops=bool(crop_count))
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prepare visual perception records from derived OSRS telemetry bundles.",
        epilog=(
            "Signal filters such as --combat, --ui, and --frame-issues combine with OR. "
            "Signal filters apply before tick/range constraints. "
            "--latest selects the newest matching ticks and takes precedence over --limit. "
            "Crop mode prefers existing-frame ticks by default unless --tick selects one exact tick. "
            "--generate-grid-slots only writes slot crop files when --generate-crops is also used."
        ),
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum selected visual records to write after filters.")
    parser.add_argument("--latest", type=int, default=None, metavar="N", help="Select the newest N matching visual records.")
    parser.add_argument("--tick", type=parse_tick_id, help="Select one tick.")
    parser.add_argument("--range", nargs=2, type=parse_tick_id, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument("--combat", action="store_true", help="Select ticks with hasCombatSignal.")
    parser.add_argument("--inventory", action="store_true", help="Select ticks with hasInventorySignal.")
    parser.add_argument("--ui", action="store_true", help="Select ticks with hasUiSignal.")
    parser.add_argument("--frame-issues", action="store_true", help="Select ticks with hasFrameIssue.")
    parser.add_argument("--only-existing-frames", action="store_true", help="Select only ticks whose frame file exists, except for an explicit --tick.")
    parser.add_argument("--prefer-existing-frames", action="store_true", help="Prioritize existing-frame ticks before missing-frame ticks.")
    parser.add_argument("--generate-crops", action="store_true", help="Attempt to generate region crops if Pillow is already available.")
    parser.add_argument("--generate-grid-slots", action="store_true", help="With --generate-crops, also write derived grid slot crop files such as inventorySlot00.jpg.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    if args.tick_range is not None:
        start, end = args.tick_range

        if end < start:
            args.tick_range = (end, start)

    try:
        index = build_visual_perception(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(error)
        return 1
    except (OSError, ValueError) as error:
        print(f"Unable to prepare visual perception records: {error}")
        return 1

    print(f"Prepared visual perception metadata: {index['sessionPath']}")
    print(f"  perception/visual_perception_index.json")
    print(f"  perception/visual_tick_records.jsonl ({index['selectedTickCount']} rows)")
    print(f"  cropsGenerated: {index['cropsGenerated']}")
    print(f"  cropGenerationReason: {index['cropGenerationReason']}")
    print(f"  selectedFrameExistsCount: {index['selectedFrameExistsCount']}")
    print(f"  selectedFrameMissingCount: {index['selectedFrameMissingCount']}")
    print(f"  cropEligibleCount: {index['cropEligibleCount']}")
    print(f"  cropSkippedMissingFrameCount: {index['cropSkippedMissingFrameCount']}")
    print(f"  regionTypeCounts: {index['regionTypeCounts']}")
    print(f"  slotBoxesGeneratedCount: {index['slotBoxesGeneratedCount']}")
    print(f"  slotCropsGeneratedCount: {index['slotCropsGeneratedCount']}")
    print(f"  frameIssueCount: {index['frameIssueCount']}")
    print(f"  captureErrorCount: {index['captureErrorCount']}")
    print(f"  warningCount: {index['warningCount']}")

    if index["warnings"]:
        print("  warnings:")

        for warning in index["warnings"][:10]:
            print(f"    - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

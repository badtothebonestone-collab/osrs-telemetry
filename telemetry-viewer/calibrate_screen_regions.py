import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from telemetry_paths import find_newest_session, get_sessions_dir, safe_read_json


MISSING_PERCEPTION_MESSAGE = (
    "Required perception files not found. "
    "Run python telemetry-viewer\\build_perception_dataset.py first."
)
PILLOW_MISSING_MESSAGE = (
    "Pillow is required to render calibration previews. "
    "Install is not attempted by this tool; install Pillow separately or run in an environment where it is already available."
)
OVERLAY_COLORS = (
    (255, 64, 64),
    (64, 220, 255),
    (255, 204, 64),
    (120, 255, 120),
    (220, 120, 255),
    (255, 140, 64),
    (120, 160, 255),
    (255, 255, 255),
)
FRAME_FILE_RE = re.compile(r"frame-tick-(\d+)\.jpg$", re.IGNORECASE)
NORMALIZED_PRECISION = 6
INTERACTIVE_HOST = "127.0.0.1"
DEFAULT_INTERACTIVE_PORT = 8770
SCHEMA_VERSION_SCREEN_REGIONS = "perception.screen_regions.v1"
CALIBRATION_PROFILES_DIR = Path(__file__).resolve().parent / "calibration_profiles"
DEFAULT_SCREEN_REGIONS_PROFILE = CALIBRATION_PROFILES_DIR / "default_screen_regions.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAB_PROFILE = "inventory"
DEFAULT_TAB_PROFILES = (
    "inventory",
    "equipment",
    "prayer",
    "magic",
    "combat",
    "stats",
    "quests",
    "friends",
    "clan",
    "settings",
    "emotes",
    "music",
    "logout",
)
INTERACTIVE_BASE_PROFILE = "__base__"
INTERACTIVE_BASE_PROFILE_LABEL = "Base regions"
BASE_REGION_NAMES = {
    "fullframe",
    "gameviewport",
    "minimap",
    "chatbox",
    "sidepanel",
    "tabs",
    "toptabs",
    "bottomtabs",
    "orbs",
    "compass",
    "compassorbare",
}
SESSION_CALIBRATION_METADATA_KEYS = {
    "calibrationGeneratedAtUtc",
    "calibratedAtUtc",
    "sourceTickId",
    "calibratedFromTickId",
    "sourceFramePath",
    "calibratedFramePath",
    "sourceFrameWidth",
    "sourceFrameHeight",
    "frameWidth",
    "frameHeight",
    "adjustmentsApplied",
    "pixelBoxes",
    "initializedAtUtc",
    "initializedFromProfile",
    "initializedFromProfilePath",
}


def parse_tick_id(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer tick id: {value}") from error


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        "calibrationDir": perception_dir / "region_calibration",
    }


def load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None, None, None

    return Image, ImageDraw, ImageFont


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


def frame_path_for_bundle(session: Path, bundle: dict) -> Path | None:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    path_value = frame.get("absolutePath") or frame.get("path")

    if not path_value:
        return None

    frame_path = Path(path_value)

    if not frame_path.is_absolute():
        frame_path = session / frame_path

    try:
        frame_path.resolve().relative_to(session.resolve())
    except (OSError, ValueError):
        return None

    return frame_path


def frame_exists(session: Path, bundle: dict) -> bool:
    frame = bundle.get("frame") if isinstance(bundle.get("frame"), dict) else {}
    frame_path = frame_path_for_bundle(session, bundle)

    if frame_path is not None:
        return frame_path.exists()

    return frame.get("exists") is True


def tick_id_from_frame_path(path: Path) -> int | None:
    match = FRAME_FILE_RE.search(path.name)

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def relative_frame_path(session: Path, frame_path: Path) -> str:
    try:
        return str(frame_path.resolve().relative_to(session.resolve()))
    except (OSError, ValueError):
        return str(frame_path)


def synthetic_bundle_for_frame(session: Path, frame_path: Path) -> dict:
    tick_id = tick_id_from_frame_path(frame_path)
    return {
        "tickId": tick_id,
        "frame": {
            "path": relative_frame_path(session, frame_path),
            "absolutePath": str(frame_path),
            "exists": True,
        },
    }


def frame_file_for_tick(session: Path, tick_id: int) -> Path | None:
    frame_path = session / "frames" / f"frame-tick-{tick_id:08d}.jpg"
    return frame_path if frame_path.exists() else None


def latest_existing_frame_file(session: Path) -> Path | None:
    frame_files = []

    for frame_path in (session / "frames").glob("frame-tick-*.jpg"):
        tick_id = tick_id_from_frame_path(frame_path)

        if tick_id is not None and frame_path.exists():
            frame_files.append((tick_id, frame_path))

    if not frame_files:
        return None

    return max(frame_files, key=lambda item: item[0])[1]


def find_selected_bundle(session: Path, tick_bundles_path: Path, tick_id: int | None) -> tuple[dict | None, list[str]]:
    warnings = []
    selected = None

    for bundle in iter_jsonl_records(tick_bundles_path):
        if tick_id is not None:
            if bundle.get("tickId") == tick_id:
                selected = bundle
                break

            continue

        if frame_exists(session, bundle):
            selected = bundle

    if tick_id is not None and selected is None:
        fallback_frame = frame_file_for_tick(session, tick_id)

        if fallback_frame is not None:
            warnings.append(f"tick {tick_id} was not found in tick_bundles.jsonl; using retained frame file")
            return synthetic_bundle_for_frame(session, fallback_frame), warnings

        warnings.append(f"tick {tick_id} was not found in tick_bundles.jsonl")

    if selected is not None and frame_exists(session, selected):
        return selected, warnings

    if tick_id is not None and selected is not None:
        fallback_frame = frame_file_for_tick(session, tick_id)

        if fallback_frame is not None:
            warnings.append(f"tick {tick_id} bundle frame path was stale; using retained frame file")
            return synthetic_bundle_for_frame(session, fallback_frame), warnings

        return selected, warnings

    fallback_frame = latest_existing_frame_file(session)

    if fallback_frame is not None:
        warnings.append("no tick bundle currently references a retained frame; using latest retained frame file")
        return synthetic_bundle_for_frame(session, fallback_frame), warnings

    return selected, warnings


def parse_region_filter(value: str | None) -> list[str] | None:
    if not value:
        return None

    names = [name.strip() for name in value.split(",")]
    return [name for name in names if name]


def selected_regions(regions: dict, requested: list[str] | None) -> tuple[list[tuple[str, dict]], list[str]]:
    warnings = []

    if not requested:
        return [(name, box) for name, box in regions.items()], warnings

    selected = []

    for name in requested:
        box = regions.get(name)

        if isinstance(box, dict):
            selected.append((name, box))
        else:
            warnings.append(f"requested region not found: {name}")

    return selected, warnings


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


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


def unique_region_name(regions: dict, requested: str) -> str:
    root = str(requested or "region").strip() or "region"

    if root not in regions:
        return root

    index = 2

    while f"{root}_{index}" in regions:
        index += 1

    return f"{root}_{index}"


def normalize_region_map(value, source_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{source_name} must be an object")

    regions = {}

    for name, raw_region in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{source_name} region names must be non-empty strings")

        if not isinstance(raw_region, dict):
            raise ValueError(f"{source_name} region {name} must be an object")

        regions[name] = serialize_region_for_save(normalize_region_record(name, raw_region))

    return regions


def flat_region_target(name: str, region: dict) -> tuple[str, str]:
    lowered = re.sub(r"[^a-z0-9]+", "", name.lower())
    region_type = region.get("type")

    if "inventory" in lowered:
        return "inventory", "inventoryGrid" if region_type == "grid" else name

    if "equipment" in lowered or lowered.startswith("equip"):
        return "equipment", "equipmentSlots" if region_type == "grid" else name

    if "prayer" in lowered:
        return "prayer", "prayerGrid" if region_type == "grid" else name

    if "magic" in lowered or "spell" in lowered:
        return "magic", name

    if "combat" in lowered or "specialattack" in lowered:
        return "combat", name

    if lowered in BASE_REGION_NAMES:
        return "base", name

    for profile_name in DEFAULT_TAB_PROFILES:
        if profile_name != DEFAULT_TAB_PROFILE and profile_name in lowered:
            return profile_name, name

    return "base", name


def migrate_flat_regions_to_base_regions(raw: dict) -> dict:
    flat_regions = normalize_region_map(raw.get("regions"), "regions")
    output = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in ("regions", "baseRegions", "tabProfiles")
    }
    output["baseRegions"] = {}
    output["tabProfiles"] = {name: {} for name in DEFAULT_TAB_PROFILES}

    for name, region in flat_regions.items():
        profile_name, region_name = flat_region_target(name, region)

        if profile_name == "base":
            target = output["baseRegions"]
        else:
            target = output["tabProfiles"].setdefault(profile_name, {})

        target[unique_region_name(target, region_name)] = region

    output.setdefault("defaultTabProfile", DEFAULT_TAB_PROFILE)
    return output


def normalize_tab_profile(profile_name: str, raw_profile) -> dict:
    if raw_profile is None:
        return {}

    if not isinstance(raw_profile, dict):
        raise ValueError(f"tab profile {profile_name} must be an object")

    if isinstance(raw_profile.get("regions"), dict):
        source = raw_profile["regions"]
    else:
        source = {
            name: region
            for name, region in raw_profile.items()
            if isinstance(region, dict)
        }

    return normalize_region_map(source, f"tabProfiles.{profile_name}")


def normalize_screen_regions_document(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("screen regions profile is not a JSON object")

    if "baseRegions" not in raw and "tabProfiles" not in raw:
        if not isinstance(raw.get("regions"), dict):
            raise ValueError("screen regions profile does not contain regions, baseRegions, or tabProfiles")

        raw = migrate_flat_regions_to_base_regions(raw)

    output = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in ("regions", "baseRegions", "tabProfiles")
    }
    output["schemaVersion"] = output.get("schemaVersion") or SCHEMA_VERSION_SCREEN_REGIONS
    output["coordinateSpace"] = output.get("coordinateSpace") or "normalized"
    output.setdefault("defaultTabProfile", DEFAULT_TAB_PROFILE)
    output["baseRegions"] = normalize_region_map(raw.get("baseRegions", {}), "baseRegions")
    output["tabProfiles"] = {}

    raw_tab_profiles = raw.get("tabProfiles", {})

    if raw_tab_profiles is not None and not isinstance(raw_tab_profiles, dict):
        raise ValueError("tabProfiles must be an object")

    for profile_name in DEFAULT_TAB_PROFILES:
        output["tabProfiles"][profile_name] = {}

    for profile_name, raw_profile in (raw_tab_profiles or {}).items():
        if not isinstance(profile_name, str) or not profile_name:
            raise ValueError("tab profile names must be non-empty strings")

        output["tabProfiles"][profile_name] = normalize_tab_profile(profile_name, raw_profile)

    return output


def serialize_screen_regions_document(doc: dict) -> dict:
    output = normalize_screen_regions_document(doc)
    output.pop("regions", None)
    return output


def get_base_regions(doc: dict) -> dict:
    return deepcopy(normalize_screen_regions_document(doc)["baseRegions"])


def get_tab_profile(doc: dict, profile_name: str) -> dict:
    normalized = normalize_screen_regions_document(doc)
    return deepcopy(normalized["tabProfiles"].get(profile_name, {}))


def set_tab_profile_region(doc: dict, profile_name: str, region_name: str, region: dict) -> dict:
    output = normalize_screen_regions_document(doc)
    output["tabProfiles"].setdefault(profile_name, {})
    output["tabProfiles"][profile_name][region_name] = serialize_region_for_save(normalize_region_record(region_name, region))
    return output


def list_tab_profiles(doc: dict) -> list[str]:
    return list(normalize_screen_regions_document(doc)["tabProfiles"].keys())


def profile_region_map(doc: dict, profile_name: str) -> dict:
    normalized = normalize_screen_regions_document(doc)

    if profile_name in (INTERACTIVE_BASE_PROFILE, "base", ""):
        return deepcopy(normalized["baseRegions"])

    return deepcopy(normalized["tabProfiles"].get(profile_name, {}))


def set_profile_regions(doc: dict, profile_name: str, regions: dict) -> dict:
    normalized = normalize_screen_regions_document(doc)

    if profile_name in (INTERACTIVE_BASE_PROFILE, "base", ""):
        normalized["baseRegions"] = validated_regions_payload(regions)
    else:
        normalized["tabProfiles"][profile_name] = validated_regions_payload(regions)

    return serialize_screen_regions_document(normalized)


def ensure_tab_profile(doc: dict, profile_name: str) -> dict:
    normalized = normalize_screen_regions_document(doc)

    if profile_name and profile_name not in normalized["tabProfiles"]:
        normalized["tabProfiles"][profile_name] = {}

    return serialize_screen_regions_document(normalized)


def screen_regions_document_counts(doc: dict) -> dict:
    normalized = normalize_screen_regions_document(doc)
    tab_profiles = normalized["tabProfiles"]
    return {
        "baseRegionCount": len(normalized["baseRegions"]),
        "tabProfileCount": len(tab_profiles),
        "tabRegionCount": sum(len(regions) for regions in tab_profiles.values()),
    }


def labeled_regions_for_document(doc: dict) -> list[tuple[str, dict]]:
    normalized = normalize_screen_regions_document(doc)
    items = [(f"base.{name}", region) for name, region in normalized["baseRegions"].items()]

    for profile_name, regions in normalized["tabProfiles"].items():
        for region_name, region in regions.items():
            items.append((f"{profile_name}.{region_name}", region))

    return items


def pixel_boxes_for_region_items(region_items: list[tuple[str, dict]], width: int | None, height: int | None) -> dict[str, dict]:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return {}

    boxes = {}

    for name, region in region_items:
        calculated_box = pixel_box(region, width, height)

        if calculated_box is not None:
            boxes[name] = calculated_box

    return boxes


def pixel_boxes_for_screen_regions_document(doc: dict, width: int | None, height: int | None) -> dict:
    normalized = normalize_screen_regions_document(doc)
    return {
        "baseRegions": pixel_boxes_for_regions(normalized["baseRegions"], width, height),
        "tabProfiles": {
            profile_name: pixel_boxes_for_regions(regions, width, height)
            for profile_name, regions in normalized["tabProfiles"].items()
        },
    }


def clamp_region(region: dict) -> dict:
    return serialize_region_for_save(region)


def bounding_box_for_region(region: dict) -> dict:
    normalized = normalize_region_record("region", region)
    region_type = normalized["type"]

    if region_type in ("rect", "grid"):
        return normalized["box"]

    if region_type == "circle":
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


def pixel_box_from_normalized_box(normalized_box: dict, width: int, height: int) -> dict | None:
    try:
        normalized_x = float(normalized_box["x"])
        normalized_y = float(normalized_box["y"])
        normalized_w = float(normalized_box["w"])
        normalized_h = float(normalized_box["h"])
    except (KeyError, TypeError, ValueError):
        return None

    left = clamp(round(normalized_x * width), 0, width)
    upper = clamp(round(normalized_y * height), 0, height)
    right = clamp(round((normalized_x + normalized_w) * width), 0, width)
    lower = clamp(round((normalized_y + normalized_h) * height), 0, height)

    if right <= left:
        right = clamp(left + 1, 0, width)

    if lower <= upper:
        lower = clamp(upper + 1, 0, height)

    if right <= left or lower <= upper:
        return None

    return {
        "left": left,
        "upper": upper,
        "right": right,
        "lower": lower,
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
                "slot": index + 1,
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


def region_to_pixel_geometry(region: dict, frame_width: int, frame_height: int) -> dict | None:
    if frame_width <= 0 or frame_height <= 0:
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


def pixel_box(normalized_box: dict, width: int, height: int) -> dict | None:
    geometry = region_to_pixel_geometry(normalized_box, width, height)

    if geometry is None:
        return None

    return geometry.get("boundingBox")


def image_dimensions(frame_path: Path) -> tuple[int | None, int | None]:
    image_module, _draw_module, _font_module = load_pillow()

    if image_module is None:
        return None, None

    try:
        with image_module.open(frame_path) as image:
            return image.size
    except OSError:
        return None, None


def normalized_box_from_pixel(pixel: dict, width: int, height: int) -> dict:
    return {
        "x": round(pixel["x"] / width, NORMALIZED_PRECISION),
        "y": round(pixel["y"] / height, NORMALIZED_PRECISION),
        "w": round(pixel["w"] / width, NORMALIZED_PRECISION),
        "h": round(pixel["h"] / height, NORMALIZED_PRECISION),
    }


def clamped_pixel_from_components(x: float, y: float, w: float, h: float, width: int, height: int) -> dict | None:
    left = clamp(round(x), 0, width)
    upper = clamp(round(y), 0, height)
    right = clamp(round(x + max(w, 1)), 0, width)
    lower = clamp(round(y + max(h, 1)), 0, height)

    if right <= left:
        if left < width:
            right = left + 1
        else:
            left = max(0, width - 1)
            right = width

    if lower <= upper:
        if upper < height:
            lower = upper + 1
        else:
            upper = max(0, height - 1)
            lower = height

    if right <= left or lower <= upper:
        return None

    return {
        "left": left,
        "upper": upper,
        "right": right,
        "lower": lower,
        "x": left,
        "y": upper,
        "w": right - left,
        "h": lower - upper,
    }


def serialize_adjustment(adjustment: tuple) -> dict:
    if adjustment[0] == "nudge":
        _, region, dx, dy, dw, dh = adjustment
        return {
            "type": "nudge",
            "region": region,
            "dx": dx,
            "dy": dy,
            "dw": dw,
            "dh": dh,
            "unit": "pixels",
        }

    _, region, x, y, w, h = adjustment
    return {
        "type": "set-region",
        "region": region,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "unit": "normalized",
    }


def adjustment_args(args) -> list[tuple]:
    adjustments = []

    for values in args.nudge or []:
        region, dx, dy, dw, dh = values
        adjustments.append(("nudge", region, float(dx), float(dy), float(dw), float(dh)))

    for values in args.set_region or []:
        region, x, y, w, h = values
        adjustments.append(("set-region", region, float(x), float(y), float(w), float(h)))

    return adjustments


def apply_adjustments(screen_regions: dict, width: int, height: int, args) -> tuple[dict, list[dict]]:
    calibrated = normalize_screen_regions_document(screen_regions)
    regions = get_base_regions(calibrated)

    applied = []

    for adjustment in adjustment_args(args):
        kind = adjustment[0]
        region_name = adjustment[1]
        current_region = regions.get(region_name)

        if not isinstance(current_region, dict):
            raise ValueError(f"adjustment target region not found: {region_name}")

        if kind == "nudge":
            _, _region, dx, dy, dw, dh = adjustment
            current_pixel = pixel_box(current_region, width, height)

            if current_pixel is None:
                raise ValueError(f"region {region_name} has no valid source pixel box")

            adjusted_pixel = clamped_pixel_from_components(
                current_pixel["x"] + dx,
                current_pixel["y"] + dy,
                current_pixel["w"] + dw,
                current_pixel["h"] + dh,
                width,
                height,
            )

            if adjusted_pixel is None:
                raise ValueError(f"region {region_name} adjustment produced an invalid box")

            regions[region_name] = region_from_normalized_box(
                current_region,
                normalized_box_from_pixel(adjusted_pixel, width, height),
            )
        else:
            _, _region, x, y, w, h = adjustment
            adjusted_pixel = pixel_box_from_normalized_box({"x": x, "y": y, "w": w, "h": h}, width, height)

            if adjusted_pixel is None:
                raise ValueError(f"region {region_name} set-region values produced an invalid box")

            regions[region_name] = region_from_normalized_box(
                current_region,
                normalized_box_from_pixel(adjusted_pixel, width, height),
            )

        applied.append(serialize_adjustment(adjustment))

    calibrated["baseRegions"] = regions
    return serialize_screen_regions_document(calibrated), applied


def pixel_boxes_for_regions(regions: dict, width: int | None, height: int | None) -> dict[str, dict]:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return {}

    boxes = {}

    for name, region in regions.items():
        if not isinstance(region, dict):
            continue

        box = pixel_box(region, width, height)

        if box is not None:
            boxes[name] = {
                "x": box["x"],
                "y": box["y"],
                "w": box["w"],
                "h": box["h"],
            }

    return boxes


def region_from_normalized_box(existing_region: dict, normalized_box: dict) -> dict:
    normalized = normalize_region_record("region", existing_region)
    region_type = normalized["type"]
    box = clamped_normalized_box(normalized_box)

    if region_type == "grid":
        updated = deepcopy(normalized)
        updated["box"] = box
        return serialize_region_for_save(updated)

    if region_type == "circle":
        radius = min(box["w"], box["h"]) / 2
        updated = {
            **normalized,
            "center": {"x": box["x"] + box["w"] / 2, "y": box["y"] + box["h"] / 2},
            "radius": radius,
        }
        return serialize_region_for_save(updated)

    if region_type == "ellipse":
        updated = {
            **normalized,
            "center": {"x": box["x"] + box["w"] / 2, "y": box["y"] + box["h"] / 2},
            "radiusX": box["w"] / 2,
            "radiusY": box["h"] / 2,
        }
        return serialize_region_for_save(updated)

    return serialize_region_for_save({"type": "rect", "box": box, "tags": normalized.get("tags", [])})


def label_lines(name: str, region: dict, pixel: dict) -> list[str]:
    normalized_box = bounding_box_for_region(region)
    region_type = normalize_region_record(name, region).get("type")
    normalized_text = (
        f"n {normalized_box.get('x')},{normalized_box.get('y')},"
        f"{normalized_box.get('w')},{normalized_box.get('h')}"
    )
    pixel_text = f"px {pixel['x']},{pixel['y']},{pixel['w']},{pixel['h']}"
    return [f"{name} ({region_type})", normalized_text, pixel_text]


def text_size(draw, text: str) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text)
    return box[2] - box[0], box[3] - box[1]


def draw_label(draw, position: tuple[int, int], lines: list[str], fill: tuple[int, int, int]) -> None:
    line_sizes = [text_size(draw, line) for line in lines]
    width = max(size[0] for size in line_sizes) + 8
    height = sum(size[1] + 4 for size in line_sizes) + 4
    x, y = position
    draw.rectangle((x, y, x + width, y + height), fill=(0, 0, 0))

    cursor_y = y + 4

    for line, (_, line_height) in zip(lines, line_sizes):
        draw.text((x + 4, cursor_y), line, fill=fill)
        cursor_y += line_height + 4


def draw_overlay(image, regions: list[tuple[str, dict]], pixel_boxes: dict[str, dict], output_path: Path) -> None:
    overlay = image.copy().convert("RGB")
    draw = ImageDraw.Draw(overlay)

    for index, (name, normalized_box) in enumerate(regions):
        pixel = pixel_boxes.get(name)

        if pixel is None:
            continue

        color = OVERLAY_COLORS[index % len(OVERLAY_COLORS)]
        rectangle = (pixel["left"], pixel["upper"], pixel["right"], pixel["lower"])

        for offset in range(3):
            draw.rectangle(
                (
                    rectangle[0] + offset,
                    rectangle[1] + offset,
                    max(rectangle[2] - offset, rectangle[0] + offset),
                    max(rectangle[3] - offset, rectangle[1] + offset),
                ),
                outline=color,
            )

        label_x = clamp(pixel["left"] + 4, 0, max(0, overlay.width - 220))
        label_y = clamp(pixel["upper"] + 4, 0, max(0, overlay.height - 70))
        draw_label(draw, (label_x, label_y), label_lines(name, normalized_box, pixel), color)

    overlay.save(output_path, "JPEG", quality=92)


def generate_contact_sheet(image, regions: list[tuple[str, dict]], pixel_boxes: dict[str, dict], output_path: Path) -> None:
    entries = [(name, normalized_box, pixel_boxes.get(name)) for name, normalized_box in regions]
    entries = [(name, normalized_box, pixel) for name, normalized_box, pixel in entries if pixel is not None]

    if not entries:
        raise ValueError("no valid regions available for contact sheet")

    columns = 2
    cell_width = 420
    cell_height = 320
    label_height = 58
    rows = (len(entries) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)

    for index, (name, _normalized_box, pixel) in enumerate(entries):
        column = index % columns
        row = index // columns
        cell_x = column * cell_width
        cell_y = row * cell_height
        crop = image.crop((pixel["left"], pixel["upper"], pixel["right"], pixel["lower"])).convert("RGB")
        crop.thumbnail((cell_width - 20, cell_height - label_height - 16))
        crop_x = cell_x + (cell_width - crop.width) // 2
        crop_y = cell_y + label_height + 8
        sheet.paste(crop, (crop_x, crop_y))
        draw.rectangle((cell_x, cell_y, cell_x + cell_width - 1, cell_y + cell_height - 1), outline=(80, 80, 80))
        draw.text((cell_x + 10, cell_y + 8), name, fill=(255, 255, 255))
        draw.text(
            (cell_x + 10, cell_y + 30),
            f"px {pixel['x']},{pixel['y']},{pixel['w']},{pixel['h']}",
            fill=(210, 210, 210),
        )

    sheet.save(output_path, "JPEG", quality=92)


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".tmp-{path.name}-{os.getpid()}")

    try:
        write_json(temp_path, data)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_screen_regions_atomic(path: Path, data: dict) -> None:
    write_json_atomic(path, data)


def validate_screen_regions_document(data, source: Path | str) -> dict:
    try:
        return normalize_screen_regions_document(data)
    except ValueError as error:
        raise ValueError(f"{error}: {source}") from error


def load_screen_regions_document(path: Path) -> dict:
    data = safe_read_json(path)
    return validate_screen_regions_document(data, path)


def reusable_profile_document(screen_regions: dict, profile_name: str, regions: dict | None = None) -> dict:
    profile = normalize_screen_regions_document(screen_regions)

    for key in SESSION_CALIBRATION_METADATA_KEYS:
        profile.pop(key, None)

    profile["schemaVersion"] = profile.get("schemaVersion") or SCHEMA_VERSION_SCREEN_REGIONS
    profile["coordinateSpace"] = profile.get("coordinateSpace") or "normalized"
    profile["profileName"] = profile_name
    profile["updatedAtUtc"] = utc_now()
    profile["approximate"] = False
    if regions is not None:
        profile["baseRegions"] = validated_regions_payload(regions)
    profile["note"] = "Reusable screen-region calibration profile. Copy or load this into future sessions."
    return serialize_screen_regions_document(profile)


def add_calibration_metadata(
    calibrated: dict,
    *,
    tick_id,
    frame_path: Path,
    width: int,
    height: int,
    adjustments: list[dict],
    pixel_boxes: dict[str, dict],
) -> dict:
    output = deepcopy(calibrated)
    output["calibrationGeneratedAtUtc"] = utc_now()
    output["calibratedAtUtc"] = output["calibrationGeneratedAtUtc"]
    output["sourceTickId"] = tick_id
    output["calibratedFromTickId"] = tick_id
    output["sourceFramePath"] = str(frame_path)
    output["calibratedFramePath"] = str(frame_path)
    output["sourceFrameWidth"] = width
    output["sourceFrameHeight"] = height
    output["frameWidth"] = width
    output["frameHeight"] = height
    output["approximate"] = False
    output["adjustmentsApplied"] = adjustments
    output["pixelBoxes"] = {
        name: {
            "x": box["x"],
            "y": box["y"],
            "w": box["w"],
            "h": box["h"],
        }
        for name, box in pixel_boxes.items()
    }
    output["note"] = (
        "Preview calibration output. Original perception/screen_regions.json is unchanged "
        "unless --write-screen-regions is used."
    )
    return output


def publish_outputs(temp_dir: Path, calibration_dir: Path, filenames: tuple[str, ...]) -> None:
    calibration_dir.mkdir(parents=True, exist_ok=True)

    for filename in filenames:
        os.replace(temp_dir / filename, calibration_dir / filename)


def load_calibration_source(session: Path, args) -> tuple[dict, list[str]]:
    paths = perception_paths(session)
    tick_bundles_path = paths["tickBundles"]
    screen_regions_path = paths["screenRegions"]
    warnings = []

    if not tick_bundles_path.exists() or not screen_regions_path.exists():
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)

    screen_regions = load_screen_regions_document(screen_regions_path)
    normalized_regions = get_base_regions(screen_regions)

    bundle, lookup_warnings = find_selected_bundle(session, tick_bundles_path, args.tick)
    warnings.extend(lookup_warnings)

    if bundle is None:
        raise ValueError("no tick bundle with an existing frame file was found")

    tick_id = bundle.get("tickId")
    frame_path = frame_path_for_bundle(session, bundle)

    if frame_path is None:
        raise ValueError(f"tick {tick_id} does not contain a usable frame path")

    if not frame_path.exists():
        raise ValueError(f"tick {tick_id} frame file does not exist: {frame_path}")

    return (
        {
            "paths": paths,
            "screenRegions": screen_regions,
            "regions": normalized_regions,
            "bundle": bundle,
            "tickId": tick_id,
            "framePath": frame_path,
        },
        warnings,
    )


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

    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive after clamping")

    return {
        "x": round(x, NORMALIZED_PRECISION),
        "y": round(y, NORMALIZED_PRECISION),
        "w": round(w, NORMALIZED_PRECISION),
        "h": round(h, NORMALIZED_PRECISION),
    }


def validated_regions_payload(value) -> dict:
    return normalize_region_map(value, "regions")


class InteractiveCalibrationState:
    def __init__(self, session: Path, source: dict, warnings: list[str]):
        self.session = session
        self.paths = source["paths"]
        self.screen_regions_path = self.paths["screenRegions"]
        self.calibration_dir = self.paths["calibrationDir"]
        self.tick_id = source["tickId"]
        self.frame_path = source["framePath"]
        self.screen_regions = normalize_screen_regions_document(source["screenRegions"])
        self.original_screen_regions = deepcopy(self.screen_regions)
        self.current_screen_regions = deepcopy(self.screen_regions)
        self.original_regions = get_base_regions(self.original_screen_regions)
        self.current_regions = get_base_regions(self.current_screen_regions)
        self.warnings = list(warnings)
        bundle_frame = source["bundle"].get("frame") if isinstance(source["bundle"].get("frame"), dict) else {}
        detected_width, detected_height = image_dimensions(self.frame_path)
        self.frame_width = detected_width if detected_width is not None else bundle_frame.get("width")
        self.frame_height = detected_height if detected_height is not None else bundle_frame.get("height")

    def output_paths(self) -> dict:
        return {
            "perceptionDir": str(self.paths["perceptionDir"]),
            "calibrationDir": str(self.calibration_dir),
            "calibratedScreenRegions": str(self.calibration_dir / "calibrated_screen_regions.json"),
            "screenRegions": str(self.screen_regions_path),
            "defaultScreenRegionsProfile": str(DEFAULT_SCREEN_REGIONS_PROFILE),
            "calibrationProfilesDir": str(CALIBRATION_PROFILES_DIR),
            "testCrops": str(self.paths["perceptionDir"] / "test_crops"),
        }

    def state_payload(self) -> dict:
        return {
            "selectedTickId": self.tick_id,
            "framePath": str(self.frame_path),
            "frameUrl": "/frame",
            "frameWidth": self.frame_width,
            "frameHeight": self.frame_height,
            "baseProfileId": INTERACTIVE_BASE_PROFILE,
            "baseProfileLabel": INTERACTIVE_BASE_PROFILE_LABEL,
            "defaultTabProfiles": list(DEFAULT_TAB_PROFILES),
            "defaultTabProfile": self.current_screen_regions.get("defaultTabProfile") or DEFAULT_TAB_PROFILE,
            "profileNames": list_tab_profiles(self.current_screen_regions),
            "screenRegions": self.current_screen_regions,
            "originalScreenRegions": self.original_screen_regions,
            "currentRegions": self.current_regions,
            "originalRegions": self.original_regions,
            "currentPixelBoxes": pixel_boxes_for_regions(self.current_regions, self.frame_width, self.frame_height),
            "originalPixelBoxes": pixel_boxes_for_regions(self.original_regions, self.frame_width, self.frame_height),
            "currentPixelGeometries": pixel_boxes_for_screen_regions_document(
                self.current_screen_regions,
                self.frame_width,
                self.frame_height,
            ),
            "documentCounts": screen_regions_document_counts(self.current_screen_regions),
            "sessionPath": str(self.session),
            "outputPaths": self.output_paths(),
            "persistence": {
                "sessionCalibrationPath": str(self.screen_regions_path),
                "defaultProfilePath": str(DEFAULT_SCREEN_REGIONS_PROFILE),
                "sessionCalibrationNote": "Session calibration affects this session only.",
                "defaultProfileNote": "Default profile initializes future sessions.",
                "existingSessionsNote": "Existing sessions keep their own screen_regions.json unless explicitly overwritten.",
            },
            "warnings": self.warnings,
        }

    def latest_frame_info(self) -> dict:
        tick_bundles_path = self.paths["tickBundles"]

        if not tick_bundles_path.exists():
            return {
                "ok": False,
                "error": MISSING_PERCEPTION_MESSAGE,
            }

        bundle, lookup_warnings = find_selected_bundle(self.session, tick_bundles_path, None)

        if bundle is None:
            return {
                "ok": False,
                "error": "No retained frame found yet. Log in and wait a few ticks, then refresh calibration.",
            }

        tick_id = bundle.get("tickId")
        frame_path = frame_path_for_bundle(self.session, bundle)

        if frame_path is None or not frame_path.exists():
            return {
                "ok": False,
                "error": "No retained frame found yet. Log in and wait a few ticks, then refresh calibration.",
                "tickId": tick_id,
                "framePath": str(frame_path) if frame_path else None,
            }

        width, height = image_dimensions(frame_path)

        return {
            "ok": True,
            "tickId": tick_id,
            "framePath": str(frame_path),
            "frameWidth": width,
            "frameHeight": height,
            "warnings": lookup_warnings,
        }

    def refresh_latest_frame(self) -> dict:
        latest = self.latest_frame_info()

        if not latest.get("ok"):
            return latest

        self.tick_id = latest["tickId"]
        self.frame_path = Path(latest["framePath"])
        self.frame_width = latest.get("frameWidth")
        self.frame_height = latest.get("frameHeight")

        for warning in latest.get("warnings") or []:
            if warning not in self.warnings:
                self.warnings.append(warning)

        payload = self.state_payload()
        payload["ok"] = True
        payload["refreshed"] = True
        payload["message"] = f"Using latest retained frame for tick {self.tick_id}"
        return payload

    def update_dimensions(self, width, height) -> None:
        if isinstance(width, int) and width > 0:
            self.frame_width = width

        if isinstance(height, int) and height > 0:
            self.frame_height = height

    def update_regions(self, regions: dict | None = None, *, screen_regions: dict | None = None, frame_width=None, frame_height=None) -> dict:
        self.update_dimensions(frame_width, frame_height)
        if screen_regions is not None:
            self.current_screen_regions = normalize_screen_regions_document(screen_regions)
        elif regions is not None:
            self.current_screen_regions = set_profile_regions(
                self.current_screen_regions,
                INTERACTIVE_BASE_PROFILE,
                regions,
            )
        else:
            raise ValueError("region update request must include screenRegions or regions")

        self.current_regions = get_base_regions(self.current_screen_regions)
        return self.state_payload()

    def calibrated_output(self) -> dict:
        calibrated = serialize_screen_regions_document(self.current_screen_regions)
        pixel_boxes = {
            name: {
                "x": box["x"],
                "y": box["y"],
                "w": box["w"],
                "h": box["h"],
            }
            for name, box in pixel_boxes_for_region_items(
                labeled_regions_for_document(calibrated),
                self.frame_width,
                self.frame_height,
            ).items()
        }
        counts = screen_regions_document_counts(calibrated)
        return add_calibration_metadata(
            calibrated,
            tick_id=self.tick_id,
            frame_path=self.frame_path,
            width=self.frame_width,
            height=self.frame_height,
            adjustments=[
                {
                    "type": "interactive-update",
                    "baseRegionCount": counts["baseRegionCount"],
                    "tabProfileCount": counts["tabProfileCount"],
                    "tabRegionCount": counts["tabRegionCount"],
                }
            ],
            pixel_boxes=pixel_boxes,
        )

    def save_calibrated(self) -> dict:
        output_path = self.calibration_dir / "calibrated_screen_regions.json"
        write_json_atomic(output_path, self.calibrated_output())
        return {
            "ok": True,
            "path": str(output_path),
            "screenRegionsUpdated": False,
        }

    def save_session_calibration(self) -> dict:
        return self.write_screen_regions()

    def write_screen_regions(self) -> dict:
        output = self.calibrated_output()
        write_screen_regions_atomic(self.screen_regions_path, output)
        self.screen_regions = normalize_screen_regions_document(output)
        self.current_screen_regions = deepcopy(self.screen_regions)
        self.original_screen_regions = deepcopy(self.screen_regions)
        self.current_regions = get_base_regions(self.current_screen_regions)
        self.original_regions = get_base_regions(self.original_screen_regions)
        return {
            "ok": True,
            "path": str(self.screen_regions_path),
            "screenRegionsUpdated": True,
            "message": "Saved session profile for this session only.",
        }

    def save_default_profile(self) -> dict:
        profile = reusable_profile_document(self.current_screen_regions, "default_screen_regions")
        write_json_atomic(DEFAULT_SCREEN_REGIONS_PROFILE, profile)
        counts = screen_regions_document_counts(profile)
        return {
            "ok": True,
            "path": str(DEFAULT_SCREEN_REGIONS_PROFILE),
            "profileName": profile["profileName"],
            "regionCount": counts["baseRegionCount"] + counts["tabRegionCount"],
            "baseRegionCount": counts["baseRegionCount"],
            "tabProfileCount": counts["tabProfileCount"],
            "message": "Saved default profile for future sessions.",
        }

    def load_default_profile(self) -> dict:
        profile = load_screen_regions_document(DEFAULT_SCREEN_REGIONS_PROFILE)
        self.screen_regions = deepcopy(profile)
        self.current_screen_regions = deepcopy(profile)
        self.current_regions = get_base_regions(self.current_screen_regions)
        payload = self.state_payload()
        payload["ok"] = True
        payload["path"] = str(DEFAULT_SCREEN_REGIONS_PROFILE)
        payload["profileName"] = profile.get("profileName") or DEFAULT_SCREEN_REGIONS_PROFILE.stem
        payload["message"] = (
            "Loaded default profile into the UI only. Save Session Profile or Raw write screen_regions.json "
            "to update this session."
        )
        return payload

    def export_profile(self, path_value) -> dict:
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("export profile request must include a non-empty path")

        output_path = Path(path_value).expanduser()

        if not output_path.is_absolute():
            output_path = self.calibration_dir / output_path

        try:
            output_path.resolve().relative_to(self.calibration_dir.resolve())
        except (OSError, ValueError) as error:
            raise ValueError(f"export profile path must stay under {self.calibration_dir}") from error

        profile_name = output_path.stem or "screen_regions_profile"
        profile = reusable_profile_document(self.current_screen_regions, profile_name)
        write_json_atomic(output_path, profile)
        counts = screen_regions_document_counts(profile)
        return {
            "ok": True,
            "path": str(output_path),
            "profileName": profile["profileName"],
            "regionCount": counts["baseRegionCount"] + counts["tabRegionCount"],
            "baseRegionCount": counts["baseRegionCount"],
            "tabProfileCount": counts["tabProfileCount"],
        }

    def export_preview(self) -> dict:
        image_module, draw_module, _font_module = load_pillow()

        if image_module is None or draw_module is None:
            return {
                "ok": False,
                "error": PILLOW_MISSING_MESSAGE,
            }

        globals()["Image"] = image_module
        globals()["ImageDraw"] = draw_module
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        overlay_name = f"region_overlay_tick-{self.tick_id:08d}.jpg"
        sheet_name = f"crop_contact_sheet_tick-{self.tick_id:08d}.jpg"
        overlay_path = self.calibration_dir / overlay_name
        sheet_path = self.calibration_dir / sheet_name

        with image_module.open(self.frame_path) as frame_image:
            frame = frame_image.convert("RGB")
            width, height = frame.size
            self.frame_width = width
            self.frame_height = height
            region_items = labeled_regions_for_document(self.current_screen_regions)
            pixel_boxes = pixel_boxes_for_region_items(region_items, width, height)

            if not pixel_boxes:
                raise ValueError("no valid region boxes were available after clamping")

            draw_overlay(frame, region_items, pixel_boxes, overlay_path)
            generate_contact_sheet(frame, region_items, pixel_boxes, sheet_path)

        return {
            "ok": True,
            "overlayPath": str(overlay_path),
            "contactSheetPath": str(sheet_path),
            "regionCount": len(pixel_boxes),
        }

    def prepare_test_crops(self) -> dict:
        display_command = (
            "python telemetry-viewer\\prepare_visual_perception.py "
            f"--session \"{self.session}\" "
            "--generate-crops --generate-grid-slots --latest 25 --only-existing-frames --active-tab auto"
        )
        command = [
            sys.executable,
            "telemetry-viewer\\prepare_visual_perception.py",
            "--session",
            str(self.session),
            "--generate-crops",
            "--generate-grid-slots",
            "--latest",
            "25",
            "--only-existing-frames",
            "--active-tab",
            "auto",
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            result = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                shell=False,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "ok": False,
                "command": display_command,
                "error": str(error),
            }

        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)

        if len(output) > 12000:
            output = output[-12000:]

        visual_index_path = self.paths["perceptionDir"] / "visual_perception_index.json"
        visual_index = safe_read_json(visual_index_path)
        visual_index = visual_index if isinstance(visual_index, dict) else {}
        test_crop_run_path = visual_index.get("testCropRunPath")
        crops_dir = (
            self.session / test_crop_run_path
            if isinstance(test_crop_run_path, str)
            else self.paths["perceptionDir"] / "test_crops"
        )

        return {
            "ok": result.returncode == 0,
            "command": display_command,
            "returnCode": result.returncode,
            "output": output,
            "visualPerceptionIndex": str(visual_index_path),
            "testCropRunId": visual_index.get("testCropRunId"),
            "testCropRunPath": str(crops_dir),
            "cropsDir": str(crops_dir),
            "message": "Generated disposable test crops. Training data, raw telemetry, and source frame images were not modified.",
        }

    def latest_test_crops_dir(self) -> Path | None:
        test_crops_dir = self.paths["perceptionDir"] / "test_crops"

        if not test_crops_dir.exists():
            return None

        try:
            run_dirs = [path for path in test_crops_dir.iterdir() if path.is_dir()]
        except OSError:
            return None

        if not run_dirs:
            return None

        return max(run_dirs, key=lambda path: path.stat().st_mtime)

    def open_latest_test_crops(self) -> dict:
        latest = self.latest_test_crops_dir()

        if latest is None:
            return {
                "ok": False,
                "path": str(self.paths["perceptionDir"] / "test_crops"),
                "error": "No test crop runs found yet. Use Generate Test Crops first.",
            }

        return self.open_folder(latest, "latest test crops")

    def clear_test_crops(self) -> dict:
        test_crops_dir = self.paths["perceptionDir"] / "test_crops"

        if test_crops_dir.exists():
            shutil.rmtree(test_crops_dir)

        return {
            "ok": True,
            "path": str(test_crops_dir),
            "message": "Cleared disposable test crop runs. Training data, raw telemetry, and source frame images were not modified.",
        }

    def open_folder(self, folder: Path, label: str) -> dict:
        folder = folder.expanduser()

        if not folder.exists():
            return {
                "ok": False,
                "path": str(folder),
                "error": f"{label} does not exist: {folder}",
            }

        try:
            if hasattr(os, "startfile"):
                os.startfile(str(folder))
            else:
                webbrowser.open(folder.resolve().as_uri())
        except OSError as error:
            return {
                "ok": False,
                "path": str(folder),
                "error": f"Unable to open {label}: {error}",
            }

        return {
            "ok": True,
            "path": str(folder),
            "opened": True,
        }


HOME_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Screen Region Calibration</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font: 14px system-ui, sans-serif; background: #101114; color: #eee; overflow: hidden; }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 390px; height: 100vh; }
    .stage { overflow: auto; padding: 12px; border-right: 1px solid #333842; background: #17191f; }
    .frameWrap { position: relative; display: inline-block; line-height: 0; background: #000; user-select: none; }
    #frameImage { display: block; max-width: calc(100vw - 430px); max-height: calc(100vh - 24px); width: auto; height: auto; }
    #overlay { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
    .panel { padding: 14px; overflow: auto; }
    h1 { font-size: 18px; margin: 0 0 10px; }
    h2 { font-size: 13px; margin: 18px 0 8px; color: #bac4d6; text-transform: uppercase; letter-spacing: .04em; }
    label { display: block; margin: 8px 0 4px; color: #c8ced9; }
    select, input, button { font: inherit; }
    select, input { width: 100%; background: #181b22; color: #eee; border: 1px solid #3a4050; border-radius: 4px; padding: 6px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
    .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
    button { background: #273246; color: #fff; border: 1px solid #4a5870; border-radius: 4px; padding: 7px 9px; cursor: pointer; }
    button:hover { background: #33415a; }
    button.danger { border-color: #8b3a46; background: #44232c; }
    button[disabled] { color: #8b94a5; cursor: not-allowed; background: #1a1d24; }
    .checkRow { display: grid; grid-template-columns: 1fr; gap: 6px; margin-top: 8px; }
    .checkRow label { display: flex; align-items: center; gap: 8px; margin: 0; }
    .checkRow input { width: auto; }
    .smallNote { font-size: 12px; color: #9da8ba; margin: 6px 0 0; }
    .helpBox { background: #151821; border: 1px solid #3a4050; border-radius: 4px; padding: 10px; color: #d8e2f2; margin: 10px 0 12px; }
    .helpBox p { margin: 4px 0; }
    details { border: 1px solid #303747; border-radius: 4px; padding: 8px; margin: 12px 0; background: #12151c; }
    summary { cursor: pointer; color: #d8e2f2; font-weight: 600; }
    pre { background: #151821; border: 1px solid #303747; padding: 8px; color: #d5e4ff; white-space: pre-wrap; word-break: break-word; max-height: 170px; overflow: auto; }
    .muted { color: #aab2c0; }
    .status { min-height: 1.4em; color: #b9f6ca; }
    .warning { color: #ffd180; }
    .slotLabel { pointer-events: none; user-select: none; }
  </style>
</head>
<body>
<main>
  <section class="stage">
    <div class="frameWrap" id="frameWrap">
      <img id="frameImage" src="/frame" alt="Selected captured frame">
      <svg id="overlay"></svg>
    </div>
  </section>
  <aside class="panel">
    <h1>Screen Region Calibration</h1>
    <p class="muted">Click and drag on the frame to redraw the selected region. Arrow keys move it; Shift moves 10 px; Ctrl resizes.</p>
    <div class="helpBox">
      <p><strong>Save Default Profile</strong> = future sessions.</p>
      <p><strong>Save Session Profile</strong> = this session only.</p>
      <p><strong>Generate Test Crops</strong> = preview/verification only.</p>
      <p><strong>Build Training Dataset</strong> = persistent data for model training.</p>
    </div>
    <div class="buttons">
      <button id="saveDefaultProfile">Save Default Profile</button>
      <button id="saveSession">Save Session Profile</button>
      <button id="prepareTestCrops">Generate Test Crops</button>
      <button id="openLatestTestCrops">Open Latest Test Crops</button>
      <button id="openProfileFolder">Open Default Profile Folder</button>
      <button id="openPerceptionFolder">Open Session Perception Folder</button>
    </div>
    <h2>Frame</h2>
    <div class="buttons">
      <button id="refreshLatestFrame">Refresh to newest frame</button>
      <button id="useLatestFrame">Use latest existing frame</button>
    </div>
    <label for="profileSelect">Profile</label>
    <select id="profileSelect"></select>
    <p class="smallNote">Base regions are always visible regions; tab profiles are side-panel-specific.</p>
    <div class="buttons">
      <button id="addProfile">Add tab profile</button>
      <button id="renameProfile">Rename tab profile</button>
      <button id="duplicateProfile">Duplicate tab profile</button>
      <button id="deleteProfile" class="danger">Delete tab profile</button>
    </div>
    <div class="checkRow">
      <label><input id="showBaseRegions" type="checkbox" checked> Show base regions</label>
      <label><input id="showActiveProfileRegions" type="checkbox" checked> Show active tab profile regions</label>
      <label><input id="showAllProfiles" type="checkbox"> Show all profiles</label>
    </div>
    <label for="regionSelect">Region</label>
    <select id="regionSelect"></select>
    <div class="buttons">
      <button id="addRegion">Add region</button>
      <button id="renameRegion">Rename</button>
      <button id="duplicateRegion">Duplicate</button>
      <button id="deleteRegion" class="danger">Delete</button>
    </div>
    <div class="row">
      <label>Type
        <select id="regionType">
          <option value="rect">rect</option>
          <option value="circle">circle</option>
          <option value="ellipse">ellipse</option>
          <option value="grid">grid</option>
        </select>
      </label>
      <label>Tags<input id="regionTags" placeholder="inventory, ui, minimap"></label>
    </div>
    <h2>Grid</h2>
    <div class="grid3">
      <label>rows<input id="gridRows" type="number" step="1" min="1"></label>
      <label>cols<input id="gridCols" type="number" step="1" min="1"></label>
      <label>slots<input id="gridSlotCount" type="number" step="1" min="0"></label>
    </div>
    <h2>Pixel Box</h2>
    <div class="grid4">
      <label>x<input id="pxX" type="number" step="1"></label>
      <label>y<input id="pxY" type="number" step="1"></label>
      <label>w<input id="pxW" type="number" step="1" min="1"></label>
      <label>h<input id="pxH" type="number" step="1" min="1"></label>
    </div>
    <h2>Live Values</h2>
    <pre id="typeValues"></pre>
    <h2>Normalized Box</h2>
    <pre id="normalizedValues"></pre>
    <h2>Frame</h2>
    <pre id="frameInfo"></pre>
    <details>
      <summary>Advanced</summary>
      <div class="buttons">
        <button id="save">Save calibrated copy</button>
        <button id="exportProfile">Export profile as...</button>
        <button id="loadDefaultProfile">Load default profile</button>
        <button id="resetSelected">Reset selected region</button>
        <button id="reloadOriginal">Reload original</button>
        <button id="exportPreview">Export overlay/contact sheet</button>
        <button id="clearTestCrops" class="danger">Clear old test crops</button>
        <button id="write" class="danger" title="Overwrites derived perception/screen_regions.json with the current calibrated model">Raw write screen_regions.json</button>
      </div>
      <p class="smallNote">Advanced actions are for preview copies, export/backup, restore, and explicit derived screen_regions.json writes.</p>
    </details>
    <p id="status" class="status"></p>
    <h2>State</h2>
    <pre id="state">Loading...</pre>
  </aside>
</main>
<script>
const image = document.getElementById('frameImage');
const overlay = document.getElementById('overlay');
const profileSelect = document.getElementById('profileSelect');
const regionSelect = document.getElementById('regionSelect');
const regionTypeSelect = document.getElementById('regionType');
const regionTagsInput = document.getElementById('regionTags');
const statusEl = document.getElementById('status');
const showBaseToggle = document.getElementById('showBaseRegions');
const showActiveToggle = document.getElementById('showActiveProfileRegions');
const showAllToggle = document.getElementById('showAllProfiles');
const BASE_PROFILE = '__base__';
const DEFAULT_TAB_PROFILES = ['inventory', 'equipment', 'prayer', 'magic', 'combat', 'stats', 'quests', 'friends', 'clan', 'settings', 'emotes', 'music', 'logout'];
const inputs = {
  x: document.getElementById('pxX'),
  y: document.getElementById('pxY'),
  w: document.getElementById('pxW'),
  h: document.getElementById('pxH')
};
const gridInputs = {
  rows: document.getElementById('gridRows'),
  cols: document.getElementById('gridCols'),
  slotCount: document.getElementById('gridSlotCount')
};
let appState = null;
let screenRegions = {baseRegions: {}, tabProfiles: {}};
let originalScreenRegions = {baseRegions: {}, tabProfiles: {}};
let selectedProfile = BASE_PROFILE;
let selectedRegion = null;
let dragStart = null;

function clone(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function setStatus(text, warning=false) {
  statusEl.textContent = text || '';
  statusEl.className = warning ? 'status warning' : 'status';
}

function frameSize() {
  return {
    width: image.naturalWidth || appState?.frameWidth || 0,
    height: image.naturalHeight || appState?.frameHeight || 0
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function round6(value) {
  return Math.round(value * 1000000) / 1000000;
}

function cleanName(value, fallback='region') {
  return String(value || fallback).trim().replace(/\\s+/g, '_') || fallback;
}

function cleanProfileName(value) {
  return cleanName(value, 'custom').replace(/[^A-Za-z0-9_-]/g, '_');
}

function titleCase(value) {
  return String(value || '').replace(/[_-]+/g, ' ').replace(/\\b\\w/g, letter => letter.toUpperCase());
}

function normalizeModel(raw) {
  const doc = clone(raw);
  if (!doc.baseRegions && !doc.tabProfiles && doc.regions) {
    doc.baseRegions = clone(doc.regions);
  }
  doc.baseRegions = doc.baseRegions || {};
  doc.tabProfiles = doc.tabProfiles || {};
  const defaults = appState?.defaultTabProfiles || DEFAULT_TAB_PROFILES;
  defaults.forEach(name => {
    if (!doc.tabProfiles[name]) doc.tabProfiles[name] = {};
  });
  doc.schemaVersion = doc.schemaVersion || 'perception.screen_regions.v1';
  doc.coordinateSpace = doc.coordinateSpace || 'normalized';
  doc.defaultTabProfile = doc.defaultTabProfile || 'inventory';
  delete doc.regions;
  return doc;
}

function profileNames() {
  const names = [];
  const seen = new Set();
  (appState?.defaultTabProfiles || DEFAULT_TAB_PROFILES).forEach(name => {
    if (!seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  });
  Object.keys(screenRegions.tabProfiles || {}).forEach(name => {
    if (!seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  });
  return names;
}

function profileLabel(profile) {
  if (profile === BASE_PROFILE) return appState?.baseProfileLabel || 'Base regions';
  return titleCase(profile);
}

function activeRegionMap() {
  if (selectedProfile === BASE_PROFILE) {
    screenRegions.baseRegions = screenRegions.baseRegions || {};
    return screenRegions.baseRegions;
  }
  screenRegions.tabProfiles = screenRegions.tabProfiles || {};
  if (!screenRegions.tabProfiles[selectedProfile]) screenRegions.tabProfiles[selectedProfile] = {};
  return screenRegions.tabProfiles[selectedProfile];
}

function originalProfileRegionMap() {
  if (selectedProfile === BASE_PROFILE) return originalScreenRegions.baseRegions || {};
  return (originalScreenRegions.tabProfiles || {})[selectedProfile] || {};
}

function selectedProfileIsBase() {
  return selectedProfile === BASE_PROFILE;
}

function normalizeBox(pixel) {
  const size = frameSize();
  return {
    x: round6(pixel.x / Math.max(1, size.width)),
    y: round6(pixel.y / Math.max(1, size.height)),
    w: round6(pixel.w / Math.max(1, size.width)),
    h: round6(pixel.h / Math.max(1, size.height))
  };
}

function tagsFromText(value) {
  return String(value || '').split(',').map(tag => tag.trim()).filter(Boolean);
}

function tagsText(region) {
  return Array.isArray(region?.tags) ? region.tags.join(', ') : '';
}

function validBox(box) {
  return {
    x: clamp(Number(box?.x) || 0, 0, 0.999999),
    y: clamp(Number(box?.y) || 0, 0, 0.999999),
    w: Math.max(0.000001, Number(box?.w) || 0.1),
    h: Math.max(0.000001, Number(box?.h) || 0.1)
  };
}

function clampBox(box) {
  const result = validBox(box);
  result.w = round6(Math.min(result.w, 1 - result.x));
  result.h = round6(Math.min(result.h, 1 - result.y));
  result.x = round6(result.x);
  result.y = round6(result.y);
  return result;
}

function regionType(region) {
  return ['rect', 'circle', 'ellipse', 'grid'].includes(region?.type) ? region.type : 'rect';
}

function regionBox(region) {
  if (!region) return {x: 0, y: 0, w: 0.1, h: 0.1};
  if (Number.isFinite(Number(region.x)) && Number.isFinite(Number(region.y)) && Number.isFinite(Number(region.w)) && Number.isFinite(Number(region.h))) {
    return clampBox(region);
  }
  if (region.box) return clampBox(region.box);
  if (region.type === 'circle' && region.center) {
    const radius = Math.max(0.000001, Number(region.radius) || 0.001);
    return clampBox({x: Number(region.center.x) - radius, y: Number(region.center.y) - radius, w: radius * 2, h: radius * 2});
  }
  if (region.type === 'ellipse' && region.center) {
    const radiusX = Math.max(0.000001, Number(region.radiusX) || 0.001);
    const radiusY = Math.max(0.000001, Number(region.radiusY) || 0.001);
    return clampBox({x: Number(region.center.x) - radiusX, y: Number(region.center.y) - radiusY, w: radiusX * 2, h: radiusY * 2});
  }
  return {x: 0, y: 0, w: 0.1, h: 0.1};
}

function defaultGridValues(name, existing={}) {
  const lowered = String(name || selectedProfile || '').toLowerCase();
  const isInventory = lowered.includes('inventory');
  const isPrayer = lowered.includes('prayer');
  const isEquipment = lowered.includes('equipment') || lowered.includes('equip');
  const rows = Number(existing.rows) || (isInventory ? 7 : (isPrayer ? 5 : (isEquipment ? 4 : 2)));
  const cols = Number(existing.cols) || (isInventory ? 4 : (isPrayer ? 6 : (isEquipment ? 4 : 2)));
  const slotCount = Number(existing.slotCount) || (isInventory ? 28 : rows * cols);
  return {rows, cols, slotCount};
}

function regionFromBoxForType(type, box, existing={}, name='region') {
  const tags = Array.isArray(existing.tags) ? existing.tags : [];
  const cleanBox = clampBox(box);
  if (type === 'grid') {
    const grid = defaultGridValues(name, existing);
    return {type: 'grid', box: cleanBox, rows: grid.rows, cols: grid.cols, slotCount: Math.min(grid.slotCount, grid.rows * grid.cols), tags};
  }
  if (type === 'circle') {
    const radius = round6(Math.min(cleanBox.w, cleanBox.h) / 2);
    return {type: 'circle', center: {x: round6(cleanBox.x + cleanBox.w / 2), y: round6(cleanBox.y + cleanBox.h / 2)}, radius, tags};
  }
  if (type === 'ellipse') {
    return {type: 'ellipse', center: {x: round6(cleanBox.x + cleanBox.w / 2), y: round6(cleanBox.y + cleanBox.h / 2)}, radiusX: round6(cleanBox.w / 2), radiusY: round6(cleanBox.h / 2), rotation: Number(existing.rotation) || 0, tags};
  }
  return {type: 'rect', box: cleanBox, tags};
}

function normalizeRegion(region, name='region') {
  const type = regionType(region);
  if (type === 'rect' && region?.box) return regionFromBoxForType('rect', region.box, region, name);
  if (type === 'grid' && region?.box) return regionFromBoxForType('grid', region.box, region, name);
  if (type === 'circle' && region?.center) return regionFromBoxForType('circle', regionBox(region), region, name);
  if (type === 'ellipse' && region?.center) return regionFromBoxForType('ellipse', regionBox(region), region, name);
  return regionFromBoxForType('rect', regionBox(region), region || {}, name);
}

function regionFromPixel(existing, pixel, name=selectedRegion) {
  const box = normalizeBox(cleanPixel(pixel));
  const current = normalizeRegion(existing || {}, name);
  return regionFromBoxForType(regionType(current), box, current, name);
}

function pixelBox(region) {
  const size = frameSize();
  const box = regionBox(region);
  const left = clamp(Math.round(box.x * size.width), 0, size.width);
  const upper = clamp(Math.round(box.y * size.height), 0, size.height);
  let right = clamp(Math.round((box.x + box.w) * size.width), 0, size.width);
  let lower = clamp(Math.round((box.y + box.h) * size.height), 0, size.height);
  if (right <= left) right = clamp(left + 1, 0, size.width);
  if (lower <= upper) lower = clamp(upper + 1, 0, size.height);
  return {x: left, y: upper, w: right - left, h: lower - upper};
}

function cleanPixel(pixel) {
  const size = frameSize();
  let x = Math.round(Number(pixel.x) || 0);
  let y = Math.round(Number(pixel.y) || 0);
  let w = Math.max(1, Math.round(Number(pixel.w) || 1));
  let h = Math.max(1, Math.round(Number(pixel.h) || 1));
  x = clamp(x, 0, Math.max(0, size.width - 1));
  y = clamp(y, 0, Math.max(0, size.height - 1));
  w = clamp(w, 1, size.width - x);
  h = clamp(h, 1, size.height - y);
  return {x, y, w, h};
}

function screenToPixel(event) {
  const rect = image.getBoundingClientRect();
  const size = frameSize();
  const x = clamp((event.clientX - rect.left) * size.width / rect.width, 0, size.width);
  const y = clamp((event.clientY - rect.top) * size.height / rect.height, 0, size.height);
  return {x: Math.round(x), y: Math.round(y)};
}

function overlayScale() {
  const rect = image.getBoundingClientRect();
  const size = frameSize();
  return {x: rect.width / Math.max(1, size.width), y: rect.height / Math.max(1, size.height), width: rect.width, height: rect.height};
}

function setSelectedRegion(name) {
  selectedRegion = name;
  regionSelect.value = name || '';
  updateInputs();
  drawRegions();
}

function setSelectedProfile(profile) {
  selectedProfile = profile || BASE_PROFILE;
  if (selectedProfile !== BASE_PROFILE && !screenRegions.tabProfiles[selectedProfile]) {
    screenRegions.tabProfiles[selectedProfile] = {};
  }
  selectedRegion = null;
  loadProfilesIntoSelect();
  loadRegionsIntoSelect();
  refreshStatePanel();
  drawRegions();
}

function displayEntries() {
  const entries = [];
  const seen = new Set();
  function add(scope, profile, name, region, active, tone) {
    const key = `${scope}:${profile}:${name}`;
    if (seen.has(key)) return;
    seen.add(key);
    entries.push({scope, profile, name, region, active, tone, key});
  }
  if (showBaseToggle.checked) {
    Object.entries(screenRegions.baseRegions || {}).forEach(([name, region]) => {
      add('base', BASE_PROFILE, name, region, selectedProfile === BASE_PROFILE && name === selectedRegion, 'base');
    });
  }
  if (selectedProfile !== BASE_PROFILE && showActiveToggle.checked) {
    Object.entries(activeRegionMap()).forEach(([name, region]) => {
      add('profile', selectedProfile, name, region, name === selectedRegion, 'active');
    });
  }
  if (showAllToggle.checked) {
    Object.entries(screenRegions.tabProfiles || {}).forEach(([profile, regions]) => {
      Object.entries(regions || {}).forEach(([name, region]) => {
        add('profile', profile, name, region, profile === selectedProfile && name === selectedRegion, profile === selectedProfile ? 'active' : 'other');
      });
    });
  }
  return entries;
}

function updateInputs() {
  const regions = activeRegionMap();
  if (!selectedRegion || !regions[selectedRegion]) {
    Object.values(inputs).forEach(input => input.value = '');
    regionTagsInput.value = '';
    document.getElementById('normalizedValues').textContent = '{}';
    document.getElementById('typeValues').textContent = '{}';
    return;
  }
  regions[selectedRegion] = normalizeRegion(regions[selectedRegion], selectedRegion);
  const region = regions[selectedRegion];
  const pixel = pixelBox(region);
  inputs.x.value = pixel.x;
  inputs.y.value = pixel.y;
  inputs.w.value = pixel.w;
  inputs.h.value = pixel.h;
  regionTypeSelect.value = regionType(region);
  regionTagsInput.value = tagsText(region);
  const grid = defaultGridValues(selectedRegion, region);
  gridInputs.rows.value = region.type === 'grid' ? grid.rows : '';
  gridInputs.cols.value = region.type === 'grid' ? grid.cols : '';
  gridInputs.slotCount.value = region.type === 'grid' ? grid.slotCount : '';
  gridInputs.rows.disabled = region.type !== 'grid';
  gridInputs.cols.disabled = region.type !== 'grid';
  gridInputs.slotCount.disabled = region.type !== 'grid';
  document.getElementById('normalizedValues').textContent = JSON.stringify(region, null, 2);
  document.getElementById('typeValues').textContent = JSON.stringify(typeValues(region, pixel), null, 2);
}

function typeValues(region, pixel) {
  const type = regionType(region);
  const values = {
    profile: profileLabel(selectedProfile),
    type,
    pixelBox: pixel,
    normalizedBox: regionBox(region)
  };
  if (type === 'circle') {
    values.center = {normalized: region.center, pixel: centerPixel(region)};
    values.radius = {normalized: region.radius, pixelApprox: Math.round((Number(region.radius) || 0) * Math.min(frameSize().width, frameSize().height))};
  }
  if (type === 'ellipse') {
    values.center = {normalized: region.center, pixel: centerPixel(region)};
    values.radiusX = {normalized: region.radiusX, pixel: Math.round((Number(region.radiusX) || 0) * frameSize().width)};
    values.radiusY = {normalized: region.radiusY, pixel: Math.round((Number(region.radiusY) || 0) * frameSize().height)};
    values.rotation = region.rotation || 0;
  }
  if (type === 'grid') {
    values.rows = region.rows;
    values.cols = region.cols;
    values.slotCount = region.slotCount;
    values.slotBoxes = gridSlotBoxes(region).map(slot => ({slot: slot.slot, pixelBox: pixelBox({type: 'rect', box: slot.box})}));
  }
  return values;
}

function centerPixel(region) {
  const size = frameSize();
  return {
    x: Math.round(Number(region.center?.x || 0) * size.width),
    y: Math.round(Number(region.center?.y || 0) * size.height)
  };
}

function gridSlotBoxes(region) {
  const clean = normalizeRegion(region, selectedRegion || 'grid');
  if (clean.type !== 'grid') return [];
  const rows = Math.max(1, Number(clean.rows) || 1);
  const cols = Math.max(1, Number(clean.cols) || 1);
  const slotCount = clamp(Number(clean.slotCount) || rows * cols, 0, rows * cols);
  const box = regionBox(clean);
  const slotW = box.w / cols;
  const slotH = box.h / rows;
  const slots = [];
  for (let index = 0; index < slotCount; index += 1) {
    const row = Math.floor(index / cols);
    const col = index % cols;
    slots.push({slot: index, row, col, box: clampBox({x: box.x + col * slotW, y: box.y + row * slotH, w: slotW, h: slotH})});
  }
  return slots;
}

function svgNode(name, attributes) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function drawGridLines(region, scale, selected, tone) {
  const slots = gridSlotBoxes(region);
  slots.forEach(slot => {
    const px = pixelBox({type: 'rect', box: slot.box});
    const rect = svgNode('rect', {
      x: px.x * scale.x,
      y: px.y * scale.y,
      width: px.w * scale.x,
      height: px.h * scale.y,
      fill: 'transparent',
      stroke: selected ? 'rgba(255,204,64,0.46)' : (tone === 'base' ? 'rgba(110,231,183,0.34)' : 'rgba(80,180,255,0.30)'),
      'stroke-width': 1
    });
    overlay.appendChild(rect);
    if (px.w * scale.x >= 18 && px.h * scale.y >= 14) {
      const label = svgNode('text', {
        x: px.x * scale.x + 3,
        y: px.y * scale.y + 11,
        fill: selected ? '#ffcc40' : '#dff4ff',
        'font-size': 10,
        class: 'slotLabel'
      });
      label.textContent = slot.slot;
      overlay.appendChild(label);
    }
  });
}

function styleForEntry(entry) {
  if (entry.active) return {stroke: '#ffcc40', fill: 'rgba(255,204,64,0.18)', width: 3, dash: ''};
  if (entry.tone === 'base') return {stroke: '#6ee7b7', fill: 'rgba(110,231,183,0.08)', width: 2, dash: '7 5'};
  if (entry.tone === 'other') return {stroke: '#a78bfa', fill: 'rgba(167,139,250,0.06)', width: 1.5, dash: '4 5'};
  return {stroke: '#50b4ff', fill: 'rgba(80,180,255,0.10)', width: 2, dash: ''};
}

function drawRegions() {
  const scale = overlayScale();
  overlay.setAttribute('viewBox', `0 0 ${scale.width} ${scale.height}`);
  overlay.style.width = `${scale.width}px`;
  overlay.style.height = `${scale.height}px`;
  overlay.innerHTML = '';
  displayEntries().forEach(entry => {
    const clean = normalizeRegion(entry.region, entry.name);
    const px = pixelBox(clean);
    const style = styleForEntry(entry);
    let shape;
    if (clean.type === 'circle' || clean.type === 'ellipse') {
      shape = svgNode('ellipse', {
        cx: (px.x + px.w / 2) * scale.x,
        cy: (px.y + px.h / 2) * scale.y,
        rx: Math.max(1, (px.w / 2) * scale.x),
        ry: Math.max(1, (px.h / 2) * scale.y),
        fill: style.fill,
        stroke: style.stroke,
        'stroke-width': style.width,
        'stroke-dasharray': style.dash
      });
      if (clean.type === 'ellipse' && clean.rotation) {
        shape.setAttribute('transform', `rotate(${clean.rotation} ${(px.x + px.w / 2) * scale.x} ${(px.y + px.h / 2) * scale.y})`);
      }
    } else {
      shape = svgNode('rect', {
        x: px.x * scale.x,
        y: px.y * scale.y,
        width: px.w * scale.x,
        height: px.h * scale.y,
        fill: style.fill,
        stroke: style.stroke,
        'stroke-width': style.width,
        'stroke-dasharray': style.dash
      });
    }
    overlay.appendChild(shape);
    if (clean.type === 'grid') drawGridLines(clean, scale, entry.active, entry.tone);
    const label = svgNode('text', {
      x: px.x * scale.x + 5,
      y: px.y * scale.y + 16,
      fill: style.stroke,
      'font-size': 13
    });
    label.textContent = `${entry.tone === 'base' ? 'base' : entry.profile}.${entry.name} (${clean.type})`;
    overlay.appendChild(label);
  });
}

function setRegionFromPixel(name, pixel) {
  if (!name) return;
  const regions = activeRegionMap();
  regions[name] = regionFromPixel(regions[name], pixel, name);
  updateInputs();
  drawRegions();
}

async function pushRegions() {
  const size = frameSize();
  const response = await fetch('/api/regions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({screenRegions, frameWidth: size.width, frameHeight: size.height})
  });
  const result = await response.json();
  if (!response.ok) {
    setStatus(result.error || 'Unable to update regions', true);
    return null;
  }
  appState = {...appState, documentCounts: result.documentCounts, profileNames: result.profileNames};
  return result;
}

function updateFrameInfo() {
  const size = frameSize();
  document.getElementById('frameInfo').textContent = JSON.stringify({
    naturalWidth: size.width,
    naturalHeight: size.height,
    selectedTickId: appState?.selectedTickId,
    framePath: appState?.framePath
  }, null, 2);
}

function refreshStatePanel() {
  document.getElementById('state').textContent = JSON.stringify({
    sessionPath: appState?.sessionPath,
    outputPaths: appState?.outputPaths,
    persistence: appState?.persistence,
    selectedProfile: profileLabel(selectedProfile),
    editableRegionCount: Object.keys(activeRegionMap()).length,
    documentCounts: appState?.documentCounts || {
      baseRegionCount: Object.keys(screenRegions.baseRegions || {}).length,
      tabProfileCount: Object.keys(screenRegions.tabProfiles || {}).length,
      tabRegionCount: Object.values(screenRegions.tabProfiles || {}).reduce((total, regions) => total + Object.keys(regions || {}).length, 0)
    },
    warnings: appState?.warnings
  }, null, 2);
}

function loadProfilesIntoSelect() {
  const prior = selectedProfile;
  profileSelect.innerHTML = '';
  const baseOption = document.createElement('option');
  baseOption.value = BASE_PROFILE;
  baseOption.textContent = profileLabel(BASE_PROFILE);
  profileSelect.appendChild(baseOption);
  profileNames().forEach(name => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = profileLabel(name);
    profileSelect.appendChild(option);
  });
  selectedProfile = prior && (prior === BASE_PROFILE || profileNames().includes(prior)) ? prior : BASE_PROFILE;
  profileSelect.value = selectedProfile;
}

function loadRegionsIntoSelect() {
  const prior = selectedRegion;
  const regions = activeRegionMap();
  regionSelect.innerHTML = '';
  Object.keys(regions).forEach(name => {
    regions[name] = normalizeRegion(regions[name], name);
    const option = document.createElement('option');
    option.value = name;
    option.textContent = `${name} (${regionType(regions[name])})`;
    regionSelect.appendChild(option);
  });
  const names = Object.keys(regions);
  if (names.length === 0) {
    selectedRegion = null;
    updateInputs();
    updateFrameInfo();
    drawRegions();
    return;
  }
  setSelectedRegion(names.includes(prior) ? prior : names[0]);
}

function updateFrameImage() {
  const frameUrl = appState?.frameUrl || '/frame';
  image.src = `${frameUrl}?tick=${appState?.selectedTickId || 'latest'}&v=${Date.now()}`;
}

function applyServerState(payload, options={}) {
  const priorProfile = selectedProfile;
  const priorRegion = selectedRegion;
  appState = payload;
  screenRegions = normalizeModel(appState.screenRegions || {baseRegions: appState.currentRegions || {}, tabProfiles: {}});
  originalScreenRegions = normalizeModel(appState.originalScreenRegions || {baseRegions: appState.originalRegions || {}, tabProfiles: {}});

  if (options.preserveSelection && priorProfile && (priorProfile === BASE_PROFILE || Object.prototype.hasOwnProperty.call(screenRegions.tabProfiles || {}, priorProfile))) {
    selectedProfile = priorProfile;
  } else {
    selectedProfile = screenRegions.defaultTabProfile || appState.defaultTabProfile || BASE_PROFILE;
    if (!screenRegions.tabProfiles[selectedProfile]) selectedProfile = BASE_PROFILE;
  }

  selectedRegion = options.preserveSelection ? priorRegion : selectedRegion;
  loadProfilesIntoSelect();
  loadRegionsIntoSelect();
  if (options.refreshImage) updateFrameImage();
  updateFrameInfo();
  refreshStatePanel();
}

async function loadState() {
  const response = await fetch('/api/state');
  const payload = await response.json();
  applyServerState(payload, {refreshImage: true});
}

function uniqueRegionName(base) {
  const regions = activeRegionMap();
  const root = cleanName(base, 'region');
  if (!regions[root]) return root;
  let index = 2;
  while (regions[`${root}_${index}`]) index += 1;
  return `${root}_${index}`;
}

function uniqueProfileName(base) {
  const root = cleanProfileName(base);
  const names = new Set(profileNames());
  if (!names.has(root)) return root;
  let index = 2;
  while (names.has(`${root}_${index}`)) index += 1;
  return `${root}_${index}`;
}

function applySelectedMetadata() {
  const regions = activeRegionMap();
  if (!selectedRegion || !regions[selectedRegion]) return;
  const current = normalizeRegion(regions[selectedRegion], selectedRegion);
  current.tags = tagsFromText(regionTagsInput.value);
  if (current.type === 'grid') {
    const defaults = defaultGridValues(selectedRegion, current);
    const rows = Math.max(1, Math.round(Number(gridInputs.rows.value) || defaults.rows));
    const cols = Math.max(1, Math.round(Number(gridInputs.cols.value) || defaults.cols));
    const slotCount = clamp(Math.round(Number(gridInputs.slotCount.value) || defaults.slotCount), 0, rows * cols);
    current.rows = rows;
    current.cols = cols;
    current.slotCount = slotCount;
  }
  regions[selectedRegion] = normalizeRegion(current, selectedRegion);
  updateInputs();
  drawRegions();
}

async function pushAndReport(message) {
  await pushRegions();
  if (message) setStatus(message);
  refreshStatePanel();
}

function applyVisibleInputs() {
  if (!selectedRegion) return;
  if (inputs.x.value !== '' && inputs.y.value !== '' && inputs.w.value !== '' && inputs.h.value !== '') {
    setRegionFromPixel(selectedRegion, {x: inputs.x.value, y: inputs.y.value, w: inputs.w.value, h: inputs.h.value});
  }
  applySelectedMetadata();
}

async function refreshLatestFrame(buttonLabel) {
  applyVisibleInputs();
  await pushRegions();
  const response = await fetch('/api/refresh-latest-frame', {method: 'POST'});
  const result = await response.json();

  if (!result.ok) {
    setStatus(result.error || 'No retained frame found yet. Log in and wait a few ticks, then refresh calibration.', true);
    return;
  }

  applyServerState(result, {preserveSelection: true, refreshImage: true});
  setStatus(`${buttonLabel}: ${result.message || `using tick ${result.selectedTickId}`}`);
}

async function openFolderEndpoint(endpoint, label) {
  const response = await fetch(endpoint);
  const result = await response.json();
  if (result.ok) {
    setStatus(`${label}: ${result.path}`);
  } else {
    setStatus(result.error || `${label}: ${result.path || 'unavailable'}`, true);
  }
}

async function prepareTestCrops() {
  applyVisibleInputs();
  await pushRegions();
  setStatus('Generating disposable test crops...');
  const response = await fetch('/api/prepare-test-crops', {method: 'POST'});
  const result = await response.json();
  document.getElementById('state').textContent = JSON.stringify(result, null, 2);
  setStatus(
    result.ok
      ? `${result.message || 'Generated test crops.'} ${result.testCropRunPath || result.cropsDir || ''}`
      : `Unable to generate test crops. ${result.command || ''} ${result.error || ''}`,
    !result.ok
  );
}

profileSelect.addEventListener('change', () => setSelectedProfile(profileSelect.value));
regionSelect.addEventListener('change', () => setSelectedRegion(regionSelect.value));
[showBaseToggle, showActiveToggle, showAllToggle].forEach(toggle => toggle.addEventListener('change', drawRegions));

document.getElementById('refreshLatestFrame').addEventListener('click', () => refreshLatestFrame('Refreshed to newest frame'));
document.getElementById('useLatestFrame').addEventListener('click', () => refreshLatestFrame('Using latest existing frame'));
document.getElementById('prepareTestCrops').addEventListener('click', prepareTestCrops);
document.getElementById('openLatestTestCrops').addEventListener('click', () => openFolderEndpoint('/api/open-latest-test-crops', 'Latest test crops'));
document.getElementById('openPerceptionFolder').addEventListener('click', () => openFolderEndpoint('/api/open-perception-folder', 'Perception folder'));
document.getElementById('openProfileFolder').addEventListener('click', () => openFolderEndpoint('/api/open-profile-folder', 'Profile folder'));

document.getElementById('addProfile').addEventListener('click', async () => {
  const requestedName = prompt('New tab profile name:', 'custom');
  if (!requestedName) return;
  const name = uniqueProfileName(requestedName);
  screenRegions.tabProfiles[name] = {};
  setSelectedProfile(name);
  await pushAndReport(`Added tab profile ${name}`);
});

document.getElementById('renameProfile').addEventListener('click', async () => {
  if (selectedProfileIsBase()) {
    setStatus('Base regions cannot be renamed.', true);
    return;
  }
  const requestedName = prompt('Rename tab profile:', selectedProfile);
  if (!requestedName || requestedName === selectedProfile) return;
  const name = uniqueProfileName(requestedName);
  screenRegions.tabProfiles[name] = activeRegionMap();
  delete screenRegions.tabProfiles[selectedProfile];
  selectedProfile = name;
  loadProfilesIntoSelect();
  loadRegionsIntoSelect();
  await pushAndReport(`Renamed tab profile to ${name}`);
});

document.getElementById('duplicateProfile').addEventListener('click', async () => {
  const requestedName = prompt('Duplicate tab profile as:', selectedProfileIsBase() ? 'custom_copy' : `${selectedProfile}_copy`);
  if (!requestedName) return;
  const name = uniqueProfileName(requestedName);
  screenRegions.tabProfiles[name] = clone(activeRegionMap());
  setSelectedProfile(name);
  await pushAndReport(`Duplicated profile as ${name}`);
});

document.getElementById('deleteProfile').addEventListener('click', async () => {
  if (selectedProfileIsBase()) {
    setStatus('Base regions cannot be deleted.', true);
    return;
  }
  if (!confirm(`Delete tab profile "${selectedProfile}" from the in-memory calibration set? Built-in empty profiles may still appear as available choices.`)) return;
  delete screenRegions.tabProfiles[selectedProfile];
  selectedProfile = BASE_PROFILE;
  loadProfilesIntoSelect();
  loadRegionsIntoSelect();
  await pushAndReport('Deleted tab profile');
});

document.getElementById('addRegion').addEventListener('click', async () => {
  const requestedName = prompt('New region name:', selectedProfile === 'inventory' ? 'inventoryGrid' : 'newRegion');
  if (!requestedName) return;
  const regions = activeRegionMap();
  const name = uniqueRegionName(requestedName);
  const loweredName = name.toLowerCase();
  const selectedType = regionTypeSelect.value || 'rect';
  const shouldDefaultGrid = loweredName.includes('grid') || selectedProfile === 'inventory' || selectedProfile === 'prayer';
  const type = selectedType === 'rect' && shouldDefaultGrid ? 'grid' : selectedType;
  const box = {x: 0.1, y: 0.1, w: 0.2, h: 0.2};
  regions[name] = regionFromBoxForType(type, box, {tags: tagsFromText(regionTagsInput.value)}, name);
  loadRegionsIntoSelect();
  setSelectedRegion(name);
  await pushAndReport(`Added ${name} to ${profileLabel(selectedProfile)}`);
});

document.getElementById('renameRegion').addEventListener('click', async () => {
  const regions = activeRegionMap();
  const originals = originalProfileRegionMap();
  if (!selectedRegion) return;
  const requestedName = prompt('Rename region:', selectedRegion);
  if (!requestedName || requestedName === selectedRegion) return;
  const name = uniqueRegionName(requestedName);
  regions[name] = regions[selectedRegion];
  delete regions[selectedRegion];
  if (originals[selectedRegion] && !originals[name]) {
    originals[name] = originals[selectedRegion];
    delete originals[selectedRegion];
  }
  selectedRegion = name;
  loadRegionsIntoSelect();
  await pushAndReport(`Renamed region to ${name}`);
});

document.getElementById('duplicateRegion').addEventListener('click', async () => {
  const regions = activeRegionMap();
  if (!selectedRegion || !regions[selectedRegion]) return;
  const name = uniqueRegionName(`${selectedRegion}_copy`);
  regions[name] = clone(regions[selectedRegion]);
  loadRegionsIntoSelect();
  setSelectedRegion(name);
  await pushAndReport(`Duplicated ${selectedRegion} as ${name}`);
});

document.getElementById('deleteRegion').addEventListener('click', async () => {
  const regions = activeRegionMap();
  if (!selectedRegion) return;
  if (!confirm(`Delete region "${selectedRegion}" from ${profileLabel(selectedProfile)}?`)) return;
  delete regions[selectedRegion];
  selectedRegion = null;
  loadRegionsIntoSelect();
  await pushAndReport('Deleted region');
});

regionTypeSelect.addEventListener('change', async () => {
  const regions = activeRegionMap();
  if (!selectedRegion || !regions[selectedRegion]) return;
  regions[selectedRegion] = regionFromBoxForType(regionTypeSelect.value, regionBox(regions[selectedRegion]), regions[selectedRegion], selectedRegion);
  loadRegionsIntoSelect();
  setSelectedRegion(selectedRegion);
  await pushAndReport(`Changed ${selectedRegion} to ${regionTypeSelect.value}`);
});

regionTagsInput.addEventListener('change', async () => {
  applySelectedMetadata();
  await pushAndReport(`Updated tags for ${selectedRegion}`);
});

Object.values(gridInputs).forEach(input => {
  input.addEventListener('change', async () => {
    applySelectedMetadata();
    await pushAndReport(`Updated grid settings for ${selectedRegion}`);
  });
});

Object.values(inputs).forEach(input => {
  input.addEventListener('change', async () => {
    setRegionFromPixel(selectedRegion, {x: inputs.x.value, y: inputs.y.value, w: inputs.w.value, h: inputs.h.value});
    await pushRegions();
  });
});

overlay.addEventListener('pointerdown', event => {
  overlay.setPointerCapture(event.pointerId);
  dragStart = screenToPixel(event);
});

overlay.addEventListener('pointermove', event => {
  if (!dragStart || !selectedRegion) return;
  const current = screenToPixel(event);
  const x = Math.min(dragStart.x, current.x);
  const y = Math.min(dragStart.y, current.y);
  const w = Math.abs(current.x - dragStart.x);
  const h = Math.abs(current.y - dragStart.y);
  setRegionFromPixel(selectedRegion, {x, y, w: Math.max(1, w), h: Math.max(1, h)});
});

overlay.addEventListener('pointerup', async event => {
  if (!dragStart) return;
  dragStart = null;
  overlay.releasePointerCapture(event.pointerId);
  await pushRegions();
});

document.addEventListener('keydown', async event => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  const regions = activeRegionMap();
  if (!selectedRegion || !regions[selectedRegion] || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
  event.preventDefault();
  const amount = event.shiftKey ? 10 : 1;
  const pixel = pixelBox(regions[selectedRegion]);
  if (event.ctrlKey) {
    if (event.key === 'ArrowLeft') pixel.w -= amount;
    if (event.key === 'ArrowRight') pixel.w += amount;
    if (event.key === 'ArrowUp') pixel.h -= amount;
    if (event.key === 'ArrowDown') pixel.h += amount;
  } else {
    if (event.key === 'ArrowLeft') pixel.x -= amount;
    if (event.key === 'ArrowRight') pixel.x += amount;
    if (event.key === 'ArrowUp') pixel.y -= amount;
    if (event.key === 'ArrowDown') pixel.y += amount;
  }
  setRegionFromPixel(selectedRegion, pixel);
  await pushRegions();
});

document.getElementById('save').addEventListener('click', async () => {
  await pushRegions();
  const response = await fetch('/api/save-calibrated', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `Saved ${result.path}` : result.error, !result.ok);
});

document.getElementById('saveSession').addEventListener('click', async () => {
  const confirmed = confirm('Write the current calibrated model to this session\\'s derived perception/screen_regions.json? Session calibration affects this session only. Raw telemetry and source frames are not modified.');
  if (!confirmed) return;
  await pushRegions();
  const response = await fetch('/api/save-session-calibration', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `${result.message} ${result.path}` : result.error, !result.ok);
  if (result.ok) await loadState();
});

document.getElementById('saveDefaultProfile').addEventListener('click', async () => {
  const confirmed = confirm('Save the full baseRegions/tabProfiles model as telemetry-viewer/calibration_profiles/default_screen_regions.json? Default profile initializes future sessions; existing sessions keep their own screen_regions.json.');
  if (!confirmed) return;
  await pushRegions();
  const response = await fetch('/api/save-default-profile', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `${result.message} ${result.path}` : result.error, !result.ok);
});

document.getElementById('clearTestCrops').addEventListener('click', async () => {
  const confirmed = confirm('Delete old disposable test crop runs for this session? Training data, raw telemetry, and source frame images are not modified.');
  if (!confirmed) return;
  const response = await fetch('/api/clear-test-crops', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `${result.message} ${result.path}` : result.error, !result.ok);
});

document.getElementById('loadDefaultProfile').addEventListener('click', async () => {
  const confirmed = confirm('Load the default profile into this UI session? This does not overwrite perception/screen_regions.json until you explicitly save or write it.');
  if (!confirmed) return;
  const response = await fetch('/api/load-default-profile', {method: 'POST'});
  const result = await response.json();
  if (result.ok) {
    appState = result;
    screenRegions = normalizeModel(result.screenRegions || {baseRegions: result.currentRegions || {}, tabProfiles: {}});
    selectedProfile = screenRegions.defaultTabProfile || 'inventory';
    loadProfilesIntoSelect();
    loadRegionsIntoSelect();
    refreshStatePanel();
    drawRegions();
  }
  setStatus(result.ok ? `${result.message} ${result.path}` : result.error, !result.ok);
});

document.getElementById('exportProfile').addEventListener('click', async () => {
  const defaultPath = `${appState?.outputPaths?.calibrationDir || ''}/exported_screen_regions.json`;
  const path = prompt('Export profile path:', defaultPath);
  if (!path) return;
  await pushRegions();
  const response = await fetch('/api/export-profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path})
  });
  const result = await response.json();
  setStatus(result.ok ? `Exported profile ${result.path}` : result.error, !result.ok);
});

document.getElementById('resetSelected').addEventListener('click', async () => {
  const regions = activeRegionMap();
  const originals = originalProfileRegionMap();
  if (selectedRegion && originals[selectedRegion]) {
    regions[selectedRegion] = clone(originals[selectedRegion]);
    updateInputs();
    drawRegions();
    await pushRegions();
    setStatus(`Reset ${selectedRegion}`);
  }
});

document.getElementById('reloadOriginal').addEventListener('click', async () => {
  screenRegions = normalizeModel(originalScreenRegions);
  loadProfilesIntoSelect();
  loadRegionsIntoSelect();
  await pushRegions();
  setStatus('Reloaded original screen region model');
});

document.getElementById('exportPreview').addEventListener('click', async () => {
  await pushRegions();
  const response = await fetch('/api/overlay', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `Exported ${result.overlayPath}` : result.error, !result.ok);
});

document.getElementById('write').addEventListener('click', async () => {
  const confirmed = confirm('Overwrite this session\\'s derived perception/screen_regions.json with the current calibrated model? Existing sessions keep their own screen_regions.json. Raw telemetry and source frames are not modified.');
  if (!confirmed) return;
  await pushRegions();
  const response = await fetch('/api/write-screen-regions', {method: 'POST'});
  const result = await response.json();
  setStatus(result.ok ? `${result.message} Wrote ${result.path}` : result.error, !result.ok);
  if (result.ok) await loadState();
});

image.addEventListener('load', async () => {
  updateFrameInfo();
  updateInputs();
  drawRegions();
  await pushRegions();
});

window.addEventListener('resize', drawRegions);
loadState();
</script>
</body>
</html>
"""


def send_json_response(handler: BaseHTTPRequestHandler, payload: dict, *, status: int = 200) -> None:
    data = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def send_text_response(handler: BaseHTTPRequestHandler, content: str, content_type: str, *, status: int = 200) -> None:
    data = content.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def read_json_request(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")

    if length <= 0:
        return {}

    data = handler.rfile.read(length)
    return json.loads(data.decode("utf-8"))


def calibration_handler(state: InteractiveCalibrationState):
    class CalibrationHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/":
                send_text_response(self, HOME_HTML, "text/html; charset=utf-8")
                return

            if parsed.path == "/api/state":
                send_json_response(self, state.state_payload())
                return

            if parsed.path == "/api/latest-frame":
                send_json_response(self, state.latest_frame_info())
                return

            if parsed.path == "/api/open-perception-folder":
                send_json_response(self, state.open_folder(state.paths["perceptionDir"], "perception folder"))
                return

            if parsed.path == "/api/open-profile-folder":
                send_json_response(self, state.open_folder(CALIBRATION_PROFILES_DIR, "calibration profile folder"))
                return

            if parsed.path == "/api/open-latest-test-crops":
                send_json_response(self, state.open_latest_test_crops())
                return

            if parsed.path == "/frame":
                self.serve_frame()
                return

            send_json_response(self, {"ok": False, "error": "not found"}, status=404)

        def do_POST(self):
            parsed = urlparse(self.path)

            try:
                if parsed.path == "/api/regions":
                    payload = read_json_request(self)
                    regions = payload.get("regions") if isinstance(payload, dict) else None
                    screen_regions = payload.get("screenRegions") if isinstance(payload, dict) else None
                    frame_width = payload.get("frameWidth") if isinstance(payload, dict) else None
                    frame_height = payload.get("frameHeight") if isinstance(payload, dict) else None
                    send_json_response(
                        self,
                        state.update_regions(
                            regions,
                            screen_regions=screen_regions,
                            frame_width=frame_width,
                            frame_height=frame_height,
                        ),
                    )
                    return

                if parsed.path == "/api/refresh-latest-frame":
                    send_json_response(self, state.refresh_latest_frame())
                    return

                if parsed.path == "/api/save-calibrated":
                    send_json_response(self, state.save_calibrated())
                    return

                if parsed.path == "/api/save-session-calibration":
                    send_json_response(self, state.save_session_calibration())
                    return

                if parsed.path == "/api/save-default-profile":
                    send_json_response(self, state.save_default_profile())
                    return

                if parsed.path == "/api/load-default-profile":
                    send_json_response(self, state.load_default_profile())
                    return

                if parsed.path == "/api/export-profile":
                    payload = read_json_request(self)
                    path_value = payload.get("path") if isinstance(payload, dict) else None
                    send_json_response(self, state.export_profile(path_value))
                    return

                if parsed.path == "/api/overlay":
                    result = state.export_preview()
                    send_json_response(self, result, status=200 if result.get("ok") else 501)
                    return

                if parsed.path == "/api/prepare-test-crops":
                    result = state.prepare_test_crops()
                    send_json_response(self, result, status=200 if result.get("ok") else 500)
                    return

                if parsed.path == "/api/clear-test-crops":
                    send_json_response(self, state.clear_test_crops())
                    return

                if parsed.path == "/api/write-screen-regions":
                    send_json_response(self, state.write_screen_regions())
                    return
            except (OSError, ValueError, json.JSONDecodeError) as error:
                send_json_response(self, {"ok": False, "error": str(error)}, status=400)
                return

            send_json_response(self, {"ok": False, "error": "not found"}, status=404)

        def serve_frame(self):
            try:
                data = state.frame_path.read_bytes()
            except OSError as error:
                send_json_response(self, {"ok": False, "error": str(error)}, status=404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return CalibrationHandler


def run_interactive_server(session: Path, args) -> int:
    source, warnings = load_calibration_source(session, args)
    state = InteractiveCalibrationState(session, source, warnings)
    server = ThreadingHTTPServer((INTERACTIVE_HOST, args.port), calibration_handler(state))
    url = f"http://{INTERACTIVE_HOST}:{args.port}/"

    print(f"Screen region calibration UI: {url}")
    print(f"  session: {session}")
    print(f"  selectedTick: {state.tick_id}")
    print(f"  frame: {state.frame_path}")
    print("  bind: 127.0.0.1 only")
    print("  press Ctrl+C to stop")

    if warnings:
        print("  warnings:")

        for warning in warnings[:20]:
            print(f"    - {warning}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping calibration UI.")
    finally:
        server.server_close()

    return 0


def build_calibration_preview(session: Path, args) -> tuple[dict, list[str]]:
    image_module, draw_module, _font_module = load_pillow()

    if image_module is None or draw_module is None:
        raise RuntimeError(PILLOW_MISSING_MESSAGE)

    globals()["Image"] = image_module
    globals()["ImageDraw"] = draw_module

    paths = perception_paths(session)
    tick_bundles_path = paths["tickBundles"]
    screen_regions_path = paths["screenRegions"]
    calibration_dir = paths["calibrationDir"]
    warnings = []

    if not tick_bundles_path.exists() or not screen_regions_path.exists():
        raise FileNotFoundError(MISSING_PERCEPTION_MESSAGE)

    screen_regions = load_screen_regions_document(screen_regions_path)

    bundle, lookup_warnings = find_selected_bundle(session, tick_bundles_path, args.tick)
    warnings.extend(lookup_warnings)

    if bundle is None:
        raise ValueError("no tick bundle with an existing frame file was found")

    tick_id = bundle.get("tickId")
    frame_path = frame_path_for_bundle(session, bundle)

    if frame_path is None:
        raise ValueError(f"tick {tick_id} does not contain a usable frame path")

    if not frame_path.exists():
        raise ValueError(f"tick {tick_id} frame file does not exist: {frame_path}")

    temp_dir = calibration_dir / f".tmp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    overlay_name = f"region_overlay_tick-{tick_id:08d}.jpg"
    sheet_name = f"crop_contact_sheet_tick-{tick_id:08d}.jpg"
    calibrated_name = "calibrated_screen_regions.json"
    pixel_boxes = {}
    adjustments_applied = []

    try:
        with image_module.open(frame_path) as frame_image:
            frame = frame_image.convert("RGB")
            width, height = frame.size
            calibrated_regions, adjustments_applied = apply_adjustments(screen_regions, width, height, args)
            adjusted_regions = get_base_regions(calibrated_regions)

            if not isinstance(adjusted_regions, dict):
                raise ValueError("calibrated screen regions did not contain baseRegions")

            requested_regions = parse_region_filter(args.regions)
            region_items, region_warnings = selected_regions(adjusted_regions, requested_regions)
            warnings.extend(region_warnings)

            if not region_items:
                raise ValueError("no valid regions selected")

            for name, normalized_box in region_items:
                calculated_box = pixel_box(normalized_box, width, height)

                if calculated_box is None:
                    warnings.append(f"region {name} has no valid box after clamping")
                    continue

                pixel_boxes[name] = calculated_box

            if not pixel_boxes:
                raise ValueError("no valid region boxes were available after clamping")

            draw_overlay(frame, region_items, pixel_boxes, temp_dir / overlay_name)
            generate_contact_sheet(frame, region_items, pixel_boxes, temp_dir / sheet_name)

        calibrated_output = add_calibration_metadata(
            calibrated_regions,
            tick_id=tick_id,
            frame_path=frame_path,
            width=width,
            height=height,
            adjustments=adjustments_applied,
            pixel_boxes=pixel_boxes,
        )
        write_json(temp_dir / calibrated_name, calibrated_output)
        publish_outputs(temp_dir, calibration_dir, (overlay_name, sheet_name, calibrated_name))

        if args.write_screen_regions:
            write_screen_regions_atomic(screen_regions_path, calibrated_regions)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    output = {
        "sessionPath": str(session),
        "tickId": tick_id,
        "framePath": str(frame_path),
        "overlayPath": str(calibration_dir / overlay_name),
        "contactSheetPath": str(calibration_dir / sheet_name),
        "calibratedScreenRegionsPath": str(calibration_dir / calibrated_name),
        "regionCount": len(pixel_boxes),
        "adjustmentCount": len(adjustments_applied),
        "screenRegionsUpdated": bool(args.write_screen_regions),
    }

    return output, warnings


def parse_args():
    parser = argparse.ArgumentParser(description="Render preview images for calibrating derived screen regions.")
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override the telemetry sessions directory when --session is omitted.")
    parser.add_argument("--tick", type=parse_tick_id, help="Use this tick id instead of the latest retained frame.")
    parser.add_argument("--interactive", action="store_true", help="Start a local browser calibration UI on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=DEFAULT_INTERACTIVE_PORT, help="Interactive server port. Default: 8770.")
    parser.add_argument(
        "--latest-existing-frame",
        action="store_true",
        help="Select the latest tick whose frame file exists. This is the default when --tick is omitted.",
    )
    parser.add_argument("--regions", help="Comma-separated region names to include, such as inventory,minimap,chatbox.")
    parser.add_argument(
        "--nudge",
        nargs=5,
        action="append",
        metavar=("REGION", "DX", "DY", "DW", "DH"),
        help="Adjust one region by pixel deltas, then normalize against the selected frame.",
    )
    parser.add_argument(
        "--set-region",
        nargs=5,
        action="append",
        metavar=("REGION", "X", "Y", "W", "H"),
        help="Set one region directly with normalized x y w h values.",
    )
    parser.add_argument(
        "--output-calibrated",
        action="store_true",
        help="Write calibrated_screen_regions.json. This preview tool writes it by default for inspection.",
    )
    parser.add_argument(
        "--write-screen-regions",
        action="store_true",
        help="Explicitly write adjusted regions back to perception\\screen_regions.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    if args.interactive:
        try:
            return run_interactive_server(session, args)
        except FileNotFoundError as error:
            print(f"session: {session}")
            print(error)
            return 1
        except (OSError, ValueError) as error:
            print(f"Unable to start calibration UI: {error}")
            return 1

    try:
        output, warnings = build_calibration_preview(session, args)
    except RuntimeError as error:
        print(error)
        return 1
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(error)
        return 1
    except (OSError, ValueError) as error:
        print(f"Unable to calibrate screen regions: {error}")
        return 1

    print(f"Rendered screen-region calibration preview: {output['sessionPath']}")
    print(f"  selectedTick: {output['tickId']}")
    print(f"  frame: {output['framePath']}")
    print(f"  overlay: {output['overlayPath']}")
    print(f"  contactSheet: {output['contactSheetPath']}")
    print(f"  calibratedScreenRegions: {output['calibratedScreenRegionsPath']}")
    print(f"  regionCount: {output['regionCount']}")
    print(f"  adjustmentCount: {output['adjustmentCount']}")

    if output["screenRegionsUpdated"]:
        print("  original screen_regions.json was updated because --write-screen-regions was used")
    else:
        print("  original screen_regions.json was not changed")

    if warnings:
        print("  warnings:")

        for warning in warnings[:20]:
            print(f"    - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

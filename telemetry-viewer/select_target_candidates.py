import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import inspect_target_geometry as geometry
from telemetry_paths import find_newest_session, get_sessions_dir, iter_jsonl, list_tick_files


SCHEMA_VERSION_INDEX = "interaction_geometry.target_candidates_index.v1"
SCHEMA_VERSION_RECORD = "interaction_geometry.target_candidate.v1"
PACKET_SCHEMA_VERSION = "target_candidate.v1"
DEFAULT_TARGET_LIBRARY_PATH = Path(__file__).resolve().with_name("target_library.json")
DEFAULT_TARGET_PROFILES_PATH = Path(__file__).resolve().with_name("target_profiles.json")
TARGET_TYPES = geometry.TARGET_TYPES
TARGET_ROLES = geometry.TARGET_ROLES
GEOMETRY_PRIORITY = (
    "clickableHull",
    "clickboxPolygon",
    "clickboxBounds",
    "convexHull",
    "convexHullPolygon",
    "convexHullBounds",
    "tilePolygon",
    "canvasTilePolygon",
    "pixelBox",
    "canvasPoint",
    "canvasLocation",
    "canvasCenter",
    "center",
    "boundingBox",
    "bounds",
)
GEOMETRY_QUALITY = {
    "clickableHull": 1.0,
    "clickboxPolygon": 1.0,
    "clickboxBounds": 0.95,
    "convexHull": 0.9,
    "convexHullPolygon": 0.9,
    "convexHullBounds": 0.85,
    "tilePolygon": 0.75,
    "canvasTilePolygon": 0.75,
    "pixelBox": 0.8,
    "canvasPoint": 0.55,
    "canvasLocation": 0.55,
    "canvasCenter": 0.55,
    "center": 0.55,
    "boundingBox": 0.65,
    "bounds": 0.65,
}
POLYGON_GEOMETRY_TYPES = {"clickableHull", "clickboxPolygon", "convexHull", "convexHullPolygon", "tilePolygon", "canvasTilePolygon"}
BOUNDS_GEOMETRY_TYPES = {"clickboxBounds", "convexHullBounds", "pixelBox", "boundingBox", "bounds"}
POINT_GEOMETRY_TYPES = {"canvasPoint", "canvasLocation", "canvasCenter", "center"}
WORLD_DISTANCE_ROLES = {"entity", "interactable", "item"}
WORLD_DISTANCE_TYPES = {"npc", "player", "sceneObject", "groundItem", "tile"}
UNKNOWN_CLASS_IDS = {"unknown_scene_object", "unclassified_scene_object"}
BLOCKING_UI_NAME_PARTS = (
    "minimap",
    "chatbox",
    "sidepanel",
    "side_panel",
    "side panel",
    "tabs",
    "top tabs",
    "bottom tabs",
    "inventory",
    "equipment",
    "prayer",
    "magic",
    "spell",
    "orb",
    "compass",
)
NON_BLOCKING_UI_REGION_NAMES = {"fullframe", "full_frame", "gameviewport", "game_viewport", "viewport"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def output_paths(session: Path) -> dict[str, Path]:
    output_dir = session / "interaction_geometry"
    return {
        "outputDir": output_dir,
        "candidates": output_dir / "target_candidates.jsonl",
        "index": output_dir / "target_candidates_index.json",
    }


def read_json_document(path: Path, expected_schema: str | None = None) -> tuple[dict, list[str]]:
    warnings = []

    if not path.exists():
        return {}, [f"missing configuration file: {path}"]

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        return {}, [f"could not load {path}: {error}"]

    if not isinstance(data, dict):
        return {}, [f"{path} did not contain a JSON object"]

    if expected_schema and data.get("schema") != expected_schema:
        warnings.append(f"{path} schema is {data.get('schema') or 'missing'}, expected {expected_schema}")

    return data, warnings


def normalize_list(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    return [text] if text else []


def lower_set(value) -> set[str]:
    return {item.lower() for item in normalize_list(value)}


def load_target_library(path: Path) -> tuple[dict, list[str]]:
    data, warnings = read_json_document(path, "target_library.v1")
    classes = data.get("targetClasses")

    if not isinstance(classes, list):
        data["targetClasses"] = []
        warnings.append(f"{path} has no targetClasses array")

    return data, warnings


def load_target_profiles(path: Path) -> tuple[dict, list[str]]:
    data, warnings = read_json_document(path, "target_profiles.v1")
    profiles = data.get("profiles")

    if not isinstance(profiles, list):
        data["profiles"] = []
        warnings.append(f"{path} has no profiles array")

    return data, warnings


def profile_by_id(profiles_doc: dict, profile_id: str | None) -> dict | None:
    if not profile_id:
        return None

    for profile in profiles_doc.get("profiles") or []:
        if isinstance(profile, dict) and profile.get("profileId") == profile_id:
            return profile

    return None


def session_id_for(session: Path, records: list[dict]) -> str:
    for record in records:
        value = record.get("sessionId")

        if value:
            return str(value)

    return session.name


def is_polygon(value) -> bool:
    return isinstance(value, list) and any(is_point_list(point) or is_point_dict(point) for point in value)


def is_point_list(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    )


def is_point_dict(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("x"), (int, float)) and isinstance(value.get("y"), (int, float))


def is_bounds(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("x"), (int, float))
        and isinstance(value.get("y"), (int, float))
        and isinstance(value.get("w"), (int, float))
        and isinstance(value.get("h"), (int, float))
    )


def polygon_bounds(points) -> dict | None:
    if not is_polygon(points):
        return None

    xs = []
    ys = []

    for point in points:
        if is_point_list(point):
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        elif is_point_dict(point):
            xs.append(float(point["x"]))
            ys.append(float(point["y"]))

    if not xs or not ys:
        return None

    return {
        "x": min(xs),
        "y": min(ys),
        "w": max(xs) - min(xs),
        "h": max(ys) - min(ys),
    }


def bounds_center(bounds: dict) -> dict:
    return {
        "x": bounds["x"] + bounds["w"] / 2.0,
        "y": bounds["y"] + bounds["h"] / 2.0,
    }


def polygon_center(points) -> dict | None:
    bounds = polygon_bounds(points)
    return bounds_center(bounds) if bounds else None


def available_geometry_types(record: dict) -> list[str]:
    source = geometry.geometry_for(record)
    available = []

    for key in GEOMETRY_PRIORITY:
        value = source.get(key)

        if key in POLYGON_GEOMETRY_TYPES and is_polygon(value):
            available.append(key)
        elif key in BOUNDS_GEOMETRY_TYPES and is_bounds(value):
            available.append(key)
        elif key in POINT_GEOMETRY_TYPES and is_point_dict(value):
            available.append(key)

    return available


def preferred_aim_geometry(record: dict) -> dict:
    source = geometry.geometry_for(record)
    available = available_geometry_types(record)

    for key in GEOMETRY_PRIORITY:
        if key not in available:
            continue

        value = source.get(key)

        if key in POLYGON_GEOMETRY_TYPES:
            aim_bounds = polygon_bounds(value)
            return {
                "preferredAimGeometryType": key,
                "preferredAimGeometry": value,
                "aimPoint": polygon_center(value),
                "aimBounds": aim_bounds,
                "availableGeometryTypes": available,
                "geometryQuality": GEOMETRY_QUALITY.get(key, 0.5),
            }

        if key in BOUNDS_GEOMETRY_TYPES:
            return {
                "preferredAimGeometryType": key,
                "preferredAimGeometry": value,
                "aimPoint": bounds_center(value),
                "aimBounds": value,
                "availableGeometryTypes": available,
                "geometryQuality": GEOMETRY_QUALITY.get(key, 0.5),
            }

        if key in POINT_GEOMETRY_TYPES:
            return {
                "preferredAimGeometryType": key,
                "preferredAimGeometry": value,
                "aimPoint": {"x": value["x"], "y": value["y"]},
                "aimBounds": None,
                "availableGeometryTypes": available,
                "geometryQuality": GEOMETRY_QUALITY.get(key, 0.5),
            }

    return {
        "preferredAimGeometryType": None,
        "preferredAimGeometry": None,
        "aimPoint": None,
        "aimBounds": None,
        "availableGeometryTypes": available,
        "geometryQuality": 0.0,
    }


def record_matches(record: dict, args) -> bool:
    if args.target_type != "all" and geometry.target_type_for(record) != args.target_type:
        return False

    if args.role and geometry.target_role_for(record).lower() != args.role.lower():
        return False

    if args.category and geometry.target_category_for(record).lower() != args.category.lower():
        return False

    if args.tag:
        needle = args.tag.lower()
        tags = [tag.lower() for tag in geometry.target_tags_for(record)]

        if needle not in tags:
            return False

    if args.name:
        target = geometry.target_for(record)
        haystack = " ".join(
            str(value or "")
            for value in (
                geometry.target_name_for(record),
                target.get("name"),
                target.get("targetName"),
                target.get("kind"),
                target.get("regionName"),
                geometry.target_type_for(record),
                geometry.target_role_for(record),
                geometry.target_category_for(record),
                " ".join(geometry.target_tags_for(record)),
                target.get("id"),
                target.get("rawId"),
                target.get("targetId"),
                target.get("nameSource"),
                target.get("npcNameSource"),
                target.get("objectNameSource"),
                target.get("itemNameSource"),
                target.get("fallbackName"),
            )
        ).lower()

        if args.name.lower() not in haystack:
            return False

    if args.id:
        needle = str(args.id).lower()
        ids = [value.lower() for value in geometry.target_id_values(record)]

        if not any(needle in value for value in ids):
            return False

    if args.only_on_screen and geometry.on_screen_for(record) is not True:
        return False

    if args.geometry_available and not geometry.geometry_available_for(record):
        return False

    return True


def selected_tick_ids(dataset: geometry.TargetGeometryDataset, args) -> set[int]:
    if args.tick is not None:
        return {args.tick}

    ticks = dataset.ticks()

    if args.tick_range is not None:
        start, end = args.tick_range
        return {tick for tick in ticks if start <= tick <= end}

    if args.latest is not None:
        return set(ticks[-args.latest :])

    return set(ticks)


def candidate_input_records(dataset: geometry.TargetGeometryDataset, args) -> tuple[list[dict], set[int]]:
    ticks = selected_tick_ids(dataset, args)
    records = [
        record
        for record in dataset.records
        if record.get("tickId") in ticks and record_matches(record, args)
    ]
    return records, ticks


def semantic_filter_requested(args) -> bool:
    return bool(
        args.role
        or args.category
        or args.tag
        or args.name
        or args.id
        or args.target_type != "all"
    )


def source_dimensions(record: dict) -> tuple[float | None, float | None]:
    coord_space = geometry.geometry_for(record).get("coordinateSpace")

    if coord_space == "canvasPixels":
        canvas = record.get("canvas") if isinstance(record.get("canvas"), dict) else {}
        return canvas.get("width"), canvas.get("height")

    frame = geometry.frame_for(record)
    return frame.get("width"), frame.get("height")


def ui_region_name(record: dict) -> str:
    target = geometry.target_for(record)
    return str(
        target.get("regionName")
        or target.get("targetName")
        or target.get("name")
        or target.get("targetId")
        or geometry.target_name_for(record)
        or "uiRegion"
    )


def is_blocking_ui_record(record: dict) -> bool:
    target_type = geometry.target_type_for(record)
    target = geometry.target_for(record)
    region_name = ui_region_name(record)
    normalized = re_normalize(region_name)

    if normalized in NON_BLOCKING_UI_REGION_NAMES:
        return False

    if target_type in {"inventorySlot", "equipmentSlot", "prayerIcon", "magicSpell"}:
        return True

    if target_type == "baseUiRegion":
        return any(part.replace(" ", "") in normalized for part in BLOCKING_UI_NAME_PARTS)

    profile = str(target.get("regionProfile") or "").lower()
    return profile in {"inventory", "equipment", "prayer", "magic", "base"}


def re_normalize(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def ui_block_regions_by_tick(dataset: geometry.TargetGeometryDataset) -> dict[int, list[dict]]:
    by_tick = {}

    for record in dataset.ui_records:
        tick_id = record.get("tickId")

        if not isinstance(tick_id, int) or not is_blocking_ui_record(record):
            continue

        box = geometry.geometry_for(record).get("pixelBox")

        if not is_bounds(box):
            continue

        by_tick.setdefault(tick_id, []).append(
            {
                "name": ui_region_name(record),
                "targetType": geometry.target_type_for(record),
                "box": box,
            }
        )

    return by_tick


def point_to_frame_pixels(record: dict, point: dict | None, coordinate_space: str | None) -> dict | None:
    if not is_point_dict(point):
        return None

    if coordinate_space != "canvasPixels":
        return {"x": float(point["x"]), "y": float(point["y"])}

    canvas = record.get("canvas") if isinstance(record.get("canvas"), dict) else {}
    frame = geometry.frame_for(record)
    canvas_width = canvas.get("width")
    canvas_height = canvas.get("height")
    frame_width = frame.get("width")
    frame_height = frame.get("height")

    if all(isinstance(value, (int, float)) and value > 0 for value in (canvas_width, canvas_height, frame_width, frame_height)):
        return {
            "x": float(point["x"]) * float(frame_width) / float(canvas_width),
            "y": float(point["y"]) * float(frame_height) / float(canvas_height),
        }

    return {"x": float(point["x"]), "y": float(point["y"])}


def point_in_box(point: dict, box: dict) -> bool:
    return (
        float(box["x"]) <= float(point["x"]) <= float(box["x"]) + float(box["w"])
        and float(box["y"]) <= float(point["y"]) <= float(box["y"]) + float(box["h"])
    )


def ui_block_info_for_record(record: dict, aim: dict, blockers_by_tick: dict[int, list[dict]]) -> dict:
    if record.get("_sourceKind") == "ui" or geometry.target_type_for(record) in {"inventorySlot", "equipmentSlot", "prayerIcon", "magicSpell", "baseUiRegion"}:
        return {"uiBlocked": False, "blockingUiRegions": [], "blockedReason": None}

    tick_id = record.get("tickId")
    blockers = blockers_by_tick.get(tick_id) if isinstance(tick_id, int) else None

    if not blockers:
        return {"uiBlocked": False, "blockingUiRegions": [], "blockedReason": None}

    coordinate_space = geometry.geometry_for(record).get("coordinateSpace")
    point = point_to_frame_pixels(record, aim.get("aimPoint"), coordinate_space)

    if not point:
        return {"uiBlocked": False, "blockingUiRegions": [], "blockedReason": None}

    matches = [blocker for blocker in blockers if point_in_box(point, blocker["box"])]
    names = []

    for match in matches:
        name = str(match.get("name") or "uiRegion")

        if name not in names:
            names.append(name)

    return {
        "uiBlocked": bool(names),
        "blockingUiRegions": names,
        "blockedReason": f"aimPoint intersects UI region(s): {', '.join(names)}" if names else None,
    }


def point_near_edge(record: dict, aim_point: dict | None) -> bool:
    if not is_point_dict(aim_point):
        return False

    width, height = source_dimensions(record)

    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return False

    if width <= 0 or height <= 0:
        return False

    margin = max(8.0, min(float(width), float(height)) * 0.04)
    x = float(aim_point["x"])
    y = float(aim_point["y"])
    return x < margin or y < margin or x > float(width) - margin or y > float(height) - margin


def bounds_tiny(bounds: dict | None) -> bool:
    if not is_bounds(bounds):
        return False

    width = float(bounds["w"])
    height = float(bounds["h"])
    return width <= 2 or height <= 2 or width * height < 25


def fallback_name(record: dict) -> bool:
    target = geometry.target_for(record)
    source = str(target.get("nameSource") or "").lower()
    name = geometry.target_name_for(record)
    return source == "fallback" or name.startswith(("Npc[", "SceneObject[", "GroundItem[", "Tile["))


def target_actions_for(record: dict) -> list[str]:
    target = geometry.target_for(record)
    value = target.get("actions")

    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]

    return []


def record_signal_set(record: dict) -> set[str]:
    signals = set()

    if geometry.on_screen_for(record) is True:
        signals.add("onScreen")

    if geometry.geometry_available_for(record):
        signals.add("geometryAvailable")

    if fallback_name(record):
        signals.add("fallbackName")

    role = geometry.target_role_for(record).lower()
    category = geometry.target_category_for(record).lower()

    if role in {"unknown", "decoration"} or category in {"unknown", "sceneobject", "decoration"}:
        signals.add("unclassified")

    available = set(available_geometry_types(record))

    if "clickableHull" in available or "clickboxPolygon" in available or "clickboxBounds" in available:
        signals.add("hasClickbox")

    if "convexHull" in available or "convexHullPolygon" in available or "convexHullBounds" in available:
        signals.add("hasConvexHull")

    if "canvasTilePolygon" in available or "tilePolygon" in available:
        signals.add("hasCanvasTilePolygon")

    return signals


def text_haystack_for(record: dict) -> str:
    target = geometry.target_for(record)
    pieces = [
        geometry.target_name_for(record),
        target.get("name"),
        target.get("targetName"),
        target.get("fallbackName"),
        target.get("objectName"),
        target.get("itemName"),
        target.get("npcName"),
        target.get("kind"),
        geometry.target_type_for(record),
        geometry.target_role_for(record),
        geometry.target_category_for(record),
        " ".join(geometry.target_tags_for(record)),
        " ".join(target_actions_for(record)),
    ]
    return " ".join(str(piece or "") for piece in pieces).lower()


def target_ids_for_record(record: dict) -> set[str]:
    return {value.lower() for value in geometry.target_id_values(record)}


def target_class_matches(record: dict, target_class: dict) -> bool:
    target_types = lower_set(target_class.get("targetTypes"))

    if target_types and geometry.target_type_for(record).lower() not in target_types:
        return False

    signals = record_signal_set(record)

    for required in normalize_list(target_class.get("requiredSignals")):
        if required not in signals:
            return False

    for negative in normalize_list(target_class.get("negativeSignals")):
        if negative in signals:
            return False

    strong_positives = []
    weak_positives = []
    role = geometry.target_role_for(record).lower()
    category = geometry.target_category_for(record).lower()
    tags = {tag.lower() for tag in geometry.target_tags_for(record)}
    ids = target_ids_for_record(record)
    haystack = text_haystack_for(record)
    actions_text = " ".join(target_actions_for(record)).lower()

    roles = lower_set(target_class.get("roles"))
    categories = lower_set(target_class.get("categories"))
    class_tags = lower_set(target_class.get("tags"))
    object_ids = {str(item).lower() for item in normalize_list(target_class.get("objectIds"))}
    name_contains = lower_set(target_class.get("nameContains"))
    action_contains = lower_set(target_class.get("actionContains"))

    if roles:
        weak_positives.append(role in roles)

    if categories:
        strong_positives.append(category in categories)

    if class_tags:
        strong_positives.append(bool(tags & class_tags))

    if object_ids:
        strong_positives.append(bool(ids & object_ids))

    if name_contains:
        strong_positives.append(any(needle in haystack for needle in name_contains))

    if action_contains:
        strong_positives.append(any(needle in actions_text for needle in action_contains))

    # Roles are intentionally weak identity signals. A generic role such as
    # "interactable" should not classify every tree or door as a bank target.
    if strong_positives:
        return any(strong_positives)

    return any(weak_positives) if weak_positives else True


def target_class_specificity(target_class: dict) -> tuple:
    return (
        len(normalize_list(target_class.get("objectIds"))) * 5
        + len(normalize_list(target_class.get("nameContains"))) * 3
        + len(normalize_list(target_class.get("actionContains"))) * 2
        + len(normalize_list(target_class.get("tags"))),
        float(target_class.get("defaultQualityWeight") or 0.0),
        str(target_class.get("classId") or ""),
    )


def classify_record(record: dict, library: dict) -> dict:
    matches = [
        target_class
        for target_class in library.get("targetClasses") or []
        if isinstance(target_class, dict)
        and target_class.get("classId")
        and target_class_matches(record, target_class)
    ]
    matches.sort(key=target_class_specificity, reverse=True)
    primary = matches[0] if matches else None
    class_ids = [str(item.get("classId")) for item in matches if item.get("classId")]

    return {
        "classId": primary.get("classId") if primary else None,
        "targetClassIds": class_ids,
        "targetClass": primary,
        "targetClasses": matches,
        "knownTargetClass": bool(primary and primary.get("classId") not in UNKNOWN_CLASS_IDS),
        "preferredGeometryTypes": normalize_list(primary.get("preferredGeometryTypes")) if primary else [],
        "defaultQualityWeight": float(primary.get("defaultQualityWeight") or 0.0) if primary else 0.0,
    }


def int_value(value) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return None


def world_payload_from(value) -> dict | None:
    if not isinstance(value, dict):
        return None

    x = int_value(value.get("x"))
    y = int_value(value.get("y"))

    if x is None:
        x = int_value(value.get("worldX"))

    if y is None:
        y = int_value(value.get("worldY"))

    plane = int_value(value.get("plane"))

    if x is None or y is None:
        return None

    payload = {"x": x, "y": y}

    if plane is not None:
        payload["plane"] = plane

    return payload


def tick_player_world(tick: dict) -> dict | None:
    local_player = tick.get("localPlayer")
    world = world_payload_from(local_player)

    if world:
        return world

    for key in ("player", "self", "local"):
        world = world_payload_from(tick.get(key))

        if world:
            return world

    status = tick.get("status") if isinstance(tick.get("status"), dict) else {}
    world = {
        "x": int_value(status.get("worldX")),
        "y": int_value(status.get("worldY")),
        "plane": int_value(status.get("plane")),
    }

    if world["x"] is not None and world["y"] is not None:
        return {key: value for key, value in world.items() if value is not None}

    return None


def load_player_world_by_tick(session: Path, tick_ids: set[int]) -> tuple[dict[int, dict], list[str]]:
    positions = {}
    warnings = []

    if not tick_ids:
        return positions, warnings

    tick_files = list_tick_files(session)

    if not tick_files:
        return positions, ["raw tick files unavailable; target distance scoring disabled"]

    remaining = set(tick_ids)

    for _source, tick in iter_jsonl(tick_files):
        if not remaining:
            break

        if not isinstance(tick, dict):
            continue

        tick_id = tick.get("tickId")

        if tick_id not in remaining:
            continue

        player_world = tick_player_world(tick)

        if player_world:
            positions[tick_id] = player_world

        remaining.discard(tick_id)

    if remaining:
        warnings.append(f"player position unavailable for {len(remaining)} selected ticks")

    return positions, warnings


def target_world_for_record(record: dict) -> dict | None:
    target = geometry.target_for(record)
    world = world_payload_from(target.get("world"))

    if world:
        return world

    x = int_value(target.get("worldX"))
    y = int_value(target.get("worldY"))
    plane = int_value(target.get("plane"))

    if x is None or y is None:
        return None

    payload = {"x": x, "y": y}

    if plane is not None:
        payload["plane"] = plane

    return payload


def coordinate_payload_from(value, x_keys: tuple[str, ...], y_keys: tuple[str, ...]) -> dict | None:
    if not isinstance(value, dict):
        return None

    x = None
    y = None

    for key in x_keys:
        x = int_value(value.get(key))

        if x is not None:
            break

    for key in y_keys:
        y = int_value(value.get(key))

        if y is not None:
            break

    if x is None or y is None:
        return None

    return {"x": x, "y": y}


def target_scene_for_record(record: dict) -> dict | None:
    target = geometry.target_for(record)
    return coordinate_payload_from(target.get("scene"), ("x", "sceneX"), ("y", "sceneY")) or coordinate_payload_from(
        target,
        ("sceneX",),
        ("sceneY",),
    )


def target_local_for_record(record: dict) -> dict | None:
    target = geometry.target_for(record)
    return coordinate_payload_from(target.get("local"), ("x", "localX"), ("y", "localY")) or coordinate_payload_from(
        target,
        ("localX",),
        ("localY",),
    )


def target_distance_for_record(record: dict, player_world: dict | None) -> dict:
    target_world = target_world_for_record(record)

    if not player_world or not target_world:
        return {
            "targetDistanceTiles": None,
            "targetDistanceChebyshev": None,
            "targetDistanceManhattan": None,
            "targetDistanceEuclidean": None,
            "playerWorld": player_world,
            "targetWorld": target_world,
        }

    dx = int(target_world["x"]) - int(player_world["x"])
    dy = int(target_world["y"]) - int(player_world["y"])
    chebyshev = max(abs(dx), abs(dy))
    manhattan = abs(dx) + abs(dy)
    return {
        "targetDistanceTiles": chebyshev,
        "targetDistanceChebyshev": chebyshev,
        "targetDistanceManhattan": manhattan,
        "targetDistanceEuclidean": round((dx * dx + dy * dy) ** 0.5, 3),
        "playerWorld": player_world,
        "targetWorld": target_world,
    }


def distance_available(distance: dict) -> bool:
    return isinstance(distance.get("targetDistanceChebyshev"), int)


def screen_center_distance(record: dict, aim: dict) -> float | None:
    aim_point = aim.get("aimPoint")

    if not is_point_dict(aim_point):
        return None

    width, height = source_dimensions(record)

    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None

    if width <= 0 or height <= 0:
        return None

    dx = float(aim_point["x"]) - float(width) / 2.0
    dy = float(aim_point["y"]) - float(height) / 2.0
    return round((dx * dx + dy * dy) ** 0.5, 3)


def distance_bonus_for(record: dict, distance: dict) -> tuple[int, str | None, str | None]:
    if not distance_available(distance):
        return 0, None, None

    target_type = geometry.target_type_for(record)
    role = geometry.target_role_for(record)
    chebyshev = distance["targetDistanceChebyshev"]

    if target_type in {"npc", "player"} or role == "entity":
        if chebyshev <= 2:
            return 30, "closeTarget", None
        if chebyshev <= 5:
            return 20, "closeTarget", None
        if chebyshev <= 8:
            return 10, "nearTarget", None
        return -5, None, "farTarget"

    if role in {"interactable", "item"} or target_type in {"sceneObject", "groundItem"}:
        if chebyshev <= 2:
            return 12, "nearInteractable", None
        if chebyshev <= 5:
            return 8, "nearInteractable", None
        if chebyshev <= 8:
            return 4, "nearInteractable", None
        if chebyshev > 12:
            return -3, None, "farInteractable"

    return 0, None, None


def distance_relevant(record: dict) -> bool:
    return geometry.target_role_for(record) in WORLD_DISTANCE_ROLES or geometry.target_type_for(record) in WORLD_DISTANCE_TYPES


def profile_include_match(record: dict, class_info: dict, profile: dict) -> tuple[bool, list[str]]:
    include_checks = []
    reasons = []
    class_ids = {class_id.lower() for class_id in class_info.get("targetClassIds") or []}
    target_type = geometry.target_type_for(record).lower()
    role = geometry.target_role_for(record).lower()
    category = geometry.target_category_for(record).lower()

    include_classes = lower_set(profile.get("includeTargetClasses"))
    include_types = lower_set(profile.get("includeTargetTypes"))
    include_roles = lower_set(profile.get("includeRoles"))
    include_categories = lower_set(profile.get("includeCategories"))

    if include_classes:
        matched = bool(class_ids & include_classes)
        include_checks.append(matched)

        if matched:
            reasons.append("profileClassMatch")

    if include_types:
        matched = target_type in include_types
        include_checks.append(matched)

        if matched:
            reasons.append("profileTypeMatch")

    if include_roles:
        matched = role in include_roles
        include_checks.append(matched)

        if matched:
            reasons.append("profileRoleMatch")

    if include_categories:
        matched = category in include_categories
        include_checks.append(matched)

        if matched:
            reasons.append("profileCategoryMatch")

    return (any(include_checks) if include_checks else True), reasons


def profile_evaluation(record: dict, aim: dict, class_info: dict, ui_info: dict, args, profile: dict | None) -> dict:
    selected = True
    reasons = []
    reject_reasons = []

    if profile:
        include_ok, include_reasons = profile_include_match(record, class_info, profile)
        reasons.extend(include_reasons)

        if not include_ok:
            selected = False
            reject_reasons.append("notProfileMatch")

        class_ids = {class_id.lower() for class_id in class_info.get("targetClassIds") or []}
        target_role = geometry.target_role_for(record).lower()
        target_category = geometry.target_category_for(record).lower()

        if class_ids & lower_set(profile.get("excludeTargetClasses")):
            selected = False
            reject_reasons.append("excludedTargetClass")

        if target_role in lower_set(profile.get("excludeRoles")):
            selected = False
            reject_reasons.append("excludedRole")

        if target_category in lower_set(profile.get("excludeCategories")):
            selected = False
            reject_reasons.append("excludedCategory")

        if profile.get("requireOnScreen") is True and geometry.on_screen_for(record) is not True:
            selected = False
            reject_reasons.append("requiresOnScreen")

        if profile.get("requireGeometryAvailable") is True and not geometry.geometry_available_for(record):
            selected = False
            reject_reasons.append("requiresGeometryAvailable")

        if profile.get("requirePreferredGeometry") is True and not aim.get("preferredAimGeometryType"):
            selected = False
            reject_reasons.append("requiresPreferredGeometry")

        if profile.get("excludeUiBlocked") is True and ui_info.get("uiBlocked"):
            selected = False
            reject_reasons.append("uiBlocked")

    if args.exclude_ui_blocked and ui_info.get("uiBlocked"):
        selected = False
        reject_reasons.append("uiBlocked")

    return {
        "selectedByProfile": selected,
        "profileMatchReasons": sorted(set(reasons)),
        "rejectReasons": sorted(set(reject_reasons)),
    }


def preferred_geometry_available(aim: dict, class_info: dict) -> bool:
    preferred = class_info.get("preferredGeometryTypes") or []

    if not preferred:
        return bool(aim.get("preferredAimGeometryType"))

    available = set(aim.get("availableGeometryTypes") or [])
    return bool(available & set(preferred))


def profile_weight(profile: dict | None, key: str, default: int) -> int:
    weights = profile.get("scoringWeights") if isinstance(profile, dict) else {}
    value = weights.get(key) if isinstance(weights, dict) else None
    return int(value) if isinstance(value, (int, float)) else default


def score_record(record: dict, aim: dict, args, frame_exists, distance: dict, class_info: dict, ui_info: dict, profile_eval: dict, profile: dict | None) -> dict:
    score = 0
    score_parts = []
    reasons = []
    penalties = []

    def add(label: str, value: int, *, reason: str | None = None, penalty: str | None = None) -> None:
        nonlocal score
        score += value
        score_parts.append({"name": label, "value": value})

        if reason:
            reasons.append(reason)

        if penalty:
            penalties.append(penalty)

    on_screen = geometry.on_screen_for(record)

    if on_screen is True:
        add("onScreen", 40, reason="onScreen")
    elif on_screen is False:
        add("offScreen", -30, penalty="offScreen")

    if geometry.geometry_available_for(record):
        add("geometryAvailable", 30, reason="geometryAvailable")
    else:
        add("geometryUnavailable", -30, penalty="geometryUnavailable")

    role = geometry.target_role_for(record)
    category = geometry.target_category_for(record)
    tags = set(geometry.target_tags_for(record))

    if semantic_filter_requested(args):
        add("filterMatch", 20, reason="filterMatch")

    if role in {"interactable", "entity", "item", "ui"}:
        add(f"role:{role}", 12, reason=f"role:{role}")
    elif role == "navigation":
        add("role:navigation", 4, reason="role:navigation")

    if "clickableHull" in aim["availableGeometryTypes"] or "clickboxPolygon" in aim["availableGeometryTypes"] or "clickboxBounds" in aim["availableGeometryTypes"]:
        add("clickbox", 20, reason="clickbox")

    if "convexHull" in aim["availableGeometryTypes"] or "convexHullPolygon" in aim["availableGeometryTypes"] or "convexHullBounds" in aim["availableGeometryTypes"]:
        add("convexHull", 15, reason="convexHull")

    if "tilePolygon" in aim["availableGeometryTypes"] or "canvasTilePolygon" in aim["availableGeometryTypes"]:
        add("tilePolygon", 10, reason="tilePolygon")

    if "pixelBox" in aim["availableGeometryTypes"]:
        add("pixelBox", 20, reason="pixelBox")

    if frame_exists is True:
        add("frameExists", 10, reason="frameExists")
    elif frame_exists is False:
        add("frameMissing", -5, penalty="frameMissing")

    if aim["preferredAimGeometryType"] is None:
        add("missingAimGeometry", -30, penalty="missingAimGeometry")

    if bounds_tiny(aim.get("aimBounds")):
        add("tinyBounds", -20, penalty="tinyBounds")

    if point_near_edge(record, aim.get("aimPoint")):
        add("nearEdge", -20, penalty="nearEdge")

    if not args.role and role == "unknown":
        add("unknownRole", -20, penalty="unknownRole")
    elif not args.role and role == "decoration":
        add("decorationRole", -10, penalty="decorationRole")

    if semantic_filter_requested(args) and fallback_name(record):
        add("fallbackName", -10, penalty="fallbackName")

    if profile:
        if profile_eval.get("selectedByProfile"):
            add("profileMatch", profile_weight(profile, "profileMatch", 20), reason="profileMatch")
        else:
            add("notProfileMatch", -20, penalty="notProfileMatch")

        if class_info.get("knownTargetClass"):
            add("knownTargetClass", profile_weight(profile, "knownTargetClass", 10), reason="knownTargetClass")

        if preferred_geometry_available(aim, class_info):
            add(
                "preferredGeometryAvailable",
                profile_weight(profile, "preferredGeometryAvailable", 10),
                reason="preferredGeometryAvailable",
            )

        if ui_info.get("uiBlocked"):
            add("uiBlocked", profile_weight(profile, "uiBlockedPenalty", -20), penalty="uiBlocked")

        if not geometry.geometry_available_for(record):
            add("missingGeometryProfilePenalty", profile_weight(profile, "missingGeometryPenalty", -20), penalty="missingGeometry")

    if distance_available(distance):
        chebyshev = distance["targetDistanceChebyshev"]
        reasons.append(f"distanceTiles={chebyshev}")
        bonus, reason, penalty = distance_bonus_for(record, distance)

        if bonus:
            add("distance", bonus, reason=reason, penalty=penalty)
    elif distance_relevant(record):
        add("distanceUnavailable", 0, reason="playerDistanceUnavailable")

    if category in {"bank", "tree", "door", "npc", "player", "groundItem"}:
        reasons.append(f"category:{category}")

    for tag in sorted(tags & {"bank", "banker", "bank_booth", "tree", "door", "clickable_candidate"}):
        reasons.append(tag)

    return {
        "score": score,
        "scoreParts": score_parts,
        "reasons": sorted(set(reasons)),
        "penalties": sorted(set(penalties)),
    }


def mostly_off_frame(record: dict, aim: dict) -> bool:
    bounds = aim.get("aimBounds")

    if not is_bounds(bounds):
        return False

    width, height = source_dimensions(record)

    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or width <= 0 or height <= 0:
        return False

    x1 = float(bounds["x"])
    y1 = float(bounds["y"])
    x2 = x1 + float(bounds["w"])
    y2 = y1 + float(bounds["h"])
    ix1 = max(0.0, x1)
    iy1 = max(0.0, y1)
    ix2 = min(float(width), x2)
    iy2 = min(float(height), y2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = max(0.0, float(bounds["w"])) * max(0.0, float(bounds["h"]))

    if area <= 0:
        return True

    return intersection / area < 0.5


def quality_summary(record: dict, aim: dict, class_info: dict, ui_info: dict, profile_eval: dict, distance: dict) -> dict:
    positive = []
    negative = []

    if geometry.on_screen_for(record) is True:
        positive.append("onScreen")
    else:
        negative.append("offScreen")

    if geometry.geometry_available_for(record):
        positive.append("geometryAvailable")
    else:
        negative.append("missingGeometry")

    available = set(aim.get("availableGeometryTypes") or [])

    if "clickableHull" in available or "clickboxPolygon" in available or "clickboxBounds" in available:
        positive.append("hasClickbox")
    else:
        negative.append("missingClickbox")

    if "convexHull" in available or "convexHullPolygon" in available or "convexHullBounds" in available:
        positive.append("hasConvexHull")

    if "canvasTilePolygon" in available or "tilePolygon" in available:
        positive.append("hasCanvasTilePolygon")

    if class_info.get("knownTargetClass"):
        positive.append("knownTargetClass")
    else:
        negative.append("unclassified")

    if preferred_geometry_available(aim, class_info):
        positive.append("preferredGeometryAvailable")

    if distance_available(distance) and distance.get("targetDistanceChebyshev") <= 5:
        positive.append("nearPlayer")

    if profile_eval.get("selectedByProfile"):
        positive.append("profileMatch")
    elif profile_eval.get("rejectReasons"):
        negative.append("notProfileMatch")

    if fallback_name(record):
        negative.append("fallbackName")

    if ui_info.get("uiBlocked"):
        negative.append("uiBlocked")

    if mostly_off_frame(record, aim):
        negative.append("mostlyOffFrame")

    weights = {
        "onScreen": 22,
        "geometryAvailable": 22,
        "hasClickbox": 16,
        "hasConvexHull": 10,
        "hasCanvasTilePolygon": 8,
        "knownTargetClass": 10,
        "preferredGeometryAvailable": 8,
        "nearPlayer": 6,
        "profileMatch": 8,
    }
    penalties = {
        "offScreen": -24,
        "missingGeometry": -28,
        "missingClickbox": -8,
        "fallbackName": -8,
        "unclassified": -12,
        "duplicateCandidate": -5,
        "uiBlocked": -18,
        "mostlyOffFrame": -10,
        "notProfileMatch": -20,
    }
    quality_score = 20 + sum(weights.get(signal, 0) for signal in set(positive)) + sum(penalties.get(signal, 0) for signal in set(negative))
    quality_score = max(0, min(100, quality_score))

    if quality_score >= 80:
        tier = "excellent"
    elif quality_score >= 60:
        tier = "good"
    elif quality_score >= 35:
        tier = "questionable"
    else:
        tier = "poor"

    return {
        "qualityScore": quality_score,
        "qualityTier": tier,
        "positiveSignals": sorted(set(positive)),
        "negativeSignals": sorted(set(negative)),
    }


def target_payload(record: dict, class_info: dict) -> dict:
    target = geometry.target_for(record)
    return {
        "targetId": target.get("targetId"),
        "targetType": geometry.target_type_for(record),
        "name": geometry.target_name_for(record),
        "id": target.get("id"),
        "rawId": target.get("rawId"),
        "hash": target.get("hash") or target.get("objectHash"),
        "objectKey": target.get("objectKey"),
        "classId": class_info.get("classId"),
        "targetClassIds": class_info.get("targetClassIds") or [],
        "targetRole": geometry.target_role_for(record),
        "targetCategory": geometry.target_category_for(record),
        "targetTags": geometry.target_tags_for(record),
    }


def frame_payload(record: dict, frame_exists) -> dict:
    frame = geometry.frame_for(record)
    return {
        "path": frame.get("path"),
        "exists": frame_exists if frame_exists is not None else frame.get("exists"),
        "recordedExists": frame.get("exists"),
        "width": frame.get("width"),
        "height": frame.get("height"),
    }


def source_payload(record: dict) -> dict:
    source_kind = record.get("_sourceKind") or "world"
    source_name = {
        "world": "world_targets",
        "ui": "ui_targets",
    }.get(source_kind, source_kind)
    return {
        "type": source_name,
        "fileType": source_kind,
        "staticIndex": bool(geometry.target_for(record).get("objectKey")),
    }


def geometry_summary(aim: dict) -> dict:
    bounds = aim.get("aimBounds")
    point = aim.get("aimPoint")
    return {
        "preferredAimGeometryType": aim.get("preferredAimGeometryType"),
        "aimPoint": point,
        "aimBounds": bounds,
        "availableGeometryTypes": aim.get("availableGeometryTypes") or [],
        "geometryQuality": aim.get("geometryQuality"),
    }


def preserved_overlay_geometry(record: dict, aim: dict) -> dict:
    source = geometry.geometry_for(record)
    payload = {}

    for key in POLYGON_GEOMETRY_TYPES:
        value = source.get(key)
        if is_polygon(value):
            payload[key] = value

    preferred_type = aim.get("preferredAimGeometryType")
    preferred_geometry = aim.get("preferredAimGeometry")
    if preferred_type in POLYGON_GEOMETRY_TYPES and is_polygon(preferred_geometry):
        payload.setdefault(preferred_type, preferred_geometry)

    if is_polygon(payload.get("clickableHull")) and not is_polygon(payload.get("clickboxPolygon")):
        payload["clickboxPolygon"] = payload["clickableHull"]
    elif is_polygon(payload.get("clickboxPolygon")) and not is_polygon(payload.get("clickableHull")):
        payload["clickableHull"] = payload["clickboxPolygon"]

    if is_polygon(payload.get("convexHull")) and not is_polygon(payload.get("convexHullPolygon")):
        payload["convexHullPolygon"] = payload["convexHull"]
    elif is_polygon(payload.get("convexHullPolygon")) and not is_polygon(payload.get("convexHull")):
        payload["convexHull"] = payload["convexHullPolygon"]

    if is_polygon(payload.get("tilePolygon")) and not is_polygon(payload.get("canvasTilePolygon")):
        payload["canvasTilePolygon"] = payload["tilePolygon"]
    elif is_polygon(payload.get("canvasTilePolygon")) and not is_polygon(payload.get("tilePolygon")):
        payload["tilePolygon"] = payload["canvasTilePolygon"]

    for key in ("clickboxBounds", "convexHullBounds", "bounds"):
        value = source.get(key)
        if is_bounds(value):
            payload[key] = value

    for key in ("geometrySource", "clickableHullAvailable", "clickableHullMissingReason"):
        if source.get(key) is not None:
            payload[key] = source.get(key)

    return payload


def candidate_record(
    dataset: geometry.TargetGeometryDataset,
    record: dict,
    rank: int,
    args,
    player_world_by_tick: dict[int, dict],
    library: dict,
    profile: dict | None,
    ui_blockers_by_tick: dict[int, list[dict]],
) -> dict:
    aim = preferred_aim_geometry(record)
    frame_exists = dataset.frame_exists_for_record(record)
    distance = target_distance_for_record(record, player_world_by_tick.get(record.get("tickId")))
    class_info = classify_record(record, library)
    ui_info = ui_block_info_for_record(record, aim, ui_blockers_by_tick)
    profile_eval = profile_evaluation(record, aim, class_info, ui_info, args, profile)
    scoring = score_record(record, aim, args, frame_exists, distance, class_info, ui_info, profile_eval, profile)
    quality = quality_summary(record, aim, class_info, ui_info, profile_eval, distance)
    center_distance = screen_center_distance(record, aim)
    target = target_payload(record, class_info)
    world = distance["targetWorld"] or target_world_for_record(record)
    scene = target_scene_for_record(record)
    local = target_local_for_record(record)
    target_key = target.get("objectKey") or target.get("targetId")
    preserved_geometry = preserved_overlay_geometry(record, aim)
    return {
        "schemaVersion": SCHEMA_VERSION_RECORD,
        "recordSchema": PACKET_SCHEMA_VERSION,
        "sessionId": record.get("sessionId"),
        "tickId": record.get("tickId"),
        "tick": record.get("tickId"),
        "timestampUtc": record.get("timestampUtc"),
        "rank": rank,
        "score": scoring["score"],
        "source": source_payload(record),
        "targetKey": target_key,
        "objectKey": target.get("objectKey"),
        "targetType": target.get("targetType"),
        "classId": target.get("classId"),
        "targetClassIds": target.get("targetClassIds") or [],
        "name": target.get("name"),
        "id": target.get("id"),
        "rawId": target.get("rawId"),
        "hash": target.get("hash"),
        "role": target.get("targetRole"),
        "category": target.get("targetCategory"),
        "tags": target.get("targetTags") or [],
        "worldX": world.get("x") if isinstance(world, dict) else None,
        "worldY": world.get("y") if isinstance(world, dict) else None,
        "plane": world.get("plane") if isinstance(world, dict) else None,
        "sceneX": scene.get("x") if isinstance(scene, dict) else None,
        "sceneY": scene.get("y") if isinstance(scene, dict) else None,
        "localX": local.get("x") if isinstance(local, dict) else None,
        "localY": local.get("y") if isinstance(local, dict) else None,
        "onScreen": geometry.on_screen_for(record),
        "geometryAvailable": geometry.geometry_available_for(record),
        "preferredGeometryType": aim.get("preferredAimGeometryType"),
        "aimPoint": aim.get("aimPoint"),
        "geometrySummary": geometry_summary(aim),
        "qualityScore": quality["qualityScore"],
        "qualityTier": quality["qualityTier"],
        "positiveSignals": quality["positiveSignals"],
        "negativeSignals": quality["negativeSignals"],
        "rejectReasons": profile_eval["rejectReasons"],
        "profileId": profile.get("profileId") if profile else None,
        "selectedByProfile": profile_eval["selectedByProfile"],
        "uiBlocked": ui_info["uiBlocked"],
        "blockingUiRegions": ui_info["blockingUiRegions"],
        "blockedReason": ui_info["blockedReason"],
        "target": target,
        "sourceTarget": {
            "sourceFileType": record.get("_sourceKind"),
            "originalTargetRecordIndex": record.get("_sourceIndex"),
        },
        "geometry": {
            "coordinateSpace": geometry.geometry_for(record).get("coordinateSpace"),
            **preserved_geometry,
            **aim,
        },
        "scoring": scoring,
        "targetDistanceTiles": distance["targetDistanceTiles"],
        "targetDistanceChebyshev": distance["targetDistanceChebyshev"],
        "targetDistanceManhattan": distance["targetDistanceManhattan"],
        "targetDistanceEuclidean": distance["targetDistanceEuclidean"],
        "playerWorld": distance["playerWorld"],
        "targetWorld": world,
        "screenCenterDistance": center_distance,
        "frame": frame_payload(record, frame_exists),
    }


def geometry_priority_rank(candidate: dict) -> int:
    kind = candidate.get("geometry", {}).get("preferredAimGeometryType")

    try:
        return GEOMETRY_PRIORITY.index(kind)
    except ValueError:
        return len(GEOMETRY_PRIORITY)


def sort_distance(candidate: dict) -> int:
    value = candidate.get("targetDistanceChebyshev")
    return value if isinstance(value, int) else 999999


def sort_screen_center(candidate: dict) -> float:
    value = candidate.get("screenCenterDistance")
    return float(value) if isinstance(value, (int, float)) else 999999.0


def stable_scalar(value):
    if value is None or value == "":
        return None

    if isinstance(value, float):
        return round(value, 3)

    if isinstance(value, (int, str)):
        return value

    return str(value)


def point_key(value) -> tuple | None:
    if not isinstance(value, dict):
        return None

    x = value.get("x")
    y = value.get("y")

    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None

    return (round(float(x), 3), round(float(y), 3))


def world_key(value) -> tuple | None:
    if not isinstance(value, dict):
        return None

    x = stable_scalar(value.get("x"))
    y = stable_scalar(value.get("y"))

    if x is None or y is None:
        return None

    return (x, y, stable_scalar(value.get("plane")))


def candidate_dedupe_key(candidate: dict) -> tuple:
    target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
    geometry_payload = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    aim = geometry_payload.get("aimPoint")
    target_type = target.get("targetType")
    object_key = target.get("objectKey")
    raw_id = target.get("rawId")

    if raw_id is None:
        raw_id = target.get("id")

    target_hash = target.get("hash") or target.get("objectHash")
    target_world = world_key(candidate.get("targetWorld"))
    aim_point = point_key(aim)

    if object_key:
        return (
            candidate.get("tickId"),
            target_type,
            "objectKey",
            stable_scalar(object_key),
            aim_point,
            geometry_payload.get("preferredAimGeometryType"),
        )

    if raw_id is not None or target_world is not None or aim_point is not None:
        return (
            candidate.get("tickId"),
            target_type,
            stable_scalar(raw_id),
            stable_scalar(target_hash),
            target_world,
            aim_point,
            geometry_payload.get("preferredAimGeometryType"),
            target.get("name"),
            target.get("targetCategory"),
            target.get("targetRole"),
        )

    return (
        candidate.get("tickId"),
        target_type,
        stable_scalar(target.get("targetId")),
        target.get("name"),
        target.get("targetCategory"),
        target.get("targetRole"),
    )


def dedupe_ranked_candidates(candidates: list[dict]) -> tuple[list[dict], int]:
    by_key = {}
    duplicate_counts = Counter()

    for candidate in candidates:
        key = candidate_dedupe_key(candidate)

        if key not in by_key:
            by_key[key] = candidate
            continue

        duplicate_counts[key] += 1

    deduped = list(by_key.values())

    for key, duplicate_count in duplicate_counts.items():
        if duplicate_count:
            by_key[key]["dedupeDuplicateCount"] = duplicate_count

    return deduped, sum(duplicate_counts.values())


def rank_candidates(
    dataset: geometry.TargetGeometryDataset,
    records: list[dict],
    args,
    player_world_by_tick: dict[int, dict],
    library: dict,
    profile: dict | None,
    ui_blockers_by_tick: dict[int, list[dict]],
) -> tuple[list[dict], dict]:
    candidates = []
    reject_counts = Counter()
    ui_blocked_count = 0
    excluded_ui_blocked_count = 0

    for record in records:
        candidate = candidate_record(dataset, record, 0, args, player_world_by_tick, library, profile, ui_blockers_by_tick)

        if candidate.get("uiBlocked"):
            ui_blocked_count += 1

        if not candidate.get("selectedByProfile", True):
            for reason in candidate.get("rejectReasons") or ["notProfileMatch"]:
                reject_counts[str(reason)] += 1

            if "uiBlocked" in (candidate.get("rejectReasons") or []):
                excluded_ui_blocked_count += 1

            continue

        candidates.append(candidate)

    candidates.sort(
        key=lambda candidate: (
            -candidate["score"],
            sort_distance(candidate),
            geometry_priority_rank(candidate),
            sort_screen_center(candidate),
            candidate.get("tickId") if candidate.get("tickId") is not None else -1,
            str(candidate.get("target", {}).get("name") or ""),
            str(candidate.get("target", {}).get("targetId") or ""),
        )
    )

    before_dedupe = len(candidates)

    if args.no_dedupe:
        deduped = candidates
        duplicates_removed = 0
    else:
        deduped, duplicates_removed = dedupe_ranked_candidates(candidates)

    before_limit = len(deduped)
    limit = None if args.no_limit or args.limit == 0 else args.limit
    limited = deduped if limit is None else deduped[:limit]

    for rank, candidate in enumerate(limited, start=1):
        candidate["rank"] = rank

    stats = {
        "matchingTargetCountBeforeDedupe": before_dedupe,
        "duplicatesRemoved": duplicates_removed,
        "candidateCountBeforeLimit": before_limit,
        "dedupeEnabled": not args.no_dedupe,
        "limit": 0 if limit is None else limit,
        "noLimit": limit is None,
        "discardedByLimit": 0 if limit is None else max(0, before_limit - len(limited)),
        "candidateCountAfterLimit": len(limited),
        "candidatesAfterProfileFilters": before_dedupe,
        "uiBlockedCount": ui_blocked_count,
        "excludedUiBlockedCount": excluded_ui_blocked_count,
        "rejectReasons": dict(reject_counts.most_common()),
    }
    return limited, stats


def index_for(
    session: Path,
    selected_ticks: set[int],
    matching_count: int,
    dedupe_stats: dict,
    candidates: list[dict],
    warnings: list[str],
    args,
    library_doc: dict,
    profiles_doc: dict,
    profile: dict | None,
) -> dict:
    counts_by_type = Counter()
    counts_by_role = Counter()
    counts_by_category = Counter()
    counts_by_geometry = Counter()
    counts_by_quality = Counter()
    counts_by_class = Counter()
    positive_signal_counts = Counter()
    negative_signal_counts = Counter()
    tag_counts = Counter()

    for candidate in candidates:
        target = candidate.get("target", {})
        counts_by_type[target.get("targetType") or "unknown"] += 1
        counts_by_role[target.get("targetRole") or "unknown"] += 1
        counts_by_category[target.get("targetCategory") or "unknown"] += 1
        counts_by_geometry[candidate.get("geometry", {}).get("preferredAimGeometryType") or "none"] += 1
        counts_by_quality[candidate.get("qualityTier") or "unknown"] += 1
        counts_by_class[candidate.get("classId") or "unclassified"] += 1

        for tag in target.get("targetTags") or []:
            tag_counts[str(tag)] += 1

        for signal in candidate.get("positiveSignals") or []:
            positive_signal_counts[str(signal)] += 1

        for signal in candidate.get("negativeSignals") or []:
            negative_signal_counts[str(signal)] += 1

    tick_range = [min(selected_ticks), max(selected_ticks)] if selected_ticks else None
    return {
        "schema": "target_candidates_index.v1",
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "profileId": profile.get("profileId") if profile else None,
        "targetLibrarySchema": library_doc.get("schema"),
        "targetLibraryPath": str(Path(args.target_library).expanduser()),
        "targetProfilesSchema": profiles_doc.get("schema"),
        "targetProfilesPath": str(Path(args.target_profiles).expanduser()),
        "selectedTickCount": len(selected_ticks),
        "selectedTickRange": tick_range,
        "matchingTargetsBeforeFilters": matching_count,
        "matchingTargetCountBeforeLimit": matching_count,
        "matchingTargetCountBeforeDedupe": dedupe_stats.get("matchingTargetCountBeforeDedupe", matching_count),
        "candidatesAfterProfileFilters": dedupe_stats.get("candidatesAfterProfileFilters", matching_count),
        "duplicatesRemoved": dedupe_stats.get("duplicatesRemoved", 0),
        "uiBlockedCount": dedupe_stats.get("uiBlockedCount", 0),
        "excludedUiBlockedCount": dedupe_stats.get("excludedUiBlockedCount", 0),
        "candidateCountBeforeLimit": dedupe_stats.get("candidateCountBeforeLimit", matching_count),
        "dedupeEnabled": dedupe_stats.get("dedupeEnabled", True),
        "limit": dedupe_stats.get("limit"),
        "noLimit": dedupe_stats.get("noLimit", False),
        "discardedByLimit": dedupe_stats.get("discardedByLimit", 0),
        "candidateCountAfterLimit": dedupe_stats.get("candidateCountAfterLimit", len(candidates)),
        "candidateCount": len(candidates),
        "countsByQualityTier": dict(counts_by_quality.most_common()),
        "countsByClassId": dict(counts_by_class.most_common()),
        "countsByTargetType": dict(counts_by_type.most_common()),
        "countsByRole": dict(counts_by_role.most_common()),
        "countsByCategory": dict(counts_by_category.most_common()),
        "countsByPreferredAimGeometryType": dict(counts_by_geometry.most_common()),
        "topTags": dict(tag_counts.most_common(25)),
        "topPositiveSignals": dict(positive_signal_counts.most_common(25)),
        "topNegativeSignals": dict(negative_signal_counts.most_common(25)),
        "topRejectReasons": dedupe_stats.get("rejectReasons", {}),
        "warnings": warnings[:100],
        "paths": {
            "targetCandidates": "interaction_geometry/target_candidates.jsonl",
            "targetCandidatesIndex": "interaction_geometry/target_candidates_index.json",
        },
    }


def atomic_write_outputs(paths: dict[str, Path], candidates: list[dict], index: dict) -> None:
    output_dir = paths["outputDir"]
    temp_dir = output_dir / f".tmp-target-candidates-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    try:
        with (temp_dir / "target_candidates.jsonl").open("w", encoding="utf-8") as file:
            for candidate in candidates:
                file.write(json_dump_compact(candidate))
                file.write("\n")

        with (temp_dir / "target_candidates_index.json").open("w", encoding="utf-8") as file:
            json.dump(index, file, indent=2)
            file.write("\n")

        output_dir.mkdir(parents=True, exist_ok=True)
        os.replace(temp_dir / "target_candidates.jsonl", paths["candidates"])
        os.replace(temp_dir / "target_candidates_index.json", paths["index"])
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def select_target_candidates(session: Path, args) -> tuple[list[dict], dict]:
    dataset = geometry.TargetGeometryDataset(session)
    dataset.load()

    if not dataset.records:
        raise RuntimeError("No target geometry records found. Build world/UI target geometry first.")

    library_doc, library_warnings = load_target_library(Path(args.target_library).expanduser())
    profiles_doc, profile_warnings = load_target_profiles(Path(args.target_profiles).expanduser())
    profile = profile_by_id(profiles_doc, args.profile)

    if args.profile and profile is None:
        available = ", ".join(
            str(item.get("profileId"))
            for item in profiles_doc.get("profiles") or []
            if isinstance(item, dict) and item.get("profileId")
        )
        raise RuntimeError(f"Unknown target profile: {args.profile}. Available profiles: {available or 'none'}")

    if args.limit is None:
        args.limit = 50

        if profile and not args.no_limit:
            default_limit = profile.get("defaultLimit")

            if isinstance(default_limit, int) and default_limit >= 0:
                args.limit = default_limit

    records, ticks = candidate_input_records(dataset, args)
    player_world_by_tick, distance_warnings = load_player_world_by_tick(
        session,
        {tick for tick in (record.get("tickId") for record in records) if isinstance(tick, int)},
    )
    ui_blockers_by_tick = ui_block_regions_by_tick(dataset)
    candidates, dedupe_stats = rank_candidates(dataset, records, args, player_world_by_tick, library_doc, profile, ui_blockers_by_tick)
    warnings = list(dataset.messages) + list(dataset.warnings) + distance_warnings + library_warnings + profile_warnings
    index = index_for(session, ticks, len(records), dedupe_stats, candidates, warnings, args, library_doc, profiles_doc, profile)
    paths = output_paths(session)
    atomic_write_outputs(paths, candidates, index)
    return candidates, index


def compact_candidate_line(candidate: dict) -> str:
    target = candidate.get("target") or {}
    aim_point = candidate.get("geometry", {}).get("aimPoint")
    aim_text = "-"

    if isinstance(aim_point, dict) and isinstance(aim_point.get("x"), (int, float)) and isinstance(aim_point.get("y"), (int, float)):
        aim_text = f"{aim_point['x']:.0f},{aim_point['y']:.0f}"

    reasons = ",".join(candidate.get("scoring", {}).get("reasons") or [])
    target_id = target.get("id")

    if target_id is None:
        target_id = target.get("rawId")

    distance = candidate.get("targetDistanceChebyshev")
    distance_text = distance if isinstance(distance, int) else "-"
    return (
        f"{candidate.get('rank')} score={candidate.get('score')} quality={candidate.get('qualityTier')} tick={candidate.get('tickId')} "
        f"type={target.get('targetType')} name=\"{target.get('name') or '-'}\" "
        f"id={target_id if target_id is not None else '-'} "
        f"class={candidate.get('classId') or '-'} role={target.get('targetRole')} category={target.get('targetCategory')} "
        f"dist={distance_text} "
        f"aim={aim_text} geometry={candidate.get('geometry', {}).get('preferredAimGeometryType') or 'none'} "
        f"uiBlocked={str(bool(candidate.get('uiBlocked'))).lower()} "
        f"reasons={reasons or '-'}"
    )


def print_counts(title: str, counts: dict) -> None:
    print(f"{title}:")

    if counts:
        for key, value in counts.items():
            print(f"  {key}: {value}")
    else:
        print("  none")


def print_summary(index: dict, candidates: list[dict]) -> None:
    print(f"session: {index['sessionPath']}")
    print(f"profile: {index.get('profileId') or 'none'}")
    print(f"selected ticks: {index['selectedTickCount']}")
    print(f"selected tick range: {index.get('selectedTickRange') or 'none'}")
    print(f"matching targets before filters: {index.get('matchingTargetsBeforeFilters', index['matchingTargetCountBeforeLimit'])}")
    print(f"candidates after profile filters: {index.get('candidatesAfterProfileFilters', index['matchingTargetCountBeforeLimit'])}")
    print(f"matching targets before dedupe: {index.get('matchingTargetCountBeforeDedupe', index['matchingTargetCountBeforeLimit'])}")
    print(f"duplicates removed: {index.get('duplicatesRemoved', 0)}")
    print(f"UI-blocked candidates: {index.get('uiBlockedCount', 0)}")
    print(f"excluded UI-blocked candidates: {index.get('excludedUiBlockedCount', 0)}")
    print(f"candidates before limit: {index.get('candidateCountBeforeLimit', index['matchingTargetCountBeforeLimit'])}")
    print(f"dedupe enabled: {index.get('dedupeEnabled', True)}")
    print(f"limit: {'none' if index.get('noLimit') else index.get('limit')}")
    print(f"discarded by limit: {index.get('discardedByLimit', 0)}")
    print(f"candidate count: {index['candidateCount']}")
    print_counts("counts by qualityTier", index.get("countsByQualityTier") or {})
    print_counts("counts by classId", index.get("countsByClassId") or {})
    print_counts("counts by targetType", index["countsByTargetType"])
    print_counts("counts by role", index["countsByRole"])
    print_counts("counts by category", index["countsByCategory"])
    print_counts("counts by preferredAimGeometryType", index["countsByPreferredAimGeometryType"])
    print_counts("top tags", index["topTags"])
    print_counts("top positive signals", index.get("topPositiveSignals") or {})
    print_counts("top negative signals", index.get("topNegativeSignals") or {})
    print_counts("top reject reasons", index.get("topRejectReasons") or {})

    if index["warnings"]:
        print("warnings:")

        for warning in index["warnings"][:20]:
            print(f"  - {warning}")

    print("top candidates:")

    for candidate in candidates[:10]:
        print(f"  {compact_candidate_line(candidate)}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Select and rank target candidates from existing derived UI/world geometry. "
            "This writes geometry-only candidate records and never generates actions."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--tick", type=int, help="Only score records for one tick.")
    parser.add_argument("--latest", type=int, metavar="N", help="Only score records from the latest N target ticks.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--target-type", choices=sorted(TARGET_TYPES), default="all", help="Target type filter.")
    parser.add_argument("--role", choices=sorted(TARGET_ROLES), help="Target role filter.")
    parser.add_argument("--category", help="Case-insensitive exact target category filter.")
    parser.add_argument("--tag", help="Exact target tag filter.")
    parser.add_argument("--name", help="Case-insensitive text filter against target name/type/role/category/tags/id fields.")
    parser.add_argument("--id", help="Text filter against id/rawId/targetId.")
    parser.add_argument("--only-on-screen", action="store_true", help="Only score targets with onScreen=true.")
    parser.add_argument("--geometry-available", action="store_true", help="Only score targets with usable geometry.")
    parser.add_argument("--exclude-ui-blocked", action="store_true", help="Exclude candidates whose aim point intersects known UI regions.")
    parser.add_argument("--profile", help="Reusable target profile id, for example broad_qa or woodcutting.")
    parser.add_argument("--target-library", default=str(DEFAULT_TARGET_LIBRARY_PATH), help="Path to target_library.json.")
    parser.add_argument("--target-profiles", default=str(DEFAULT_TARGET_PROFILES_PATH), help="Path to target_profiles.json.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum candidate records to write. Use 0 for no limit. Default: 50, or profile default when --profile is used.")
    parser.add_argument("--no-limit", action="store_true", help="Write every matching candidate after dedupe.")
    parser.add_argument("--no-dedupe", action="store_true", help="Disable best-effort duplicate candidate collapse before applying --limit.")
    parser.add_argument("--summary", action="store_true", help="Print summary counts and top candidates.")
    parser.add_argument("--json", action="store_true", help="Print selected candidate records as JSON lines.")
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

    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be zero or positive")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    try:
        candidates, index = select_target_candidates(session.expanduser().resolve(), args)
    except RuntimeError as error:
        print(f"session: {session}")
        print(str(error))
        return 1

    if args.json:
        for candidate in candidates:
            print(json_dump_compact(candidate))
    elif args.summary:
        print_summary(index, candidates)
    else:
        if not candidates:
            print("No target candidates matched.")

        for candidate in candidates:
            print(compact_candidate_line(candidate))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
TARGET_TYPES = geometry.TARGET_TYPES
TARGET_ROLES = geometry.TARGET_ROLES
GEOMETRY_PRIORITY = (
    "clickboxPolygon",
    "clickboxBounds",
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
    "clickboxPolygon": 1.0,
    "clickboxBounds": 0.95,
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
WORLD_DISTANCE_ROLES = {"entity", "interactable", "item"}
WORLD_DISTANCE_TYPES = {"npc", "player", "sceneObject", "groundItem", "tile"}


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


def session_id_for(session: Path, records: list[dict]) -> str:
    for record in records:
        value = record.get("sessionId")

        if value:
            return str(value)

    return session.name


def is_polygon(value) -> bool:
    return isinstance(value, list) and any(is_point_list(point) for point in value)


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

        if key in {"clickboxPolygon", "convexHullPolygon", "tilePolygon", "canvasTilePolygon"} and is_polygon(value):
            available.append(key)
        elif key in {"clickboxBounds", "convexHullBounds", "pixelBox", "boundingBox", "bounds"} and is_bounds(value):
            available.append(key)
        elif key in {"canvasPoint", "canvasLocation", "canvasCenter", "center"} and is_point_dict(value):
            available.append(key)

    return available


def preferred_aim_geometry(record: dict) -> dict:
    source = geometry.geometry_for(record)
    available = available_geometry_types(record)

    for key in GEOMETRY_PRIORITY:
        if key not in available:
            continue

        value = source.get(key)

        if key in {"clickboxPolygon", "convexHullPolygon", "tilePolygon", "canvasTilePolygon"}:
            aim_bounds = polygon_bounds(value)
            return {
                "preferredAimGeometryType": key,
                "preferredAimGeometry": value,
                "aimPoint": polygon_center(value),
                "aimBounds": aim_bounds,
                "availableGeometryTypes": available,
                "geometryQuality": GEOMETRY_QUALITY.get(key, 0.5),
            }

        if key in {"clickboxBounds", "convexHullBounds", "pixelBox", "boundingBox", "bounds"}:
            return {
                "preferredAimGeometryType": key,
                "preferredAimGeometry": value,
                "aimPoint": bounds_center(value),
                "aimBounds": value,
                "availableGeometryTypes": available,
                "geometryQuality": GEOMETRY_QUALITY.get(key, 0.5),
            }

        if key in {"canvasPoint", "canvasLocation", "canvasCenter", "center"}:
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


def score_record(record: dict, aim: dict, args, frame_exists, distance: dict) -> dict:
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

    if "clickboxPolygon" in aim["availableGeometryTypes"] or "clickboxBounds" in aim["availableGeometryTypes"]:
        add("clickbox", 20, reason="clickbox")

    if "convexHullPolygon" in aim["availableGeometryTypes"] or "convexHullBounds" in aim["availableGeometryTypes"]:
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


def target_payload(record: dict) -> dict:
    target = geometry.target_for(record)
    return {
        "targetId": target.get("targetId"),
        "targetType": geometry.target_type_for(record),
        "name": geometry.target_name_for(record),
        "id": target.get("id"),
        "rawId": target.get("rawId"),
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


def candidate_record(dataset: geometry.TargetGeometryDataset, record: dict, rank: int, args, player_world_by_tick: dict[int, dict]) -> dict:
    aim = preferred_aim_geometry(record)
    frame_exists = dataset.frame_exists_for_record(record)
    distance = target_distance_for_record(record, player_world_by_tick.get(record.get("tickId")))
    scoring = score_record(record, aim, args, frame_exists, distance)
    center_distance = screen_center_distance(record, aim)
    return {
        "schemaVersion": SCHEMA_VERSION_RECORD,
        "sessionId": record.get("sessionId"),
        "tickId": record.get("tickId"),
        "timestampUtc": record.get("timestampUtc"),
        "rank": rank,
        "score": scoring["score"],
        "target": target_payload(record),
        "sourceTarget": {
            "sourceFileType": record.get("_sourceKind"),
            "originalTargetRecordIndex": record.get("_sourceIndex"),
        },
        "geometry": {
            "coordinateSpace": geometry.geometry_for(record).get("coordinateSpace"),
            **aim,
        },
        "scoring": scoring,
        "targetDistanceTiles": distance["targetDistanceTiles"],
        "targetDistanceChebyshev": distance["targetDistanceChebyshev"],
        "targetDistanceManhattan": distance["targetDistanceManhattan"],
        "targetDistanceEuclidean": distance["targetDistanceEuclidean"],
        "playerWorld": distance["playerWorld"],
        "targetWorld": distance["targetWorld"],
        "screenCenterDistance": center_distance,
        "frame": frame_payload(record, frame_exists),
        "safety": {
            "readOnly": True,
            "actionGenerated": False,
        },
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


def rank_candidates(dataset: geometry.TargetGeometryDataset, records: list[dict], args, player_world_by_tick: dict[int, dict]) -> list[dict]:
    candidates = []

    for record in records:
        candidate = candidate_record(dataset, record, 0, args, player_world_by_tick)
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

    limited = candidates[: args.limit]

    for rank, candidate in enumerate(limited, start=1):
        candidate["rank"] = rank

    return limited


def index_for(session: Path, selected_ticks: set[int], matching_count: int, candidates: list[dict], warnings: list[str]) -> dict:
    counts_by_type = Counter()
    counts_by_role = Counter()
    counts_by_category = Counter()
    counts_by_geometry = Counter()
    tag_counts = Counter()

    for candidate in candidates:
        target = candidate.get("target", {})
        counts_by_type[target.get("targetType") or "unknown"] += 1
        counts_by_role[target.get("targetRole") or "unknown"] += 1
        counts_by_category[target.get("targetCategory") or "unknown"] += 1
        counts_by_geometry[candidate.get("geometry", {}).get("preferredAimGeometryType") or "none"] += 1

        for tag in target.get("targetTags") or []:
            tag_counts[str(tag)] += 1

    tick_range = [min(selected_ticks), max(selected_ticks)] if selected_ticks else None
    return {
        "schemaVersion": SCHEMA_VERSION_INDEX,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "selectedTickCount": len(selected_ticks),
        "selectedTickRange": tick_range,
        "matchingTargetCountBeforeLimit": matching_count,
        "candidateCount": len(candidates),
        "countsByTargetType": dict(counts_by_type.most_common()),
        "countsByRole": dict(counts_by_role.most_common()),
        "countsByCategory": dict(counts_by_category.most_common()),
        "countsByPreferredAimGeometryType": dict(counts_by_geometry.most_common()),
        "topTags": dict(tag_counts.most_common(25)),
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

    records, ticks = candidate_input_records(dataset, args)
    player_world_by_tick, distance_warnings = load_player_world_by_tick(
        session,
        {tick for tick in (record.get("tickId") for record in records) if isinstance(tick, int)},
    )
    candidates = rank_candidates(dataset, records, args, player_world_by_tick)
    warnings = list(dataset.messages) + list(dataset.warnings) + distance_warnings
    index = index_for(session, ticks, len(records), candidates, warnings)
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
        f"{candidate.get('rank')} score={candidate.get('score')} tick={candidate.get('tickId')} "
        f"type={target.get('targetType')} name=\"{target.get('name') or '-'}\" "
        f"id={target_id if target_id is not None else '-'} "
        f"role={target.get('targetRole')} category={target.get('targetCategory')} "
        f"dist={distance_text} "
        f"aim={aim_text} geometry={candidate.get('geometry', {}).get('preferredAimGeometryType') or 'none'} "
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
    print(f"selected ticks: {index['selectedTickCount']}")
    print(f"selected tick range: {index.get('selectedTickRange') or 'none'}")
    print(f"matching targets before limit: {index['matchingTargetCountBeforeLimit']}")
    print(f"candidate count: {index['candidateCount']}")
    print_counts("counts by targetType", index["countsByTargetType"])
    print_counts("counts by role", index["countsByRole"])
    print_counts("counts by category", index["countsByCategory"])
    print_counts("counts by preferredAimGeometryType", index["countsByPreferredAimGeometryType"])
    print_counts("top tags", index["topTags"])

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
    parser.add_argument("--limit", type=int, default=50, help="Maximum candidate records to write. Default: 50.")
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

    if args.limit < 1:
        parser.error("--limit must be positive")

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

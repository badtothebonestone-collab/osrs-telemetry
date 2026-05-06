import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir, list_tick_files, safe_read_json


REPORT_SCHEMA = "target_coverage_diagnostic.v1"
DEFAULT_SAMPLE_TICKS = 25
SCENE_OBJECT_TYPES = {"sceneObject", "object", "worldObject"}
RAW_SCENE_KINDS = ("GAME_OBJECT", "WALL_OBJECT", "DECORATIVE_OBJECT", "GROUND_OBJECT")
GEOMETRY_FIELDS = (
    "canvasLocation",
    "canvasPoint",
    "canvasCenter",
    "clickboxBounds",
    "clickboxPolygon",
    "convexHullBounds",
    "convexHullPolygon",
    "canvasTilePolygon",
    "tilePolygon",
    "pixelBox",
    "onScreen",
    "geometryAvailable",
)
SCENE_CAPTURE_SUMMARY_NUMERIC_FIELDS = (
    "configuredRadius",
    "configuredMaxSceneObjects",
    "scanRadius",
    "maxSceneObjects",
    "maxGroundItems",
    "scannedPlane",
    "scannedTiles",
    "tilesWithObjects",
    "scanMinSceneX",
    "scanMaxSceneX",
    "scanMinSceneY",
    "scanMaxSceneY",
    "sceneObjectsSeen",
    "sceneObjectsCaptured",
    "sceneObjectsSkippedByCap",
    "gameObjectsSeen",
    "wallObjectsSeen",
    "decorativeObjectsSeen",
    "groundObjectsSeen",
    "gameObjectsCaptured",
    "wallObjectsCaptured",
    "decorativeObjectsCaptured",
    "groundObjectsCaptured",
    "gameObjectsSkippedByCap",
    "wallObjectsSkippedByCap",
    "decorativeObjectsSkippedByCap",
    "groundObjectsSkippedByCap",
    "nullObjectsSkipped",
    "groundItemsSeen",
    "groundItemsCaptured",
    "groundItemsSkippedByCap",
    "nullGroundItemsSkipped",
    "scanWidth",
    "scanHeight",
)
SCENE_CAPTURE_SUMMARY_FLOAT_FIELDS = ("captureRatio",)
SCENE_CAPTURE_SUMMARY_STRING_FIELDS = ("sceneCaptureMode",)
SCENE_CAPTURE_SUMMARY_BOOLEAN_FIELDS = ("sceneObjectCapHit", "groundItemCapHit", "fullCurrentPlaneScan")
PERFORMANCE_NUMERIC_FIELDS = (
    "sceneCaptureDurationMillis",
    "snapshotBuildDurationMillis",
    "writerQueueSize",
    "writerDroppedRecords",
    "approximateTickJsonBytes",
)
SCENE_INDEX_NUMERIC_FIELDS = (
    "indexObjectCount",
    "presentObjectCount",
    "newlyIndexedCount",
    "updatedCount",
    "despawnedCount",
    "maxSceneIndexObjects",
    "sceneIndexBuildDurationMillis",
    "sceneIndexUpdateDurationMillis",
)
SCENE_INDEX_BOOLEAN_FIELDS = ("indexEnabled", "fullResyncThisTick", "indexCapHit")
SCENE_INDEX_STRING_FIELDS = ("sceneCaptureMode", "resyncReason")
SCENE_PROJECTION_NUMERIC_FIELDS = (
    "projectionCandidatesConsidered",
    "projectionObjectsUpdated",
    "projectionObjectsReused",
    "projectionDurationMillis",
    "visibleObjectCount",
    "onScreenObjectCount",
    "geometryAvailableCount",
    "missingGeometryCount",
)
SCENE_PROJECTION_BOOLEAN_FIELDS = ("projectionStateChanged",)
SCENE_PROJECTION_STRING_FIELDS = ("projectionStateHash", "projectionRefreshMode")
SOURCE_AUDIT_TERMS = (
    "MAX_SCENE_OBJECTS",
    "SCENE_OBJECT_LIMIT",
    "MAX_OBJECTS",
    "SceneCaptureMode",
    "STATIC_SCENE_INDEX_DIAGNOSTIC",
    "sceneIndexSummary",
    "sceneProjectionSummary",
    "visibleSceneObjectRefs",
    "sceneObjectDeltas",
    "configuredMaxSceneObjects",
    "maxSceneObjects",
    "sceneObjectCapHit",
    "radius",
    "scanRadius",
    "fullCurrentPlaneScan",
    "distance",
    "getGameObjects",
    "getWallObject",
    "getDecorativeObject",
    "getGroundObject",
    "getSceneTilePaint",
    "getSceneTileModel",
    "geometryAvailable",
    "onScreen",
    "retainedFrame",
    "maxDraw",
    "maxTargets",
    "targetCategory",
    "targetRole",
    "targetType",
)
FRAME_TICK_RE = re.compile(r"frame-tick-(\d+)\.[^.\\/]+$", re.IGNORECASE)


def compact_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_key(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "missing"


def safe_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def add_count(counter: Counter, value, default: str = "unknown") -> None:
    text = str(value) if value not in (None, "") else default
    counter[text] += 1


def sorted_tick_range(ticks) -> list[int] | None:
    values = sorted(tick for tick in ticks if isinstance(tick, int))
    if not values:
        return None
    return [values[0], values[-1]]


def relative_or_str(path: Path, root: Path | None = None) -> str:
    try:
        if root:
            return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        pass
    return str(path)


def parse_frame_tick(path_text) -> int | None:
    if not path_text:
        return None
    match = FRAME_TICK_RE.search(str(path_text).replace("\\", "/"))
    return safe_int(match.group(1)) if match else None


def first_present(mapping: dict, keys: tuple[str, ...] | list[str]):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def nested_dict(record: dict, key: str) -> dict:
    value = record.get(key) if isinstance(record, dict) else None
    return value if isinstance(value, dict) else {}


def target_for(record: dict) -> dict:
    return nested_dict(record, "target")


def geometry_for(record: dict) -> dict:
    return nested_dict(record, "geometry")


def frame_for(record: dict) -> dict:
    return nested_dict(record, "frame")


def target_type_for(record: dict) -> str:
    target = target_for(record)
    return str(target.get("targetType") or record.get("targetType") or "unknown")


def target_role_for(record: dict) -> str:
    target = target_for(record)
    return str(target.get("targetRole") or record.get("targetRole") or "unknown")


def target_category_for(record: dict) -> str:
    target = target_for(record)
    return str(target.get("targetCategory") or record.get("targetCategory") or "unknown")


def target_tags_for(record: dict) -> list[str]:
    target = target_for(record)
    tags = target.get("targetTags") or record.get("targetTags") or []
    if isinstance(tags, list):
        return [str(tag) for tag in tags if str(tag)]
    if isinstance(tags, str):
        return [tag.strip() for tag in tags.split(",") if tag.strip()]
    return []


def target_name_for(record: dict) -> str:
    target = target_for(record)
    value = (
        target.get("name")
        or target.get("targetName")
        or target.get("objectName")
        or target.get("itemName")
        or target.get("npcName")
        or record.get("name")
        or record.get("targetName")
    )
    return str(value) if value not in (None, "") else ""


def target_id_values(record: dict) -> list[str]:
    target = target_for(record)
    values = []
    for source in (target, record):
        if not isinstance(source, dict):
            continue
        for key in ("id", "rawId", "objectId", "npcId", "itemId", "targetId"):
            value = source.get(key)
            if value not in (None, ""):
                values.append(str(value))
    return values


def object_hash_values(record: dict) -> list[str]:
    target = target_for(record)
    values = []
    for source in (target, record):
        if not isinstance(source, dict):
            continue
        for key in ("hash", "objectHash", "rawHash", "targetHash"):
            value = source.get(key)
            if value not in (None, ""):
                values.append(str(value))
    return values


def world_location(record: dict) -> dict | None:
    target = target_for(record)
    for source in (
        target.get("world") if isinstance(target, dict) else None,
        record.get("world") if isinstance(record, dict) else None,
        record.get("targetWorld") if isinstance(record, dict) else None,
        record,
    ):
        if not isinstance(source, dict):
            continue
        x = first_present(source, ("x", "worldX", "targetWorldX"))
        y = first_present(source, ("y", "worldY", "targetWorldY"))
        plane = first_present(source, ("plane", "z", "worldPlane"))
        if x is not None and y is not None:
            return {"x": safe_int(x), "y": safe_int(y), "plane": safe_int(plane)}
    return None


def scene_location(record: dict) -> dict | None:
    target = target_for(record)
    for source in (target.get("scene") if isinstance(target, dict) else None, record):
        if not isinstance(source, dict):
            continue
        x = first_present(source, ("x", "sceneX"))
        y = first_present(source, ("y", "sceneY"))
        if x is not None and y is not None:
            return {"x": safe_int(x), "y": safe_int(y)}
    return None


def local_location(record: dict) -> dict | None:
    target = target_for(record)
    for source in (target.get("local") if isinstance(target, dict) else None, record):
        if not isinstance(source, dict):
            continue
        x = first_present(source, ("x", "localX"))
        y = first_present(source, ("y", "localY"))
        if x is not None and y is not None:
            return {"x": safe_int(x), "y": safe_int(y)}
    return None


def coordinate_key(value: dict | None) -> tuple:
    if not isinstance(value, dict):
        return (None, None, None)
    return (value.get("x"), value.get("y"), value.get("plane"))


def flat_location_key(location: dict | None) -> tuple:
    if not isinstance(location, dict):
        return (None, None)
    return (location.get("x"), location.get("y"))


def object_identity(record: dict, source_kind: str = "") -> tuple:
    target = target_for(record)
    target_type = target_type_for(record)
    if source_kind == "raw_scene":
        target_type = "sceneObject"
    elif source_kind == "raw_npc":
        target_type = "npc"
    elif source_kind == "raw_ground":
        target_type = "groundItem"

    target_id = first_present(target, ("rawId", "id", "targetId"))
    if target_id is None:
        target_id = first_present(record, ("rawId", "id", "targetId"))
    object_key = first_present(target, ("objectKey",))
    if object_key is None:
        object_key = first_present(record, ("objectKey",))

    kind = first_present(target, ("kind", "type", "layer"))
    if kind is None:
        kind = first_present(record, ("kind", "type", "layer"))

    object_hash = first_present(target, ("objectHash", "hash", "rawHash"))
    if object_hash is None:
        object_hash = first_present(record, ("objectHash", "hash", "rawHash"))

    world = world_location(record)
    scene = scene_location(record)
    local = local_location(record)

    # Schema drift is intentional here: use the richest stable tuple available,
    # and tolerate missing identity pieces rather than dropping the record.
    return (
        target_type,
        str(object_key) if object_key is not None else None,
        str(kind) if kind is not None else None,
        str(target_id) if target_id is not None else None,
        str(object_hash) if object_hash is not None else None,
        coordinate_key(world),
        flat_location_key(scene),
        flat_location_key(local),
    )


def identity_text(identity: tuple) -> str:
    return compact_json(identity)


def read_jsonl_filtered(
    path: Path,
    selected_ticks: set[int] | None,
    tick_getter,
    collect_records: bool = True,
) -> tuple[list[dict], set[int], int, int]:
    records = []
    ticks = set()
    malformed = 0
    total = 0

    if not path.exists():
        return records, ticks, malformed, total

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    malformed += 1
                    continue
                total += 1
                tick_id = tick_getter(record)
                if isinstance(tick_id, int):
                    ticks.add(tick_id)
                if collect_records and (selected_ticks is None or tick_id in selected_ticks):
                    records.append(record)
    except OSError:
        malformed += 1

    return records, ticks, malformed, total


def tick_id_for_record(record: dict) -> int | None:
    return safe_int(record.get("tickId"))


def tick_id_for_candidate(record: dict) -> int | None:
    return safe_int(record.get("tickId") or target_for(record).get("tickId"))


def tick_id_for_scenario(record: dict) -> int | None:
    return safe_int(record.get("tickId"))


def scan_raw_tick_ids(tick_files: list[Path]) -> tuple[list[int], dict[str, int], int]:
    ticks = []
    malformed = {}
    total = 0
    for path in tick_files:
        count = 0
        if not path.exists():
            malformed[str(path)] = 1
            continue
        try:
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError:
                        count += 1
                        continue
                    if not isinstance(record, dict):
                        count += 1
                        continue
                    total += 1
                    tick_id = tick_id_for_record(record)
                    if isinstance(tick_id, int):
                        ticks.append(tick_id)
        except OSError:
            count += 1
        if count:
            malformed[str(path)] = count
    return sorted(set(ticks)), malformed, total


def read_selected_raw_ticks(tick_files: list[Path], selected_ticks: set[int]) -> tuple[list[dict], dict[str, int]]:
    records = []
    malformed = {}
    for path in tick_files:
        count = 0
        try:
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except json.JSONDecodeError:
                        count += 1
                        continue
                    if not isinstance(record, dict):
                        count += 1
                        continue
                    if tick_id_for_record(record) in selected_ticks:
                        records.append(record)
        except OSError:
            count += 1
        if count:
            malformed[str(path)] = count
    return records, malformed


def resolve_session(args) -> tuple[Path | None, str]:
    if args.session:
        return Path(args.session).expanduser().resolve(), "explicit --session"

    sessions_dir = get_sessions_dir(args.sessions_dir)
    session = find_newest_session(sessions_dir)
    mode = "--sessions-dir newest" if args.sessions_dir else "default newest session"
    return (session.resolve() if session else None), mode


def output_paths(session: Path, scenario: str | None) -> dict[str, Path]:
    interaction = session / "interaction_geometry"
    scenario_dir = session / "scenario_datasets"
    return {
        "manifest": session / "manifest.json",
        "worldTargets": interaction / "world_targets.jsonl",
        "worldGeometryIndex": interaction / "world_geometry_index.json",
        "sceneStaticIndex": interaction / "scene_static_index.jsonl",
        "uiTargets": interaction / "ui_targets.jsonl",
        "targetCandidates": interaction / "target_candidates.jsonl",
        "targetCandidatesIndex": interaction / "target_candidates_index.json",
        "scenario": scenario_dir / f"{scenario}.jsonl" if scenario else None,
        "scenarioIndex": scenario_dir / "scenario_index.json",
    }


def select_ticks(all_raw_ticks: list[int], args, fallback_ticks: set[int]) -> tuple[list[int], str]:
    available = sorted(set(all_raw_ticks) or set(fallback_ticks))

    if args.tick is not None:
        return [args.tick], "explicit --tick"

    if args.tick_range is not None:
        start, end = args.tick_range
        if start > end:
            start, end = end, start
        if available:
            return [tick for tick in available if start <= tick <= end], "explicit --range"
        return list(range(start, end + 1)), "explicit --range"

    if args.latest is not None:
        return available[-args.latest :], "explicit --latest"

    if args.all_ticks:
        return available, "explicit --all-ticks"

    return available[-DEFAULT_SAMPLE_TICKS:], f"default sample latest {DEFAULT_SAMPLE_TICKS}"


def retained_frame_ticks(session: Path) -> set[int]:
    frames_dir = session / "frames"
    if not frames_dir.exists():
        return set()
    ticks = set()
    for path in frames_dir.iterdir():
        if path.is_file():
            tick = parse_frame_tick(path.name)
            if tick is not None:
                ticks.add(tick)
    return ticks


def summarize_raw_ticks(raw_ticks: list[dict]) -> dict:
    by_tick = {}
    totals = Counter()
    scene_kind_counts = Counter()
    scene_capture_by_tick = {}
    scene_index_by_tick = {}
    scene_projection_by_tick = {}
    field_presence = {field: {"present": 0, "missing": 0} for field in (
        "localX/localY",
        "worldX/worldY/plane",
        "sceneX/sceneY",
        *GEOMETRY_FIELDS,
    )}

    for tick in raw_ticks:
        tick_id = tick_id_for_record(tick)
        scene_capture_summary = normalize_scene_capture_summary(tick.get("sceneCaptureSummary"))
        if scene_capture_summary:
            scene_capture_by_tick[str(tick_id)] = scene_capture_summary
        scene_index_summary = normalize_summary(
            tick.get("sceneIndexSummary"),
            SCENE_INDEX_NUMERIC_FIELDS,
            (),
            SCENE_INDEX_STRING_FIELDS,
            SCENE_INDEX_BOOLEAN_FIELDS,
        )
        if scene_index_summary:
            scene_index_by_tick[str(tick_id)] = scene_index_summary
        scene_projection_summary = normalize_summary(
            tick.get("sceneProjectionSummary"),
            SCENE_PROJECTION_NUMERIC_FIELDS,
            (),
            SCENE_PROJECTION_STRING_FIELDS,
            SCENE_PROJECTION_BOOLEAN_FIELDS,
        )
        if scene_projection_summary:
            scene_projection_by_tick[str(tick_id)] = scene_projection_summary
        npcs = [item for item in tick.get("npcs") or [] if isinstance(item, dict)]
        players = [item for item in tick.get("players") or [] if isinstance(item, dict)]
        scene_objects = [item for item in tick.get("sceneObjects") or [] if isinstance(item, dict)]
        visible_scene_refs = [item for item in tick.get("visibleSceneObjectRefs") or [] if isinstance(item, dict)]
        deltas = tick.get("sceneObjectDeltas") if isinstance(tick.get("sceneObjectDeltas"), dict) else {}
        delta_new = [item for item in deltas.get("newObjects") or [] if isinstance(item, dict)]
        delta_updated = [item for item in deltas.get("updatedObjects") or [] if isinstance(item, dict)]
        delta_despawned = [item for item in deltas.get("despawnedObjects") or [] if isinstance(item, dict)]
        ground_items = [item for item in tick.get("groundItems") or [] if isinstance(item, dict)]
        tiles = [item for item in tick.get("tiles") or [] if isinstance(item, dict)]
        kind_counts = Counter()

        for obj in scene_objects + visible_scene_refs + delta_new + delta_updated + delta_despawned:
            kind = str(obj.get("kind") or obj.get("type") or obj.get("layer") or "unknown")
            kind_counts[kind] += 1
            scene_kind_counts[kind] += 1

        records_for_field_scan = scene_objects + visible_scene_refs + delta_new + delta_updated + delta_despawned + npcs + players + ground_items + tiles
        for record in records_for_field_scan:
            update_field_presence(field_presence, "localX/localY", record.get("localX") is not None and record.get("localY") is not None)
            update_field_presence(
                field_presence,
                "worldX/worldY/plane",
                record.get("worldX") is not None and record.get("worldY") is not None and record.get("plane") is not None,
            )
            update_field_presence(
                field_presence,
                "sceneX/sceneY",
                record.get("sceneX") is not None and record.get("sceneY") is not None,
            )
            for field in GEOMETRY_FIELDS:
                update_field_presence(field_presence, field, field in record and record.get(field) is not None)

        raw_counts = {
            "npcs": len(npcs),
            "players": len(players),
            "sceneObjects": len(scene_objects),
            "visibleSceneObjectRefs": len(visible_scene_refs),
            "sceneObjectDeltasNew": len(delta_new),
            "sceneObjectDeltasUpdated": len(delta_updated),
            "sceneObjectDeltasDespawned": len(delta_despawned),
            "sceneObjectsByKind": dict(kind_counts),
            "groundItems": len(ground_items),
            "tiles": len(tiles),
        }
        by_tick[str(tick_id)] = raw_counts
        totals["npcs"] += len(npcs)
        totals["players"] += len(players)
        totals["sceneObjects"] += len(scene_objects)
        totals["visibleSceneObjectRefs"] += len(visible_scene_refs)
        totals["sceneObjectDeltasNew"] += len(delta_new)
        totals["sceneObjectDeltasUpdated"] += len(delta_updated)
        totals["sceneObjectDeltasDespawned"] += len(delta_despawned)
        totals["groundItems"] += len(ground_items)
        totals["tiles"] += len(tiles)

    return {
        "byTick": by_tick,
        "totals": dict(totals),
        "sceneObjectsByKind": dict(scene_kind_counts),
        "fieldPresence": field_presence,
        "sceneCaptureSummary": summarize_scene_capture_summaries(scene_capture_by_tick),
        "sceneIndexSummary": summarize_normalized_summaries(
            scene_index_by_tick,
            SCENE_INDEX_NUMERIC_FIELDS,
            SCENE_INDEX_STRING_FIELDS,
            SCENE_INDEX_BOOLEAN_FIELDS,
        ),
        "sceneProjectionSummary": summarize_normalized_summaries(
            scene_projection_by_tick,
            SCENE_PROJECTION_NUMERIC_FIELDS,
            SCENE_PROJECTION_STRING_FIELDS,
            SCENE_PROJECTION_BOOLEAN_FIELDS,
        ),
        "performance": summarize_performance(raw_ticks),
    }


def update_field_presence(field_presence: dict, field: str, present: bool) -> None:
    if field not in field_presence:
        field_presence[field] = {"present": 0, "missing": 0}
    field_presence[field]["present" if present else "missing"] += 1


def normalize_scene_capture_summary(value) -> dict | None:
    if not isinstance(value, dict):
        return None

    normalized = {}

    for field in SCENE_CAPTURE_SUMMARY_NUMERIC_FIELDS:
        parsed = safe_int(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    for field in SCENE_CAPTURE_SUMMARY_FLOAT_FIELDS:
        parsed = safe_float(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    for field in SCENE_CAPTURE_SUMMARY_STRING_FIELDS:
        raw = value.get(field)

        if raw not in (None, ""):
            normalized[field] = str(raw)

    for field in SCENE_CAPTURE_SUMMARY_BOOLEAN_FIELDS:
        parsed = safe_bool(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    return normalized


def normalize_summary(value, numeric_fields: tuple[str, ...], float_fields: tuple[str, ...], string_fields: tuple[str, ...], boolean_fields: tuple[str, ...]) -> dict | None:
    if not isinstance(value, dict):
        return None

    normalized = {}

    for field in numeric_fields:
        parsed = safe_int(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    for field in float_fields:
        parsed = safe_float(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    for field in string_fields:
        raw = value.get(field)

        if raw not in (None, ""):
            normalized[field] = str(raw)

    for field in boolean_fields:
        parsed = safe_bool(value.get(field))

        if parsed is not None:
            normalized[field] = parsed

    return normalized


def summarize_normalized_summaries(by_tick: dict[str, dict], numeric_fields: tuple[str, ...], string_count_fields: tuple[str, ...], boolean_count_fields: tuple[str, ...]) -> dict:
    totals = Counter()
    averages = {}
    maxima = {}
    string_counts = {field: Counter() for field in string_count_fields}
    boolean_counts = {field: Counter() for field in boolean_count_fields}

    for summary in by_tick.values():
        for field in numeric_fields:
            totals[field] += int(summary.get(field) or 0)

        for field in string_count_fields:
            add_count(string_counts[field], summary.get(field))

        for field in boolean_count_fields:
            boolean_counts[field][bool_key(summary.get(field))] += 1

    for field in numeric_fields:
        values = []

        for summary in by_tick.values():
            value = safe_float(summary.get(field))

            if value is not None:
                values.append(value)

        stats = numeric_stats(values)
        averages[field] = stats["avg"]
        maxima[field] = stats["max"]

    return {
        "present": bool(by_tick),
        "byTick": by_tick,
        "totals": dict(totals),
        "averagesPerTick": averages,
        "maxPerTick": maxima,
        "stringCounts": {field: dict(counts) for field, counts in string_counts.items()},
        "booleanCounts": {field: dict(counts) for field, counts in boolean_counts.items()},
    }


def numeric_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "max": None}
    return {"count": len(values), "avg": sum(values) / len(values), "max": max(values)}


def summarize_scene_capture_summaries(by_tick: dict[str, dict]) -> dict:
    totals = Counter()
    cap_hit_ticks = 0
    ground_item_cap_hit_ticks = 0
    mode_counts = Counter()
    full_current_plane_counts = Counter()
    scan_radius_counts = Counter()
    configured_max_counts = Counter()
    averages = {}
    maxima = {}

    for summary in by_tick.values():
        if summary.get("sceneObjectCapHit") is True:
            cap_hit_ticks += 1
        if summary.get("groundItemCapHit") is True:
            ground_item_cap_hit_ticks += 1
        add_count(mode_counts, summary.get("sceneCaptureMode"))
        full_current_plane_counts[bool_key(summary.get("fullCurrentPlaneScan"))] += 1
        add_count(scan_radius_counts, summary.get("configuredRadius", summary.get("scanRadius")))
        add_count(configured_max_counts, summary.get("configuredMaxSceneObjects", summary.get("maxSceneObjects")))

        for field in SCENE_CAPTURE_SUMMARY_NUMERIC_FIELDS:
            if field.startswith("scanMin") or field.startswith("scanMax") or field in {
                "configuredRadius",
                "configuredMaxSceneObjects",
                "scanRadius",
                "maxSceneObjects",
                "maxGroundItems",
                "scannedPlane",
                "scanWidth",
                "scanHeight",
            }:
                continue
            totals[field] += int(summary.get(field) or 0)

    for field in (
        "scannedTiles",
        "sceneObjectsSeen",
        "sceneObjectsCaptured",
        "sceneObjectsSkippedByCap",
        "captureRatio",
    ):
        values = []
        for summary in by_tick.values():
            value = safe_float(summary.get(field))
            if value is not None:
                values.append(value)
        stats = numeric_stats(values)
        averages[field] = stats["avg"]
        maxima[field] = stats["max"]

    return {
        "present": bool(by_tick),
        "byTick": by_tick,
        "totals": dict(totals),
        "sceneObjectCapHitTickCount": cap_hit_ticks,
        "groundItemCapHitTickCount": ground_item_cap_hit_ticks,
        "modeCounts": dict(mode_counts),
        "fullCurrentPlaneScanCounts": dict(full_current_plane_counts),
        "configuredRadiusCounts": dict(scan_radius_counts),
        "configuredMaxSceneObjectCounts": dict(configured_max_counts),
        "averagesPerTick": averages,
        "maxPerTick": maxima,
    }


def summarize_performance(raw_ticks: list[dict]) -> dict:
    summaries = {}

    for field in PERFORMANCE_NUMERIC_FIELDS:
        values = []

        for tick in raw_ticks:
            value = safe_float(tick.get(field))

            if value is not None:
                values.append(value)

        summaries[field] = numeric_stats(values)

    return summaries


def fallback_scene_object(record: dict) -> bool:
    target = target_for(record)
    if target_type_for(record) != "sceneObject":
        return False
    source = str(target.get("nameSource") or target.get("objectNameSource") or "").lower()
    name = target_name_for(record)
    fallback = str(target.get("fallbackName") or "")
    return source == "fallback" or name == fallback or name.startswith("SceneObject[")


def unclassified_scene_object(record: dict) -> bool:
    return target_type_for(record) == "sceneObject" and (
        target_role_for(record).lower() in {"", "unknown"} or target_category_for(record).lower() in {"", "unknown"}
    )


def summarize_world_targets(records: list[dict], all_ticks: set[int], selected_ticks: set[int], frame_ticks: set[int]) -> dict:
    by_tick = defaultdict(lambda: {
        "total": 0,
        "byTargetType": Counter(),
        "byTargetRole": Counter(),
        "byTargetCategory": Counter(),
        "geometryAvailable": Counter(),
        "onScreen": Counter(),
        "coordinateSpace": Counter(),
        "fallbackSceneObjects": 0,
        "unclassifiedSceneObjects": 0,
    })
    counts = {
        "byTargetType": Counter(),
        "byTargetRole": Counter(),
        "byTargetCategory": Counter(),
        "geometryAvailable": Counter(),
        "onScreen": Counter(),
        "coordinateSpace": Counter(),
    }
    fallback_ids = Counter()
    unclassified_ids = Counter()
    fallback_kind_counts = Counter()
    unclassified_kind_counts = Counter()
    fallback_samples = {}
    frame_path_ticks = set()
    selected_world_ticks = set()

    for record in records:
        tick_id = tick_id_for_record(record)
        if tick_id is not None:
            selected_world_ticks.add(tick_id)
        target_type = target_type_for(record)
        role = target_role_for(record)
        category = target_category_for(record)
        geometry = geometry_for(record)
        coordinate_space = geometry.get("coordinateSpace") or "missing"
        frame_tick = parse_frame_tick(frame_for(record).get("path"))
        if frame_tick is not None:
            frame_path_ticks.add(frame_tick)

        item = by_tick[str(tick_id)]
        item["total"] += 1
        item["byTargetType"][target_type] += 1
        item["byTargetRole"][role] += 1
        item["byTargetCategory"][category] += 1
        item["geometryAvailable"][bool_key(geometry.get("geometryAvailable"))] += 1
        item["onScreen"][bool_key(geometry.get("onScreen"))] += 1
        item["coordinateSpace"][str(coordinate_space)] += 1
        if fallback_scene_object(record):
            item["fallbackSceneObjects"] += 1
            target = target_for(record)
            object_id = first_present(target, ("rawId", "id", "targetId"))
            add_count(fallback_ids, object_id)
            add_count(fallback_kind_counts, first_present(target, ("kind", "type", "layer")))
            key = str(object_id) if object_id is not None else "unknown"
            fallback_samples.setdefault(
                key,
                {
                    "name": target_name_for(record),
                    "role": role,
                    "category": category,
                    "world": world_location(record),
                    "scene": scene_location(record),
                    "onScreen": geometry.get("onScreen"),
                    "geometryAvailable": geometry.get("geometryAvailable"),
                },
            )
        if unclassified_scene_object(record):
            item["unclassifiedSceneObjects"] += 1
            target = target_for(record)
            add_count(unclassified_ids, first_present(target, ("rawId", "id", "targetId")))
            add_count(unclassified_kind_counts, first_present(target, ("kind", "type", "layer")))

        counts["byTargetType"][target_type] += 1
        counts["byTargetRole"][role] += 1
        counts["byTargetCategory"][category] += 1
        counts["geometryAvailable"][bool_key(geometry.get("geometryAvailable"))] += 1
        counts["onScreen"][bool_key(geometry.get("onScreen"))] += 1
        counts["coordinateSpace"][str(coordinate_space)] += 1

    serialized_by_tick = {}
    for tick, value in by_tick.items():
        serialized_by_tick[tick] = {
            "total": value["total"],
            "byTargetType": dict(value["byTargetType"]),
            "byTargetRole": dict(value["byTargetRole"]),
            "byTargetCategory": dict(value["byTargetCategory"]),
            "geometryAvailable": dict(value["geometryAvailable"]),
            "onScreen": dict(value["onScreen"]),
            "coordinateSpace": dict(value["coordinateSpace"]),
            "fallbackSceneObjects": value["fallbackSceneObjects"],
            "unclassifiedSceneObjects": value["unclassifiedSceneObjects"],
        }

    return {
        "present": bool(records),
        "byTick": serialized_by_tick,
        "total": len(records),
        "counts": {key: dict(value) for key, value in counts.items()},
        "fallbackSceneObjectCount": sum(value["fallbackSceneObjects"] for value in by_tick.values()),
        "unclassifiedSceneObjectCount": sum(value["unclassifiedSceneObjects"] for value in by_tick.values()),
        "topFallbackSceneObjectIds": dict(fallback_ids.most_common(25)),
        "topUnclassifiedSceneObjectIds": dict(unclassified_ids.most_common(25)),
        "fallbackSceneObjectKinds": dict(fallback_kind_counts),
        "unclassifiedSceneObjectKinds": dict(unclassified_kind_counts),
        "fallbackSceneObjectSamples": fallback_samples,
        "targetTickRange": sorted_tick_range(all_ticks),
        "selectedWorldTickRange": sorted_tick_range(selected_world_ticks),
        "retainedFrameTickRange": sorted_tick_range(frame_ticks),
        "framePathTickRange": sorted_tick_range(frame_path_ticks),
        "selectedRawWorldOverlap": bool(selected_ticks & selected_world_ticks),
    }


def summarize_candidates(records: list[dict], all_ticks: set[int]) -> dict:
    by_tick = defaultdict(lambda: {
        "total": 0,
        "byTargetType": Counter(),
        "byTargetRole": Counter(),
        "byTargetCategory": Counter(),
        "selectedRejected": Counter(),
        "rejectionReasons": Counter(),
    })
    totals = {
        "byTargetType": Counter(),
        "byTargetRole": Counter(),
        "byTargetCategory": Counter(),
        "selectedRejected": Counter(),
        "rejectionReasons": Counter(),
    }

    for record in records:
        tick = str(tick_id_for_candidate(record))
        status = "selected"
        if record.get("rejected") is True or record.get("selected") is False:
            status = "rejected"
        reasons = record.get("rejectionReasons") or record.get("rejectionReason") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        target_type = target_type_for(record)
        role = target_role_for(record)
        category = target_category_for(record)
        by_tick[tick]["total"] += 1
        by_tick[tick]["byTargetType"][target_type] += 1
        by_tick[tick]["byTargetRole"][role] += 1
        by_tick[tick]["byTargetCategory"][category] += 1
        by_tick[tick]["selectedRejected"][status] += 1
        totals["byTargetType"][target_type] += 1
        totals["byTargetRole"][role] += 1
        totals["byTargetCategory"][category] += 1
        totals["selectedRejected"][status] += 1
        for reason in reasons:
            by_tick[tick]["rejectionReasons"][str(reason)] += 1
            totals["rejectionReasons"][str(reason)] += 1

    return {
        "present": bool(records),
        "total": len(records),
        "byTick": {
            tick: {
                "total": value["total"],
                "byTargetType": dict(value["byTargetType"]),
                "byTargetRole": dict(value["byTargetRole"]),
                "byTargetCategory": dict(value["byTargetCategory"]),
                "selectedRejected": dict(value["selectedRejected"]),
                "rejectionReasons": dict(value["rejectionReasons"]),
            }
            for tick, value in by_tick.items()
        },
        "counts": {key: dict(value) for key, value in totals.items()},
        "candidateTickRange": sorted_tick_range(all_ticks),
    }


def summarize_scenario(records: list[dict], all_ticks: set[int], candidate_summary: dict) -> dict:
    by_tick = {}
    total_selected = 0
    total_records = 0
    for record in records:
        tick_id = tick_id_for_scenario(record)
        selected = record.get("selectedCandidates") if isinstance(record.get("selectedCandidates"), list) else []
        context = nested_dict(record, "context").get("targets", [])
        if not isinstance(context, list):
            context = []
        count = len(selected)
        total_selected += count
        total_records += 1
        candidate_count = candidate_summary.get("byTick", {}).get(str(tick_id), {}).get("total", 0)
        by_tick[str(tick_id)] = {
            "records": 1,
            "selectedCandidates": count,
            "contextTargets": len(context),
            "candidateCountForTick": candidate_count,
            "candidateToScenarioLoss": candidate_count - count if candidate_count else None,
        }
    return {
        "present": bool(records),
        "recordCount": total_records,
        "selectedCandidateCount": total_selected,
        "byTick": by_tick,
        "scenarioTickRange": sorted_tick_range(all_ticks),
    }


def build_loss_ledger(selected_ticks: list[int], raw_summary: dict, world_summary: dict, candidate_summary: dict, scenario_summary: dict) -> dict:
    ledger = []
    largest = None

    for tick in selected_ticks:
        key = str(tick)
        raw_tick = raw_summary.get("byTick", {}).get(key, {})
        world_tick = world_summary.get("byTick", {}).get(key, {})
        candidate_tick = candidate_summary.get("byTick", {}).get(key, {})
        scenario_tick = scenario_summary.get("byTick", {}).get(key, {})
        raw_scene = int(raw_tick.get("sceneObjects") or 0)
        raw_visible_refs = int(raw_tick.get("visibleSceneObjectRefs") or 0)
        raw_npc = int(raw_tick.get("npcs") or 0)
        raw_ground = int(raw_tick.get("groundItems") or 0)
        world_types = world_tick.get("byTargetType") or {}
        world_scene = int(world_types.get("sceneObject") or 0)
        world_npc = int(world_types.get("npc") or 0)
        world_ground = int(world_types.get("groundItem") or 0)
        world_total = int(world_tick.get("total") or 0)
        candidate_total = int(candidate_tick.get("total") or 0)
        scenario_selected = int(scenario_tick.get("selectedCandidates") or 0)
        edges = []

        def edge(name, before, after):
            if before is None or after is None:
                return
            loss = before - after
            edges.append({"edge": name, "before": before, "after": after, "loss": loss})

        if raw_scene:
            edge("raw.sceneObjects -> worldTargets.sceneObject", raw_scene, world_scene)
        elif raw_visible_refs:
            edge("raw.visibleSceneObjectRefs -> worldTargets.sceneObject", raw_visible_refs, world_scene)
        else:
            edge("raw.sceneObjects -> worldTargets.sceneObject", raw_scene, world_scene)
        edge("raw.npcs -> worldTargets.npc", raw_npc, world_npc)
        edge("raw.groundItems -> worldTargets.groundItem", raw_ground, world_ground)
        edge("worldTargets.total -> targetCandidates.total", world_total, candidate_total)
        edge("targetCandidates.total -> scenario.selected", candidate_total, scenario_selected if scenario_summary.get("present") else None)
        best = max(edges, key=lambda item: item["loss"], default=None)
        if best and (largest is None or best["loss"] > largest["loss"]):
            largest = {"tick": tick, **best}
        ledger.append(
            {
                "tickId": tick,
                "raw": {"sceneObjects": raw_scene, "visibleSceneObjectRefs": raw_visible_refs, "npcs": raw_npc, "groundItems": raw_ground},
                "worldTargets": {"sceneObject": world_scene, "npc": world_npc, "groundItem": world_ground, "total": world_total},
                "targetCandidates": {"total": candidate_total},
                "scenario": {"selected": scenario_selected if scenario_summary.get("present") else None},
                "edges": edges,
                "largestLossEdge": best,
                "likelyCauses": likely_causes_for_edge(best),
            }
        )

    return {
        "bestEffort": True,
        "comparisonNotes": [
            "Stage comparisons are best-effort because derived files may have been built with filters, limits, or different tick ranges.",
            "raw sceneObjects are compared to world target sceneObject records; world targets are compared to already-selected target candidate records.",
        ],
        "byTick": ledger,
        "largestComparableLoss": largest,
    }


def likely_causes_for_edge(edge: dict | None) -> list[str]:
    if not edge:
        return []
    name = edge.get("edge", "")
    if name.startswith("raw."):
        return [
            "Java capture cap/radius/layer skip",
            "Python builder filter such as --only-on-screen",
            "missing projection/geometry fields",
            "visible scenery may be non-TileObject background/model/paint",
        ]
    if name.startswith("worldTargets"):
        return [
            "candidate filter/limit",
            "candidate profile filter",
            "UI-blocked exclusion",
            "semantic role/category/name filter",
            "geometry or onScreen requirement",
        ]
    if name.startswith("targetCandidates"):
        return ["scenario rules", "scenario minScore", "scenario de-duplication", "scenario limitPerTick"]
    return ["inspector display filter or source mismatch"]


def record_traits(record: dict, raw_scene: bool = False) -> dict:
    geometry = geometry_for(record) if not raw_scene else record
    name = target_name_for(record) if not raw_scene else str(record.get("objectName") or record.get("name") or "")
    world = world_location(record)
    scene = scene_location(record)
    local = local_location(record)
    projection_missing = not any(geometry.get(field) is not None for field in GEOMETRY_FIELDS if field not in {"onScreen", "geometryAvailable"})
    return {
        "kind": str(record.get("kind") or target_for(record).get("kind") or "unknown"),
        "geometryAvailable": bool_key(geometry.get("geometryAvailable")),
        "onScreen": bool_key(geometry.get("onScreen")),
        "nameMissing": not bool(name),
        "actionsMissing": not bool(record.get("actions") or target_for(record).get("actions")),
        "projectionMissing": projection_missing,
        "coordinateMissing": not bool(world or scene or local),
    }


def summarize_missing_traits(records: list[dict], raw_scene: bool = False) -> dict:
    counters = {
        "kind": Counter(),
        "geometryAvailable": Counter(),
        "onScreen": Counter(),
        "nameMissing": Counter(),
        "actionsMissing": Counter(),
        "projectionMissing": Counter(),
        "coordinateMissing": Counter(),
    }
    for record in records:
        traits = record_traits(record, raw_scene=raw_scene)
        for key, value in traits.items():
            counters[key][str(value)] += 1
    return {key: dict(counter) for key, counter in counters.items()}


def identity_matching(raw_ticks: list[dict], world_records: list[dict], candidate_records: list[dict], selected_ticks: list[int]) -> dict:
    raw_scene_by_tick = defaultdict(list)
    world_by_tick = defaultdict(list)
    candidates_by_tick = defaultdict(list)

    for tick in raw_ticks:
        tick_id = tick_id_for_record(tick)
        scene_records = list(tick.get("sceneObjects") or []) + list(tick.get("visibleSceneObjectRefs") or [])
        for obj in scene_records:
            if isinstance(obj, dict):
                raw_scene_by_tick[tick_id].append(obj)

    for record in world_records:
        tick_id = tick_id_for_record(record)
        if target_type_for(record) in SCENE_OBJECT_TYPES:
            world_by_tick[tick_id].append(record)

    for record in candidate_records:
        tick_id = tick_id_for_candidate(record)
        candidates_by_tick[tick_id].append(record)

    by_tick = {}
    all_raw_missing = []
    all_world_missing = []

    for tick in selected_ticks:
        raw_records = raw_scene_by_tick.get(tick, [])
        world_records_for_tick = world_by_tick.get(tick, [])
        candidate_records_for_tick = candidates_by_tick.get(tick, [])
        raw_ids = {object_identity(record, "raw_scene") for record in raw_records}
        world_ids = {object_identity(record, "world") for record in world_records_for_tick}
        candidate_ids = {object_identity(record, "candidate") for record in candidate_records_for_tick}
        raw_missing_ids = raw_ids - world_ids
        world_missing_ids = world_ids - candidate_ids
        raw_missing_records = [record for record in raw_records if object_identity(record, "raw_scene") in raw_missing_ids]
        world_missing_records = [record for record in world_records_for_tick if object_identity(record, "world") in world_missing_ids]
        all_raw_missing.extend(raw_missing_records)
        all_world_missing.extend(world_missing_records)
        by_tick[str(tick)] = {
            "rawSceneObjectIdentityCount": len(raw_ids),
            "worldSceneObjectIdentityCount": len(world_ids),
            "candidateIdentityCount": len(candidate_ids),
            "rawIdentitiesMissingFromWorldTargets": len(raw_missing_ids),
            "worldTargetIdentitiesMissingFromCandidates": len(world_missing_ids),
            "sampleRawMissingIdentity": identity_text(next(iter(raw_missing_ids))) if raw_missing_ids else None,
            "sampleWorldMissingIdentity": identity_text(next(iter(world_missing_ids))) if world_missing_ids else None,
        }

    return {
        "bestEffort": True,
        "byTick": by_tick,
        "rawIdentitiesMissingTraits": summarize_missing_traits(all_raw_missing, raw_scene=True),
        "worldIdentitiesMissingFromCandidatesTraits": summarize_missing_traits(all_world_missing),
    }


def target_distance_to(world: dict | None, x: int, y: int) -> int | None:
    if not isinstance(world, dict) or world.get("x") is None or world.get("y") is None:
        return None
    return max(abs(int(world["x"]) - x), abs(int(world["y"]) - y))


def trace_matches_record(record: dict, args, raw_kind: str = "") -> tuple[bool, dict]:
    reasons = {}
    matched_any_filter = False
    passed = True

    if args.object_id is not None:
        matched_any_filter = True
        ids = target_id_values(record)
        if raw_kind:
            for key in ("id", "rawId", "objectId", "npcId", "itemId"):
                if record.get(key) not in (None, ""):
                    ids.append(str(record.get(key)))
        if str(args.object_id) not in ids:
            passed = False
        else:
            reasons["objectId"] = str(args.object_id)

    if args.object_hash is not None:
        matched_any_filter = True
        hashes = object_hash_values(record)
        if raw_kind:
            for key in ("hash", "objectHash", "rawHash"):
                if record.get(key) not in (None, ""):
                    hashes.append(str(record.get(key)))
        if str(args.object_hash) not in hashes:
            passed = False
        else:
            reasons["objectHash"] = str(args.object_hash)

    if args.near is not None:
        matched_any_filter = True
        x, y, radius = args.near
        world = world_location(record)
        distance = target_distance_to(world, x, y)
        if distance is None or distance > radius:
            passed = False
        else:
            reasons["near"] = {"worldX": x, "worldY": y, "radius": radius, "distance": distance}

    return passed if matched_any_filter else False, reasons


def trace_stage_records(raw_ticks: list[dict], world_records: list[dict], candidate_records: list[dict], scenario_records: list[dict], args) -> dict:
    if not (args.object_id is not None or args.object_hash is not None or args.near is not None):
        return {"enabled": False}

    stages = {
        "raw": defaultdict(list),
        "worldTargets": defaultdict(list),
        "targetCandidates": defaultdict(list),
        "scenario": defaultdict(list),
    }

    for tick in raw_ticks:
        tick_id = tick_id_for_record(tick)
        for collection_name, raw_kind in (
            ("sceneObjects", "raw_scene"),
            ("visibleSceneObjectRefs", "raw_scene"),
            ("npcs", "raw_npc"),
            ("groundItems", "raw_ground"),
        ):
            for record in tick.get(collection_name) or []:
                if isinstance(record, dict):
                    matched, reasons = trace_matches_record(record, args, raw_kind=raw_kind)
                    if matched:
                        stages["raw"][tick_id].append(trace_summary_record(record, reasons, raw_kind=raw_kind))

    for record in world_records:
        matched, reasons = trace_matches_record(record, args)
        if matched:
            stages["worldTargets"][tick_id_for_record(record)].append(trace_summary_record(record, reasons))

    for record in candidate_records:
        matched, reasons = trace_matches_record(record, args)
        if matched:
            stages["targetCandidates"][tick_id_for_candidate(record)].append(trace_summary_record(record, reasons))

    for record in scenario_records:
        tick_id = tick_id_for_scenario(record)
        for candidate in record.get("selectedCandidates") or []:
            if isinstance(candidate, dict):
                matched, reasons = trace_matches_record(candidate, args)
                if matched:
                    stages["scenario"][tick_id].append(trace_summary_record(candidate, reasons))

    counts_by_tick = {}
    all_ticks = sorted({tick for stage in stages.values() for tick in stage if isinstance(tick, int)})
    first_absent = {}
    order = ("raw", "worldTargets", "targetCandidates", "scenario")
    for tick in all_ticks:
        counts = {stage: len(stages[stage].get(tick, [])) for stage in order}
        absent = None
        seen_prior = False
        for stage in order:
            if counts[stage]:
                seen_prior = True
            elif seen_prior and absent is None:
                absent = stage
        counts_by_tick[str(tick)] = counts
        if absent:
            first_absent[str(tick)] = absent

    return {
        "enabled": True,
        "filters": {
            "objectId": args.object_id,
            "objectHash": args.object_hash,
            "near": {"worldX": args.near[0], "worldY": args.near[1], "radius": args.near[2]} if args.near else None,
        },
        "countsByTick": counts_by_tick,
        "firstAbsentStageByTick": first_absent,
        "samples": {
            stage: {str(tick): values[:5] for tick, values in tick_map.items() if values}
            for stage, tick_map in stages.items()
        },
    }


def trace_summary_record(record: dict, reasons: dict, raw_kind: str = "") -> dict:
    target = target_for(record)
    return {
        "targetType": target_type_for(record) if not raw_kind else raw_kind.replace("raw_", ""),
        "idValues": target_id_values(record) or [str(record.get("id"))] if record.get("id") is not None else target_id_values(record),
        "name": target_name_for(record) or str(record.get("objectName") or record.get("npcName") or record.get("itemName") or ""),
        "kind": target.get("kind") or record.get("kind"),
        "world": world_location(record),
        "scene": scene_location(record),
        "local": local_location(record),
        "geometryAvailable": geometry_for(record).get("geometryAvailable") if not raw_kind else record.get("geometryAvailable"),
        "onScreen": geometry_for(record).get("onScreen") if not raw_kind else record.get("onScreen"),
        "match": reasons,
    }


def point_from_bounds(bounds) -> tuple[float, float] | None:
    if not isinstance(bounds, dict):
        return None
    x = safe_float(bounds.get("x"))
    y = safe_float(bounds.get("y"))
    w = safe_float(bounds.get("w"))
    h = safe_float(bounds.get("h"))
    if x is None or y is None or w is None or h is None:
        return None
    return (x + w / 2.0, y + h / 2.0)


def point_from_polygon(points) -> tuple[float, float] | None:
    if not isinstance(points, list):
        return None
    xs = []
    ys = []
    for point in points:
        if isinstance(point, list) and len(point) >= 2:
            x = safe_float(point[0])
            y = safe_float(point[1])
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def best_canvas_point(record: dict, raw: bool = False) -> tuple[float, float] | None:
    geometry = record if raw else geometry_for(record)
    for key in ("canvasPoint", "canvasLocation", "canvasCenter", "aimPoint"):
        point = geometry.get(key)
        if isinstance(point, dict):
            x = safe_float(point.get("x"))
            y = safe_float(point.get("y"))
            if x is not None and y is not None:
                return (x, y)
    for key in ("clickboxBounds", "convexHullBounds", "pixelBox", "aimBounds"):
        point = point_from_bounds(geometry.get(key))
        if point:
            return point
    for key in ("clickboxPolygon", "convexHullPolygon", "tilePolygon", "canvasTilePolygon", "preferredAimGeometry"):
        point = point_from_polygon(geometry.get(key))
        if point:
            return point
    return None


def canvas_dims_for(record: dict, raw: bool = False) -> tuple[float | None, float | None]:
    if raw:
        return safe_float(record.get("canvasWidth")), safe_float(record.get("canvasHeight"))
    canvas = nested_dict(record, "canvas")
    frame = frame_for(record)
    return safe_float(canvas.get("width")) or safe_float(frame.get("width")), safe_float(canvas.get("height")) or safe_float(frame.get("height"))


def sector_name(point: tuple[float, float], width: float, height: float) -> str:
    x, y = point
    col = "left" if x < width / 3 else "center" if x < (2 * width) / 3 else "right"
    row = "top" if y < height / 3 else "middle" if y < (2 * height) / 3 else "bottom"
    return f"{row}-{col}"


def sector_summary_for_records(records: list[dict], raw: bool = False) -> dict:
    by_tick_points = defaultdict(list)
    explicit_dims = {}
    for record in records:
        tick_id = tick_id_for_record(record) if raw else tick_id_for_candidate(record) or tick_id_for_record(record)
        point = best_canvas_point(record, raw=raw)
        if not point:
            continue
        by_tick_points[tick_id].append((point, record))
        width, height = canvas_dims_for(record, raw=raw)
        if width and height:
            explicit_dims[tick_id] = (width, height)

    by_tick = {}
    for tick_id, values in by_tick_points.items():
        dims = explicit_dims.get(tick_id)
        inferred = False
        origin_x = 0.0
        origin_y = 0.0
        if not dims:
            xs = [point[0] for point, _record in values]
            ys = [point[1] for point, _record in values]
            origin_x = min(xs) if xs else 0.0
            origin_y = min(ys) if ys else 0.0
            width = max(xs) - origin_x if xs else 1
            height = max(ys) - origin_y if ys else 1
            dims = (max(1.0, width), max(1.0, height))
            inferred = True
        counts = Counter()
        for point, _record in values:
            local_point = (point[0] - origin_x, point[1] - origin_y) if inferred else point
            counts[sector_name(local_point, dims[0], dims[1])] += 1
        low = [sector for sector in all_sectors() if counts.get(sector, 0) == 0]
        by_tick[str(tick_id)] = {
            "counts": {sector: counts.get(sector, 0) for sector in all_sectors()},
            "dimensions": {
                "width": dims[0],
                "height": dims[1],
                "inferred": inferred,
                "originX": origin_x if inferred else 0.0,
                "originY": origin_y if inferred else 0.0,
                "pointCount": len(values),
                "inferenceReliable": bool(not inferred or len(values) >= 2),
            },
            "zeroSectors": low,
        }
    return by_tick


def all_sectors() -> list[str]:
    return [
        "top-left",
        "top-center",
        "top-right",
        "middle-left",
        "middle-center",
        "middle-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ]


def viewport_sector_coverage(raw_ticks: list[dict], world_records: list[dict], candidate_records: list[dict]) -> dict:
    raw_scene_records = []
    for tick in raw_ticks:
        tick_id = tick_id_for_record(tick)
        scene_records = list(tick.get("sceneObjects") or []) + list(tick.get("visibleSceneObjectRefs") or [])
        for record in scene_records:
            if isinstance(record, dict):
                copy = dict(record)
                copy["tickId"] = tick_id
                copy["canvasWidth"] = tick.get("canvasWidth")
                copy["canvasHeight"] = tick.get("canvasHeight")
                raw_scene_records.append(copy)
    raw = sector_summary_for_records(raw_scene_records, raw=True)
    world = sector_summary_for_records(world_records, raw=False)
    candidates = sector_summary_for_records(candidate_records, raw=False)
    warnings = []
    for stage, data in (("rawSceneObjects", raw), ("worldTargets", world), ("candidates", candidates)):
        for tick, summary in data.items():
            zero = summary.get("zeroSectors") or []
            if len(zero) >= 3:
                warnings.append(f"{stage} tick {tick} has {len(zero)} empty viewport sectors")
    return {"rawSceneObjects": raw, "worldTargets": world, "candidates": candidates, "warnings": warnings}


def nearest_tick(target: int, ticks: set[int]) -> dict:
    if not ticks:
        return {"tick": None, "delta": None}
    nearest = min(ticks, key=lambda tick: (abs(tick - target), tick))
    return {"tick": nearest, "delta": nearest - target}


def frame_alignment(selected_ticks: list[int], raw_tick_ids: set[int], world_ticks: set[int], candidate_ticks: set[int], scenario_ticks: set[int], frame_ticks: set[int], scenario_enabled: bool) -> dict:
    by_tick = {}
    warnings = []
    for tick in selected_ticks:
        row = {
            "rawTickExists": tick in raw_tick_ids,
            "worldTargetExactTickExists": tick in world_ticks,
            "nearestWorldTargetTick": nearest_tick(tick, world_ticks),
            "retainedFrameTickExactMatch": tick in frame_ticks,
            "nearestRetainedFrameTick": nearest_tick(tick, frame_ticks),
            "candidateExactTickExists": tick in candidate_ticks,
            "nearestCandidateTick": nearest_tick(tick, candidate_ticks),
        }
        if scenario_enabled:
            row["scenarioExactTickExists"] = tick in scenario_ticks
            row["nearestScenarioTick"] = nearest_tick(tick, scenario_ticks)
        if row["nearestRetainedFrameTick"]["delta"] in {-1, 1}:
            warnings.append(f"tick {tick} is one tick away from nearest retained frame {row['nearestRetainedFrameTick']['tick']}")
        if row["rawTickExists"] and not row["worldTargetExactTickExists"]:
            warnings.append(f"tick {tick} has raw tick data but no exact world target tick")
        by_tick[str(tick)] = row
    return {"byTick": by_tick, "warnings": warnings}


def infer_project_root(args) -> Path | None:
    if args.project_root:
        return Path(args.project_root).expanduser().resolve()
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src" / "main" / "java").exists() or (parent / "telemetry-viewer").exists():
            return parent
    return None


def source_audit(project_root: Path | None) -> dict:
    if not project_root or not project_root.exists():
        return {"available": False, "projectRoot": str(project_root) if project_root else None, "findings": [], "summary": {}}

    likely_files = []
    direct = [
        project_root / "src" / "main" / "java" / "com" / "osrstelemetry" / "TelemetryPlugin.java",
        project_root / "telemetry-viewer" / "build_world_target_geometry.py",
        project_root / "telemetry-viewer" / "select_target_candidates.py",
        project_root / "telemetry-viewer" / "build_scenario_dataset.py",
        project_root / "telemetry-viewer" / "target_geometry_inspector.py",
        project_root / "telemetry-viewer" / "telemetry_paths.py",
    ]
    missing_likely_files = [str(path) for path in direct if not path.exists()]
    likely_files.extend(path for path in direct if path.exists())
    findings = []
    seen = set()

    for path in likely_files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for line_number, line in enumerate(file, start=1):
                    for term in SOURCE_AUDIT_TERMS:
                        if term.lower() in line.lower():
                            key = (str(path), line_number, term)
                            if key in seen:
                                continue
                            seen.add(key)
                            findings.append(
                                {
                                    "file": str(path),
                                    "line": line_number,
                                    "term": term,
                                    "text": line.strip()[:240],
                                }
                            )
        except OSError:
            continue

    terms = Counter(finding["term"] for finding in findings)
    text_blob = "\n".join(finding["text"] for finding in findings).lower()
    summary = {
        "possibleCaps": [
            finding
            for finding in findings
            if finding["term"] in {
                "MAX_SCENE_OBJECTS",
                "SCENE_OBJECT_LIMIT",
                "MAX_OBJECTS",
                "configuredMaxSceneObjects",
                "maxSceneObjects",
                "sceneObjectCapHit",
            }
        ],
        "possibleRadiusLimitedScans": [
            finding
            for finding in findings
            if finding["term"].lower() in {"radius", "scanradius", "fullcurrentplanescan"}
            and (
                finding["file"].endswith(".java")
                or "SCENE_CAPTURE_RADIUS" in finding["text"]
                or "scanRadius" in finding["text"]
                or "fullCurrentPlaneScan" in finding["text"]
            )
        ],
        "capturedObjectLayerTerms": sorted(term for term in ("getGameObjects", "getWallObject", "getDecorativeObject", "getGroundObject") if term.lower() in text_blob),
        "tilePaintModelTermsFound": sorted(term for term in ("getSceneTilePaint", "getSceneTileModel") if term.lower() in text_blob),
        "geometryFilterHints": [finding for finding in findings if finding["term"] in {"geometryAvailable", "onScreen"}][:50],
        "inspectorDrawHints": [finding for finding in findings if finding["term"] in {"maxDraw", "maxTargets"}],
        "termCounts": dict(terms),
    }
    if not summary["tilePaintModelTermsFound"]:
        summary["objectLayersApparentlyNotCaptured"] = ["getSceneTilePaint", "getSceneTileModel"]
    return {
        "available": True,
        "projectRoot": str(project_root),
        "filesSearched": [str(path) for path in likely_files],
        "missingLikelyFiles": missing_likely_files,
        "findings": findings[:300],
        "summary": summary,
    }


def build_session_summary(session: Path, selection_mode: str, tick_files: list[Path], paths: dict, selected_ticks: list[int], tick_selection_mode: str, malformed: dict) -> dict:
    return {
        "sessionPath": str(session),
        "selectionMode": selection_mode,
        "manifestPresent": paths["manifest"].exists(),
        "rawTickFilesFound": [str(path) for path in tick_files],
        "worldTargetsPresent": paths["worldTargets"].exists(),
        "worldGeometryIndexPresent": paths["worldGeometryIndex"].exists(),
        "sceneStaticIndexPresent": paths["sceneStaticIndex"].exists(),
        "uiTargetsPresent": paths["uiTargets"].exists(),
        "targetCandidatesPresent": paths["targetCandidates"].exists(),
        "targetCandidatesIndexPresent": paths["targetCandidatesIndex"].exists(),
        "scenarioFilePresent": bool(paths["scenario"] and paths["scenario"].exists()),
        "selectedTicks": selected_ticks,
        "tickSelectionMode": tick_selection_mode,
        "malformedRecordCountsByFile": malformed,
    }


def build_report(args) -> dict:
    session, session_selection_mode = resolve_session(args)
    if session is None:
        return {
            "reportSchema": REPORT_SCHEMA,
            "error": "No telemetry session found.",
            "session": None,
            "selectedTicks": [],
        }

    paths = output_paths(session, args.scenario)
    tick_files = list_tick_files(session)
    raw_tick_ids, raw_scan_malformed, raw_total_count = scan_raw_tick_ids(tick_files)
    fallback_ticks = set()

    for path_name, getter in (("worldTargets", tick_id_for_record), ("targetCandidates", tick_id_for_candidate)):
        path = paths[path_name]
        if path.exists():
            _records, ticks, _malformed, _total = read_jsonl_filtered(path, None, getter, collect_records=False)
            fallback_ticks.update(ticks)

    if paths["scenario"] and paths["scenario"].exists():
        _records, ticks, _malformed, _total = read_jsonl_filtered(paths["scenario"], None, tick_id_for_scenario, collect_records=False)
        fallback_ticks.update(ticks)

    selected_ticks, tick_selection_mode = select_ticks(raw_tick_ids, args, fallback_ticks)
    selected_tick_set = set(selected_ticks)
    raw_ticks, raw_read_malformed = read_selected_raw_ticks(tick_files, selected_tick_set)
    malformed = dict(raw_scan_malformed)
    for path, count in raw_read_malformed.items():
        malformed[path] = max(malformed.get(path, 0), count)

    world_records, world_all_ticks, world_malformed, world_total_read = read_jsonl_filtered(paths["worldTargets"], selected_tick_set, tick_id_for_record)
    ui_records, ui_all_ticks, ui_malformed, ui_total_read = read_jsonl_filtered(paths["uiTargets"], selected_tick_set, tick_id_for_record)
    candidate_records, candidate_all_ticks, candidate_malformed, candidate_total_read = read_jsonl_filtered(paths["targetCandidates"], selected_tick_set, tick_id_for_candidate)
    scenario_records = []
    scenario_all_ticks = set()
    scenario_malformed = 0
    scenario_total_read = 0
    if paths["scenario"]:
        scenario_records, scenario_all_ticks, scenario_malformed, scenario_total_read = read_jsonl_filtered(paths["scenario"], selected_tick_set, tick_id_for_scenario)

    for path, count in (
        (paths["worldTargets"], world_malformed),
        (paths["uiTargets"], ui_malformed),
        (paths["targetCandidates"], candidate_malformed),
        (paths["scenario"], scenario_malformed if paths["scenario"] else 0),
    ):
        if path and count:
            malformed[str(path)] = count

    frame_ticks = retained_frame_ticks(session)
    manifest = safe_read_json(paths["manifest"]) if paths["manifest"].exists() else {}
    if manifest is None:
        manifest = {}
    world_index = safe_read_json(paths["worldGeometryIndex"]) if paths["worldGeometryIndex"].exists() else {}
    if not isinstance(world_index, dict):
        world_index = {}
    candidate_index = safe_read_json(paths["targetCandidatesIndex"]) if paths["targetCandidatesIndex"].exists() else {}
    if not isinstance(candidate_index, dict):
        candidate_index = {}
    raw_summary = summarize_raw_ticks(raw_ticks)
    world_summary = summarize_world_targets(world_records, world_all_ticks, selected_tick_set, frame_ticks)
    candidate_summary = summarize_candidates(candidate_records, candidate_all_ticks)
    candidate_summary["index"] = candidate_index
    scenario_summary = summarize_scenario(scenario_records, scenario_all_ticks, candidate_summary)
    loss = build_loss_ledger(selected_ticks, raw_summary, world_summary, candidate_summary, scenario_summary)
    identity = identity_matching(raw_ticks, world_records, candidate_records, selected_ticks)
    trace = trace_stage_records(raw_ticks, world_records, candidate_records, scenario_records, args)
    sectors = viewport_sector_coverage(raw_ticks, world_records, candidate_records)
    alignment = frame_alignment(
        selected_ticks,
        set(raw_tick_ids),
        world_all_ticks,
        candidate_all_ticks,
        scenario_all_ticks,
        frame_ticks,
        bool(args.scenario),
    )
    audit = source_audit(infer_project_root(args))
    conclusion = conclude(report_pieces={
        "selectedTicks": selected_ticks,
        "loss": loss,
        "rawSummary": raw_summary,
        "worldSummary": world_summary,
        "candidateSummary": candidate_summary,
        "scenarioSummary": scenario_summary,
        "sourceAudit": audit,
        "frameAlignment": alignment,
        "candidateIndex": candidate_index,
    })

    files = {
        "manifest": str(paths["manifest"]),
        "rawTickFiles": [str(path) for path in tick_files],
        "worldTargets": str(paths["worldTargets"]),
        "worldGeometryIndex": str(paths["worldGeometryIndex"]),
        "sceneStaticIndex": str(paths["sceneStaticIndex"]),
        "uiTargets": str(paths["uiTargets"]),
        "targetCandidates": str(paths["targetCandidates"]),
        "targetCandidatesIndex": str(paths["targetCandidatesIndex"]),
        "scenario": str(paths["scenario"]) if paths["scenario"] else None,
    }
    session_summary = build_session_summary(session, session_selection_mode, tick_files, paths, selected_ticks, tick_selection_mode, malformed)

    return {
        "reportSchema": REPORT_SCHEMA,
        "session": session_summary,
        "selectedTicks": selected_ticks,
        "files": {
            **files,
            "recordCountsRead": {
                "rawTicksTotalAvailable": raw_total_count,
                "rawTicksSelected": len(raw_ticks),
                "worldTargetsSelected": len(world_records),
                "worldTargetsTotalRead": world_total_read,
                "uiTargetsSelected": len(ui_records),
                "uiTargetsTotalRead": ui_total_read,
                "targetCandidatesSelected": len(candidate_records),
                "targetCandidatesTotalRead": candidate_total_read,
                "scenarioRecordsSelected": len(scenario_records),
                "scenarioRecordsTotalRead": scenario_total_read,
            },
        },
        "malformedCounts": malformed,
        "manifest": manifest if isinstance(manifest, dict) else {},
        "worldGeometryIndex": world_index,
        "rawCoverage": raw_summary,
        "worldTargetCoverage": world_summary,
        "candidateCoverage": candidate_summary,
        "scenarioCoverage": scenario_summary,
        "lossLedger": loss,
        "identityMatching": identity,
        "trace": trace,
        "viewportSectors": sectors,
        "frameAlignment": alignment,
        "sourceAudit": audit,
        "conclusion": conclusion,
    }


def conclude(report_pieces: dict) -> dict:
    selected_ticks = report_pieces.get("selectedTicks") or []
    loss = report_pieces["loss"].get("largestComparableLoss")
    source_audit_report = report_pieces["sourceAudit"]
    raw_summary = report_pieces["rawSummary"]
    world_summary = report_pieces["worldSummary"]
    candidate_summary = report_pieces.get("candidateSummary") or {}
    candidate_index = report_pieces.get("candidateIndex") or candidate_summary.get("index") or {}
    frame_alignment_report = report_pieces["frameAlignment"]
    evidence = []
    likely = []
    most_likely_stage = "unknown"

    if loss:
        most_likely_stage = loss.get("edge")
        evidence.append(f"largest comparable loss: tick {loss.get('tick')} {loss.get('edge')} loss={loss.get('loss')}")
        likely.extend(likely_causes_for_edge(loss))

    scene_capture = raw_summary.get("sceneCaptureSummary") or {}
    scene_capture_totals = scene_capture.get("totals") or {}
    skipped_by_cap = int(scene_capture_totals.get("sceneObjectsSkippedByCap") or 0)
    cap_hit_ticks = int(scene_capture.get("sceneObjectCapHitTickCount") or 0)
    mode_counts = scene_capture.get("modeCounts") or {}
    primary_mode = max(mode_counts, key=mode_counts.get) if mode_counts else None
    raw_scene_counts = [int(value.get("sceneObjects") or 0) for value in raw_summary.get("byTick", {}).values()]
    fixed_raw_count = raw_scene_counts and max(raw_scene_counts) == min(raw_scene_counts) and max(raw_scene_counts) >= 250
    if fixed_raw_count and not scene_capture.get("present"):
        evidence.append(f"raw scene object count is fixed at {max(raw_scene_counts)} on selected ticks")
        likely.append("uncaptured due to Java scene object cap")
    elif fixed_raw_count and (cap_hit_ticks or skipped_by_cap):
        evidence.append(f"raw scene object count is fixed at {max(raw_scene_counts)} on selected ticks")

    if scene_capture.get("present"):
        if cap_hit_ticks:
            evidence.append(f"sceneCaptureSummary reports scene object cap hit on {cap_hit_ticks} selected tick(s)")
            likely.append("uncaptured due to Java scene object cap")
        if skipped_by_cap:
            seen = int(scene_capture_totals.get("sceneObjectsSeen") or 0)
            captured = int(scene_capture_totals.get("sceneObjectsCaptured") or 0)
            evidence.append(f"sceneCaptureSummary saw {seen} scene objects, captured {captured}, skipped {skipped_by_cap} by cap")
            likely.append("uncaptured due to Java scene object cap")
        if cap_hit_ticks == 0 and skipped_by_cap == 0:
            seen = int(scene_capture_totals.get("sceneObjectsSeen") or 0)
            captured = int(scene_capture_totals.get("sceneObjectsCaptured") or 0)
            if seen == captured:
                evidence.append("sceneCaptureSummary shows all seen scene objects were captured for selected ticks")

    if world_summary.get("fallbackSceneObjectCount"):
        evidence.append(f"fallback scene objects in world targets: {world_summary.get('fallbackSceneObjectCount')}")
        likely.append("filtered by semantic labels/categories until overrides/classification catch up")

    if frame_alignment_report.get("warnings"):
        likely.append("outside retained frame alignment")

    candidate_limit = candidate_index.get("limit")
    candidate_discarded = int(candidate_index.get("discardedByLimit") or 0)
    candidate_dedupe_enabled = bool(candidate_index.get("dedupeEnabled")) if candidate_index else None
    candidate_duplicates_removed = int(candidate_index.get("duplicatesRemoved") or 0)
    candidate_profile_id = candidate_index.get("profileId")
    candidates_after_profile = candidate_index.get("candidatesAfterProfileFilters")
    matching_before_filters = candidate_index.get("matchingTargetsBeforeFilters")
    excluded_ui_blocked = int(candidate_index.get("excludedUiBlockedCount") or 0)
    profile_filter_active = bool(candidate_profile_id)
    semantic_filter_active = bool(
        profile_filter_active
        or candidate_index.get("topRejectReasons")
        or excluded_ui_blocked
        or (
            isinstance(matching_before_filters, int)
            and isinstance(candidates_after_profile, int)
            and candidates_after_profile < matching_before_filters
        )
    )

    if candidate_discarded:
        evidence.append(f"target_candidates_index discarded {candidate_discarded} candidates by limit")
        likely.append("candidate filter/limit")

    if candidate_duplicates_removed:
        evidence.append(f"target_candidates_index removed {candidate_duplicates_removed} duplicate candidates before limit")

    if candidate_profile_id:
        evidence.append(f"target_candidates_index used profile {candidate_profile_id}")
        likely.append("candidate profile filter")

    if excluded_ui_blocked:
        evidence.append(f"target_candidates_index excluded {excluded_ui_blocked} UI-blocked candidates")
        likely.append("candidate UI-blocked filter")

    audit_summary = source_audit_report.get("summary", {}) if isinstance(source_audit_report, dict) else {}
    cap_lines = audit_summary.get("possibleCaps") or []
    radius_lines = audit_summary.get("possibleRadiusLimitedScans") or []
    if cap_lines:
        evidence.append("source audit found possible object caps")
    if radius_lines:
        evidence.append("source audit found possible radius-limited scene scan")
    source_has_cap_logic = bool(cap_lines or radius_lines)
    runtime_cap_hit = bool(cap_hit_ticks or skipped_by_cap)
    seen_total = int(scene_capture_totals.get("sceneObjectsSeen") or 0)
    captured_total = int(scene_capture_totals.get("sceneObjectsCaptured") or 0)
    current_capture_complete = bool(scene_capture.get("present") and not runtime_cap_hit and seen_total == captured_total)
    warnings = []
    if len(selected_ticks) == 1:
        warnings.append("Only one tick selected; collect/process more ticks before judging stability or performance.")

    categories = {
        "uncaptured": runtime_cap_hit or any("Java capture" in item for item in likely),
        "unprojected": world_summary.get("counts", {}).get("geometryAvailable", {}).get("false", 0) > 0,
        "filtered": any("filter" in item or "scenario" in item or "candidate" in item for item in likely),
        "outsideRetainedFrameAlignment": bool(frame_alignment_report.get("warnings")),
        "nonTileObjectSceneryBackground": "visible scenery may be non-TileObject background/model/paint" in likely
        or bool(audit_summary.get("objectLayersApparentlyNotCaptured")),
        "inspectorHidden": "inspector display filter or source mismatch" in likely,
    }
    recommended = "python telemetry-viewer\\diagnose_target_coverage.py --latest 25 --project-root ."
    if scene_capture.get("present") and cap_hit_ticks:
        if primary_mode in {None, "LOCAL_DEFAULT"}:
            recommended = "Set Scene Capture Mode to WIDE_DIAGNOSTIC for a short session, then rerun build_world_target_geometry.py and diagnose_target_coverage.py."
        elif primary_mode == "WIDE_DIAGNOSTIC":
            recommended = "Set Scene Capture Mode to FULL_CURRENT_PLANE_DIAGNOSTIC for a short session, or raise the diagnostic scene object cap later."
        elif primary_mode == "FULL_CURRENT_PLANE_DIAGNOSTIC":
            recommended = "FULL_CURRENT_PLANE_DIAGNOSTIC still hit the cap; consider a higher cap or a static scene index/dedup pass later."
    elif scene_capture.get("present"):
        recommended = "Scene capture cap is not currently hit; check non-TileObject scenery, projection availability, and inspector/world-vs-candidate source filters."
        if primary_mode == "STATIC_SCENE_INDEX_DIAGNOSTIC":
            recommended = "STATIC_SCENE_INDEX_DIAGNOSTIC is active; inspect sceneIndexSummary/projectionSummary and use world_targets or scene_static_index for broad QA before candidate filters."
    return {
        "mostLikelyLossStage": most_likely_stage,
        "strongestEvidence": evidence,
        "sourceHasCapSafetyLogic": source_has_cap_logic,
        "selectedTicksHitSceneObjectCap": bool(cap_hit_ticks),
        "selectedSceneObjectsSkippedByCap": skipped_by_cap,
        "currentCaptureModeLikelyCompleteForSelectedScan": current_capture_complete,
        "javaAppearsToCapOrSkipSceneObjects": runtime_cap_hit,
        "candidateLimitActive": bool(candidate_limit and candidate_discarded),
        "candidateLimit": candidate_limit,
        "candidateDiscardedByLimit": candidate_discarded,
        "candidateDedupeEnabled": candidate_dedupe_enabled,
        "candidateDuplicatesRemoved": candidate_duplicates_removed,
        "candidateProfileId": candidate_profile_id,
        "candidateProfileFilterActive": profile_filter_active,
        "candidateSemanticFilterActive": semantic_filter_active,
        "candidateExcludedUiBlocked": excluded_ui_blocked,
        "missingObjectsLikely": categories,
        "warnings": warnings,
        "recommendedNextDiagnosticCommand": recommended,
    }


def print_counts(title: str, counts: dict, indent: str = "  ") -> None:
    print(title)
    if not counts:
        print(f"{indent}(none)")
        return
    for key, value in counts.items():
        print(f"{indent}{key}: {value}")


def print_human(report: dict) -> None:
    if report.get("error"):
        print(report["error"])
        return

    session = report["session"]
    print("Target Coverage Diagnostic")
    print(f"schema: {report['reportSchema']}")
    print()
    print("A. Session summary")
    print(f"  session: {session['sessionPath']}")
    print(f"  selection mode: {session['selectionMode']}")
    print(f"  tick selection: {session['tickSelectionMode']}")
    print(f"  selected ticks: {format_tick_list(report['selectedTicks'])}")
    print(f"  manifest: {'yes' if session['manifestPresent'] else 'no'}")
    print(f"  raw tick files: {len(session['rawTickFilesFound'])}")
    print(f"  world_targets: {'yes' if session['worldTargetsPresent'] else 'no'}")
    print(f"  scene_static_index: {'yes' if session.get('sceneStaticIndexPresent') else 'no'}")
    print(f"  ui_targets: {'yes' if session['uiTargetsPresent'] else 'no'}")
    print(f"  target_candidates: {'yes' if session['targetCandidatesPresent'] else 'no'}")
    if report["files"].get("scenario"):
        print(f"  scenario file: {'yes' if session['scenarioFilePresent'] else 'no'} ({report['files']['scenario']})")
    print_counts("  malformed records by file:", report["malformedCounts"], indent="    ")
    print()

    raw = report["rawCoverage"]
    print("B. Raw tick coverage")
    print_counts("  raw totals:", raw.get("totals", {}), indent="    ")
    print_counts("  scene object kinds:", raw.get("sceneObjectsByKind", {}), indent="    ")
    print("  projection/location field presence:")
    for field, counts in raw.get("fieldPresence", {}).items():
        print(f"    {field}: present={counts.get('present', 0)} missing={counts.get('missing', 0)}")
    scene_capture = raw.get("sceneCaptureSummary") or {}
    print("  scene capture summary:")
    if not scene_capture.get("present"):
        print("    (not present in selected raw ticks)")
    else:
        print(f"    cap-hit ticks: {scene_capture.get('sceneObjectCapHitTickCount', 0)}")
        print(f"    ground-item cap-hit ticks: {scene_capture.get('groundItemCapHitTickCount', 0)}")
        print(f"    modes: {scene_capture.get('modeCounts') or {}}")
        print(f"    full current-plane scans: {scene_capture.get('fullCurrentPlaneScanCounts') or {}}")
        print(f"    configured radii: {scene_capture.get('configuredRadiusCounts') or {}}")
        print(f"    configured max scene objects: {scene_capture.get('configuredMaxSceneObjectCounts') or {}}")
        totals = scene_capture.get("totals") or {}
        averages = scene_capture.get("averagesPerTick") or {}
        maxima = scene_capture.get("maxPerTick") or {}
        print(
            "    totals: "
            f"seen={totals.get('sceneObjectsSeen', 0)} "
            f"captured={totals.get('sceneObjectsCaptured', 0)} "
            f"skippedByCap={totals.get('sceneObjectsSkippedByCap', 0)} "
            f"scannedTiles={totals.get('scannedTiles', 0)}"
        )
        print(
            "    average/tick: "
            f"seen={format_number(averages.get('sceneObjectsSeen'))} "
            f"captured={format_number(averages.get('sceneObjectsCaptured'))} "
            f"skippedByCap={format_number(averages.get('sceneObjectsSkippedByCap'))} "
            f"captureRatio={format_number(averages.get('captureRatio'))}"
        )
        print(
            "    max/tick: "
            f"seen={format_number(maxima.get('sceneObjectsSeen'))} "
            f"captured={format_number(maxima.get('sceneObjectsCaptured'))} "
            f"skippedByCap={format_number(maxima.get('sceneObjectsSkippedByCap'))}"
        )
        for tick, summary in list((scene_capture.get("byTick") or {}).items())[:5]:
            print(
                f"    tick {tick}: "
                f"mode={summary.get('sceneCaptureMode')} "
                f"capHit={summary.get('sceneObjectCapHit')} "
                f"fullPlane={summary.get('fullCurrentPlaneScan')} "
                f"radius={summary.get('configuredRadius', summary.get('scanRadius'))} "
                f"max={summary.get('configuredMaxSceneObjects', summary.get('maxSceneObjects'))} "
                f"bounds=({summary.get('scanMinSceneX')}..{summary.get('scanMaxSceneX')},"
                f"{summary.get('scanMinSceneY')}..{summary.get('scanMaxSceneY')}) "
                f"size={summary.get('scanWidth')}x{summary.get('scanHeight')} "
                f"seen={summary.get('sceneObjectsSeen', 0)} "
                f"captured={summary.get('sceneObjectsCaptured', 0)} "
                f"skippedByCap={summary.get('sceneObjectsSkippedByCap', 0)} "
                f"ratio={format_number(summary.get('captureRatio'))}"
            )
    scene_index = raw.get("sceneIndexSummary") or {}
    print("  scene index summary:")
    if not scene_index.get("present"):
        print("    (not present in selected raw ticks)")
    else:
        totals = scene_index.get("totals") or {}
        averages = scene_index.get("averagesPerTick") or {}
        print(f"    modes: {(scene_index.get('stringCounts') or {}).get('sceneCaptureMode') or {}}")
        print(f"    full resync ticks: {(scene_index.get('booleanCounts') or {}).get('fullResyncThisTick') or {}}")
        print(
            "    totals: "
            f"indexObjects={totals.get('indexObjectCount', 0)} "
            f"present={totals.get('presentObjectCount', 0)} "
            f"new={totals.get('newlyIndexedCount', 0)} "
            f"updated={totals.get('updatedCount', 0)} "
            f"despawned={totals.get('despawnedCount', 0)}"
        )
        print(
            "    average/tick: "
            f"buildMs={format_number(averages.get('sceneIndexBuildDurationMillis'))} "
            f"updateMs={format_number(averages.get('sceneIndexUpdateDurationMillis'))}"
        )
    scene_projection = raw.get("sceneProjectionSummary") or {}
    print("  scene projection summary:")
    if not scene_projection.get("present"):
        print("    (not present in selected raw ticks)")
    else:
        totals = scene_projection.get("totals") or {}
        averages = scene_projection.get("averagesPerTick") or {}
        print(f"    refresh modes: {(scene_projection.get('stringCounts') or {}).get('projectionRefreshMode') or {}}")
        print(f"    state changed ticks: {(scene_projection.get('booleanCounts') or {}).get('projectionStateChanged') or {}}")
        print(
            "    totals: "
            f"considered={totals.get('projectionCandidatesConsidered', 0)} "
            f"updated={totals.get('projectionObjectsUpdated', 0)} "
            f"reused={totals.get('projectionObjectsReused', 0)} "
            f"visibleRefs={totals.get('visibleObjectCount', 0)}"
        )
        print(f"    average projection ms/tick: {format_number(averages.get('projectionDurationMillis'))}")
    performance = raw.get("performance") or {}
    print("  performance:")
    for field in PERFORMANCE_NUMERIC_FIELDS:
        stats = performance.get(field) or {}
        if stats.get("count"):
            print(f"    {field}: avg={format_number(stats.get('avg'))} max={format_number(stats.get('max'))} count={stats.get('count')}")
        else:
            print(f"    {field}: (not present)")
    print()

    world = report["worldTargetCoverage"]
    print("C. World target coverage")
    print(f"  present: {'yes' if world.get('present') else 'no'}")
    print(f"  selected record count: {world.get('total', 0)}")
    print(f"  world target tick range: {world.get('targetTickRange')}")
    print(f"  retained frame tick range: {world.get('retainedFrameTickRange')}")
    print(f"  frame path tick range: {world.get('framePathTickRange')}")
    print(f"  selected raw/world overlap: {'yes' if world.get('selectedRawWorldOverlap') else 'no'}")
    for key, title in (
        ("byTargetType", "  by targetType:"),
        ("byTargetRole", "  by targetRole:"),
        ("byTargetCategory", "  by targetCategory:"),
        ("geometryAvailable", "  geometryAvailable:"),
        ("onScreen", "  onScreen:"),
        ("coordinateSpace", "  coordinateSpace:"),
    ):
        print_counts(title, world.get("counts", {}).get(key, {}), indent="    ")
    print(f"  fallback scene objects: {world.get('fallbackSceneObjectCount', 0)}")
    print(f"  unclassified scene objects: {world.get('unclassifiedSceneObjectCount', 0)}")
    print_counts("  top fallback scene object ids:", world.get("topFallbackSceneObjectIds", {}), indent="    ")
    print_counts("  top unclassified scene object ids:", world.get("topUnclassifiedSceneObjectIds", {}), indent="    ")
    print()

    candidates = report["candidateCoverage"]
    scenario = report["scenarioCoverage"]
    print("D. Candidate/scenario coverage")
    print(f"  candidates present: {'yes' if candidates.get('present') else 'no'}")
    print(f"  candidate count: {candidates.get('total', 0)}")
    print(f"  candidate tick range: {candidates.get('candidateTickRange')}")
    candidate_index = candidates.get("index") or {}
    if candidate_index:
        print(f"  candidate profile: {candidate_index.get('profileId') or 'none'}")
        print(f"  matching targets before filters: {candidate_index.get('matchingTargetsBeforeFilters', 'unknown')}")
        print(f"  candidates after profile filters: {candidate_index.get('candidatesAfterProfileFilters', 'unknown')}")
        print(f"  candidate index limit: {'none' if candidate_index.get('noLimit') else candidate_index.get('limit')}")
        print(f"  duplicates removed: {candidate_index.get('duplicatesRemoved', 0)}")
        print(f"  UI-blocked count: {candidate_index.get('uiBlockedCount', 0)}")
        print(f"  excluded UI-blocked count: {candidate_index.get('excludedUiBlockedCount', 0)}")
        print(f"  discarded by limit: {candidate_index.get('discardedByLimit', 0)}")
    print_counts("  candidates by type:", candidates.get("counts", {}).get("byTargetType", {}), indent="    ")
    print_counts("  candidates by role:", candidates.get("counts", {}).get("byTargetRole", {}), indent="    ")
    print_counts("  candidates by category:", candidates.get("counts", {}).get("byTargetCategory", {}), indent="    ")
    print_counts("  candidates by class:", candidate_index.get("countsByClassId", {}) if candidate_index else {}, indent="    ")
    print_counts("  candidates by quality:", candidate_index.get("countsByQualityTier", {}) if candidate_index else {}, indent="    ")
    print_counts("  top candidate negative signals:", candidate_index.get("topNegativeSignals", {}) if candidate_index else {}, indent="    ")
    print_counts("  top candidate reject reasons:", candidate_index.get("topRejectReasons", {}) if candidate_index else {}, indent="    ")
    print(f"  scenario present: {'yes' if scenario.get('present') else 'no'}")
    print(f"  scenario records: {scenario.get('recordCount', 0)}")
    print(f"  scenario selected candidates: {scenario.get('selectedCandidateCount', 0)}")
    print(f"  scenario tick range: {scenario.get('scenarioTickRange')}")
    print()

    print("E. Pipeline loss ledger")
    if report["lossLedger"].get("bestEffort"):
        print("  comparisons: best-effort; derived files may include prior filters, limits, or different tick ranges")
    for row in report["lossLedger"].get("byTick", []):
        print(f"  tick {row['tickId']}")
        print(f"    raw.sceneObjects             {row['raw']['sceneObjects']}")
        if row["raw"].get("visibleSceneObjectRefs"):
            print(f"    raw.visibleSceneObjectRefs   {row['raw']['visibleSceneObjectRefs']}")
            comparable_raw_scene = row["raw"]["visibleSceneObjectRefs"]
        else:
            comparable_raw_scene = row["raw"]["sceneObjects"]
        print(f"    worldTargets.sceneObject     {row['worldTargets']['sceneObject']}   loss from previous comparable stage: {comparable_raw_scene - row['worldTargets']['sceneObject']}")
        print(f"    worldTargets.total           {row['worldTargets']['total']}")
        print(f"    targetCandidates.total       {row['targetCandidates']['total']}   loss from previous comparable stage: {row['worldTargets']['total'] - row['targetCandidates']['total']}")
        if row["scenario"]["selected"] is not None:
            print(f"    scenario.selected            {row['scenario']['selected']}   loss from previous comparable stage: {row['targetCandidates']['total'] - row['scenario']['selected']}")
        if row.get("largestLossEdge"):
            edge = row["largestLossEdge"]
            print(f"    largest loss edge: {edge['edge']} ({edge['loss']})")
            print(f"    likely causes: {', '.join(row.get('likelyCauses') or [])}")
    largest = report["lossLedger"].get("largestComparableLoss")
    print(f"  overall largest comparable loss: {largest}")
    print()

    print("F. Stable identity matching (best-effort)")
    identity = report["identityMatching"]
    for tick, item in list(identity.get("byTick", {}).items())[:10]:
        print(
            f"  tick {tick}: raw identities={item['rawSceneObjectIdentityCount']} "
            f"world identities={item['worldSceneObjectIdentityCount']} "
            f"raw missing world={item['rawIdentitiesMissingFromWorldTargets']} "
            f"world missing candidates={item['worldTargetIdentitiesMissingFromCandidates']}"
        )
    print_counts("  raw missing traits: kind", identity.get("rawIdentitiesMissingTraits", {}).get("kind", {}), indent="    ")
    print_counts("  world missing candidate traits: kind", identity.get("worldIdentitiesMissingFromCandidatesTraits", {}).get("kind", {}), indent="    ")
    print()

    print("G. Trace filters")
    trace = report["trace"]
    if not trace.get("enabled"):
        print("  not enabled; use --object-id, --object-hash, or --near")
    else:
        print(f"  filters: {trace.get('filters')}")
        for tick, counts in trace.get("countsByTick", {}).items():
            print(f"  tick {tick}: {counts}; first absent={trace.get('firstAbsentStageByTick', {}).get(tick)}")
    print()

    print("H. Viewport-sector coverage")
    sectors = report["viewportSectors"]
    for stage in ("rawSceneObjects", "worldTargets", "candidates"):
        print(f"  {stage}:")
        stage_data = sectors.get(stage, {})
        if not stage_data:
            print("    (none)")
            continue
        for tick, summary in list(stage_data.items())[:5]:
            inferred = " inferred" if summary.get("dimensions", {}).get("inferred") else ""
            print(f"    tick {tick}{inferred}: {summary.get('counts')}")
    if sectors.get("warnings"):
        print("  warnings:")
        for warning in sectors["warnings"][:10]:
            print(f"    {warning}")
    print()

    print("I. Tick/frame alignment")
    for tick, row in report["frameAlignment"].get("byTick", {}).items():
        print(
            f"  tick {tick}: raw={row['rawTickExists']} world={row['worldTargetExactTickExists']} "
            f"nearestWorld={row['nearestWorldTargetTick']} retainedFrame={row['retainedFrameTickExactMatch']} "
            f"nearestFrame={row['nearestRetainedFrameTick']} candidates={row['candidateExactTickExists']}"
            + (f" scenario={row.get('scenarioExactTickExists')}" if "scenarioExactTickExists" in row else "")
        )
    for warning in report["frameAlignment"].get("warnings", [])[:10]:
        print(f"  warning: {warning}")
    print()

    print("J. Source audit")
    audit = report["sourceAudit"]
    print(f"  available: {'yes' if audit.get('available') else 'no'}")
    print(f"  project root: {audit.get('projectRoot')}")
    print(f"  files searched: {len(audit.get('filesSearched') or [])}")
    if audit.get("missingLikelyFiles"):
        print(f"  missing likely files: {len(audit.get('missingLikelyFiles') or [])}")
    summary = audit.get("summary", {})
    print(f"  possible caps: {len(summary.get('possibleCaps') or [])}")
    print(f"  possible radius-limited scans: {len(summary.get('possibleRadiusLimitedScans') or [])}")
    print(f"  object layer terms captured: {summary.get('capturedObjectLayerTerms')}")
    print(f"  tile paint/model terms found: {summary.get('tilePaintModelTermsFound')}")
    print(f"  inspector draw hints: {len(summary.get('inspectorDrawHints') or [])}")
    for finding in (summary.get("possibleCaps") or [])[:5]:
        print(f"    cap hint: {finding['file']}:{finding['line']} {finding['text']}")
    for finding in (summary.get("possibleRadiusLimitedScans") or [])[:5]:
        print(f"    radius hint: {finding['file']}:{finding['line']} {finding['text']}")
    print()

    print("L. Conclusion")
    conclusion = report["conclusion"]
    print(f"  most likely loss stage: {conclusion.get('mostLikelyLossStage')}")
    print("  strongest evidence:")
    for item in conclusion.get("strongestEvidence") or []:
        print(f"    {item}")
    print(f"  source has cap/radius safety logic: {'yes' if conclusion.get('sourceHasCapSafetyLogic') else 'no'}")
    print(f"  selected ticks hit scene object cap: {'yes' if conclusion.get('selectedTicksHitSceneObjectCap') else 'no'}")
    print(f"  selected scene objects skipped by cap: {conclusion.get('selectedSceneObjectsSkippedByCap', 0)}")
    print(
        "  current capture mode likely complete for selected scan: "
        f"{'yes' if conclusion.get('currentCaptureModeLikelyCompleteForSelectedScan') else 'no'}"
    )
    print(f"  Java appears to cap/skip scene objects in selected ticks: {'yes' if conclusion.get('javaAppearsToCapOrSkipSceneObjects') else 'no'}")
    print(f"  candidate profile: {conclusion.get('candidateProfileId') or 'none'}")
    print(f"  candidate profile filter active: {'yes' if conclusion.get('candidateProfileFilterActive') else 'no'}")
    print(f"  candidate semantic/UI filter active: {'yes' if conclusion.get('candidateSemanticFilterActive') else 'no'}")
    print(f"  candidate limit active: {'yes' if conclusion.get('candidateLimitActive') else 'no'}")
    print(f"  candidate discarded by limit: {conclusion.get('candidateDiscardedByLimit', 0)}")
    print(f"  candidate excluded UI-blocked: {conclusion.get('candidateExcludedUiBlocked', 0)}")
    print(f"  candidate duplicates removed: {conclusion.get('candidateDuplicatesRemoved', 0)}")
    print("  missing objects likely:")
    for key, value in conclusion.get("missingObjectsLikely", {}).items():
        print(f"    {key}: {value}")
    print(f"  recommended next diagnostic command: {conclusion.get('recommendedNextDiagnosticCommand')}")
    for warning in conclusion.get("warnings") or []:
        print(f"  warning: {warning}")


def format_tick_list(ticks: list[int]) -> str:
    if not ticks:
        return "(none)"
    if len(ticks) <= 12:
        return ", ".join(str(tick) for tick in ticks)
    return f"{ticks[0]}..{ticks[-1]} ({len(ticks)} ticks)"


def format_number(value) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    if abs(number - round(number)) < 0.001:
        return str(int(round(number)))
    return f"{number:.3f}"


def parse_near(values: list[str] | None):
    if values is None:
        return None
    if len(values) not in {2, 3}:
        raise argparse.ArgumentTypeError("--near requires worldX worldY [radius]")
    x = safe_int(values[0])
    y = safe_int(values[1])
    radius = safe_int(values[2]) if len(values) == 3 else 3
    if x is None or y is None or radius is None:
        raise argparse.ArgumentTypeError("--near values must be integers")
    return [x, y, radius]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose read-only target coverage across raw ticks, world targets, "
            "target candidates, scenario datasets, and inspector filter hints."
        )
    )
    parser.add_argument("--session", help="Explicit telemetry session path.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --session is omitted.")
    parser.add_argument("--tick", type=int, help="Inspect one tick.")
    parser.add_argument("--range", nargs=2, type=int, dest="tick_range", metavar=("START", "END"), help="Inclusive tick range.")
    parser.add_argument("--latest", type=int, metavar="N", help="Inspect latest N raw ticks.")
    parser.add_argument("--all-ticks", action="store_true", help="Inspect all available raw ticks instead of the default latest sample.")
    parser.add_argument("--scenario", help="Scenario dataset name, for example tree_cutting.")
    parser.add_argument("--json", action="store_true", help="Print one machine-readable JSON report.")
    parser.add_argument("--performance", action="store_true", help="Emphasize capture/index/projection timing fields in the report.")
    parser.add_argument("--project-root", help="Repo root for read-only source audit.")
    parser.add_argument("--object-id", help="Trace object/id/rawId/targetId through pipeline.")
    parser.add_argument("--object-hash", help="Trace object hash fields through pipeline when present.")
    parser.add_argument("--near", nargs="+", metavar="WORLD", help="Trace records near worldX worldY with optional radius. Default radius: 3.")
    args = parser.parse_args()
    if args.tick is not None and args.tick_range is not None:
        parser.error("--tick cannot be combined with --range")
    if args.tick is not None and args.latest is not None:
        parser.error("--tick cannot be combined with --latest")
    if args.tick_range is not None and args.latest is not None:
        parser.error("--range cannot be combined with --latest")
    if args.all_ticks and args.tick is not None:
        parser.error("--all-ticks cannot be combined with --tick")
    if args.all_ticks and args.tick_range is not None:
        parser.error("--all-ticks cannot be combined with --range")
    if args.all_ticks and args.latest is not None:
        parser.error("--all-ticks cannot be combined with --latest")
    if args.latest is not None and args.latest < 1:
        parser.error("--latest must be positive")
    if args.near is not None:
        try:
            args.near = parse_near(args.near)
        except argparse.ArgumentTypeError as error:
            parser.error(str(error))
    return args


def main() -> int:
    args = parse_args()
    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())

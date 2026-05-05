import argparse
import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from telemetry_paths import (
    find_newest_session,
    get_sessions_dir,
    iter_jsonl,
    list_tick_files,
    resolve_frame_path,
    safe_read_json,
)


SCHEMA_VERSION_INDEX = "interaction_geometry.world_index.v1"
SCHEMA_VERSION_RECORD = "interaction_geometry.world_target.v1"
PROJECTION_MISSING_MESSAGE = "Run the read-only Java projection telemetry pass first."
TARGET_TYPES = {"npc", "player", "sceneObject", "groundItem", "tile", "all"}
FRAME_TICK_RE = re.compile(r"frame-tick-(\d+)\.[^.]+$", re.IGNORECASE)
FRAME_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
PROJECTION_TOP_LEVEL_FIELDS = (
    "cameraX",
    "cameraY",
    "cameraZ",
    "cameraYaw",
    "cameraPitch",
    "viewportWidth",
    "viewportHeight",
    "viewportXOffset",
    "viewportYOffset",
    "canvasWidth",
    "canvasHeight",
)
GEOMETRY_FIELDS = (
    "canvasPoint",
    "canvasLocation",
    "canvasCenter",
    "canvasTilePolygon",
    "clickboxBounds",
    "clickboxPolygon",
    "convexHullBounds",
    "convexHullPolygon",
    "geometryAvailable",
    "onScreen",
)
CLASSIFICATION_RULES = (
    ("bank_booth", ("bank booth", "bank chest", "deposit box"), "interactable", "bank", ("bank", "bank_booth", "clickable_candidate")),
    ("banker", ("banker",), "entity", "npc", ("bank", "banker", "clickable_candidate")),
    ("bank", ("bank",), "interactable", "bank", ("bank", "clickable_candidate")),
    ("oak", ("oak",), "interactable", "tree", ("tree", "oak", "clickable_candidate")),
    ("willow", ("willow",), "interactable", "tree", ("tree", "willow", "clickable_candidate")),
    ("maple", ("maple",), "interactable", "tree", ("tree", "maple", "clickable_candidate")),
    ("yew", ("yew",), "interactable", "tree", ("tree", "yew", "clickable_candidate")),
    ("tree", ("tree",), "interactable", "tree", ("tree", "clickable_candidate")),
    ("door", ("door", "gate"), "interactable", "door", ("door", "clickable_candidate", "navigation_geometry")),
    ("ladder", ("ladder", "stair", "staircase", "stairs"), "interactable", "door", ("ladder", "clickable_candidate", "navigation_geometry")),
    ("furnace", ("furnace",), "interactable", "sceneObject", ("furnace", "clickable_candidate")),
    ("range", ("range", "cooking range"), "interactable", "sceneObject", ("range", "clickable_candidate")),
    ("altar", ("altar",), "interactable", "sceneObject", ("altar", "clickable_candidate")),
    ("water", ("water", "fountain", "well"), "decoration", "sceneObject", ("water", "decoration")),
    ("wall", ("wall", "fence", "counter", "barrier", "railing", "building"), "obstacle", "wall", ("wall", "obstacle", "navigation_geometry")),
)
FALLBACK_NAME_PREFIXES = ("Npc[", "SceneObject[", "GroundItem[", "Tile[")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump_compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def parse_int(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected integer: {value}") from error


def positive_int(value: str) -> int:
    parsed = parse_int(value)

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
        "targets": output_dir / "world_targets.jsonl",
        "index": output_dir / "world_geometry_index.json",
    }


def session_id_for(session: Path) -> str:
    manifest = safe_read_json(session / "manifest.json")

    if isinstance(manifest, dict) and manifest.get("sessionId"):
        return str(manifest["sessionId"])

    return session.name


def selected_by_tick_args(tick: dict, args) -> bool:
    tick_id = tick.get("tickId")

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


def tick_range_payload(ticks: list[dict]) -> list[int] | None:
    tick_ids = [tick.get("tickId") for tick in ticks if isinstance(tick.get("tickId"), int)]

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


def read_selected_ticks(session: Path, args) -> tuple[list[dict], dict]:
    tick_files = list_tick_files(session)

    if not tick_files:
        raise FileNotFoundError(f"Raw tick files not found in session: {session}")

    ticks = []
    retained_ticks = retained_frame_tick_ids(session)
    warnings = []

    for _source, tick in iter_jsonl(tick_files):
        if isinstance(tick, dict) and selected_by_tick_args(tick, args):
            ticks.append(tick)

    if args.latest_with_frames is not None:
        if not retained_ticks:
            raise RuntimeError("No retained frame files found. Capture a fresh session or adjust frame retention.")

        ticks = [tick for tick in ticks if tick.get("tickId") in retained_ticks]

        if not ticks:
            raise RuntimeError(
                "No selected raw ticks match retained frame files. Capture a fresh session or adjust the tick selection."
            )

        if len(ticks) < args.latest_with_frames:
            warnings.append(
                f"requested {args.latest_with_frames} retained-frame ticks, but only {len(ticks)} matching raw ticks were found"
            )

        ticks = ticks[-args.latest_with_frames :]
    elif args.latest is not None:
        ticks = ticks[-args.latest :]

    selected_tick_ids = {tick.get("tickId") for tick in ticks if isinstance(tick.get("tickId"), int)}
    selected_frame_ticks = sorted(selected_tick_ids & retained_ticks)
    selection_info = {
        "selectedBy": selection_mode(args),
        "retainedFrameTickCount": len(retained_ticks),
        "retainedFrameTickRange": [min(retained_ticks), max(retained_ticks)] if retained_ticks else None,
        "selectedFrameTickCount": len(selected_frame_ticks),
        "selectedFrameTickRange": [selected_frame_ticks[0], selected_frame_ticks[-1]] if selected_frame_ticks else None,
        "selectedTickRange": tick_range_payload(ticks),
        "warnings": warnings,
    }

    return ticks, selection_info


def read_tick_bundles_by_tick(session: Path) -> dict[int, dict]:
    path = session / "perception" / "tick_bundles.jsonl"

    if not path.exists():
        return {}

    bundles = {}

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    bundle = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(bundle, dict) and isinstance(bundle.get("tickId"), int):
                    bundles[bundle["tickId"]] = bundle
    except OSError:
        return {}

    return bundles


def has_projection_fields(tick: dict) -> bool:
    if any(key in tick for key in PROJECTION_TOP_LEVEL_FIELDS):
        return True

    for collection_name in ("npcs", "players", "sceneObjects", "groundItems"):
        values = tick.get(collection_name)

        if not isinstance(values, list):
            continue

        for value in values:
            if isinstance(value, dict) and any(key in value for key in GEOMETRY_FIELDS):
                return True

    return False


def frame_payload(session: Path, tick: dict, bundle: dict | None) -> dict:
    frame = bundle.get("frame") if isinstance(bundle, dict) and isinstance(bundle.get("frame"), dict) else {}

    path = frame.get("path") or tick.get("framePath")
    recorded_exists = frame.get("exists")
    exists = recorded_exists

    if path:
        resolved = resolve_frame_path(session, path)
        exists = resolved.exists() if resolved else None

    return {
        "path": path,
        "width": frame.get("width"),
        "height": frame.get("height"),
        "exists": exists,
        "recordedExists": recorded_exists,
    }


def camera_payload(tick: dict) -> dict:
    return {
        "x": tick.get("cameraX"),
        "y": tick.get("cameraY"),
        "z": tick.get("cameraZ"),
        "yaw": tick.get("cameraYaw"),
        "pitch": tick.get("cameraPitch"),
    }


def viewport_payload(tick: dict) -> dict:
    return {
        "xOffset": tick.get("viewportXOffset"),
        "yOffset": tick.get("viewportYOffset"),
        "width": tick.get("viewportWidth"),
        "height": tick.get("viewportHeight"),
    }


def canvas_payload(tick: dict) -> dict:
    return {
        "width": tick.get("canvasWidth"),
        "height": tick.get("canvasHeight"),
    }


def world_payload(record: dict) -> dict | None:
    x = record.get("worldX")
    y = record.get("worldY")
    plane = record.get("plane")

    if not any(isinstance(value, int) for value in (x, y, plane)):
        return None

    return {"x": x, "y": y, "plane": plane}


def scene_payload(record: dict) -> dict | None:
    x = record.get("sceneX")
    y = record.get("sceneY")

    if not isinstance(x, int) and not isinstance(y, int):
        return None

    return {"x": x, "y": y}


def local_payload(record: dict) -> dict | None:
    x = record.get("localX")
    y = record.get("localY")

    if not isinstance(x, int) and not isinstance(y, int):
        return None

    return {"x": x, "y": y}


def useful_name(value) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()

    if not text or text.lower() in {"null", "hidden"}:
        return None

    return text


def fallback_label(prefix: str, value) -> str:
    return f"{prefix}[{value}]" if value is not None else f"{prefix}[unknown]"


def source_name(value, fallback: str) -> str:
    text = useful_name(value)
    return text or fallback


def normalize_text(value) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def add_tags(tags: list[str], values) -> None:
    for value in values:
        if value not in tags:
            tags.append(value)


def is_fallback_target_name(name: str | None) -> bool:
    return isinstance(name, str) and any(name.startswith(prefix) for prefix in FALLBACK_NAME_PREFIXES)


def classify_target(target: dict) -> dict:
    target_type = str(target.get("targetType") or "unknown")
    name = str(target.get("name") or target.get("targetName") or target.get("fallbackName") or "")
    haystack = " ".join(
        normalize_text(value)
        for value in (
            name,
            target.get("targetName"),
            target.get("fallbackName"),
            target.get("kind"),
            target.get("nameSource"),
            target.get("id"),
            target.get("rawId"),
        )
    )
    tags: list[str] = []

    if target_type == "npc":
        role = "entity"
        category = "npc"
        add_tags(tags, ("npc", "clickable_candidate"))
    elif target_type == "player":
        role = "entity"
        category = "player"
        add_tags(tags, ("player",))
    elif target_type == "groundItem":
        role = "item"
        category = "groundItem"
        add_tags(tags, ("groundItem", "item", "clickable_candidate"))
    elif target_type == "tile":
        role = "navigation"
        category = "tile"
        add_tags(tags, ("tile", "navigation_geometry"))
    elif target_type == "sceneObject":
        role = "unknown" if is_fallback_target_name(name) else "decoration"
        category = "unknown" if is_fallback_target_name(name) else "sceneObject"
        add_tags(tags, ("sceneObject",))
    else:
        role = "unknown"
        category = "unknown"

    if target_type == "sceneObject":
        for _rule_name, patterns, rule_role, rule_category, rule_tags in CLASSIFICATION_RULES:
            if any(pattern in haystack for pattern in patterns):
                role = rule_role
                category = rule_category
                add_tags(tags, rule_tags)
                break
    elif target_type == "npc":
        for _rule_name, patterns, _rule_role, rule_category, rule_tags in CLASSIFICATION_RULES:
            if any(pattern in haystack for pattern in patterns):
                category = "npc" if rule_category == "bank" else category
                add_tags(tags, rule_tags)
                break
    elif target_type == "groundItem":
        for _rule_name, patterns, _rule_role, _rule_category, rule_tags in CLASSIFICATION_RULES:
            if any(pattern in haystack for pattern in patterns):
                add_tags(tags, rule_tags)
                break

    if role in {"obstacle", "navigation"}:
        add_tags(tags, ("navigation_geometry",))

    if role == "obstacle":
        add_tags(tags, ("obstacle",))

    return {
        "targetRole": role,
        "targetCategory": category,
        "targetTags": tags,
    }


def geometry_available(record: dict) -> bool:
    value = record.get("geometryAvailable")

    if isinstance(value, bool):
        return value

    return any(record.get(key) is not None for key in GEOMETRY_FIELDS if key != "geometryAvailable")


def on_screen_value(record: dict) -> bool:
    return bool(record.get("onScreen"))


def geometry_payload(record: dict) -> dict:
    return {
        "coordinateSpace": "canvasPixels",
        "canvasPoint": record.get("canvasPoint"),
        "canvasLocation": record.get("canvasLocation"),
        "canvasCenter": record.get("canvasCenter"),
        "tilePolygon": record.get("canvasTilePolygon"),
        "clickboxBounds": record.get("clickboxBounds"),
        "clickboxPolygon": record.get("clickboxPolygon"),
        "convexHullBounds": record.get("convexHullBounds"),
        "convexHullPolygon": record.get("convexHullPolygon"),
        "onScreen": on_screen_value(record),
        "geometryAvailable": geometry_available(record),
        "geometryWarning": record.get("geometryWarning"),
    }


def state_payload(record: dict) -> dict:
    state = {}

    for key in ("animation", "poseAnimation", "orientation", "healthRatio", "healthScale", "dead"):
        if key in record:
            state[key] = record.get(key)

    return state


def polygon_bounds(points) -> dict | None:
    if not isinstance(points, list) or not points:
        return None

    xs = []
    ys = []

    for point in points:
        if (
            isinstance(point, list)
            and len(point) >= 2
            and isinstance(point[0], int)
            and isinstance(point[1], int)
        ):
            xs.append(point[0])
            ys.append(point[1])

    if not xs or not ys:
        return None

    return {
        "x": min(xs),
        "y": min(ys),
        "w": max(1, max(xs) - min(xs)),
        "h": max(1, max(ys) - min(ys)),
    }


def polygon_center(points) -> dict | None:
    bounds = polygon_bounds(points)

    if not bounds:
        return None

    return {
        "x": bounds["x"] + bounds["w"] / 2.0,
        "y": bounds["y"] + bounds["h"] / 2.0,
    }


def record_warnings(source: dict) -> list[str]:
    warnings = []
    warning = source.get("geometryWarning")

    if isinstance(warning, str) and warning:
        warnings.append(warning)

    if not geometry_available(source):
        warnings.append("projection geometry unavailable")

    return warnings


def should_include_record(record: dict, args) -> bool:
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}

    if args.target_type != "all" and target.get("targetType") != args.target_type:
        return False

    if args.id is not None and target.get("id") != args.id:
        return False

    if args.name:
        haystack = " ".join(
            str(value or "")
            for value in (
                target.get("name"),
                target.get("targetName"),
                target.get("kind"),
                target.get("targetType"),
                target.get("targetRole"),
                target.get("targetCategory"),
                " ".join(target.get("targetTags") or []),
                target.get("rawId"),
                target.get("nameSource"),
                target.get("id"),
            )
        ).lower()

        if args.name.lower() not in haystack:
            return False

    if args.only_on_screen and not args.include_off_screen and geometry.get("onScreen") is not True:
        return False

    return True


def base_target_record(session_id: str, tick: dict, frame: dict, target: dict, geometry: dict, state: dict, warnings: list[str]) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION_RECORD,
        "sessionId": session_id,
        "tickId": tick.get("tickId"),
        "timestampUtc": tick.get("timestampUtc"),
        "frame": frame,
        "camera": camera_payload(tick),
        "viewport": viewport_payload(tick),
        "canvas": canvas_payload(tick),
        "target": target,
        "geometry": geometry,
        "state": state,
        "source": "runelite_read_only_projection",
        "confidence": 1.0 if geometry.get("geometryAvailable") else 0.0,
        "warnings": warnings,
    }


def npc_records(session_id: str, tick: dict, frame: dict) -> list[dict]:
    records = []

    for npc in tick.get("npcs") or []:
        if not isinstance(npc, dict):
            continue

        index = npc.get("index")
        npc_id = npc.get("id")
        npc_name = useful_name(npc.get("npcName"))
        legacy_name = useful_name(npc.get("name"))
        fallback_name = fallback_label("Npc", npc_id)
        name = npc_name or legacy_name or fallback_name
        name_source = (
            source_name(npc.get("npcNameSource"), "npcName")
            if npc_name
            else "name"
            if legacy_name
            else "fallback"
        )
        target = {
            "targetType": "npc",
            "targetId": f"{tick.get('tickId')}:npc:{index if index is not None else npc_id}",
            "id": npc_id,
            "rawId": npc_id,
            "index": index,
            "name": name,
            "targetName": name,
            "nameSource": name_source,
            "npcNameSource": npc.get("npcNameSource"),
            "fallbackName": fallback_name,
            "world": world_payload(npc),
            "scene": scene_payload(npc),
            "local": local_payload(npc),
        }
        target.update(classify_target(target))
        records.append(base_target_record(session_id, tick, frame, target, geometry_payload(npc), state_payload(npc), record_warnings(npc)))

    return records


def player_records(session_id: str, tick: dict, frame: dict) -> list[dict]:
    records = []

    for player in tick.get("players") or []:
        if not isinstance(player, dict):
            continue

        index = player.get("index")
        target = {
            "targetType": "player",
            "targetId": f"{tick.get('tickId')}:player:{index}",
            "index": index,
            "name": player.get("name"),
            "nameHash": player.get("nameHash"),
            "world": world_payload(player),
            "scene": scene_payload(player),
            "local": local_payload(player),
        }
        target.update(classify_target(target))
        records.append(base_target_record(session_id, tick, frame, target, geometry_payload(player), state_payload(player), record_warnings(player)))

    return records


def scene_object_records(session_id: str, tick: dict, frame: dict) -> list[dict]:
    records = []

    for scene_object in tick.get("sceneObjects") or []:
        if not isinstance(scene_object, dict):
            continue

        object_id = scene_object.get("id")
        kind = scene_object.get("kind")
        scene = scene_payload(scene_object)
        scene_suffix = f"{scene.get('x')}:{scene.get('y')}" if scene else "unknown"
        object_name = useful_name(scene_object.get("objectName"))
        legacy_name = useful_name(scene_object.get("name"))
        fallback_name = fallback_label("SceneObject", object_id)
        target_name = object_name or legacy_name or fallback_name
        name_source = (
            source_name(scene_object.get("objectNameSource"), "objectDefinition")
            if object_name
            else "legacy"
            if legacy_name
            else "fallback"
        )
        target = {
            "targetType": "sceneObject",
            "targetId": f"{tick.get('tickId')}:sceneObject:{kind}:{object_id}:{scene_suffix}",
            "id": object_id,
            "rawId": object_id,
            "kind": kind,
            "name": target_name,
            "targetName": target_name,
            "nameSource": name_source,
            "objectNameSource": scene_object.get("objectNameSource"),
            "fallbackName": fallback_name,
            "world": world_payload(scene_object),
            "scene": scene,
            "local": local_payload(scene_object),
        }
        target.update(classify_target(target))
        records.append(
            base_target_record(
                session_id,
                tick,
                frame,
                target,
                geometry_payload(scene_object),
                state_payload(scene_object),
                record_warnings(scene_object),
            )
        )

    return records


def ground_item_records(session_id: str, tick: dict, frame: dict) -> list[dict]:
    records = []

    for item in tick.get("groundItems") or []:
        if not isinstance(item, dict):
            continue

        item_id = item.get("id")
        scene = scene_payload(item)
        scene_suffix = f"{scene.get('x')}:{scene.get('y')}" if scene else "unknown"
        item_name = useful_name(item.get("itemName"))
        legacy_name = useful_name(item.get("name"))
        fallback_name = fallback_label("GroundItem", item_id)
        target_name = item_name or legacy_name or fallback_name
        name_source = (
            source_name(item.get("itemNameSource"), "itemDefinition")
            if item_name
            else "legacy"
            if legacy_name
            else "fallback"
        )
        target = {
            "targetType": "groundItem",
            "targetId": f"{tick.get('tickId')}:groundItem:{item_id}:{scene_suffix}",
            "id": item_id,
            "rawId": item_id,
            "quantity": item.get("quantity"),
            "name": target_name,
            "targetName": target_name,
            "nameSource": name_source,
            "itemNameSource": item.get("itemNameSource"),
            "fallbackName": fallback_name,
            "world": world_payload(item),
            "scene": scene,
            "local": local_payload(item),
        }
        target.update(classify_target(target))
        records.append(base_target_record(session_id, tick, frame, target, geometry_payload(item), state_payload(item), record_warnings(item)))

    return records


def tile_records(session_id: str, tick: dict, frame: dict) -> list[dict]:
    records = []
    seen = set()

    for source_type, collection_name in (("sceneObject", "sceneObjects"), ("groundItem", "groundItems")):
        for source in tick.get(collection_name) or []:
            if not isinstance(source, dict):
                continue

            polygon = source.get("canvasTilePolygon")

            if not polygon:
                continue

            world = world_payload(source)
            scene = scene_payload(source)
            local = local_payload(source)
            key = (
                tick.get("tickId"),
                world.get("x") if world else None,
                world.get("y") if world else None,
                world.get("plane") if world else source.get("plane"),
                scene.get("x") if scene else None,
                scene.get("y") if scene else None,
            )

            if key in seen:
                continue

            seen.add(key)
            geometry = {
                "coordinateSpace": "canvasPixels",
                "canvasPoint": None,
                "canvasLocation": None,
                "canvasCenter": source.get("canvasCenter") or polygon_center(polygon),
                "tilePolygon": polygon,
                "clickboxBounds": None,
                "clickboxPolygon": None,
                "convexHullBounds": None,
                "convexHullPolygon": None,
                "onScreen": on_screen_value(source),
                "geometryAvailable": True,
                "geometryWarning": source.get("geometryWarning"),
            }
            name = f"Tile[{world.get('x')},{world.get('y')},{world.get('plane')}]" if world else "Tile[unknown]"
            target = {
                "targetType": "tile",
                "targetId": f"{tick.get('tickId')}:tile:{key[1]}:{key[2]}:{key[3]}:{key[4]}:{key[5]}",
                "targetName": name,
                "name": name,
                "nameSource": "derivedTile",
                "derivedFrom": source_type,
                "world": world,
                "scene": scene,
                "local": local,
            }
            target.update(classify_target(target))
            warnings = []
            warning = source.get("geometryWarning")

            if isinstance(warning, str) and warning:
                warnings.append(warning)

            records.append(base_target_record(session_id, tick, frame, target, geometry, {}, warnings))

    return records


def records_for_tick(session_id: str, session: Path, tick: dict, bundle: dict | None) -> list[dict]:
    frame = frame_payload(session, tick, bundle)
    records = []
    records.extend(npc_records(session_id, tick, frame))
    records.extend(player_records(session_id, tick, frame))
    records.extend(scene_object_records(session_id, tick, frame))
    records.extend(ground_item_records(session_id, tick, frame))
    records.extend(tile_records(session_id, tick, frame))
    return records


def atomic_write_outputs(paths: dict[str, Path], records: list[dict], index: dict) -> None:
    output_dir = paths["outputDir"]
    temp_dir = output_dir / f".tmp-world-targets-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    try:
        with (temp_dir / "world_targets.jsonl").open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json_dump_compact(record))
                file.write("\n")

        with (temp_dir / "world_geometry_index.json").open("w", encoding="utf-8") as file:
            json.dump(index, file, indent=2)
            file.write("\n")

        output_dir.mkdir(parents=True, exist_ok=True)
        os.replace(temp_dir / "world_targets.jsonl", paths["targets"])
        os.replace(temp_dir / "world_geometry_index.json", paths["index"])
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def build_world_target_geometry(session: Path, args) -> dict:
    session = session.expanduser().resolve()
    session_id = session_id_for(session)
    ticks, selection_info = read_selected_ticks(session, args)
    tick_bundles = read_tick_bundles_by_tick(session)
    warnings = list(selection_info.get("warnings") or [])

    if ticks and not any(has_projection_fields(tick) for tick in ticks):
        raise RuntimeError(PROJECTION_MISSING_MESSAGE)

    if not tick_bundles:
        warnings.append("perception tick bundles unavailable; frame width/height metadata may be missing")

    records = []
    counts_by_target_type = Counter()
    counts_by_on_screen = Counter()
    counts_by_geometry_available = Counter()
    counts_by_name = Counter()
    counts_by_id = Counter()
    counts_by_target_role = Counter()
    counts_by_target_category = Counter()
    counts_by_target_tag = Counter()
    name_diagnostics = Counter()
    unclassified_scene_object_count = 0
    missing_projection_count = 0
    missing_frame_target_count = 0

    for tick in ticks:
        tick_id = tick.get("tickId")
        bundle = tick_bundles.get(tick_id)

        for record in records_for_tick(session_id, session, tick, bundle):
            if not should_include_record(record, args):
                continue

            records.append(record)
            target = record.get("target") if isinstance(record.get("target"), dict) else {}
            geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}
            target_type = target.get("targetType") or "unknown"
            target_name = target.get("name") or target.get("targetName") or target.get("kind")
            target_id = target.get("id")

            counts_by_target_type[target_type] += 1
            counts_by_target_role[target.get("targetRole") or "unknown"] += 1
            counts_by_target_category[target.get("targetCategory") or "unknown"] += 1
            counts_by_on_screen[str(bool(geometry.get("onScreen"))).lower()] += 1
            counts_by_geometry_available[str(bool(geometry.get("geometryAvailable"))).lower()] += 1

            for tag in target.get("targetTags") or []:
                counts_by_target_tag[str(tag)] += 1

            if target_type == "sceneObject" and target.get("targetCategory") in {None, "unknown"}:
                unclassified_scene_object_count += 1

            if not geometry.get("geometryAvailable"):
                missing_projection_count += 1

            frame = record.get("frame") if isinstance(record.get("frame"), dict) else {}

            if frame.get("path") and frame.get("exists") is not True:
                missing_frame_target_count += 1

            if target_name:
                counts_by_name[str(target_name)] += 1

            if target_id is not None:
                counts_by_id[str(target_id)] += 1

            name_source = str(target.get("nameSource") or "")

            if target_type == "npc":
                name_diagnostics["fallbackNpcCount" if name_source == "fallback" else "namedNpcCount"] += 1
            elif target_type == "sceneObject":
                name_diagnostics["fallbackSceneObjectCount" if name_source == "fallback" else "namedSceneObjectCount"] += 1
            elif target_type == "groundItem":
                name_diagnostics["fallbackGroundItemCount" if name_source == "fallback" else "namedGroundItemCount"] += 1

    paths = output_paths(session)
    fallback_scene_objects = name_diagnostics["fallbackSceneObjectCount"]
    named_scene_objects = name_diagnostics["namedSceneObjectCount"]

    if fallback_scene_objects and fallback_scene_objects >= max(25, named_scene_objects):
        warnings.append(
            f"{fallback_scene_objects} scene object records used fallback labels; "
            "fresh sessions may still contain hidden or unavailable object definitions"
        )

    if unclassified_scene_object_count >= 100:
        warnings.append(
            f"{unclassified_scene_object_count} scene object records are unclassified; "
            "use inspector role/category/tag filters to hide clutter without deleting obstacle data"
        )

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
        "selectedTickCount": len(ticks),
        "targetRecordCount": len(records),
        "countsByTargetType": dict(counts_by_target_type.most_common()),
        "countsByTargetRole": dict(counts_by_target_role.most_common()),
        "countsByTargetCategory": dict(counts_by_target_category.most_common()),
        "topTargetTags": dict(counts_by_target_tag.most_common(25)),
        "countsByOnScreen": dict(counts_by_on_screen.most_common()),
        "countsByGeometryAvailable": dict(counts_by_geometry_available.most_common()),
        "topTargetNames": dict(counts_by_name.most_common(25)),
        "topTargetIds": dict(counts_by_id.most_common(25)),
        "nameDiagnostics": {
            "namedNpcCount": name_diagnostics["namedNpcCount"],
            "fallbackNpcCount": name_diagnostics["fallbackNpcCount"],
            "namedSceneObjectCount": name_diagnostics["namedSceneObjectCount"],
            "fallbackSceneObjectCount": name_diagnostics["fallbackSceneObjectCount"],
            "namedGroundItemCount": name_diagnostics["namedGroundItemCount"],
            "fallbackGroundItemCount": name_diagnostics["fallbackGroundItemCount"],
            "unclassifiedSceneObjectCount": unclassified_scene_object_count,
        },
        "missingProjectionCount": missing_projection_count,
        "missingFrameTargetCount": missing_frame_target_count,
        "filters": {
            "latest": args.latest,
            "latestWithFrames": args.latest_with_frames,
            "range": list(args.tick_range) if args.tick_range else None,
            "targetType": args.target_type,
            "name": args.name,
            "id": args.id,
            "onlyOnScreen": bool(args.only_on_screen and not args.include_off_screen),
            "includeOffScreen": bool(args.include_off_screen),
        },
        "paths": {
            "worldTargets": "interaction_geometry/world_targets.jsonl",
            "worldGeometryIndex": "interaction_geometry/world_geometry_index.json",
        },
        "warnings": warnings[:100],
        "warningCount": len(warnings),
    }
    atomic_write_outputs(paths, records, index)
    return index


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build derived world target geometry from existing read-only projection telemetry. "
            "This emits geometry records only; it does not create actions or modify raw telemetry."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to process.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --session is omitted.")
    parser.add_argument("--latest", type=positive_int, metavar="N", help="Select the newest N matching raw ticks.")
    parser.add_argument(
        "--latest-with-frames",
        type=positive_int,
        metavar="N",
        help="Select the newest N raw ticks that have retained frame image files on disk.",
    )
    parser.add_argument("--range", nargs=2, type=parse_int, dest="tick_range", metavar=("START", "END"), help="Select an inclusive tick range.")
    parser.add_argument("--target-type", choices=sorted(TARGET_TYPES), default="all", help="Target type to emit. Default: all.")
    parser.add_argument("--name", help="Case-insensitive text filter against target name/type/kind.")
    parser.add_argument("--id", type=parse_int, help="Filter by target id when available.")
    parser.add_argument("--only-on-screen", action="store_true", help="Emit only records whose projected geometry is on-screen.")
    parser.add_argument("--include-off-screen", action="store_true", help="Explicitly include off-screen/null geometry records even if --only-on-screen is set.")
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

    print("counts by targetRole:")

    if index["countsByTargetRole"]:
        for role, count in index["countsByTargetRole"].items():
            print(f"  {role}: {count}")
    else:
        print("  none")

    print("counts by targetCategory:")

    if index["countsByTargetCategory"]:
        for category, count in index["countsByTargetCategory"].items():
            print(f"  {category}: {count}")
    else:
        print("  none")

    print("counts by onScreen:")

    if index["countsByOnScreen"]:
        for value, count in index["countsByOnScreen"].items():
            print(f"  {value}: {count}")
    else:
        print("  none")

    diagnostics = index.get("nameDiagnostics") or {}
    print("name diagnostics:")
    print(f"  named NPCs: {diagnostics.get('namedNpcCount', 0)}")
    print(f"  fallback NPCs: {diagnostics.get('fallbackNpcCount', 0)}")
    print(f"  named scene objects: {diagnostics.get('namedSceneObjectCount', 0)}")
    print(f"  fallback scene objects: {diagnostics.get('fallbackSceneObjectCount', 0)}")
    print(f"  unclassified scene objects: {diagnostics.get('unclassifiedSceneObjectCount', 0)}")
    print(f"  named ground items: {diagnostics.get('namedGroundItemCount', 0)}")
    print(f"  fallback ground items: {diagnostics.get('fallbackGroundItemCount', 0)}")

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
        index = build_world_target_geometry(session, args)
    except FileNotFoundError as error:
        print(f"session: {session}")
        print(str(error))
        return 1
    except RuntimeError as error:
        print(f"session: {session}")
        print(str(error))
        return 1

    print_summary(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

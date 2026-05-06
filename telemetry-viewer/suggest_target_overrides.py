import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from telemetry_paths import find_newest_session, get_sessions_dir


FALLBACK_PREFIX = "SceneObject["


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser().resolve()

    return find_newest_session(get_sessions_dir(args.sessions_dir))


def jsonl_records(path: Path):
    if not path.exists():
        return

    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()

                if not text:
                    continue

                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue

                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def target_for(record: dict) -> dict:
    value = record.get("target")
    return value if isinstance(value, dict) else record


def geometry_for(record: dict) -> dict:
    value = record.get("geometry")
    return value if isinstance(value, dict) else record.get("sampleProjection") if isinstance(record.get("sampleProjection"), dict) else {}


def world_for(record: dict, target: dict) -> dict:
    value = target.get("world") if isinstance(target.get("world"), dict) else record.get("world")
    return value if isinstance(value, dict) else {}


def scene_for(record: dict, target: dict) -> dict:
    value = target.get("scene") if isinstance(target.get("scene"), dict) else record.get("scene")
    return value if isinstance(value, dict) else {}


def target_id(target: dict, record: dict):
    return target.get("rawId") or target.get("id") or record.get("rawId") or record.get("id")


def fallback_like(target: dict) -> bool:
    name = str(target.get("name") or target.get("targetName") or target.get("fallbackName") or "")
    name_source = str(target.get("nameSource") or "").lower()
    return name.startswith(FALLBACK_PREFIX) or name_source == "fallback"


def unclassified(target: dict) -> bool:
    role = str(target.get("targetRole") or "unknown").lower()
    category = str(target.get("targetCategory") or "unknown").lower()
    return role in {"", "unknown", "decoration"} or category in {"", "unknown", "sceneobject", "decoration"}


def collect_candidates(session: Path, use_static_index: bool) -> tuple[dict, list[str]]:
    interaction = session / "interaction_geometry"
    paths = []
    warnings = []

    if use_static_index:
        paths.append(interaction / "scene_static_index.jsonl")

    paths.append(interaction / "world_targets.jsonl")
    groups = defaultdict(lambda: {
        "count": 0,
        "kind": Counter(),
        "onScreen": Counter(),
        "geometryAvailable": Counter(),
        "objectKeys": [],
        "sampleLocations": [],
        "sampleAim": None,
        "currentNames": Counter(),
        "roles": Counter(),
        "categories": Counter(),
        "tags": Counter(),
    })

    for path in paths:
        if not path.exists():
            warnings.append(f"missing input: {path}")
            continue

        for record in jsonl_records(path) or []:
            target = target_for(record)
            target_type = target.get("targetType") or record.get("targetType") or "sceneObject"

            if target_type != "sceneObject":
                continue

            if not (fallback_like(target) or unclassified(target)):
                continue

            object_id = target_id(target, record)

            if object_id is None:
                continue

            geometry = geometry_for(record)
            world = world_for(record, target)
            scene = scene_for(record, target)
            group = groups[str(object_id)]
            group["count"] += 1
            group["kind"][str(target.get("kind") or record.get("kind") or "unknown")] += 1
            group["onScreen"][str(bool(geometry.get("onScreen"))).lower()] += 1
            group["geometryAvailable"][str(bool(geometry.get("geometryAvailable"))).lower()] += 1
            group["currentNames"][str(target.get("name") or target.get("targetName") or target.get("fallbackName") or "unknown")] += 1
            group["roles"][str(target.get("targetRole") or "unknown")] += 1
            group["categories"][str(target.get("targetCategory") or "unknown")] += 1

            for tag in target.get("targetTags") or []:
                group["tags"][str(tag)] += 1

            object_key = target.get("objectKey") or record.get("objectKey")

            if object_key and len(group["objectKeys"]) < 3:
                group["objectKeys"].append(str(object_key))

            if len(group["sampleLocations"]) < 5:
                group["sampleLocations"].append({"world": world or None, "scene": scene or None})

            if group["sampleAim"] is None:
                group["sampleAim"] = geometry.get("canvasLocation") or geometry.get("canvasPoint") or geometry.get("canvasCenter")

    return groups, warnings


def override_snippet(object_id: str) -> str:
    snippet = {
        object_id: {
            "name": "Tree",
            "role": "interactable",
            "category": "tree",
            "tags": ["tree", "clickable_candidate"],
        }
    }
    return json.dumps(snippet, indent=2)


def print_report(session: Path, groups: dict, warnings: list[str], args) -> None:
    print(f"session: {session}")

    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    rows = sorted(groups.items(), key=lambda item: (-item[1]["count"], item[0]))
    if args.min_count:
        rows = [(object_id, group) for object_id, group in rows if group["count"] >= args.min_count]

    rows = rows[: args.limit]
    print(f"unclassified/fallback scene object ids: {len(rows)} shown")

    for object_id, group in rows:
        best_name = group["currentNames"].most_common(1)[0][0] if group["currentNames"] else f"SceneObject[{object_id}]"
        print()
        print(
            f"id={object_id} count={group['count']} name={best_name} "
            f"kind={dict(group['kind'].most_common())} onScreen={dict(group['onScreen'])} "
            f"geometryAvailable={dict(group['geometryAvailable'])}"
        )
        print(f"  roles={dict(group['roles'].most_common())} categories={dict(group['categories'].most_common())}")
        print(f"  tags={dict(group['tags'].most_common(8))}")
        print(f"  sample objectKeys={group['objectKeys']}")
        print(f"  sample locations={group['sampleLocations']}")
        print(f"  sample aim={group['sampleAim']}")
        print("  suggested manual override skeleton:")
        for line in override_snippet(object_id).splitlines():
            print(f"    {line}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Suggest read-only target_name_overrides.json skeletons from fallback or unclassified scene objects. "
            "This tool prints suggestions only and never edits telemetry or override files."
        )
    )
    parser.add_argument("--session", help="Telemetry session directory to inspect.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --session is omitted.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum object ids to print. Default: 25.")
    parser.add_argument("--min-count", type=int, default=1, help="Minimum record count for an object id. Default: 1.")
    parser.add_argument("--world-targets-only", action="store_true", help="Ignore scene_static_index.jsonl even when present.")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be positive")

    if args.min_count < 1:
        parser.error("--min-count must be positive")

    return args


def main() -> int:
    args = parse_args()
    session = resolve_session(args)

    if session is None:
        print(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
        return 1

    groups, warnings = collect_candidates(session, use_static_index=not args.world_targets_only)
    print_report(session, groups, warnings, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

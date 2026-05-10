import argparse
import json
import sys
from pathlib import Path

from telemetry_paths import find_newest_session


SCHEMA = "overlay_geometry_diagnostic.v1"
LIVE_DIR = Path("interaction_geometry") / "live"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict]:
    records = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        pass
    return records


def polygon_points(value) -> list[dict]:
    if isinstance(value, dict):
        if isinstance(value.get("points"), list):
            value = value.get("points")
        elif isinstance(value.get("x"), list) and isinstance(value.get("y"), list):
            xs = value.get("x")
            ys = value.get("y")
            count = min(len(xs), len(ys), int(value.get("n") or min(len(xs), len(ys))))
            return [
                {"x": xs[index], "y": ys[index]}
                for index in range(count)
                if isinstance(xs[index], (int, float)) and isinstance(ys[index], (int, float))
            ]
        else:
            return []
    if not isinstance(value, list):
        return []
    points = []
    for point in value:
        if isinstance(point, dict):
            x = point.get("x")
            y = point.get("y")
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        else:
            return []
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return []
        points.append({"x": x, "y": y})
    return points if len(points) >= 3 else []


def has_polygon(record: dict, *keys: str) -> bool:
    return bool(first_polygon(record, *keys))


def first_polygon(record: dict, *keys: str) -> list[dict]:
    if not isinstance(record, dict):
        return []
    geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}
    summary = record.get("geometrySummary") if isinstance(record.get("geometrySummary"), dict) else {}
    for key in keys:
        points = polygon_points(geometry.get(key) or summary.get(key) or record.get(key))
        if points:
            return points
    return []


def bounds_for(record: dict):
    if not isinstance(record, dict):
        return None
    geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}
    summary = record.get("geometrySummary") if isinstance(record.get("geometrySummary"), dict) else {}
    for key in ("bounds", "aimBounds", "clickboxBounds", "convexHullBounds"):
        value = record.get(key) or geometry.get(key) or summary.get(key)
        if isinstance(value, dict):
            return value
    return None


def aim_for(record: dict):
    if not isinstance(record, dict):
        return None
    geometry = record.get("geometry") if isinstance(record.get("geometry"), dict) else {}
    value = record.get("aimPoint") or record.get("aimPointContext") or geometry.get("aimPoint")
    if isinstance(value, dict):
        return value
    return None


def first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def nested(record: dict, *keys: str):
    value = record
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def match_keys(record: dict) -> list[tuple[str, str]]:
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    keys = []
    object_key = record.get("objectKey") or record.get("targetKey") or target.get("objectKey")
    if object_key:
        keys.append(("objectKey", str(object_key)))
    raw_hash = record.get("hash") if record.get("hash") is not None else target.get("hash")
    if raw_hash is not None:
        keys.append(("hash", str(raw_hash)))
    raw_id = first_present(record.get("id"), record.get("rawId"), target.get("id"), target.get("rawId"))
    kind = first_present(record.get("kind"), record.get("layer"), target.get("kind"), target.get("layer"))
    world_x = first_present(record.get("worldX"), nested(record, "targetWorld", "x"), nested(target, "world", "x"))
    world_y = first_present(record.get("worldY"), nested(record, "targetWorld", "y"), nested(target, "world", "y"))
    plane = first_present(record.get("plane"), nested(record, "targetWorld", "plane"), nested(target, "world", "plane"))
    scene_x = first_present(record.get("sceneX"), nested(target, "scene", "x"))
    scene_y = first_present(record.get("sceneY"), nested(target, "scene", "y"))
    if raw_id is not None and world_x is not None and world_y is not None and plane is not None:
        keys.append(("idWorld", f"{raw_id}:{world_x}:{world_y}:{plane}:{kind}"))
    if raw_id is not None and scene_x is not None and scene_y is not None and plane is not None:
        keys.append(("idScene", f"{raw_id}:{scene_x}:{scene_y}:{plane}:{kind}"))
    return keys


def index_by_keys(records: list[dict]) -> dict[tuple[str, str], dict]:
    index = {}
    for record in records:
        for key in match_keys(record):
            index.setdefault(key, record)
    return index


def matches_class(record: dict, class_id: str) -> bool:
    if not class_id or class_id == "all":
        return True
    values = {str(record.get("classId") or ""), str(record.get("category") or "")}
    values.update(str(item) for item in record.get("targetClassIds") or [])
    if class_id == "tree":
        return bool(values & {"tree", "oak_tree", "willow_tree", "yew_tree", "maple_tree", "magic_tree"})
    return class_id in values


def latest_projection_refs(session: Path) -> tuple[list[dict], dict]:
    live_dir = session / "live_packets"
    latest_name = None
    latest_txt = live_dir / "latest_segment.txt"
    try:
        latest_name = latest_txt.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if not latest_name:
        index = read_json(live_dir / "live_packet_index.json")
        latest_name = index.get("latestSegment") or index.get("activeSegment")
    if not latest_name:
        return [], {}
    segment = live_dir / latest_name
    if not segment.exists():
        segment = live_dir / Path(latest_name).name
    latest_packet = {}
    for packet in read_jsonl(segment):
        if packet.get("packetType") == "live_projection_packet.v1":
            latest_packet = packet
    payload = latest_packet.get("payload") if isinstance(latest_packet.get("payload"), dict) else {}
    refs = payload.get("visibleObjectRefs") if isinstance(payload.get("visibleObjectRefs"), list) else []
    return [ref for ref in refs if isinstance(ref, dict)], latest_packet


def latest_candidates(session: Path, class_id: str, top: int) -> list[dict]:
    candidates = read_jsonl(session / LIVE_DIR / "live_candidates.jsonl")
    if not candidates:
        return []
    latest_tick = max((candidate.get("tickId") or candidate.get("tick") or -1) for candidate in candidates)
    filtered = [
        candidate
        for candidate in candidates
        if (candidate.get("tickId") or candidate.get("tick") or -1) == latest_tick and matches_class(candidate, class_id)
    ]
    filtered.sort(key=lambda candidate: int(candidate.get("rank") or 999999))
    return filtered[:top]


def target_summary(record: dict) -> dict:
    nav = record.get("navigation") if isinstance(record.get("navigation"), dict) else {}
    polygon = first_polygon(record, "clickableHull", "clickboxPolygon", "convexHull", "convexHullPolygon", "canvasTilePolygon", "tilePolygon")
    return {
        "objectKey": record.get("objectKey") or record.get("targetKey"),
        "id": record.get("rawId") if record.get("rawId") is not None else record.get("id"),
        "hash": record.get("hash"),
        "name": record.get("name"),
        "classId": record.get("classId"),
        "category": record.get("category"),
        "worldX": record.get("worldX"),
        "worldY": record.get("worldY"),
        "plane": record.get("plane"),
        "sceneX": record.get("sceneX"),
        "sceneY": record.get("sceneY"),
        "aimPoint": aim_for(record),
        "bounds": bounds_for(record),
        "directReachability": record.get("directReachability") or nav.get("directReachability"),
        "targetLiveState": record.get("targetLiveState"),
        "geometrySource": record.get("geometrySource") or nested(record, "geometry", "geometrySource"),
        "hasClickableHull": has_polygon(record, "clickableHull"),
        "hasClickboxPolygon": has_polygon(record, "clickboxPolygon"),
        "hasCanvasTilePolygon": has_polygon(record, "canvasTilePolygon", "tilePolygon"),
        "polygonPointCount": len(polygon),
        "missingHullReason": record.get("clickableHullMissingReason") or nested(record, "geometry", "clickableHullMissingReason"),
    }


def match_record(record: dict, index: dict[tuple[str, str], dict]) -> tuple[dict | None, str | None]:
    for key in match_keys(record):
        if key in index:
            return index[key], key[0]
    return None, None


def rank_bucket(rank) -> str:
    if rank == 1:
        return "rank1"
    if isinstance(rank, (int, float)) and 2 <= rank <= 5:
        return "ranks2to5"
    if isinstance(rank, (int, float)) and 6 <= rank <= 10:
        return "ranks6to10"
    return "ranks11plus"


def has_clickable_hull(record: dict) -> bool:
    return bool(first_polygon(record, "clickableHull", "clickboxPolygon"))


def hull_rank_buckets(records: list[dict]) -> dict:
    buckets = {
        "rank1": 0,
        "ranks2to5": 0,
        "ranks6to10": 0,
        "ranks11plus": 0,
    }
    for record in records:
        if has_clickable_hull(record):
            buckets[rank_bucket(record.get("rank"))] += 1
    return buckets


def build_report(session: Path, class_id: str, top: int) -> dict:
    candidates = latest_candidates(session, class_id, top)
    overlay = read_json(session / LIVE_DIR / "overlay_debug_state.json")
    status = read_json(session / LIVE_DIR / "live_status.json")
    overlay_targets = [item for item in overlay.get("targets") or [] if isinstance(item, dict)]
    compact_refs, projection_packet = latest_projection_refs(session)

    overlay_index = index_by_keys(overlay_targets)
    compact_index = index_by_keys(compact_refs)
    compact_hull_refs = [ref for ref in compact_refs if first_polygon(ref, "clickableHull", "clickboxPolygon", "convexHull", "convexHullPolygon", "canvasTilePolygon")]
    compact_hull_key_set = {key for ref in compact_hull_refs for key in match_keys(ref)}
    top_key_set = {key for candidate in candidates for key in match_keys(candidate)}
    overlay_key_set = {key for target in overlay_targets for key in match_keys(target)}

    rows = []
    for candidate in candidates:
        overlay_target, overlay_match = match_record(candidate, overlay_index)
        compact_ref, compact_match = match_record(candidate, compact_index)
        compact_hull_ref = compact_ref if compact_ref and first_polygon(compact_ref, "clickableHull", "clickboxPolygon", "convexHull", "convexHullPolygon", "canvasTilePolygon") else None
        any_compact_same_tile = compact_ref is not None
        rows.append(
            {
                "rank": candidate.get("rank"),
                "isBest": candidate.get("rank") == 1,
                "isNearest": bool((overlay_target or {}).get("isNearest")),
                **target_summary(candidate),
                "candidateHasHull": first_polygon(candidate, "clickableHull", "clickboxPolygon", "convexHull", "convexHullPolygon", "canvasTilePolygon") != [],
                "overlayPresent": overlay_target is not None,
                "overlayMatchType": overlay_match,
                "overlay": target_summary(overlay_target or {}),
                "matchedCompactPacketGeometryByObjectKey": compact_match == "objectKey" and compact_hull_ref is not None,
                "matchedCompactPacketGeometryByFallbackKey": compact_match not in (None, "objectKey") and compact_hull_ref is not None,
                "anyCompactPacketGeometryForSameTarget": compact_hull_ref is not None,
                "anyCompactPacketRefForSameTargetOrTile": any_compact_same_tile,
                "compactMatchType": compact_match,
                "compactRef": target_summary(compact_ref or {}),
            }
        )

    top_with_hull = sum(1 for row in rows if row["overlay"].get("hasClickableHull") or row["overlay"].get("hasClickboxPolygon") or row["candidateHasHull"])
    top_bounds_only = sum(1 for row in rows if row.get("overlayPresent") and not (row["overlay"].get("hasClickableHull") or row["overlay"].get("hasClickboxPolygon")))
    compact_hulls_unused_by_top = len([ref for ref in compact_hull_refs if not any(key in top_key_set for key in match_keys(ref))])
    overlay_hulls_not_top = len([target for target in overlay_targets if first_polygon(target, "clickableHull", "clickboxPolygon") and not any(key in top_key_set for key in match_keys(target))])
    overlay_hull_targets = [target for target in overlay_targets if has_clickable_hull(target)]
    best_overlay_target = next((target for target in overlay_targets if target.get("isBest") or target.get("rank") == 1), None)
    nearest_overlay_target = next((target for target in overlay_targets if target.get("isNearest")), None)
    best_has_hull = has_clickable_hull(best_overlay_target or {})
    nearest_has_hull = has_clickable_hull(nearest_overlay_target or {})
    top_targets_with_hull = [
        {
            "rank": target.get("rank"),
            "name": target.get("name"),
            "id": target.get("id"),
            "objectKey": target.get("objectKey"),
            "worldX": target.get("worldX"),
            "worldY": target.get("worldY"),
            "plane": target.get("plane"),
            "geometrySource": target.get("geometrySource"),
        }
        for target in sorted(overlay_hull_targets, key=lambda item: int(item.get("rank") or 999999))[:top]
    ]

    conclusions = []
    if not compact_hull_refs:
        conclusions.append("no Java compact projection refs in the latest segment contain hull polygons; check hull emission config or Java clickbox availability")
    elif top_with_hull == 0 and compact_hulls_unused_by_top:
        conclusions.append("geometry cap/order mismatch: compact packets contain hulls, but they are not attached to top overlay candidates")
    elif any(row["anyCompactPacketGeometryForSameTarget"] and not row["overlay"].get("hasClickableHull") for row in rows):
        conclusions.append("parser/preservation mismatch: compact packet geometry exists for a top candidate but overlay_debug_state is bounds-only")
    elif top_with_hull > 0 and overlay_hulls_not_top:
        conclusions.append("overlay state has hulls for some non-top targets; verify overlay target cap and ranking order")
    elif top_with_hull > 0:
        conclusions.append("top overlay candidates have hull geometry; if visual hulls are still corner-only, inspect RuneLite overlay drawing/mode")
    else:
        conclusions.append("top candidates are bounds-only and no matching compact hull refs were found for them")
    if best_has_hull:
        conclusions.append("best candidate has clickable hull geometry")
    elif best_overlay_target is not None:
        conclusions.append("best candidate is bounds-only")
    if nearest_has_hull:
        conclusions.append("nearest candidate has clickable hull geometry")
    elif nearest_overlay_target is not None:
        conclusions.append("nearest candidate is bounds-only")
    if overlay_hull_targets and top_with_hull == 0:
        conclusions.append("hulls are going to lower-priority overlay targets")
    if status.get("compactLiveHullsEmitted") is not None and int(status.get("compactLiveHullsEmitted") or 0) <= len(overlay_hull_targets):
        conclusions.append("Java emitted only the currently available hull refs; increase clickbox availability/fallbacks or geometry cap only if needed")
    if compact_hull_refs and not (status.get("candidateHullDirectMatches") or status.get("candidateHullFallbackMatches")):
        conclusions.append("matching appears weak: compact hull refs exist but no candidate hull matches were recorded")

    return {
        "schema": SCHEMA,
        "sessionPath": str(session),
        "classId": class_id,
        "top": top,
        "latestProjectionTick": projection_packet.get("tick"),
        "latestProjectionSequence": projection_packet.get("sequence"),
        "summary": {
            "topCandidates": len(candidates),
            "topCandidatesWithHull": top_with_hull,
            "topCandidatesBoundsOnly": top_bounds_only,
            "compactRefs": len(compact_refs),
            "compactRefsWithHull": len(compact_hull_refs),
            "compactRefsWithHullUnusedByTopCandidates": compact_hulls_unused_by_top,
            "overlayTargets": len(overlay_targets),
            "overlayTargetsWithHull": len(overlay_hull_targets),
            "overlayTargetsWithHullNotInTopCandidates": overlay_hulls_not_top,
            "bestTargetHasHull": best_has_hull,
            "nearestTargetHasHull": nearest_has_hull,
            "hullsByRankBucket": hull_rank_buckets(overlay_targets),
            "topTargetsWithHull": top_targets_with_hull,
            "candidateHullDirectMatches": status.get("candidateHullDirectMatches"),
            "candidateHullFallbackMatches": status.get("candidateHullFallbackMatches"),
            "candidateHullMissing": status.get("candidateHullMissing"),
            "compactHullRefsAvailable": status.get("compactHullRefsAvailable"),
            "compactHullRefsUnused": status.get("compactHullRefsUnused"),
            "compactLiveGeometryMaxRefs": status.get("compactLiveGeometryMaxRefs"),
            "compactLiveGeometryRefsWithPolygons": status.get("compactLiveGeometryRefsWithPolygons"),
            "compactLiveGeometryRefsSkippedByCap": status.get("compactLiveGeometryRefsSkippedByCap"),
            "compactLiveHullsEmitted": status.get("compactLiveHullsEmitted"),
            "compactLiveHullDroppedByCap": status.get("compactLiveHullDroppedByCap"),
            "compactLiveHullDroppedNullClickbox": status.get("compactLiveHullDroppedNullClickbox"),
        },
        "rows": rows,
        "conclusions": conclusions,
    }


def print_report(report: dict) -> None:
    print("Overlay Geometry Diagnostic")
    print(f"session: {report.get('sessionPath')}")
    print(f"projection: tick={report.get('latestProjectionTick')} sequence={report.get('latestProjectionSequence')}")
    summary = report.get("summary") or {}
    print(
        "summary: "
        f"top={summary.get('topCandidates')} topHull={summary.get('topCandidatesWithHull')} "
        f"topBoundsOnly={summary.get('topCandidatesBoundsOnly')} compactHullRefs={summary.get('compactRefsWithHull')} "
        f"overlayTargets={summary.get('overlayTargets')} overlayHull={summary.get('overlayTargetsWithHull')} "
        f"unusedCompactHulls={summary.get('compactRefsWithHullUnusedByTopCandidates')}"
    )
    print(
        "matching: "
        f"direct={summary.get('candidateHullDirectMatches')} fallback={summary.get('candidateHullFallbackMatches')} "
        f"missing={summary.get('candidateHullMissing')} compactAvailable={summary.get('compactHullRefsAvailable')} "
        f"compactUnused={summary.get('compactHullRefsUnused')}"
    )
    buckets = summary.get("hullsByRankBucket") or {}
    print(
        "priority: "
        f"bestHull={summary.get('bestTargetHasHull')} nearestHull={summary.get('nearestTargetHasHull')} "
        f"rank1={buckets.get('rank1')} ranks2-5={buckets.get('ranks2to5')} "
        f"ranks6-10={buckets.get('ranks6to10')} ranks11+={buckets.get('ranks11plus')}"
    )
    print(
        "java cap: "
        f"maxRefs={summary.get('compactLiveGeometryMaxRefs')} refsWithPolygons={summary.get('compactLiveGeometryRefsWithPolygons')} "
        f"skippedByCap={summary.get('compactLiveGeometryRefsSkippedByCap')} hullsEmitted={summary.get('compactLiveHullsEmitted')} "
        f"nullClickbox={summary.get('compactLiveHullDroppedNullClickbox')}"
    )
    if summary.get("topTargetsWithHull"):
        print("top targets with hull:")
        for target in summary.get("topTargetsWithHull") or []:
            print(
                f"  #{target.get('rank')} {target.get('name')} id={target.get('id')} "
                f"world={target.get('worldX')},{target.get('worldY')},{target.get('plane')} "
                f"source={target.get('geometrySource')}"
            )
    print()
    for row in report.get("rows") or []:
        overlay = row.get("overlay") or {}
        compact = row.get("compactRef") or {}
        print(
            f"#{row.get('rank')} {row.get('name')} id={row.get('id')} key={row.get('objectKey')} "
            f"world={row.get('worldX')},{row.get('worldY')},{row.get('plane')} scene={row.get('sceneX')},{row.get('sceneY')} "
            f"reach={row.get('directReachability')} live={row.get('targetLiveState')}"
        )
        print(
            f"  candidate: hull={row.get('candidateHasHull')} aim={row.get('aimPoint')} bounds={row.get('bounds')} "
            f"source={row.get('geometrySource')} points={row.get('polygonPointCount')}"
        )
        print(
            f"  overlay: present={row.get('overlayPresent')} match={row.get('overlayMatchType')} "
            f"hull={overlay.get('hasClickableHull') or overlay.get('hasClickboxPolygon')} source={overlay.get('geometrySource')} "
            f"points={overlay.get('polygonPointCount')} reason={overlay.get('missingHullReason')}"
        )
        print(
            f"  compact: match={row.get('compactMatchType')} hasHull={row.get('anyCompactPacketGeometryForSameTarget')} "
            f"source={compact.get('geometrySource')} points={compact.get('polygonPointCount')}"
        )
    print()
    print("Conclusion:")
    for conclusion in report.get("conclusions") or []:
        print(f"- {conclusion}")


def parse_args():
    parser = argparse.ArgumentParser(description="Compare top overlay candidates with compact projection hull geometry.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory for --latest-session.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--class-id", default="tree", help="Class id to inspect. Default: tree.")
    parser.add_argument("--top", type=int, default=25, help="Number of top candidates to inspect. Default: 25.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.session:
        session = Path(args.session).expanduser()
    elif args.latest_session:
        session = find_newest_session(args.sessions_dir)
        if session is None:
            print("No telemetry session found.", file=sys.stderr)
            return 2
    else:
        print("Use --session or --latest-session.", file=sys.stderr)
        return 2

    report = build_report(session, args.class_id, max(0, args.top))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

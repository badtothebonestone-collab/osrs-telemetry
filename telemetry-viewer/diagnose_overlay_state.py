from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "overlay_state_diagnostic.v1"
TREE_CLASSES = {"tree", "oak_tree", "willow_tree", "maple_tree", "yew_tree", "magic_tree"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def live_dir(session: Path) -> Path:
    return session / "interaction_geometry" / "live"


def live_paths(session: Path) -> dict[str, Path]:
    root = live_dir(session)
    return {
        "overlay": root / "overlay_debug_state.json",
        "candidates": root / "live_candidates.jsonl",
        "context": root / "live_context_index.json",
        "navigation": root / "live_navigation_summary.json",
        "status": root / "live_status.json",
    }


def read_json(path: Path, warnings: list[str], label: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
    except FileNotFoundError:
        warnings.append(f"{label} missing: {path}")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{label} unreadable: {exc}")
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path, warnings: list[str], label: str) -> list[dict]:
    records: list[dict] = []
    malformed = 0
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except FileNotFoundError:
        warnings.append(f"{label} missing: {path}")
    except OSError as exc:
        warnings.append(f"{label} unreadable: {exc}")
    if malformed:
        warnings.append(f"{label} had {malformed} malformed line(s)")
    return records


def resolve_session(args) -> Path:
    if args.session:
        return Path(args.session).expanduser().resolve()
    session = find_newest_session(get_sessions_dir(args.sessions_dir))
    if session is None:
        raise RuntimeError("No telemetry sessions found.")
    return session.resolve()


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def candidate_id(record: dict) -> Any:
    return first_value(record.get("rawId"), record.get("id"))


def target_key(record: dict | None) -> tuple | None:
    if not isinstance(record, dict):
        return None
    object_key = record.get("objectKey")
    if object_key:
        return ("objectKey", object_key)
    return (
        "tile",
        candidate_id(record),
        record.get("hash"),
        record.get("worldX"),
        record.get("worldY"),
        record.get("plane"),
        record.get("sceneX"),
        record.get("sceneY"),
        record.get("classId"),
    )


def tree_like(record: dict) -> bool:
    class_id = str(record.get("classId") or "").lower()
    category = str(record.get("category") or record.get("targetCategory") or "").lower()
    name = str(record.get("name") or "").lower()
    return class_id in TREE_CLASSES or category == "tree" or any(part in name for part in ("tree", "oak", "willow"))


def matches_class(record: dict, class_id: str | None) -> bool:
    if not class_id:
        return True
    normalized = class_id.lower()
    if normalized == "tree":
        return tree_like(record)
    return str(record.get("classId") or "").lower() == normalized


def passes_filters(record: dict, args) -> bool:
    if not matches_class(record, args.class_id):
        return False
    if args.name_contains and args.name_contains.lower() not in str(record.get("name") or "").lower():
        return False
    if args.id is not None and candidate_id(record) != args.id:
        return False
    navigation = record.get("navigation") if isinstance(record.get("navigation"), dict) else {}
    direct = record.get("directReachability") or navigation.get("directReachability")
    if args.show_blocked and direct != "blocked":
        return False
    if args.show_reachable and direct != "reachable":
        return False
    if args.show_unknown and direct != "unknown":
        return False
    return True


def sort_key(record: dict) -> tuple:
    rank = record.get("rank")
    distance = record.get("distanceTiles", record.get("targetDistanceChebyshev"))
    score = record.get("qualityScore", record.get("score"))
    return (
        rank if isinstance(rank, (int, float)) else 999999,
        distance if isinstance(distance, (int, float)) else 999999,
        -(score if isinstance(score, (int, float)) else 0),
    )


def reachability_value(record: dict) -> Any:
    navigation = record.get("navigation") if isinstance(record.get("navigation"), dict) else {}
    return first_value(record.get("directReachability"), navigation.get("directReachability"))


def label_for(record: dict) -> str:
    label = record.get("overlayLabel")
    if isinstance(label, str) and label:
        return label
    name = record.get("name") or record.get("classId") or "target"
    distance = record.get("distanceTiles", record.get("targetDistanceChebyshev"))
    direct = reachability_value(record)
    live = record.get("targetLiveState")
    parts = [str(name)]
    if isinstance(distance, (int, float)):
        parts.append(f"d{distance:g}")
    if direct == "reachable":
        parts.append("R")
    elif direct == "blocked":
        parts.append("BLOCK")
    elif direct == "unknown":
        parts.append("?")
    if live == "live_assumed":
        parts.append("assumed")
    elif live in ("depleted_or_stump", "stale", "recently_despawned"):
        parts.append(str(live))
    return " ".join(parts)


def color_for(record: dict) -> str:
    color = record.get("overlayColor")
    if isinstance(color, str) and color:
        return color
    direct = reachability_value(record)
    live = record.get("targetLiveState")
    if live in ("depleted_or_stump", "stale", "recently_despawned"):
        return "gray"
    if direct == "blocked":
        return "red"
    if direct == "reachable":
        return "green"
    return "yellow"


def has_polygon(record: dict, key: str) -> bool:
    value = record.get(key)
    if isinstance(value, dict):
        value = value.get("points")
    return isinstance(value, list) and len(value) >= 3


def geometry_source_for(record: dict) -> str:
    if has_polygon(record, "clickableHull"):
        return "clickableHull"
    if has_polygon(record, "clickboxPolygon"):
        return "clickboxPolygon"
    if has_polygon(record, "convexHull"):
        return "convexHull"
    if has_polygon(record, "convexHullPolygon"):
        return "convexHullPolygon"
    if has_polygon(record, "canvasTilePolygon"):
        return "canvasTilePolygon"
    if isinstance(record.get("bounds"), dict):
        return "bounds"
    if isinstance(record.get("aimPoint"), dict):
        return "aimPoint"
    return "none"


def geometry_counts(records: list[dict]) -> dict:
    sources = [geometry_source_for(record) for record in records if isinstance(record, dict)]
    source_counts = {}
    for source in sources:
        source_counts[source] = source_counts.get(source, 0) + 1
    first_missing_reason = None
    for record in records:
        if isinstance(record, dict) and not record.get("clickableHullAvailable") and record.get("clickableHullMissingReason"):
            first_missing_reason = record.get("clickableHullMissingReason")
            break
    return {
        "clickableHullAvailableCount": sum(1 for record in records if isinstance(record, dict) and bool(record.get("clickableHullAvailable"))),
        "clickableHullFieldCount": sum(1 for record in records if isinstance(record, dict) and has_polygon(record, "clickableHull")),
        "clickboxPolygonCount": sum(1 for record in records if isinstance(record, dict) and has_polygon(record, "clickboxPolygon")),
        "convexHullCount": sum(1 for record in records if isinstance(record, dict) and (has_polygon(record, "convexHull") or has_polygon(record, "convexHullPolygon"))),
        "canvasTilePolygonCount": sum(1 for record in records if isinstance(record, dict) and has_polygon(record, "canvasTilePolygon")),
        "boundsOnlyCount": sources.count("bounds"),
        "aimOnlyCount": sources.count("aimPoint"),
        "missingHullCount": sum(1 for record in records if isinstance(record, dict) and not record.get("clickableHullAvailable")),
        "geometrySourceCounts": source_counts,
        "firstMissingHullReason": first_missing_reason,
    }


def compact_geometry_config(overlay: dict, status: dict) -> dict:
    summary = overlay.get("summary") if isinstance(overlay.get("summary"), dict) else {}
    return {
        "compactLiveIncludeHeavyGeometry": first_value(
            status.get("compactLiveIncludeHeavyGeometry"),
            summary.get("compactLiveIncludeHeavyGeometry"),
        ),
        "compactLiveIncludeClickableHull": first_value(
            status.get("compactLiveIncludeClickableHull"),
            summary.get("compactLiveIncludeClickableHull"),
        ),
        "compactLiveIncludeCanvasTilePolygon": first_value(
            status.get("compactLiveIncludeCanvasTilePolygon"),
            summary.get("compactLiveIncludeCanvasTilePolygon"),
        ),
        "compactLiveIncludeConvexHull": first_value(
            status.get("compactLiveIncludeConvexHull"),
            summary.get("compactLiveIncludeConvexHull"),
        ),
        "compactLiveGeometryMaxRefs": first_value(
            status.get("compactLiveGeometryMaxRefs"),
            summary.get("compactLiveGeometryMaxRefs"),
        ),
        "compactLiveGeometryRefsWithPolygons": first_value(
            status.get("compactLiveGeometryRefsWithPolygons"),
            summary.get("compactLiveGeometryRefsWithPolygons"),
        ),
        "compactLiveGeometryRefsSkippedByCap": first_value(
            status.get("compactLiveGeometryRefsSkippedByCap"),
            summary.get("compactLiveGeometryRefsSkippedByCap"),
        ),
        "compactLiveGeometryCapHit": first_value(
            status.get("compactLiveGeometryCapHit"),
            summary.get("compactLiveGeometryCapHit"),
        ),
    }


def latest_tick_from_status(status: dict) -> Any:
    return first_value(status.get("latestTickProcessed"), status.get("lastProcessedTick"), status.get("latestRawTickSeen"), status.get("latestTick"))


def build_report(session: Path, args) -> dict:
    warnings: list[str] = []
    paths = live_paths(session)
    overlay = read_json(paths["overlay"], warnings, "overlay_debug_state")
    candidates = read_jsonl(paths["candidates"], warnings, "live_candidates")
    context = read_json(paths["context"], warnings, "live_context_index")
    navigation = read_json(paths["navigation"], warnings, "live_navigation_summary")
    status = read_json(paths["status"], warnings, "live_status")

    overlay_targets = overlay.get("targets") if isinstance(overlay.get("targets"), list) else []
    overlay_geometry = geometry_counts([target for target in overlay_targets if isinstance(target, dict)])
    geometry_config = compact_geometry_config(overlay, status)
    overlay_by_key = {target_key(target): target for target in overlay_targets if target_key(target) is not None}
    filtered_candidates = [candidate for candidate in candidates if passes_filters(candidate, args)]
    filtered_candidates.sort(key=sort_key)
    latest_candidate_tick = max((candidate.get("tickId") for candidate in candidates if isinstance(candidate.get("tickId"), int)), default=None)
    overlay_tick = overlay.get("latestTick")
    status_tick = latest_tick_from_status(status)
    context_tick = context.get("latestTick")
    freshness_reference = max([tick for tick in (latest_candidate_tick, status_tick, context_tick) if isinstance(tick, (int, float))], default=None)
    stale = bool(isinstance(overlay_tick, (int, float)) and isinstance(freshness_reference, (int, float)) and overlay_tick < freshness_reference)

    rows = []
    mismatch_count = 0
    blocked_count = 0
    missing_overlay_count = 0
    for candidate in filtered_candidates[: max(1, args.top)]:
        overlay_target = overlay_by_key.get(target_key(candidate))
        candidate_nav = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
        overlay_direct = reachability_value(overlay_target or {})
        candidate_direct = candidate_nav.get("directReachability")
        if candidate_direct == "blocked":
            blocked_count += 1
        if overlay_target is None:
            missing_overlay_count += 1
        elif overlay_direct != candidate_direct:
            mismatch_count += 1
        rows.append(
            {
                "rank": candidate.get("rank"),
                "name": candidate.get("name"),
                "id": candidate_id(candidate),
                "classId": candidate.get("classId"),
                "category": candidate.get("category"),
                "worldX": candidate.get("worldX"),
                "worldY": candidate.get("worldY"),
                "plane": candidate.get("plane"),
                "sceneX": candidate.get("sceneX"),
                "sceneY": candidate.get("sceneY"),
                "distanceTiles": candidate.get("distanceTiles", candidate.get("targetDistanceChebyshev")),
                "candidateDirectReachability": candidate_direct,
                "overlayDirectReachability": overlay_direct,
                "targetLiveState": candidate.get("targetLiveState"),
                "livenessInterpretation": (overlay_target or {}).get("livenessInterpretation"),
                "overlayLabel": label_for(overlay_target or candidate),
                "overlayColor": color_for(overlay_target or candidate),
                "overlayGeometrySource": geometry_source_for(overlay_target or {}),
                "clickableHullAvailable": (overlay_target or {}).get("clickableHullAvailable"),
                "clickableHullMissingReason": (overlay_target or {}).get("clickableHullMissingReason"),
                "reachabilityEvidence": candidate_nav.get("reachabilityEvidence") or [],
                "pathLengthTiles": candidate_nav.get("pathLengthTiles"),
                "interactionRadiusTiles": candidate_nav.get("interactionRadiusTiles"),
                "targetInCollisionWindow": candidate_nav.get("targetInCollisionWindow"),
                "missingNavigationFields": candidate_nav.get("missingNavigationFields") or [],
                "overlayPresent": overlay_target is not None,
            }
        )

    conclusions = []
    if not overlay:
        conclusions.append("missing fields: overlay_debug_state.json was not readable")
    if stale:
        conclusions.append("overlay stale: overlay tick is older than live status/context/candidate tick")
    if mismatch_count:
        conclusions.append("overlay label mismatch: overlay reachability differs from live_candidates for matching target keys")
    if blocked_count:
        conclusions.append("reachability actually blocked for one or more filtered candidates")
    if missing_overlay_count and filtered_candidates:
        conclusions.append("overlay/candidate mismatch: some filtered candidates are not present in overlay target cap or key set")
    if overlay_targets and overlay_geometry.get("clickableHullAvailableCount", 0) <= 0:
        if not geometry_config.get("compactLiveIncludeClickableHull") and not geometry_config.get("compactLiveIncludeHeavyGeometry"):
            conclusions.append("hull geometry is not being emitted by compact packets; enable compactLiveIncludeClickableHull or compactLiveIncludeHeavyGeometry")
        else:
            conclusions.append("hull geometry was requested, but overlay_debug_state has no clickbox polygons for inspected targets")
    if not filtered_candidates:
        conclusions.append("classification/profile mismatch or no matching candidates for the requested filters")
    if not conclusions:
        conclusions.append("overlay and live candidate reachability are consistent for the inspected rows")
    if overlay_geometry.get("clickableHullAvailableCount", 0) > 0:
        conclusions.append("hull geometry is present in overlay_debug_state; if hulls are not visible, check RuneLite overlay geometry mode/drawing")

    return {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(session),
        "overlayTick": overlay_tick,
        "liveStatusLatestTick": status_tick,
        "candidateLatestTick": latest_candidate_tick,
        "contextLatestTick": context_tick,
        "overlayStateStale": stale,
        "overlayTargetCount": len(overlay_targets),
        "candidateCount": len(candidates),
        "filteredCandidateCount": len(filtered_candidates),
        "overlayGeometrySummary": overlay_geometry,
        "compactGeometryConfig": geometry_config,
        "navigationSummary": {
            "status": navigation.get("status"),
            "collisionKnown": navigation.get("collisionKnown"),
            "collisionWindowAvailable": navigation.get("collisionWindowAvailable"),
            "collisionWindowRadius": navigation.get("collisionWindowRadius"),
            "reachabilityComputed": navigation.get("reachabilityComputed"),
        },
        "rows": rows,
        "warnings": warnings,
        "conclusions": conclusions,
        "paths": {key: str(path) for key, path in paths.items()},
    }


def print_report(report: dict) -> None:
    print("Overlay State Diagnostic")
    print(f"session: {report.get('sessionPath')}")
    print(
        f"ticks: overlay={report.get('overlayTick')} "
        f"status={report.get('liveStatusLatestTick')} "
        f"candidates={report.get('candidateLatestTick')} "
        f"context={report.get('contextLatestTick')}"
    )
    print(
        f"targets: overlay={report.get('overlayTargetCount')} "
        f"candidates={report.get('candidateCount')} "
        f"filtered={report.get('filteredCandidateCount')}"
    )
    geom = report.get("overlayGeometrySummary") or {}
    print(
        "geometry: "
        f"hulls={geom.get('clickableHullAvailableCount')} "
        f"clickableHullField={geom.get('clickableHullFieldCount')} "
        f"clickbox={geom.get('clickboxPolygonCount')} "
        f"convex={geom.get('convexHullCount')} "
        f"tile={geom.get('canvasTilePolygonCount')} "
        f"boundsOnly={geom.get('boundsOnlyCount')} "
        f"aimOnly={geom.get('aimOnlyCount')} "
        f"missingHull={geom.get('missingHullCount')}"
    )
    if geom.get("geometrySourceCounts"):
        source_text = ", ".join(f"{key}={value}" for key, value in sorted(geom["geometrySourceCounts"].items()))
        print(f"geometry sources: {source_text}")
    if geom.get("firstMissingHullReason"):
        print(f"first missing hull reason: {geom.get('firstMissingHullReason')}")
    config = report.get("compactGeometryConfig") or {}
    print(
        "geometry config: "
        f"clickableHull={config.get('compactLiveIncludeClickableHull')} "
        f"heavy={config.get('compactLiveIncludeHeavyGeometry')} "
        f"convex={config.get('compactLiveIncludeConvexHull')} "
        f"tile={config.get('compactLiveIncludeCanvasTilePolygon')} "
        f"maxRefs={config.get('compactLiveGeometryMaxRefs')} "
        f"emitted={config.get('compactLiveGeometryRefsWithPolygons')} "
        f"skippedByCap={config.get('compactLiveGeometryRefsSkippedByCap')} "
        f"capHit={config.get('compactLiveGeometryCapHit')}"
    )
    nav = report.get("navigationSummary") or {}
    print(
        "navigation: "
        f"status={nav.get('status')} collisionKnown={nav.get('collisionKnown')} "
        f"window={nav.get('collisionWindowAvailable')} radius={nav.get('collisionWindowRadius')}"
    )
    print()
    for row in report.get("rows") or []:
        print(
            f"#{row.get('rank')} {row.get('name')} id={row.get('id')} class={row.get('classId')} "
            f"world={row.get('worldX')},{row.get('worldY')},{row.get('plane')} "
            f"scene={row.get('sceneX')},{row.get('sceneY')} d={row.get('distanceTiles')} "
            f"candidateReach={row.get('candidateDirectReachability')} overlayReach={row.get('overlayDirectReachability')} "
            f"live={row.get('targetLiveState')} label='{row.get('overlayLabel')}' color={row.get('overlayColor')} "
            f"geometry={row.get('overlayGeometrySource')} hull={row.get('clickableHullAvailable')} "
            f"path={row.get('pathLengthTiles')} radius={row.get('interactionRadiusTiles')} inWindow={row.get('targetInCollisionWindow')}"
        )
        if row.get("clickableHullMissingReason"):
            print(f"  hull: {row.get('clickableHullMissingReason')}")
        evidence = row.get("reachabilityEvidence") or []
        if evidence:
            print(f"  evidence: {'; '.join(str(item) for item in evidence[:3])}")
        missing = row.get("missingNavigationFields") or []
        if missing:
            print(f"  missing navigation: {', '.join(str(item) for item in missing)}")
    if report.get("warnings"):
        print()
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    print()
    print("Conclusion:")
    for conclusion in report.get("conclusions") or []:
        print(f"- {conclusion}")


def parse_args():
    parser = argparse.ArgumentParser(description="Compare overlay_debug_state.json with live candidates and context.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--class-id", default="tree", help="Class id to inspect. 'tree' includes tree-like classes. Default: tree.")
    parser.add_argument("--name-contains", help="Filter inspected candidates by name text, for example Oak.")
    parser.add_argument("--id", type=int, help="Filter inspected candidates by object id.")
    parser.add_argument("--show-blocked", action="store_true", help="Show only candidates with blocked reachability.")
    parser.add_argument("--show-reachable", action="store_true", help="Show only candidates with reachable reachability.")
    parser.add_argument("--show-unknown", action="store_true", help="Show only candidates with unknown reachability.")
    parser.add_argument("--top", type=int, default=10, help="Number of candidates to inspect. Default: 10.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    report = build_report(session, args)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=False))
    else:
        print_report(report)
    return 0 if report.get("rows") or not report.get("warnings") else 1


if __name__ == "__main__":
    raise SystemExit(main())

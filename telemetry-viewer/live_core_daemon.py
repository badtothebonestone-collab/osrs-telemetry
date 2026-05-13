from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import brain_core
import context_service
import intent_stabilizer
import live_target_processor as live
from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "live_core_daemon.v1"
OVERLAY_INTENT_SCHEMA = "overlay_intent_state.v1"
HEALTH_SCHEMA = context_service.HEALTH_SCHEMA
STATUS_SCHEMA = context_service.STATUS_SCHEMA
READ_ONLY_NOTES = [
    "This daemon is read-only telemetry/context infrastructure.",
    "It does not click, type, move, invoke menus, execute actions, or mutate game/client state.",
    "Daily mode keeps context in memory and avoids rolling live JSON files unless debug writes are enabled.",
]
DEFAULT_CONTEXT_NEEDS = [
    "baseline",
    "best:tree",
    "nearest:tree",
    "reachability:tree",
    "inventory",
    "activity",
    "liveness",
    "navigation_readiness",
    "events",
    "diagnostics",
    "task_summary",
]
OVERLAY_MODES = {"intent", "candidates", "debug"}
DAILY_MODE_COMPACT_PACKETS = "compact-packets"
DAILY_MODE_SNAPSHOT_NO_FILES = "snapshot-no-files"
DAILY_MODES = {DAILY_MODE_COMPACT_PACKETS, DAILY_MODE_SNAPSHOT_NO_FILES}


def utc_now() -> str:
    return live.utc_now()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def resolve_session(args: argparse.Namespace) -> Path:
    if args.session:
        session = Path(args.session).expanduser()
        if not session.exists():
            raise RuntimeError(f"Session does not exist: {session}")
        return session.resolve()
    if not args.latest_session:
        raise RuntimeError("Pass --session or --latest-session.")
    session = find_newest_session(get_sessions_dir(getattr(args, "sessions_dir", None)))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(getattr(args, 'sessions_dir', None))}")
    return session.resolve()


def compact_packet_available(session: Path) -> bool:
    state = live.compact_packet_state(session)
    return bool(state.get("available") and state.get("recent"))


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def safe_json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def compact_packet_file_write_state(session: Path) -> dict:
    manifest = safe_json_dict(session / "manifest.json")
    packet_dir = session / "live_packets"
    packet_state = live.compact_packet_state(session)
    manifest_enabled = boolish(manifest.get("compactLivePacketFilesEnabled"))
    if manifest_enabled is not None:
        writing = manifest_enabled
    else:
        writing = False
    return {
        "compactPacketFilesWriting": writing,
        "compactPacketFilesEnabledInManifest": manifest_enabled,
        "compactPacketFilesAvailable": bool(packet_state.get("available")),
        "compactPacketFilesRecent": bool(packet_state.get("recent")),
        "compactPacketFileCount": len(list(packet_dir.glob("live-*.ndjson"))) if packet_dir.exists() else 0,
    }


def brain_state_scope(session: Path, task: str, goal_count: int | None) -> dict:
    resource_group = brain_core.task_resource_group(task)
    return {
        "sessionPath": str(session.resolve()),
        "task": task,
        "goalCount": goal_count,
        "resourceGroup": resource_group.get("id") or ("woodcutting_logs" if task == "woodcutting" else None),
    }


def load_daemon_brain_state(session: Path, args: argparse.Namespace) -> tuple[dict, str | None]:
    state = brain_core.load_state(args.brain_state_file, args.brain_task, args.goal_count, reset=bool(args.reset_brain_state))
    expected = brain_state_scope(session, args.brain_task, args.goal_count)
    warning = None
    path_warning = brain_core.state_file_path_warning(args.brain_state_file)
    if path_warning:
        warning = path_warning
    if args.brain_state_file and not args.reset_brain_state:
        actual = state.get("brainStateScope") if isinstance(state.get("brainStateScope"), dict) else {}
        expanded_state_path = brain_core.expand_state_path(args.brain_state_file)
        state_path_exists = bool(expanded_state_path and expanded_state_path.exists())
        mismatch = [
            key
            for key in ("sessionPath", "task", "goalCount", "resourceGroup")
            if actual.get(key) is not None and actual.get(key) != expected.get(key)
        ]
        if mismatch or (state_path_exists and not actual and (state.get("latestTick") is not None or state.get("resourceProgress"))):
            warning = "; ".join(item for item in (warning, "brain state scope changed; progress baseline was reset") if item)
            state = brain_core.default_state(args.brain_task, args.goal_count)
    elif args.reset_brain_state:
        warning = "; ".join(item for item in (warning, "brain state reset requested; progress baseline will start from the next inventory snapshot") if item)
    state["sessionPath"] = expected["sessionPath"]
    state["brainStateScope"] = expected
    state["goalResourceGroup"] = expected["resourceGroup"]
    return state, warning


def persist_daemon_brain_state(path: str | None, state: dict, session: Path, args: argparse.Namespace) -> None:
    if not path:
        return
    expected = brain_state_scope(session, args.brain_task, args.goal_count)
    state["sessionPath"] = expected["sessionPath"]
    state["brainStateScope"] = expected
    state["goalResourceGroup"] = expected["resourceGroup"]
    brain_core.write_state(path, state)


def brain_status_fields(state: dict, reset_applied: bool) -> dict:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    return {
        "brainResetApplied": bool(reset_applied),
        "brainBaselineEstablished": bool(progress.get("baselineEstablished") or state.get("baselineEstablished")),
        "brainBaselineTick": progress.get("baselineTick") or state.get("brainBaselineTick"),
        "brainLastProcessedInventorySignature": progress.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature"),
        "brainLastProcessedInventoryTick": progress.get("lastProcessedInventoryTick") or state.get("lastProcessedInventoryTick"),
        "brainObservedGained": None,
        "brainObservedRemoved": None,
        "brainCurrentHeldCount": progress.get("currentHeldCount"),
        "brainBaselineHeldCount": progress.get("baselineHeldCount") if progress.get("baselineHeldCount") is not None else state.get("baselineHeldCount"),
        "brainHasValidPostBaselineProgressHistory": False,
        "brainProgressStateRepaired": bool(progress.get("progressStateRepaired")),
        "brainProgressRepairReason": progress.get("repairReason"),
        "brainCurrentInventorySignature": progress.get("currentInventorySignature"),
        "brainCurrentSnapshotValid": progress.get("currentSnapshotValid"),
        "brainPreviousInventorySnapshotAvailable": progress.get("previousInventorySnapshotAvailable"),
        "progressInvalidSnapshotCount": progress.get("progressInvalidSnapshotCount", 0),
        "progressRetainedPreviousCount": progress.get("progressRetainedPreviousCount", 0),
        "progressFlickerPreventedCount": progress.get("progressFlickerPreventedCount", 0),
        "lastProgressInvalidReason": progress.get("lastProgressInvalidReason"),
        "lastProgressRetainedTick": progress.get("lastProgressRetainedTick"),
        "lastValidProgressTick": progress.get("lastValidProgressTick"),
        "lastValidInventorySignature": progress.get("lastValidInventorySignature"),
        "progressRetainedPreviousThisPoll": bool(progress.get("progressRetainedFromPrevious")),
        "progressInvalidSnapshotThisPoll": progress.get("currentSnapshotValid") is False,
        "progressRetainedFromPrevious": bool(progress.get("progressRetainedFromPrevious")),
        "progressRetainedReason": progress.get("retainedReason"),
    }


def one_shot_brain_warning(warning: str | None) -> bool:
    if not warning:
        return False
    text = str(warning)
    return (
        "brain state reset requested" in text
        or "brain state scope changed; progress baseline was reset" in text
    )


def context_best_label(response: dict, class_id: str = "tree") -> str:
    best = brain_core.best_target(response, class_id)
    if not best:
        return "none"
    name = best.get("targetName") or best.get("name") or "target"
    target_id = best.get("id")
    distance = best.get("distanceTiles")
    reachability = brain_core.candidate_reachability(best)
    parts = [str(name)]
    if target_id is not None:
        parts.append(str(target_id))
    if distance is not None:
        try:
            number = float(distance)
            parts.append(f"d={int(number) if number.is_integer() else round(number, 1)}")
        except (TypeError, ValueError):
            parts.append(f"d={distance}")
    if reachability and reachability != "unknown":
        parts.append("R" if reachability == "reachable" else str(reachability))
    return " ".join(parts)


def brain_progress_label(decision: dict) -> str:
    progress = decision.get("goalProgress") if isinstance(decision.get("goalProgress"), dict) else {}
    if not progress:
        return "off"
    value = progress.get("displayedGoalProgress")
    goal = progress.get("goalCount")
    if value is None or goal is None:
        return "observe"
    return f"{value}/{goal}"


def candidate_identity(candidate: dict | None) -> tuple:
    if not isinstance(candidate, dict):
        return ()
    return (
        candidate.get("objectKey"),
        candidate.get("candidateKey"),
        candidate.get("hash"),
        candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        candidate.get("worldX"),
        candidate.get("worldY"),
        candidate.get("plane"),
        candidate.get("classId"),
    )


def candidate_id_value(candidate: dict) -> Any:
    return candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id")


def target_identity_keys(candidate: dict | None) -> set[tuple]:
    if not isinstance(candidate, dict):
        return set()
    target_type = target_type_for_candidate(candidate)
    keys: set[tuple] = set()
    object_key = candidate.get("objectKey")
    if object_key:
        keys.add(("objectKey", str(object_key)))
    target_hash = candidate.get("hash")
    if target_hash is not None:
        keys.add(("hash", str(target_hash)))
    item_id = candidate_id_value(candidate)
    if item_id is not None and candidate.get("worldX") is not None and candidate.get("worldY") is not None and candidate.get("plane") is not None:
        keys.add(("world", str(item_id), str(candidate.get("worldX")), str(candidate.get("worldY")), str(candidate.get("plane")), str(target_type), str(candidate.get("kind") or candidate.get("layer") or "")))
        keys.add(("world", str(item_id), str(candidate.get("worldX")), str(candidate.get("worldY")), str(candidate.get("plane")), str(target_type)))
    if item_id is not None and candidate.get("sceneX") is not None and candidate.get("sceneY") is not None and candidate.get("plane") is not None:
        keys.add(("scene", str(item_id), str(candidate.get("sceneX")), str(candidate.get("sceneY")), str(candidate.get("plane")), str(target_type)))
    return keys


def same_target_identity(left: dict | None, right: dict | None) -> bool:
    left_keys = target_identity_keys(left)
    right_keys = target_identity_keys(right)
    return bool(left_keys and right_keys and left_keys.intersection(right_keys))


def polygon_points(value: Any) -> list | None:
    if isinstance(value, dict):
        value = value.get("points")
    if isinstance(value, list) and len(value) >= 3:
        return value
    return None


def marker_geometry_value(source: dict, key: str) -> Any:
    value = source.get(key)
    if value is not None:
        return value
    geometry = source.get("geometry") if isinstance(source.get("geometry"), dict) else {}
    return geometry.get(key)


def marker_bounds_value(source: dict) -> dict | None:
    for key in ("bounds", "clickboxBounds", "convexHullBounds", "canvasLocation"):
        value = source.get(key)
        if value is None and isinstance(source.get("geometry"), dict):
            value = source["geometry"].get(key)
        if isinstance(value, dict):
            return value
    return None


def best_marker_geometry_source(marker: dict) -> str:
    if polygon_points(marker.get("clickableHull")):
        return "clickableHull"
    if polygon_points(marker.get("clickboxPolygon")):
        return "clickboxPolygon"
    if polygon_points(marker.get("convexHull")):
        return "convexHull"
    if polygon_points(marker.get("canvasTilePolygon")):
        return "canvasTilePolygon"
    if isinstance(marker.get("bounds"), dict):
        return "bounds"
    if isinstance(marker.get("aimPoint"), dict):
        return "aimPoint"
    return "none"


def finalize_intent_marker(marker: dict) -> dict:
    source = best_marker_geometry_source(marker)
    if source != "none" and (source != "aimPoint" or not marker.get("geometrySource")):
        marker["geometrySource"] = source
    marker["clickableHullAvailable"] = source in {"clickableHull", "clickboxPolygon"}
    has_projection_identity = any(marker.get(key) is not None for key in ("worldX", "worldY", "sceneX", "sceneY", "localX", "localY"))
    target_type = marker.get("targetType")
    if target_type == "sceneObject" and has_projection_identity:
        marker["projectionMode"] = "live_object_pending"
        marker["projectionStale"] = False
        marker.pop("projectionFallbackReason", None)
    elif source in {"clickableHull", "clickboxPolygon"}:
        marker["projectionMode"] = "marker_clickable_hull"
        marker["projectionStale"] = False
        marker.pop("projectionFallbackReason", None)
    elif has_projection_identity:
        marker["projectionMode"] = "live_tile_fallback"
        marker["projectionStale"] = False
        marker.pop("projectionFallbackReason", None)
    elif isinstance(marker.get("aimPoint"), dict):
        marker["projectionMode"] = "last_known_aim"
        marker["projectionStale"] = True
        marker["projectionFallbackReason"] = "stable world/scene/local identity unavailable"
    else:
        marker["projectionMode"] = "label_only"
        marker["projectionStale"] = True
        marker["projectionFallbackReason"] = "stable world/scene/local identity unavailable"
    if marker.get("objectKey"):
        marker["markerId"] = marker.get("objectKey")
    return marker


def merge_marker_from_source(marker: dict, source: dict) -> dict:
    for key in (
        "objectKey",
        "hash",
        "id",
        "rawId",
        "name",
        "targetName",
        "classId",
        "targetType",
        "kind",
        "layer",
        "worldX",
        "worldY",
        "plane",
        "sceneX",
        "sceneY",
        "localX",
        "localY",
        "onScreen",
        "geometryAvailable",
        "qualityTier",
        "qualityScore",
        "distanceTiles",
        "tick",
    ):
        value = source.get(key)
        if marker.get(key) is None and value is not None:
            marker[key] = value
    for source_key in ("lastSeenTick", "lastUpdatedTick"):
        if marker.get("tick") is None and source.get(source_key) is not None:
            marker["tick"] = source.get(source_key)
    navigation = source.get("navigation") if isinstance(source.get("navigation"), dict) else {}
    if marker.get("reachability") is None:
        marker["reachability"] = navigation.get("directReachability") or source.get("directReachability")
    if marker.get("liveness") is None:
        marker["liveness"] = source.get("targetLiveState") or source.get("liveness")
    if marker.get("aimPoint") is None and isinstance(source.get("aimPoint"), dict):
        marker["aimPoint"] = source.get("aimPoint")
    for geometry_key in ("clickableHull", "clickboxPolygon", "convexHull", "canvasTilePolygon"):
        value = marker_geometry_value(source, geometry_key)
        if marker.get(geometry_key) is None and value is not None:
            marker[geometry_key] = value
    if marker.get("bounds") is None:
        bounds = marker_bounds_value(source)
        if bounds is not None:
            marker["bounds"] = bounds
    return finalize_intent_marker(marker)


def target_type_for_candidate(candidate: dict) -> str:
    target_type = candidate.get("targetType")
    if target_type:
        return str(target_type)
    class_id = str(candidate.get("classId") or "").lower()
    if "npc" in class_id or candidate.get("npcId") is not None:
        return "npc"
    if candidate.get("uiTargetId") is not None:
        return "ui"
    if candidate.get("slot") is not None and candidate.get("itemId") is not None:
        return "inventorySlot"
    if candidate.get("worldX") is not None and candidate.get("worldY") is not None:
        return "sceneObject"
    return "tile"


def intent_marker_from_candidate(
    candidate: dict,
    marker_type: str,
    label: str,
    reason: str,
    *,
    confidence: float | None = None,
    source: str = "brain",
) -> dict:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    aim = candidate.get("aimPoint") if isinstance(candidate.get("aimPoint"), dict) else None
    target_type = target_type_for_candidate(candidate)
    marker_id = intent_stabilizer.build_target_key(candidate, target_type)
    has_projection_identity = any(
        candidate.get(key) is not None
        for key in ("worldX", "worldY", "sceneX", "sceneY", "localX", "localY")
    )
    projection_mode = "live_object_pending" if target_type == "sceneObject" and has_projection_identity else ("live_tile_fallback" if has_projection_identity else ("last_known_aim" if aim else "label_only"))
    projection_stale = projection_mode in {"last_known_aim", "label_only"}
    selected = marker_type == "selected_target"
    role = "selected" if selected else ("backup" if marker_type == "backup_candidate" else marker_type)
    marker = {
        "markerVersion": "overlay_intent_marker.v1",
        "markerId": marker_id,
        "markerType": marker_type,
        "label": label,
        "selected": selected,
        "role": role,
        "priority": intent_stabilizer.PRIORITY_SELECTED_TARGET if selected else intent_stabilizer.PRIORITY_BACKUP,
        "reason": reason,
        "confidence": confidence,
        "source": source,
        "targetType": target_type,
        "classId": candidate.get("classId"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "hash": candidate.get("hash"),
        "objectKey": candidate.get("objectKey"),
        "kind": candidate.get("kind"),
        "layer": candidate.get("layer"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "localX": candidate.get("localX"),
        "localY": candidate.get("localY"),
        "aimPoint": aim,
        "reachability": navigation.get("directReachability") or candidate.get("directReachability"),
        "liveness": candidate.get("targetLiveState") or candidate.get("liveness"),
        "qualityTier": candidate.get("qualityTier"),
        "qualityScore": candidate.get("qualityScore") or candidate.get("score") or candidate.get("candidateScore"),
        "geometrySource": candidate.get("geometrySource"),
        "projectionMode": projection_mode,
        "projectionStale": projection_stale,
        "projectionFallbackReason": "stable world/scene/local identity unavailable" if projection_stale else None,
        "name": candidate.get("targetName") or candidate.get("name"),
        "distanceTiles": candidate.get("distanceTiles"),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
        "tick": candidate.get("lastSeenTick") or candidate.get("lastUpdatedTick") or candidate.get("tick"),
    }
    geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
    for geometry_key in ("clickableHull", "clickboxPolygon", "convexHull", "canvasTilePolygon"):
        geometry_value = candidate.get(geometry_key)
        if geometry_value is None:
            geometry_value = geometry.get(geometry_key)
        if geometry_value is not None:
            marker[geometry_key] = geometry_value
    bounds = candidate.get("bounds") if isinstance(candidate.get("bounds"), dict) else None
    if bounds is None:
        for bounds_key in ("clickboxBounds", "convexHullBounds", "canvasLocation"):
            candidate_bounds = candidate.get(bounds_key)
            if candidate_bounds is None:
                candidate_bounds = geometry.get(bounds_key)
            if isinstance(candidate_bounds, dict):
                bounds = candidate_bounds
                break
    if bounds:
        marker["bounds"] = bounds
    marker = finalize_intent_marker(marker)
    return {key: value for key, value in marker.items() if value is not None}


def warning_intent_marker(label: str, reason: str, *, source: str = "brain") -> dict:
    return {
        "markerType": "warning",
        "label": label,
        "selected": False,
        "role": "warning",
        "priority": intent_stabilizer.PRIORITY_DIAGNOSTIC,
        "reason": reason,
        "confidence": 0.5,
        "source": source,
        "targetType": "tile",
    }


def overlay_target_from_intent_marker(marker: dict) -> dict:
    target = {
        "markerType": marker.get("markerType"),
        "source": marker.get("source"),
        "reason": marker.get("reason"),
        "targetType": marker.get("targetType"),
        "markerId": marker.get("markerId"),
        "markerVersion": marker.get("markerVersion"),
        "targetKey": marker.get("targetKey"),
        "selected": marker.get("selected"),
        "role": marker.get("role"),
        "priority": marker.get("priority"),
        "classId": marker.get("classId"),
        "name": marker.get("name") or marker.get("label"),
        "id": marker.get("id"),
        "hash": marker.get("hash"),
        "objectKey": marker.get("objectKey"),
        "kind": marker.get("kind"),
        "layer": marker.get("layer"),
        "worldX": marker.get("worldX"),
        "worldY": marker.get("worldY"),
        "plane": marker.get("plane"),
        "sceneX": marker.get("sceneX"),
        "sceneY": marker.get("sceneY"),
        "localX": marker.get("localX"),
        "localY": marker.get("localY"),
        "distanceTiles": marker.get("distanceTiles"),
        "onScreen": marker.get("onScreen", True),
        "geometryAvailable": marker.get("geometryAvailable"),
        "qualityTier": marker.get("qualityTier"),
        "qualityScore": marker.get("qualityScore"),
        "geometrySource": marker.get("geometrySource"),
        "projectionMode": marker.get("projectionMode"),
        "projectionStale": marker.get("projectionStale"),
        "projectionFallbackReason": marker.get("projectionFallbackReason"),
        "tick": marker.get("tick"),
        "targetLiveState": marker.get("liveness"),
        "directReachability": marker.get("reachability"),
        "isBest": marker.get("markerType") == "selected_target",
        "overlayLabel": marker.get("label"),
        "aimPoint": marker.get("aimPoint"),
        "bounds": marker.get("bounds"),
        "clickableHullAvailable": marker.get("clickableHullAvailable"),
        "clickableHull": marker.get("clickableHull"),
        "clickboxPolygon": marker.get("clickboxPolygon"),
        "convexHull": marker.get("convexHull"),
        "canvasTilePolygon": marker.get("canvasTilePolygon"),
    }
    return {key: value for key, value in target.items() if value is not None}


def candidate_is_tree(candidate: dict) -> bool:
    class_id = str(candidate.get("classId") or "").lower()
    name = str(candidate.get("targetName") or candidate.get("name") or "").lower()
    return "tree" in class_id or "tree" in name


def marker_label_for_candidate(candidate: dict, prefix: str = "Target") -> str:
    name = candidate.get("targetName") or candidate.get("name") or candidate.get("classId") or "target"
    return f"{prefix}: {name}"


def target_required_for_intent(active_intent: str) -> bool:
    intent = str(active_intent or "").lower()
    if intent in {"goal_complete", "inventory_full", "stale_context", "no_context", "observe"}:
        return False
    return True


def candidate_key(candidate: dict) -> str:
    return intent_stabilizer.build_target_key(candidate, target_type_for_candidate(candidate))


def candidate_matches_key(candidate: dict, key: str) -> bool:
    if candidate_key(candidate) == key:
        return True
    return any(":".join(str(part) for part in identity) == key for identity in target_identity_keys(candidate))


def stable_backup_candidates(
    candidates: list[dict],
    selected_marker: dict,
    *,
    selected_key: str | None,
    selected_target_type: str | None,
    selected_class_id: Any,
    limit: int,
    stable_intent: intent_stabilizer.IntentResult | None,
) -> list[dict]:
    if limit <= 0:
        if stable_intent:
            stable_intent.state.backupTargetKeys = []
        return []
    eligible: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_target_type = target_type_for_candidate(candidate)
        candidate_class_id = candidate.get("classId")
        key = candidate_key(candidate)
        if key == selected_key or same_target_identity(selected_marker, candidate):
            merge_marker_from_source(selected_marker, candidate)
            continue
        if selected_class_id and candidate_class_id != selected_class_id:
            continue
        if selected_target_type and candidate_target_type != selected_target_type:
            continue
        duplicate = next((existing for existing in eligible if same_target_identity(existing, candidate)), None)
        if duplicate is not None:
            merge_marker_from_source(duplicate, candidate)
            continue
        eligible.append(candidate)

    selected_changed = bool(stable_intent and stable_intent.candidateWasSwitched)
    previous_keys = [] if selected_changed or stable_intent is None else list(stable_intent.state.backupTargetKeys)
    chosen: list[dict] = []
    used_keys: set[str] = set()
    for key in previous_keys:
        match = next((candidate for candidate in eligible if candidate_matches_key(candidate, key)), None)
        if match is not None:
            chosen.append(match)
            used_keys.add(candidate_key(match))
        if len(chosen) >= limit:
            break
    for candidate in eligible:
        if len(chosen) >= limit:
            break
        key = candidate_key(candidate)
        if key in used_keys or any(same_target_identity(candidate, existing) for existing in chosen):
            continue
        chosen.append(candidate)
        used_keys.add(key)
    if stable_intent:
        stable_intent.state.backupTargetKeys = [candidate_key(candidate) for candidate in chosen]
    return chosen


def build_intent_overlay_state(
    context: dict,
    brain_decision: dict,
    args: argparse.Namespace,
    generated_at: str,
    stable_intent: intent_stabilizer.IntentResult | None = None,
) -> dict:
    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    active_task = str(getattr(args, "brain_task", None) or brain_decision.get("task") or "")
    active_intent = str(brain_decision.get("phase") or "observe")
    markers: list[dict] = []
    selected = None
    selected_key = None
    selected_target_type = None
    selected_class_id = None
    if stable_intent and stable_intent.selectedTarget:
        selected = stable_intent.selectedTarget.raw
        selected_key = stable_intent.selectedTargetKey
        selected_target_type = stable_intent.selectedTarget.targetType
        selected_class_id = stable_intent.selectedTarget.classId
    if isinstance(selected, dict) and selected:
        marker = intent_marker_from_candidate(
            selected,
            "selected_target",
            marker_label_for_candidate(selected),
            f"stabilized brain intent: {stable_intent.switchReason if stable_intent else 'selected'}",
            confidence=brain_decision.get("confidence"),
            source="brain",
        )
        marker["targetKey"] = selected_key
        if stable_intent:
            marker["stableForTicks"] = stable_intent.stableForTicks
            marker["switchReason"] = stable_intent.switchReason
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_target_type = target_type_for_candidate(candidate)
            candidate_key = intent_stabilizer.build_target_key(candidate, candidate_target_type)
            if candidate_key == selected_key or same_target_identity(marker, candidate):
                marker = merge_marker_from_source(marker, candidate)
        markers.append(marker)
        backup_limit = max(0, int(getattr(args, "overlay_backup_candidates", 2) or 0))
        backups = stable_backup_candidates(
            candidates,
            marker,
            selected_key=selected_key,
            selected_target_type=selected_target_type,
            selected_class_id=selected_class_id,
            limit=backup_limit,
            stable_intent=stable_intent,
        )
        for candidate in backups:
            markers.append(
                intent_marker_from_candidate(
                    candidate,
                    "backup_candidate",
                    "Backup",
                    "nearby backup candidate retained for context",
                    confidence=None,
                    source="context",
                )
            )
    elif active_task == "woodcutting" and target_required_for_intent(active_intent):
        markers.append(warning_intent_marker("No reachable tree", "brain did not select a reachable woodcutting target"))

    return {
        "schema": OVERLAY_INTENT_SCHEMA,
        "generatedAtUtc": generated_at,
        "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
        "activeTask": active_task or None,
        "activeIntent": active_intent,
        "status": "WARN" if any(marker.get("markerType") == "warning" for marker in markers) else "PASS",
        "selectedTargetKey": stable_intent.selectedTargetKey if stable_intent else selected_key,
        "rawBestTargetKey": stable_intent.rawBestTargetKey if stable_intent else None,
        "stableForTicks": stable_intent.stableForTicks if stable_intent else None,
        "missingForTicks": stable_intent.currentMissingTicks if stable_intent else 0,
        "retainedDueToGrace": stable_intent.retainedDueToGrace if stable_intent else False,
        "switchReason": stable_intent.switchReason if stable_intent else None,
        "switchAuditTail": (stable_intent.switchAuditTail[-5:] if stable_intent else []),
        "backupKeys": (stable_intent.state.backupTargetKeys if stable_intent else []),
        "markers": markers,
    }


def build_overlay_state_for_mode(
    session: Path,
    args: argparse.Namespace,
    result: dict,
    context: dict,
    brain_decision: dict,
    generated_at: str,
    stable_intent: intent_stabilizer.IntentResult | None = None,
) -> dict:
    overlay = dict(result.get("overlayDebug") or {})
    summary = dict(overlay.get("summary") or {})
    mode = str(getattr(args, "overlay_mode", "intent") or "intent")
    if mode not in OVERLAY_MODES:
        mode = "intent"
    if mode != "intent":
        summary["overlayMode"] = mode
        summary["intentMarkerCount"] = 0
        summary["candidateMarkersSuppressed"] = 0
        overlay["summary"] = summary
        return overlay

    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    intent = build_intent_overlay_state(context, brain_decision if isinstance(brain_decision, dict) else {}, args, generated_at, stable_intent)
    markers = list(intent.get("markers") or [])
    targets = [overlay_target_from_intent_marker(marker) for marker in markers if marker.get("markerType") != "warning"]
    candidate_marker_count = sum(1 for marker in markers if marker.get("markerType") in {"selected_target", "backup_candidate"})
    selected_marker = next((marker for marker in markers if marker.get("markerType") == "selected_target"), None)
    summary.update(
        {
            "overlayMode": "intent",
            "candidateCount": len(candidates),
            "targetsWritten": len(targets),
            "targetLimit": 1 + max(0, int(getattr(args, "overlay_backup_candidates", 2) or 0)),
            "intentMarkerCount": len(markers),
            "candidateMarkersSuppressed": max(0, len(candidates) - candidate_marker_count),
            "clickableHullTargets": sum(1 for marker in targets if best_marker_geometry_source(marker) in {"clickableHull", "clickboxPolygon"}),
            "clickboxPolygonTargets": sum(1 for marker in targets if polygon_points(marker.get("clickboxPolygon"))),
            "convexHullTargets": sum(1 for marker in targets if polygon_points(marker.get("convexHull"))),
            "canvasTilePolygonTargets": sum(1 for marker in targets if polygon_points(marker.get("canvasTilePolygon"))),
            "boundsOnlyTargets": sum(1 for marker in targets if best_marker_geometry_source(marker) == "bounds"),
            "aimOnlyTargets": sum(1 for marker in targets if best_marker_geometry_source(marker) == "aimPoint"),
            "selectedTargetAvailable": bool(selected_marker),
            "selectedTargetHasClickableHull": bool(selected_marker and best_marker_geometry_source(selected_marker) in {"clickableHull", "clickboxPolygon"}),
            "backupMarkerCount": sum(1 for marker in markers if marker.get("markerType") == "backup_candidate"),
            "rawBestTarget": stable_intent.rawBestTargetKey if stable_intent else summary.get("rawBestTarget"),
            "stabilizedIntentTarget": stable_intent.selectedTargetKey if stable_intent else summary.get("stabilizedIntentTarget"),
            "intentStableForTicks": stable_intent.stableForTicks if stable_intent else None,
            "intentSwitchReason": stable_intent.switchReason if stable_intent else None,
            "intentRetainedDueToGrace": stable_intent.retainedDueToGrace if stable_intent else False,
            "intentCurrentMissingTicks": stable_intent.currentMissingTicks if stable_intent else 0,
        }
    )
    if not overlay:
        overlay = {
            "schema": live.LIVE_OVERLAY_DEBUG_SCHEMA,
            "generatedAtUtc": generated_at,
            "sessionPath": str(session),
            "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
            "profile": getattr(args, "profile", None),
            "status": intent.get("status"),
            "safety": {"readOnly": True, "drawOnly": True},
        }
    overlay["safety"] = {"readOnly": True, "drawOnly": True}
    overlay["summary"] = summary
    overlay["targets"] = targets
    overlay["markers"] = markers
    overlay["intentState"] = intent
    overlay["status"] = "WARN" if intent.get("status") == "WARN" or status.get("warnings") else "PASS"
    return overlay


def timing_bottleneck(status: dict) -> str | None:
    timing = status.get("timingBreakdownMillis") if isinstance(status.get("timingBreakdownMillis"), dict) else {}
    if not timing:
        return None
    best_key = None
    best_value = None
    for key, value in timing.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if best_value is None or number > best_value:
            best_key = str(key)
            best_value = number
    return best_key


def plugin_snapshot_health_available(args: argparse.Namespace) -> dict:
    return live.plugin_snapshot_state(
        args.plugin_snapshot_host,
        args.plugin_snapshot_port,
        getattr(args, "plugin_snapshot_token", ""),
        getattr(args, "plugin_snapshot_timeout", 0.5),
        probe=True,
    )


def processor_args(args: argparse.Namespace, input_source: str, *, suppress_output_writes: bool) -> SimpleNamespace:
    overlay_target_limit, overlay_hull_limit = live.effective_overlay_draw_limits(args)
    return SimpleNamespace(
        session=str(getattr(args, "session", "") or ""),
        latest_session=bool(getattr(args, "latest_session", False)),
        sessions_dir=getattr(args, "sessions_dir", None),
        input_source=input_source,
        compact_stream_host="127.0.0.1",
        compact_stream_port=8891,
        compact_stream_timeout=0.1,
        stream_fallback_to_compact_packets=False,
        stream_required_types_timeout=2.0,
        plugin_snapshot_host=args.plugin_snapshot_host,
        plugin_snapshot_port=args.plugin_snapshot_port,
        plugin_snapshot_token=getattr(args, "plugin_snapshot_token", ""),
        plugin_snapshot_timeout=args.plugin_snapshot_timeout,
        plugin_snapshot_tier=args.plugin_snapshot_tier,
        plugin_snapshot_max_projection_refs=args.plugin_snapshot_max_projection_refs,
        plugin_snapshot_max_age_ticks=5,
        plugin_snapshot_include_geometry=False,
        plugin_snapshot_response_mode="compact",
        plugin_snapshot_projection_field_mode="compact",
        plugin_snapshot_fallback=getattr(args, "plugin_snapshot_fallback", "none"),
        plugin_snapshot_auto_escalate=False,
        plugin_snapshot_min_candidates=1,
        auto_prefer_plugin_snapshot=False,
        compare_input_sources=False,
        require_compact_packets=input_source == live.COMPACT_PACKET_SOURCE,
        profile=args.profile,
        target_type="all",
        limit=100,
        window_ticks=10,
        poll_interval=args.poll_interval,
        once=False,
        follow=True,
        latency_mode="realtime",
        max_new_ticks_per_update=1,
        candidate_output_window="latest",
        drop_backlog_to_meet_budget=True,
        drain_backlog_on_overrun=True,
        include_ui_targets=False,
        latest=None,
        latest_with_frames=None,
        exclude_ui_blocked=False,
        emit_world_targets="candidates",
        world_target_output_limit=2000,
        depleted_suppress_ticks=20,
        liveness_mode="delta",
        liveness_budget_ms=20.0,
        max_recently_unavailable=1000,
        liveness_visible_ref_scan_limit=500,
        target_update_ms=100.0,
        warn_update_ms=250.0,
        benchmark=bool(args.benchmark),
        verbose=False,
        quiet=True,
        log_every=1,
        event_limit=args.max_events,
        event_timeline_limit=args.max_events,
        disable_event_timeline=False,
        overlay_debug_target_limit=overlay_target_limit,
        overlay_debug_hull_limit=overlay_hull_limit,
        force_window_rebuild=False,
        startup_backfill_ticks=1,
        no_startup_backfill=False,
        process_existing=False,
        no_ui_targets=True,
        summary=bool(args.summary),
        clear_live_output=False,
        max_runtime_seconds=None,
        write_retry_attempts=10,
        write_retry_delay_ms=10,
        strict_writes=False,
        target_library=str(live.DEFAULT_TARGET_LIBRARY_PATH),
        target_profiles=str(live.DEFAULT_TARGET_PROFILES_PATH),
        suppress_output_writes=suppress_output_writes,
    )


@dataclass
class LiveCoreState:
    session: Path
    profile: str
    write_debug_live_files: bool = False
    write_overlay_state: bool = False
    latest_result: dict | None = None
    latest_context: dict = field(default_factory=dict)
    source_status: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    generated_at_utc: str | None = None
    overlay_state_written: bool = False
    overlay_write_error: str | None = None
    update_count: int = 0
    brain_state: dict = field(default_factory=dict)
    brain_decision: dict = field(default_factory=dict)
    context_retained_previous_count: int = 0
    candidate_retained_previous_count: int = 0
    context_retention_streak: int = 0
    last_good_context: dict = field(default_factory=dict)
    last_good_tick: int | None = None

    def result_is_good_context(self, result: dict, status: dict) -> bool:
        baseline = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
        player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        return bool(baseline and player.get("worldX") is not None and candidates)

    def retention_age_ticks(self, status: dict) -> int | None:
        current_tick = status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick")
        try:
            if current_tick is not None and self.last_good_tick is not None:
                return max(0, int(current_tick) - int(self.last_good_tick))
        except (TypeError, ValueError):
            return None
        return None

    def update_from_result(
        self,
        result: dict,
        *,
        input_source_requested: str,
        fallback_reason: str | None,
        active_millis: float,
        overlay_state_written: bool,
        overlay_write_error: str | None,
        brain_decision: dict | None = None,
    ) -> None:
        status = dict(result.get("status") or {})
        status["liveCoreDaemonActive"] = True
        status["liveCoreSchema"] = SCHEMA
        status["writeDebugLiveFiles"] = bool(self.write_debug_live_files)
        status["overlayStateWritten"] = bool(overlay_state_written)
        status["inputSourceRequestedByDaemon"] = input_source_requested
        status["liveCoreFallbackReason"] = fallback_reason
        status["liveCoreActiveMillis"] = round(active_millis, 3)
        status.setdefault("inputSourceActive", status.get("inputSourceActive") or input_source_requested)
        good_context = self.result_is_good_context(result, status)
        retain_context = bool(self.last_good_context and not good_context and self.context_retention_streak < 2)
        retained_reason = None
        retained_age_ticks = self.retention_age_ticks(status)
        if retain_context:
            retained_reason = "current poll had incomplete context; retaining previous good context"
            self.context_retention_streak += 1
            self.context_retained_previous_count += 1
            if not result.get("candidates"):
                self.candidate_retained_previous_count += 1
            retained = dict(self.last_good_context)
            retained_status = dict(retained.get("status") or {})
            retained_status.update(status)
            retained_status.update(
                {
                    "contextRetainedPrevious": True,
                    "contextRetainedReason": retained_reason,
                    "candidateRetainedPrevious": not bool(result.get("candidates")),
                    "candidateRetainedAgeTicks": retained_age_ticks,
                    "contextRetainedPreviousCount": self.context_retained_previous_count,
                    "candidateRetainedPreviousCount": self.candidate_retained_previous_count,
                }
            )
            retained["status"] = retained_status
            retained["warnings"] = list(dict.fromkeys((retained.get("warnings") or []) + [retained_reason]))
            retained["cacheStats"] = dict(retained.get("cacheStats") or {})
            retained["cacheStats"]["retainedPreviousContext"] = True
            retained["cacheStats"]["retainedAgeTicks"] = retained_age_ticks
            self.latest_context = retained
        else:
            if good_context:
                self.context_retention_streak = 0

        self.latest_result = result
        self.generated_at_utc = utc_now()
        self.overlay_state_written = bool(overlay_state_written)
        self.overlay_write_error = overlay_write_error
        self.update_count += 1
        status["contextRetainedPrevious"] = retain_context
        status["contextRetainedReason"] = retained_reason
        status["candidateRetainedPrevious"] = bool(retain_context and not result.get("candidates"))
        status["candidateRetainedAgeTicks"] = retained_age_ticks if retain_context else None
        status["contextRetainedPreviousCount"] = self.context_retained_previous_count
        status["candidateRetainedPreviousCount"] = self.candidate_retained_previous_count
        self.source_status = status
        self.warnings = list(status.get("warnings") or [])
        if overlay_write_error:
            self.warnings.append(f"overlay state write failed: {overlay_write_error}")

        if not retain_context:
            self.latest_context = {
                "session": self.session,
                "paths": live.live_output_paths(self.session),
                "baseline": result.get("baseline") or {},
                "context": result.get("contextIndex") or {},
                "status": status,
                "activity": result.get("activity") or {},
                "events": result.get("events") or [],
                "navigation": result.get("navigation") or {},
                "watchValues": result.get("watchValues") or {},
                "performance": result.get("performance") or {},
                "candidates": result.get("candidates") or [],
                "warnings": list(self.warnings),
                "missingFields": [],
                "sourceFiles": [],
                "cacheStats": {
                    "source": "memory",
                    "reloadCount": self.update_count,
                    "lastReloadUtc": self.generated_at_utc,
                },
            }
        if good_context:
            self.last_good_context = dict(self.latest_context)
            self.last_good_tick = status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick")
        if brain_decision:
            self.brain_decision = brain_decision

    def context(self) -> dict:
        if self.latest_context:
            return dict(self.latest_context)
        return {
            "session": self.session,
            "paths": live.live_output_paths(self.session),
            "baseline": {},
            "context": {},
            "status": {
                "schema": "live_status.v1",
                "liveCoreDaemonActive": True,
                "inputSourceActive": None,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "dailyMode": None,
            },
            "activity": {},
            "events": [],
            "navigation": {},
            "watchValues": {},
            "performance": {},
            "candidates": [],
            "warnings": ["live core daemon has not processed a telemetry update yet"],
            "missingFields": ["baseline", "candidates", "status"],
            "sourceFiles": [],
            "cacheStats": {"source": "memory", "reloadCount": self.update_count},
        }

    def health(self) -> dict:
        context = self.context()
        payload = context_service.health_payload(context)
        status = context.get("status") if isinstance(context.get("status"), dict) else {}
        payload.update(
            {
                "schema": HEALTH_SCHEMA,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "inputSourceActive": status.get("inputSourceActive"),
                "dailyMode": status.get("dailyMode"),
                "noFileDaily": status.get("noFileDaily"),
                "compactPacketFilesRequired": status.get("compactPacketFilesRequired"),
                "compactPacketFilesWriting": status.get("compactPacketFilesWriting"),
                "debugMirrorEnabled": status.get("debugMirrorEnabled"),
                "candidateCount": len(context.get("candidates") or []),
                "updateCount": self.update_count,
                "readOnlyTelemetry": True,
            }
        )
        return payload

    def status(self) -> dict:
        context = self.context()
        payload = context_service.status_payload(context)
        brain = self.brain_decision if isinstance(self.brain_decision, dict) else {}
        progress = brain.get("goalProgress") if isinstance(brain.get("goalProgress"), dict) else {}
        target = brain_core.safe_get(brain, "currentContextSummary.bestTarget", {}) if brain else {}
        passthrough_status_keys = (
            "activeMs",
            "budgetExceeded",
            "activeBottleneck",
            "contextRetainedPrevious",
            "contextRetainedReason",
            "candidateRetainedPrevious",
            "candidateRetainedAgeTicks",
            "contextRetainedPreviousCount",
            "candidateRetainedPreviousCount",
            "progressInvalidSnapshotThisPoll",
            "progressRetainedPreviousThisPoll",
            "progressInvalidSnapshotCount",
            "progressRetainedPreviousCount",
            "progressFlickerPreventedCount",
            "lastProgressInvalidReason",
            "lastProgressRetainedTick",
            "lastValidProgressTick",
            "lastValidInventorySignature",
            "progressRetainedFromPrevious",
            "progressRetainedReason",
            "overlayMode",
            "intentMarkerCount",
            "candidateMarkersSuppressed",
            "overlayStateBytes",
            "overlayWriteError",
            "rawBestTarget",
            "stabilizedIntentTarget",
            "intentRawBestTarget",
            "intentStableTarget",
            "stabilizedIntentTargetLabel",
            "intentStableForTicks",
            "intentSwitchReason",
            "intentHardSwitch",
            "intentSoftSwitch",
            "intentSelectedTargetKey",
            "intentRawBestTargetKey",
            "intentPreviousTargetKey",
            "intentCandidateWasRetained",
            "intentCandidateWasSwitched",
            "intentSwitchedThisTick",
            "intentInterruptReason",
            "intentRetainedDueToGrace",
            "intentCurrentMissingTicks",
            "intentCurrentMissingThisTick",
            "intentCurrentInvalidReason",
            "intentSwitchAuditTail",
            "intentStabilizerMillis",
            "intentCandidatesConsidered",
            "dailyMode",
            "noFileDaily",
            "compactPacketFilesRequired",
            "compactPacketFilesWriting",
            "compactPacketFilesEnabledInManifest",
            "compactPacketFilesAvailable",
            "compactPacketFilesRecent",
            "compactPacketFileCount",
            "debugMirrorEnabled",
        )
        for key in passthrough_status_keys:
            if key in self.source_status:
                payload[key] = self.source_status.get(key)
        payload.update(
            {
                "schema": STATUS_SCHEMA,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "latestUpdateUtc": self.generated_at_utc,
                "brain": brain or None,
                "brainPhase": brain.get("phase"),
                "brainProgress": progress or None,
                "brainBestTree": target if isinstance(target, dict) and target else None,
            }
        )
        return payload


class LiveCoreDaemon:
    def __init__(self, session: Path, args: argparse.Namespace):
        self.session = session
        self.args = args
        brain_state, brain_state_warning = load_daemon_brain_state(session, args)
        self.brain_state_warning = brain_state_warning
        self.state = LiveCoreState(
            session=session,
            profile=args.profile,
            write_debug_live_files=bool(args.write_debug_live_files),
            write_overlay_state=bool(args.write_overlay_state),
            brain_state=brain_state,
        )
        if brain_state_warning:
            self.state.warnings.append(brain_state_warning)
        self.brain_reset_applied = bool(args.reset_brain_state)
        self.args.reset_brain_state = False
        self.processors: dict[str, live.LiveTargetProcessor] = {}
        self.intent_stabilizer = intent_stabilizer.IntentStabilizer()
        self.latest_intent_result: intent_stabilizer.IntentResult | None = None
        self.active_source: str | None = None
        self.fallback_reason: str | None = None
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def make_processor(self, input_source: str) -> live.LiveTargetProcessor:
        existing = self.processors.get(input_source)
        if existing is not None:
            return existing
        args = processor_args(self.args, input_source, suppress_output_writes=not self.args.write_debug_live_files)
        processor = live.LiveTargetProcessor(self.session, args)
        processor.initialize_from_existing()
        if processor.tick_window and processor.last_result is None:
            processor.process_window(rebuild_reason="startup")
        self.processors[input_source] = processor
        return processor

    def initial_source(self) -> str:
        requested = self.args.input_source
        if requested == live.PLUGIN_SNAPSHOT_SOURCE:
            return live.PLUGIN_SNAPSHOT_SOURCE
        if requested == live.COMPACT_PACKET_SOURCE:
            return live.COMPACT_PACKET_SOURCE
        if requested != "auto":
            return live.COMPACT_PACKET_SOURCE

        health = plugin_snapshot_health_available(self.args)
        if health.get("available"):
            return live.PLUGIN_SNAPSHOT_SOURCE
        self.fallback_reason = health.get("error") or "plugin snapshot endpoint is not healthy; using compact packet files"
        return live.COMPACT_PACKET_SOURCE

    def should_accept_plugin_snapshot(self, result: dict, active_millis: float) -> bool:
        status = result.get("status") if isinstance(result, dict) else {}
        if not isinstance(status, dict):
            return False
        if status.get("pluginSnapshotAvailable") is not True:
            self.fallback_reason = status.get("pluginSnapshotLastError") or "plugin snapshot endpoint unavailable"
            return False
        if status.get("candidateCount", 0) <= 0:
            self.fallback_reason = "plugin snapshot produced no candidates; using compact packet files"
            return False
        if active_millis > float(self.args.target_update_ms):
            self.fallback_reason = f"plugin snapshot active time {active_millis:.1f} ms exceeded daily target"
            return False
        return True

    def stabilize_intent(self, brain_decision: dict | None) -> tuple[intent_stabilizer.IntentResult, dict]:
        started = time.perf_counter()
        context = self.state.context()
        status = context.get("status") if isinstance(context.get("status"), dict) else {}
        candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
        decision = brain_decision if isinstance(brain_decision, dict) else {}
        raw_best = brain_core.safe_get(decision, "currentContextSummary.bestTarget", {})
        phase = str(decision.get("phase") or "observe")
        if phase in {"goal_complete", "inventory_full", "stale_context", "no_context", "observe"}:
            raw_best = {}
            candidates = []
        if not isinstance(raw_best, dict) or not raw_best:
            raw_best = candidates[0] if decision and candidates else {}
        task = str(decision.get("task") or self.args.brain_task or self.args.profile or "")
        priority = intent_stabilizer.PRIORITY_SELECTED_TARGET
        if phase in {"goal_complete", "inventory_full", "banking_needed"}:
            priority = intent_stabilizer.PRIORITY_TASK_TRANSITION
        if decision.get("interrupt") or str(decision.get("interruptReason") or "").lower() in {"threat", "emergency", "escape"}:
            priority = intent_stabilizer.PRIORITY_EMERGENCY
        intent_context = {
            "activeTask": task,
            "activeIntent": phase,
            "profile": self.args.profile,
            "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
            "rawBestTarget": raw_best,
            "intentPriority": decision.get("intentPriority") or priority,
            "forceSwitch": bool(decision.get("forceSwitch")),
            "interrupt": bool(decision.get("interrupt")),
            "interruptReason": decision.get("interruptReason"),
            "interruptPriority": decision.get("interruptPriority"),
            "interruptTarget": decision.get("interruptTarget"),
            "requireReachability": True,
            "requireAimPoint": False,
        }
        result = self.intent_stabilizer.choose(candidates, intent_context)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        selected_raw = result.selectedTarget.raw if result.selectedTarget else {}
        selected_label = None
        if selected_raw:
            selected_label = selected_raw.get("targetName") or selected_raw.get("name") or selected_raw.get("classId") or result.selectedTargetKey
        fields = {
            "rawBestTarget": result.rawBestTargetKey,
            "stabilizedIntentTarget": result.selectedTargetKey,
            "intentRawBestTarget": result.rawBestTargetKey,
            "intentStableTarget": result.selectedTargetKey,
            "stabilizedIntentTargetLabel": selected_label,
            "intentSelectedTargetKey": result.selectedTargetKey,
            "intentRawBestTargetKey": result.rawBestTargetKey,
            "intentPreviousTargetKey": result.previousTargetKey,
            "intentStableForTicks": result.stableForTicks,
            "intentSwitchReason": result.switchReason,
            "intentHardSwitch": result.hardSwitch,
            "intentSoftSwitch": result.softSwitch,
            "intentCandidateWasRetained": result.candidateWasRetained,
            "intentCandidateWasSwitched": result.candidateWasSwitched,
            "intentSwitchedThisTick": result.candidateWasSwitched,
            "intentInterruptReason": result.interruptReason,
            "intentRetainedDueToGrace": result.retainedDueToGrace,
            "intentCurrentMissingTicks": result.currentMissingTicks,
            "intentCurrentMissingThisTick": result.currentTargetMissingThisTick,
            "intentCurrentInvalidReason": result.currentInvalidReason,
            "intentSwitchAuditTail": result.switchAuditTail[-5:],
            "intentStabilizerMillis": elapsed_ms,
            "intentCandidatesConsidered": result.candidatesConsidered,
        }
        return result, fields

    def poll_once(self) -> dict:
        source = self.active_source or self.initial_source()
        start = time.perf_counter()
        processor = self.make_processor(source)
        _added, result = processor.poll_once()
        active_millis = (time.perf_counter() - start) * 1000.0

        if self.args.input_source == "auto" and source == live.PLUGIN_SNAPSHOT_SOURCE and not self.should_accept_plugin_snapshot(result, active_millis):
            if compact_packet_available(self.session):
                source = live.COMPACT_PACKET_SOURCE
                processor = self.make_processor(source)
                start = time.perf_counter()
                _added, result = processor.poll_once()
                active_millis = (time.perf_counter() - start) * 1000.0
            else:
                self.fallback_reason = (self.fallback_reason or "plugin snapshot was not accepted") + "; compact packet fallback unavailable"

        self.active_source = source
        no_file_daily = self.args.daily_mode == DAILY_MODE_SNAPSHOT_NO_FILES
        packet_write_state = compact_packet_file_write_state(self.session)
        daily_fields = {
            "dailyMode": self.args.daily_mode,
            "noFileDaily": bool(no_file_daily),
            "compactPacketFilesRequired": source == live.COMPACT_PACKET_SOURCE,
            "compactPacketFilesWriting": packet_write_state.get("compactPacketFilesWriting"),
            "compactPacketFilesEnabledInManifest": packet_write_state.get("compactPacketFilesEnabledInManifest"),
            "compactPacketFilesAvailable": packet_write_state.get("compactPacketFilesAvailable"),
            "compactPacketFilesRecent": packet_write_state.get("compactPacketFilesRecent"),
            "compactPacketFileCount": packet_write_state.get("compactPacketFileCount"),
            "debugMirrorEnabled": bool(no_file_daily and packet_write_state.get("compactPacketFilesWriting") is True),
        }
        if isinstance(result.get("status"), dict):
            result["status"] = dict(result["status"])
            result["status"]["liveCoreDaemonActive"] = True
            result["status"]["writeDebugLiveFiles"] = bool(self.args.write_debug_live_files)
            result["status"]["overlayStateWritten"] = False
            result["status"]["inputSourceRequestedByDaemon"] = self.args.input_source
            result["status"]["liveCoreFallbackReason"] = self.fallback_reason
            result["status"]["liveCoreActiveMillis"] = round(active_millis, 3)
            result["status"]["activeMs"] = round(active_millis, 3)
            result["status"]["budgetExceeded"] = bool(active_millis > float(self.args.target_update_ms))
            result["status"]["activeBottleneck"] = timing_bottleneck(result["status"])
            result["status"].update(daily_fields)
            if self.brain_state_warning is None:
                result["status"]["warnings"] = [
                    warning
                    for warning in (result["status"].get("warnings") or [])
                    if not one_shot_brain_warning(warning)
                ]
            if self.brain_state_warning:
                warnings = list(result["status"].get("warnings") or [])
                if self.brain_state_warning not in warnings:
                    warnings.append(self.brain_state_warning)
                result["status"]["warnings"] = warnings
        self.state.update_from_result(
            result,
            input_source_requested=self.args.input_source,
            fallback_reason=self.fallback_reason,
            active_millis=active_millis,
            overlay_state_written=False,
            overlay_write_error=None,
        )
        brain_decision = self.evaluate_brain_if_enabled(result)
        if brain_decision:
            self.state.brain_decision = brain_decision
        stable_intent, intent_fields = self.stabilize_intent(brain_decision)
        self.latest_intent_result = stable_intent
        self.state.source_status.update(intent_fields)
        if isinstance(result.get("status"), dict):
            result["status"].update(intent_fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(intent_fields)
        overlay_written, overlay_error, overlay_bytes = self.write_overlay_state_if_enabled(result)
        overlay_fields = {
            "overlayStateWritten": bool(overlay_written),
            "overlayWriteError": overlay_error,
            "overlayMode": self.args.overlay_mode,
            "intentMarkerCount": 0,
            "candidateMarkersSuppressed": 0,
            "overlayStateBytes": overlay_bytes,
        }
        if isinstance(result.get("overlayDebug"), dict):
            summary = result["overlayDebug"].get("summary") if isinstance(result["overlayDebug"].get("summary"), dict) else {}
            overlay_fields["overlayMode"] = summary.get("overlayMode", self.args.overlay_mode)
            overlay_fields["intentMarkerCount"] = summary.get("intentMarkerCount", 0)
            overlay_fields["candidateMarkersSuppressed"] = summary.get("candidateMarkersSuppressed", 0)
        self.state.overlay_state_written = bool(overlay_written)
        self.state.overlay_write_error = overlay_error
        self.state.source_status.update(overlay_fields)
        if isinstance(result.get("status"), dict):
            result["status"].update(overlay_fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(overlay_fields)
        if overlay_error:
            warning = f"overlay state write failed: {overlay_error}"
            if warning not in self.state.warnings:
                self.state.warnings.append(warning)
        self.brain_state_warning = None
        return result

    def write_overlay_state_if_enabled(self, result: dict) -> tuple[bool, str | None, int]:
        if not self.args.write_overlay_state:
            return False, None, 0
        generated_at = utc_now()
        overlay = build_overlay_state_for_mode(
            self.session,
            self.args,
            result,
            self.state.context(),
            self.state.brain_decision if isinstance(self.state.brain_decision, dict) else {},
            generated_at,
            self.latest_intent_result,
        )
        if not overlay:
            return False, "overlay debug state was not available", 0
        path = live.live_output_paths(self.session)["overlayDebug"]
        try:
            overlay_summary = overlay.get("summary") if isinstance(overlay.get("summary"), dict) else {}
            overlay["summary"] = overlay_summary
            result["overlayDebug"] = overlay
            text = ""
            size = 0
            for _index in range(3):
                text = json.dumps(overlay, indent=2, sort_keys=False) + "\n"
                size = len(text.encode("utf-8"))
                if overlay["summary"].get("overlayStateBytes") == size:
                    break
                overlay["summary"]["overlayStateBytes"] = size
            live.atomic_write_text(path, text)
            return True, None, size
        except OSError as error:
            return False, f"{type(error).__name__}: {error}", 0

    def default_context_request(self) -> dict:
        return {
            "schema": context_service.REQUEST_SCHEMA,
            "task": self.args.brain_task,
            "needs": list(DEFAULT_CONTEXT_NEEDS),
            "maxCandidates": max(1, int(self.args.max_candidates)),
            "maxEvents": max(0, int(self.args.max_events)),
            "responseMode": "compact",
        }

    def build_context_response(self, request: dict | None = None) -> dict:
        payload = request if isinstance(request, dict) else self.default_context_request()
        if payload.get("schema") != context_service.REQUEST_SCHEMA:
            return context_service.error_payload(f"unsupported schema: {payload.get('schema')}", request_id=payload.get("requestId"))
        return context_service.build_context_response(
            self.state.context(),
            payload,
            default_max_candidates=max(1, int(self.args.max_candidates)),
            max_response_bytes=int(self.args.max_response_bytes),
            compact_include_source_files=False,
            compact_liveness_examples=0,
        )

    def evaluate_brain_if_enabled(self, result: dict) -> dict | None:
        if not self.args.human_dashboard and not self.args.goal_count:
            return None
        response = self.build_context_response(self.default_context_request())
        decision, updated = brain_core.evaluate_brain(
            response,
            self.state.brain_state,
            task=self.args.brain_task,
            goal_count=self.args.goal_count,
            max_events=self.args.max_events,
        )
        self.state.brain_state = updated
        fields = brain_status_fields(self.state.brain_state, self.brain_reset_applied)
        self.state.source_status.update(fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.args.brain_state_file, self.state.brain_state, self.session, self.args)
        return decision

    def start_context_server(self) -> None:
        if self.server is not None:
            return
        if self.args.context_host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("live_core_daemon only binds localhost context endpoints")
        daemon = self

        class Handler(LiveCoreRequestHandler):
            pass

        self.server = ThreadingHTTPServer((self.args.context_host, int(self.args.context_port)), Handler)
        self.server.live_core_daemon = daemon  # type: ignore[attr-defined]
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="live-core-context", daemon=True)
        self.server_thread.start()
        self.args.context_port = int(self.server.server_address[1])

    def stop_context_server(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=1.0)
        self.server = None
        self.server_thread = None

    def run(self) -> int:
        self.start_context_server()
        if self.args.summary:
            print(f"Live core daemon: session={self.session}")
            print(f"context API: http://{self.args.context_host}:{self.args.context_port}")
            print("Daily mode:")
            print(f"  mode={self.args.daily_mode}")
            print(f"  source={self.args.input_source}")
            print(f"  writes={'on' if self.args.write_debug_live_files else 'off'}")
            print(f"  overlay={'on' if self.args.write_overlay_state else 'off'} ({self.args.overlay_mode})")
            print(f"  brain={'on' if (self.args.human_dashboard or self.args.goal_count) else 'off'}")
            print(f"  debug files={'on' if self.args.write_debug_live_files else 'off'}")
            print(
                "  compact packet files required="
                f"{'no' if self.args.input_source == live.PLUGIN_SNAPSHOT_SOURCE else 'yes'}"
            )
            print(
                "  experimental snapshot/stream="
                f"{'on' if self.args.input_source == live.PLUGIN_SNAPSHOT_SOURCE else 'off'}/off"
            )
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                result = self.poll_once()
                if self.args.benchmark:
                    self.print_benchmark(result)
                if self.args.human_dashboard:
                    self.print_human_summary()
            except KeyboardInterrupt:
                break
            except Exception as error:  # noqa: BLE001
                self.state.warnings.append(f"poll failed: {type(error).__name__}: {error}")
                print(f"Warning: live core poll failed: {type(error).__name__}: {error}", file=sys.stderr)
            elapsed = time.perf_counter() - started
            self.stop_event.wait(max(0.0, float(self.args.poll_interval) - elapsed))
        return 0

    def print_benchmark(self, result: dict) -> None:
        status = result.get("status") or {}
        timing = status.get("timingBreakdownMillis") if isinstance(status.get("timingBreakdownMillis"), dict) else {}
        print(
            "tick={tick} input={source} candidates={candidates} best={best} "
            "activeMs={active} contextMs={context_ms} brain={brain} progress={progress} overlay={overlay} writes={writes} "
            "budget={budget} bottleneck={bottleneck} progressRetained={progress_retained} "
            "candidateRetained={candidate_retained} intent={intent} stableFor={stable_for} switch={switch} intentMs={intent_ms}".format(
                tick=status.get("lastProcessedTick") or status.get("latestTickProcessed"),
                source=status.get("inputSourceActive"),
                candidates=status.get("candidateCount"),
                best=context_best_label(self.build_context_response(self.default_context_request())),
                active=status.get("liveCoreActiveMillis") or status.get("processingDurationMillis"),
                context_ms=round(float(timing.get("contextIndexMillis") or 0.0), 3),
                brain=(self.state.brain_decision or {}).get("phase") or "off",
                progress=brain_progress_label(self.state.brain_decision or {}),
                overlay="on" if self.args.write_overlay_state else "off",
                writes="on" if self.args.write_debug_live_files else "off",
                budget="exceeded" if status.get("budgetExceeded") else "ok",
                bottleneck=status.get("activeBottleneck") or "unknown",
                progress_retained="yes" if status.get("progressRetainedPreviousThisPoll") else "no",
                candidate_retained="yes" if status.get("candidateRetainedPrevious") else "no",
                intent=status.get("stabilizedIntentTargetLabel") or status.get("stabilizedIntentTarget") or "none",
                stable_for=status.get("intentStableForTicks"),
                switch=status.get("intentSwitchReason") or "unknown",
                intent_ms=status.get("intentStabilizerMillis"),
            )
        )
        if status.get("progressRetainedPreviousThisPoll"):
            print(
                "progress retained: {reason}; activeMs={active}; bottleneck={bottleneck}".format(
                    reason=status.get("progressRetainedReason") or "invalid inventory snapshot",
                    active=status.get("liveCoreActiveMillis") or status.get("processingDurationMillis"),
                    bottleneck=status.get("activeBottleneck") or "unknown",
                )
            )

    def print_human_summary(self) -> None:
        response = self.build_context_response(self.default_context_request())
        print(format_context_human(response, compact=True, top=max(1, int(self.args.max_candidates))))
        if self.state.brain_decision:
            print("")
            print(brain_core.format_human(self.state.brain_decision))


class LiveCoreRequestHandler(BaseHTTPRequestHandler):
    server_version = "OSRSTelemetryLiveCoreDaemon/0.1"

    @property
    def daemon(self) -> LiveCoreDaemon:
        return self.server.live_core_daemon  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/health":
            self.send_json(self.daemon.state.health())
        elif path == "/schema":
            payload = context_service.schema_payload()
            payload["service"] = "live_core_daemon"
            payload["notes"] = list(dict.fromkeys((payload.get("notes") or []) + READ_ONLY_NOTES))
            payload["endpoints"] = {
                "GET": ["/health", "/schema", "/status", "/summary", "/brain", "/capabilities", "/watches"],
                "POST": ["/context", "/context/batch", "/brain", "/watch-request"],
            }
            self.send_json(payload)
        elif path == "/status":
            self.send_json(self.daemon.state.status())
        elif path == "/summary":
            self.handle_summary(params)
        elif path == "/brain":
            self.handle_brain(params, {})
        elif path == "/capabilities":
            self.send_json(context_service.capabilities_payload(self.daemon.state.context()))
        elif path == "/watches":
            self.send_json(context_service.watches_payload(self.daemon.state.context()))
        else:
            self.send_json(context_service.error_payload(f"unknown endpoint: {self.path}"), status_code=404)

    def do_POST(self):  # noqa: N802
        if self.path not in {"/context", "/context/batch", "/brain", "/watch-request"}:
            self.send_json(context_service.error_payload(f"unknown endpoint: {self.path}"), status_code=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            body = self.rfile.read(min(length, 1_000_000)).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_json(context_service.error_payload(f"invalid JSON request: {error}"), status_code=400)
            return
        if self.path == "/context/batch":
            if not isinstance(payload, list):
                self.send_json(context_service.error_payload("/context/batch expects a JSON list"), status_code=400)
                return
            self.send_json([self.daemon.build_context_response(item) for item in payload])
        elif self.path == "/watch-request":
            self.send_json(context_service.handle_watch_request_payload(self.daemon.state.context(), payload))
        elif self.path == "/brain":
            self.handle_brain({}, payload if isinstance(payload, dict) else {})
        else:
            self.send_json(self.daemon.build_context_response(payload if isinstance(payload, dict) else {}))

    def handle_summary(self, params: dict[str, list[str]]) -> None:
        task = (params.get("task") or [self.daemon.args.brain_task])[0] or self.daemon.args.brain_task
        try:
            top = max(1, int((params.get("top") or [str(self.daemon.args.max_candidates)])[0]))
        except ValueError:
            top = max(1, int(self.daemon.args.max_candidates))
        request = dict(self.daemon.default_context_request())
        request["task"] = task
        request["maxCandidates"] = top
        request["maxEvents"] = top
        response = self.daemon.build_context_response(request)
        if ((params.get("format") or ["text"])[0] or "text").lower() == "json":
            self.send_json(response)
        else:
            self.send_text(format_context_human(response, compact=False, top=top))

    def handle_brain(self, params: dict[str, list[str]], payload: dict) -> None:
        task = payload.get("task") if isinstance(payload, dict) else None
        task = task or ((params.get("task") or [self.daemon.args.brain_task])[0] if params else self.daemon.args.brain_task)
        goal_count = payload.get("goalCount") if isinstance(payload, dict) else None
        if goal_count is None:
            goal_count = self.daemon.args.goal_count
        request = dict(self.daemon.default_context_request())
        request["task"] = task
        response = self.daemon.build_context_response(request)
        decision, updated = brain_core.evaluate_brain(
            response,
            self.daemon.state.brain_state,
            task=str(task or "woodcutting"),
            goal_count=goal_count,
            max_events=self.daemon.args.max_events,
        )
        self.daemon.state.brain_state = updated
        fields = brain_status_fields(self.daemon.state.brain_state, self.daemon.brain_reset_applied)
        self.daemon.state.source_status.update(fields)
        if isinstance(self.daemon.state.latest_context.get("status"), dict):
            self.daemon.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.daemon.args.brain_state_file, self.daemon.state.brain_state, self.daemon.session, self.daemon.args)
        self.daemon.state.brain_decision = decision
        self.send_json(decision)

    def send_json(self, payload: Any, status_code: int = 200) -> None:
        data = json_bytes(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_text(self, text: str, status_code: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    daily_mode_explicit = any(item == "--daily-mode" or item.startswith("--daily-mode=") for item in raw_argv)
    parser = argparse.ArgumentParser(
        description="Read-only in-memory daily live daemon. It serves context from telemetry observations only and emits no actions."
    )
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--session", help="Telemetry session directory.")
    session.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--sessions-dir", help="Override sessions root.")
    parser.add_argument("--profile", default="woodcutting", choices=["woodcutting", "broad_qa", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa"])
    parser.add_argument("--daily-mode", choices=sorted(DAILY_MODES), default=DAILY_MODE_COMPACT_PACKETS)
    parser.add_argument("--input-source", choices=["plugin-snapshot", "compact-packets", "auto"], default="compact-packets")
    parser.add_argument("--plugin-snapshot-host", default="127.0.0.1")
    parser.add_argument("--plugin-snapshot-port", type=int, default=8893)
    parser.add_argument("--plugin-snapshot-token", default="")
    parser.add_argument("--plugin-snapshot-timeout", type=float, default=0.5)
    parser.add_argument("--plugin-snapshot-tier", choices=sorted(live.PLUGIN_SNAPSHOT_TIERS), default="hot")
    parser.add_argument("--plugin-snapshot-max-projection-refs", type=int)
    parser.add_argument("--plugin-snapshot-fallback", choices=["none", "compact-packets"], default="none")
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--context-host", default="127.0.0.1")
    parser.add_argument("--context-port", type=int, default=8890)
    parser.add_argument("--write-overlay-state", action="store_true")
    parser.add_argument("--overlay-mode", choices=sorted(OVERLAY_MODES), default="intent")
    parser.add_argument("--overlay-backup-candidates", type=int, default=2)
    parser.add_argument("--overlay-debug-target-limit", type=int, default=10)
    parser.add_argument("--write-debug-live-files", dest="write_debug_live_files", action="store_true")
    parser.add_argument("--no-debug-live-files", dest="write_debug_live_files", action="store_false")
    parser.set_defaults(write_debug_live_files=False)
    parser.add_argument("--human-dashboard", action="store_true")
    parser.add_argument("--brain-task", default="woodcutting")
    parser.add_argument("--goal-count", type=int)
    parser.add_argument("--brain-state-file", help="Optional brain_state.v1 file. If omitted, daily daemon progress stays in process memory only.")
    parser.add_argument("--reset-brain-state", action="store_true", help="Reset in-memory or file-backed brain progress before observing.")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000)
    parser.add_argument("--target-update-ms", type=float, default=100.0)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    args.daily_mode_explicit = daily_mode_explicit
    if daily_mode_explicit:
        if args.daily_mode == DAILY_MODE_SNAPSHOT_NO_FILES:
            args.input_source = live.PLUGIN_SNAPSHOT_SOURCE
            args.plugin_snapshot_tier = "hot"
        elif args.daily_mode == DAILY_MODE_COMPACT_PACKETS:
            args.input_source = live.COMPACT_PACKET_SOURCE
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        session = resolve_session(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    daemon = LiveCoreDaemon(session, args)

    def stop(_signum=None, _frame=None):
        daemon.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    try:
        return daemon.run()
    finally:
        daemon.stop_context_server()


if __name__ == "__main__":
    raise SystemExit(main())

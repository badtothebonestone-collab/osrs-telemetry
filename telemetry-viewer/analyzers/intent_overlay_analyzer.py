from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import capabilities
import intent_stabilizer

from analyzers.live_state import IntentOverlayContext


OVERLAY_INTENT_SCHEMA = "overlay_intent_state.v1"
OVERLAY_DEBUG_SCHEMA = "telemetry_overlay_debug_state.v1"
OVERLAY_MODES = {"intent", "candidates", "debug"}
DAILY_PREDICTED_PATH_LIMIT = 8
DEBUG_PREDICTED_PATH_LIMIT = 24


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
        "interactionRadiusTiles",
        "approachRadiusTiles",
        "clickbox",
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
        "interactionRadiusTiles": candidate.get("interactionRadiusTiles"),
        "approachRadiusTiles": candidate.get("approachRadiusTiles"),
        "clickbox": candidate.get("clickbox"),
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


def diagnostic_intent_marker(label: str, reason: str, *, source: str = "brain") -> dict:
    marker = warning_intent_marker(label, reason, source=source)
    marker["markerType"] = "diagnostic"
    marker["role"] = "diagnostic"
    marker["priority"] = intent_stabilizer.PRIORITY_DIAGNOSTIC
    return marker


def tile_identity(tile: dict | None) -> tuple[Any, Any, Any] | None:
    if not isinstance(tile, dict):
        return None
    world_x = tile.get("worldX")
    world_y = tile.get("worldY")
    plane = tile.get("plane")
    if world_x is None or world_y is None or plane is None:
        return None
    return (world_x, world_y, plane)


def predicted_path_limit(args: Any, mode: str) -> int:
    override = getattr(args, "overlay_predicted_path_limit", None)
    if override is not None:
        return max(0, int(override))
    if hasattr(args, "overlay_path_tile_limit"):
        legacy = getattr(args, "overlay_path_tile_limit")
        if legacy is not None:
            return max(0, int(legacy))
    return DAILY_PREDICTED_PATH_LIMIT if mode == "intent" else DEBUG_PREDICTED_PATH_LIMIT


def marker_id_prefix(marker_type: str) -> str:
    return "next_waypoint_tile" if marker_type == "waypoint" else marker_type


def path_tile_marker(marker_type: str, label: str, tile: dict, reason: str, *, index: int | None = None) -> dict:
    marker = {
        "markerVersion": "overlay_intent_marker.v1",
        "markerType": marker_type,
        "label": label,
        "selected": False,
        "role": marker_type,
        "priority": intent_stabilizer.PRIORITY_DIAGNOSTIC,
        "reason": reason,
        "confidence": 0.65,
        "source": "pathing_context",
        "targetType": "tile",
        "worldX": tile.get("worldX"),
        "worldY": tile.get("worldY"),
        "plane": tile.get("plane"),
        "projectionMode": "live_tile_fallback",
        "projectionStale": False,
    }
    if index is not None:
        marker["pathIndex"] = index
        marker["label"] = f"{label} {index}"
    if marker_type == "predicted_path_tile" and index is not None:
        marker["markerId"] = f"predicted_path_tile:{index}:{marker.get('worldX')}:{marker.get('worldY')}:{marker.get('plane')}"
    else:
        marker["markerId"] = f"{marker_id_prefix(marker_type)}:{marker.get('worldX')}:{marker.get('worldY')}:{marker.get('plane')}"
    return {key: value for key, value in marker.items() if value is not None}


def append_pathing_markers(
    markers: list[dict],
    pathing_context: dict[str, Any],
    *,
    include_predicted_path: bool,
    include_final_approach: bool,
    path_tile_limit: int,
) -> None:
    if not isinstance(pathing_context, dict) or not pathing_context.get("pathingNeeded"):
        return
    reason = str(pathing_context.get("reason") or "read-only pathing context")
    occupied_tiles: set[tuple[Any, Any, Any]] = set()
    destination_tile = pathing_context.get("destinationTile") if isinstance(pathing_context.get("destinationTile"), dict) else None
    if destination_tile:
        markers.append(path_tile_marker("destination_tile", "Destination", destination_tile, reason))
        identity = tile_identity(destination_tile)
        if identity is not None:
            occupied_tiles.add(identity)
    waypoint = pathing_context.get("nextWaypointTile") if isinstance(pathing_context.get("nextWaypointTile"), dict) else None
    if waypoint:
        markers.append(path_tile_marker("waypoint", "Next waypoint", waypoint, "predicted next local waypoint for visualization"))
        identity = tile_identity(waypoint)
        if identity is not None:
            occupied_tiles.add(identity)
    if pathing_context.get("localReachability") == "blocked":
        markers.append(warning_intent_marker("Path blocked", "local collision path appears blocked", source="pathing_context"))
    elif pathing_context.get("localReachability") == "unknown" and pathing_context.get("pathingNeeded"):
        markers.append(diagnostic_intent_marker("Path unknown", reason, source="pathing_context"))
    if include_predicted_path and include_final_approach:
        final_approach = pathing_context.get("finalApproachTile") if isinstance(pathing_context.get("finalApproachTile"), dict) else None
        if final_approach:
            markers.append(path_tile_marker("final_approach_tile", "Final approach", final_approach, "predicted final local approach tile for visualization"))
            identity = tile_identity(final_approach)
            if identity is not None:
                occupied_tiles.add(identity)
    if include_predicted_path:
        tiles = pathing_context.get("predictedPathTiles") if isinstance(pathing_context.get("predictedPathTiles"), list) else []
        emitted = 0
        for index, tile in enumerate(tiles, start=1):
            if not isinstance(tile, dict):
                continue
            identity = tile_identity(tile)
            if identity is not None and identity in occupied_tiles:
                continue
            markers.append(path_tile_marker("predicted_path_tile", "Path", tile, "predicted local path tile for visual QA", index=index))
            if identity is not None:
                occupied_tiles.add(identity)
            emitted += 1
            if emitted >= max(0, int(path_tile_limit)):
                break


def pathing_marker_summary(markers: list[dict], pathing_context: dict[str, Any], path_limit: int) -> dict[str, Any]:
    predicted_tiles = pathing_context.get("predictedPathTiles") if isinstance(pathing_context.get("predictedPathTiles"), list) else []
    return {
        "predictedPathTilesAvailableCount": len(predicted_tiles),
        "predictedPathMarkersEmittedCount": sum(1 for marker in markers if marker.get("markerType") == "predicted_path_tile"),
        "predictedPathLimit": path_limit,
        "destinationMarkerEmitted": any(marker.get("markerType") == "destination_tile" for marker in markers),
        "nextWaypointMarkerEmitted": any(marker.get("markerType") == "waypoint" for marker in markers),
        "finalApproachMarkerEmitted": any(marker.get("markerType") == "final_approach_tile" for marker in markers),
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
        "navigationNeeded": marker.get("navigationNeeded"),
        "navigationReason": marker.get("navigationReason"),
        "navigationStatus": marker.get("navigationStatus"),
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


def marker_label_for_candidate(candidate: dict, prefix: str = "Target") -> str:
    name = candidate.get("targetName") or candidate.get("name") or candidate.get("classId") or "target"
    return f"{prefix}: {name}"


def target_required_for_intent(active_intent: str) -> bool:
    intent = str(active_intent or "").lower()
    if intent in {
        "goal_complete",
        "inventory_full",
        "needs_service",
        "process_inventory",
        "stale_context",
        "no_context",
        "observe",
        "none",
        "needs_more_context",
        "navigate_to_service",
        "service_available",
        "service_interaction_pending",
    }:
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
    args: Any,
    generated_at: str,
    stable_intent: intent_stabilizer.IntentResult | None = None,
) -> dict:
    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    active_task = str(getattr(args, "brain_task", None) or brain_decision.get("task") or "")
    generic_state = brain_decision.get("genericTaskState") if isinstance(brain_decision.get("genericTaskState"), dict) else {}
    active_intent = str(generic_state.get("activeIntent") or generic_state.get("phase") or brain_decision.get("phase") or "observe")
    service_context = brain_decision.get("serviceContext") if isinstance(brain_decision.get("serviceContext"), dict) else {}
    navigation_intent_context = brain_decision.get("navigationIntentContext") if isinstance(brain_decision.get("navigationIntentContext"), dict) else {}
    pathing_context = brain_decision.get("pathingContext") if isinstance(brain_decision.get("pathingContext"), dict) else {}
    service_candidates = service_context.get("serviceCandidates") if isinstance(service_context.get("serviceCandidates"), list) else []
    markers: list[dict] = []
    selected = None
    selected_key = None
    selected_target_type = None
    selected_class_id = None
    active_target = generic_state.get("activeIntentTarget") if isinstance(generic_state.get("activeIntentTarget"), dict) else None
    if active_target is None and service_context:
        if service_context.get("serviceNeeded"):
            active_target = service_context.get("bestServiceCandidate") if isinstance(service_context.get("bestServiceCandidate"), dict) else None
    if stable_intent and stable_intent.selectedTarget:
        selected = stable_intent.selectedTarget.raw
        selected_key = stable_intent.selectedTargetKey
        selected_target_type = stable_intent.selectedTarget.targetType
        selected_class_id = stable_intent.selectedTarget.classId
    if active_intent == "needs_service" and active_target:
        selected = active_target
        selected_key = intent_stabilizer.build_target_key(active_target, target_type_for_candidate(active_target))
        selected_target_type = None
        selected_class_id = None
    elif not target_required_for_intent(active_intent):
        selected = None
        selected_key = None
        selected_target_type = None
        selected_class_id = None
    if isinstance(selected, dict) and selected:
        label_prefix = "Service" if active_intent == "needs_service" else "Target"
        reason = (
            "policy requires service context"
            if active_intent == "needs_service"
            else f"stabilized brain intent: {stable_intent.switchReason if stable_intent else 'selected'}"
        )
        marker = intent_marker_from_candidate(
            selected,
            "selected_target",
            marker_label_for_candidate(selected, label_prefix),
            reason,
            confidence=brain_decision.get("confidence"),
            source="brain",
        )
        marker["targetKey"] = selected_key
        if stable_intent and active_intent != "needs_service":
            marker["stableForTicks"] = stable_intent.stableForTicks
            marker["switchReason"] = stable_intent.switchReason
        if navigation_intent_context:
            marker["navigationNeeded"] = navigation_intent_context.get("navigationNeeded")
            marker["navigationReason"] = navigation_intent_context.get("navigationReason")
            marker["navigationStatus"] = navigation_intent_context.get("directReachability") or "unknown"
        merge_candidates = service_candidates if active_intent == "needs_service" and service_candidates else candidates
        for candidate in merge_candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_target_type = target_type_for_candidate(candidate)
            current_key = intent_stabilizer.build_target_key(candidate, candidate_target_type)
            if current_key == selected_key or same_target_identity(marker, candidate):
                marker = merge_marker_from_source(marker, candidate)
        markers.append(marker)
        backup_limit = max(0, int(getattr(args, "overlay_backup_candidates", 2) or 0))
        backup_source_candidates = service_candidates if active_intent == "needs_service" and service_candidates else candidates
        backups = stable_backup_candidates(
            backup_source_candidates,
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
    elif active_intent == "needs_service" and service_context.get("serviceNeeded"):
        markers.append(warning_intent_marker("Inventory full: bank target not observed", "task policy requires bank service context but no service candidate is visible"))
    elif active_intent == "process_inventory":
        process_type = generic_state.get("processTypeNeeded")
        if not process_type and isinstance(brain_decision.get("processInventoryContext"), dict):
            process_type = brain_decision["processInventoryContext"].get("processTypeNeeded")
        if process_type:
            markers.append(diagnostic_intent_marker(f"Process inventory: {process_type}", "task policy requires read-only inventory processing context"))

    path_limit = predicted_path_limit(args, "intent")
    append_pathing_markers(
        markers,
        pathing_context,
        include_predicted_path=True,
        include_final_approach=True,
        path_tile_limit=path_limit,
    )
    path_summary = pathing_marker_summary(markers, pathing_context, path_limit)

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
        "pathingOverlaySummary": path_summary,
    }


def build_overlay_state_for_mode(
    session: Path,
    args: Any,
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
        pathing_context = brain_decision.get("pathingContext") if isinstance(brain_decision, dict) and isinstance(brain_decision.get("pathingContext"), dict) else {}
        markers = list(overlay.get("markers") or [])
        path_limit = predicted_path_limit(args, mode)
        append_pathing_markers(
            markers,
            pathing_context,
            include_predicted_path=mode in {"candidates", "debug"},
            include_final_approach=mode in {"candidates", "debug"},
            path_tile_limit=path_limit,
        )
        summary["overlayMode"] = mode
        summary["intentMarkerCount"] = 0
        summary["candidateMarkersSuppressed"] = 0
        summary["pathingMarkerCount"] = sum(1 for marker in markers if marker.get("source") == "pathing_context")
        summary.update(pathing_marker_summary(markers, pathing_context, path_limit))
        overlay["summary"] = summary
        if markers:
            overlay["markers"] = markers
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
            "pathingMarkerCount": sum(1 for marker in markers if marker.get("source") == "pathing_context"),
            **intent.get("pathingOverlaySummary", {}),
        }
    )
    if not overlay:
        overlay = {
            "schema": OVERLAY_DEBUG_SCHEMA,
            "generatedAtUtc": generated_at,
            "sessionPath": str(session),
            "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
            "profile": getattr(args, "profile", None),
            "status": intent.get("status"),
            "overlayPurpose": "read_only_visualization",
        }
    overlay["overlayPurpose"] = "read_only_visualization"
    overlay["summary"] = summary
    overlay["targets"] = targets
    overlay["markers"] = markers
    overlay["intentState"] = intent
    overlay["status"] = "WARN" if intent.get("status") == "WARN" or status.get("warnings") else "PASS"
    return overlay


def analyze_intent_overlay(
    *,
    session: Path,
    args: Any,
    result: dict,
    context: dict,
    brain_decision: dict,
    generated_at: str,
    stable_intent: intent_stabilizer.IntentResult | None,
) -> IntentOverlayContext:
    started = time.perf_counter()
    overlay = build_overlay_state_for_mode(session, args, result, context, brain_decision, generated_at, stable_intent)
    markers = list(overlay.get("markers") or [])
    selected = next((marker for marker in markers if marker.get("markerType") == "selected_target"), None)
    backups = [marker for marker in markers if marker.get("markerType") == "backup_candidate"]
    missing = [] if markers else ["overlay.intent_markers"]
    warnings = [str(marker.get("reason")) for marker in markers if marker.get("markerType") == "warning" and marker.get("reason")]
    source_tick = overlay.get("latestTick")
    return IntentOverlayContext(
        status=overlay.get("status") or ("WARN" if warnings or missing else "PASS"),
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick if isinstance(source_tick, int) else None,
        retained_from_previous=bool(overlay.get("intentState", {}).get("retainedDueToGrace")) if isinstance(overlay.get("intentState"), dict) else False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        overlay=overlay,
        markers=markers,
        selected_marker=selected,
        backup_markers=backups,
    )

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "route_demonstration_guide.v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GUIDE_DIR = REPO_ROOT / "route_guides"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int | None = 0) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _tile(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _int(value.get("worldX") if value.get("worldX") is not None else value.get("x"), None)
    y = _int(value.get("worldY") if value.get("worldY") is not None else value.get("y"), None)
    if x is None or y is None:
        return None
    return {"worldX": x, "worldY": y, "plane": _int(value.get("plane"), 0) or 0}


def _tile_key(value: Any) -> tuple[int | None, int | None, int | None]:
    tile = _tile(value)
    if not tile:
        return None, None, None
    return tile["worldX"], tile["worldY"], tile["plane"]


def tile_distance(a: Any, b: Any) -> float | None:
    first = _tile(a)
    second = _tile(b)
    if not first or not second or first.get("plane") != second.get("plane"):
        return None
    return math.hypot(float(first["worldX"] - second["worldX"]), float(first["worldY"] - second["worldY"]))


def guide_filename(route_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(route_name or "route_unknown"))
    return f"{safe}.route_guide.json"


def guide_path_for(route_name: str, *, root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else DEFAULT_GUIDE_DIR
    return base / guide_filename(route_name)


def load_route_guide(route_name: str, *, root: str | Path | None = None) -> dict[str, Any]:
    path = guide_path_for(route_name, root=root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) and payload.get("schema") == SCHEMA else {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _route_name_from_recording(root: Path, traversal: dict[str, Any]) -> str | None:
    route_name = _text(traversal.get("routeName"))
    if route_name and route_name != "route_unknown":
        return route_name
    comparison = _load_json(root / "route_template_comparison.json")
    for key in ("routeNameMatched", "templateName", "templateRouteName", "routeName"):
        value = _text(comparison.get(key))
        if value and value != "route_unknown":
            return value
    return None


def _append_path_point(
    path_points: list[dict[str, Any]],
    seen: set[tuple[int | None, int | None, int | None]],
    *,
    world: dict[str, int] | None,
    source_recording: str,
    source_kind: str,
    route_name: str,
    segment_index: Any = None,
    step_index: Any = None,
    tick: Any = None,
    elapsed_seconds: Any = None,
    area_label: Any = None,
    tolerance: int = 2,
) -> None:
    if not world:
        return
    key = _tile_key(world)
    if key in seen:
        return
    seen.add(key)
    path_points.append(
        {
            "schema": "route_demonstration_path_point.v1",
            "orderIndex": len(path_points),
            "routeName": route_name,
            "world": dict(world),
            "sourceTick": tick,
            "sourceTime": elapsed_seconds,
            "areaLabel": area_label,
            "reachedToleranceTiles": tolerance,
            "sourceRecording": source_recording,
            "sourceKind": source_kind,
            "segmentIndex": segment_index,
            "stepIndex": step_index,
        }
    )


def _postcondition_for_step(step: dict[str, Any]) -> dict[str, Any]:
    post = _dict(step.get("postcondition"))
    return {
        "type": "plane_change" if post.get("planeChanged") else "movement" if post.get("positionChanged") else post.get("type"),
        "result": step.get("result"),
        "planeChanged": post.get("planeChanged"),
        "positionChanged": post.get("positionChanged"),
        "beforeWorld": _tile(_dict(post.get("beforeSnapshot")).get("world")),
        "afterWorld": _tile(_dict(post.get("afterSnapshot")).get("world")),
    }


def _camera_hints_for_step(step: dict[str, Any], camera_summary: dict[str, Any]) -> list[dict[str, Any]]:
    start_time = _float(step.get("startTime"))
    hints: list[dict[str, Any]] = []
    for segment in _list(camera_summary.get("segments")) + _list(camera_summary.get("examples")):
        if not isinstance(segment, dict):
            continue
        end_time = _float(segment.get("endTime"))
        if start_time is None or end_time is None:
            continue
        delta = start_time - end_time
        if 0 <= delta <= 8.0:
            hints.append(
                {
                    "schema": "route_demonstration_camera_hint.v1",
                    "segmentId": segment.get("segmentId"),
                    "startTime": segment.get("startTime"),
                    "endTime": segment.get("endTime"),
                    "deltaYaw": segment.get("deltaYaw"),
                    "deltaPitch": segment.get("deltaPitch"),
                    "durationMs": segment.get("durationMs"),
                    "source": segment.get("source"),
                    "timeBeforeStepSeconds": round(delta, 3),
                }
            )
    return hints[:3]


def _interaction_from_step(
    step: dict[str, Any],
    *,
    source_recording: str,
    route_name: str,
    camera_summary: dict[str, Any],
) -> dict[str, Any] | None:
    action = _text(step.get("action"))
    target_name = _text(step.get("targetName"))
    step_type = _text(step.get("type"))
    target = _dict(step.get("target"))
    post = _dict(step.get("postcondition"))
    plane_changed = bool(post.get("planeChanged"))
    action_text = action.lower()
    if action_text == "cancel":
        return None
    target_text = target_name.lower()
    routeish = (
        step_type in {"plane_transition", "object_action", "menu_selection"}
        and (
            plane_changed
            or "climb" in action_text
            or any(word in target_text for word in ("stair", "stairs", "staircase", "trapdoor", "ladder"))
        )
    )
    if not routeish:
        return None
    before = _tile(_dict(post.get("beforeSnapshot")).get("world"))
    after = _tile(_dict(post.get("afterSnapshot")).get("world"))
    world = _tile(step.get("world")) or _tile(target.get("world")) or before or after
    if not world:
        return None
    plane_before = before.get("plane") if before else step.get("startPlane")
    plane_after = after.get("plane") if after else step.get("endPlane")
    return {
        "schema": "route_demonstration_interaction_step.v1",
        "orderIndex": None,
        "routeName": route_name,
        "segmentIndex": step.get("stepIndex"),
        "sourceStepId": step.get("stepId") or step.get("rawStepId"),
        "action": action or None,
        "targetName": target_name or None,
        "targetId": step.get("targetId") or target.get("rawId") or target.get("effectiveId") or target.get("id"),
        "targetKind": step.get("targetKind") or target.get("kind") or "object",
        "world": world,
        "planeBefore": plane_before,
        "planeAfter": plane_after,
        "expectedPlaneChange": (int(plane_after) - int(plane_before)) if isinstance(plane_before, int) and isinstance(plane_after, int) else None,
        "targetQuality": _dict(step.get("targetQuality")).get("quality"),
        "menuSelection": _dict(step.get("menuSelection")),
        "hoverOrMenuEvidence": {
            "menuConfirmed": bool(_dict(_dict(step.get("targetQuality")).get("evidence")).get("menuConfirmed")),
            "hoverConfirmed": bool(_dict(_dict(step.get("targetQuality")).get("evidence")).get("hoverConfirmed")),
            "inputEventSeq": step.get("inputEventSeq"),
        },
        "postcondition": _postcondition_for_step(step),
        "cameraHints": _camera_hints_for_step(step, camera_summary),
        "sourceRecording": source_recording,
    }


def _template_variants(route_name: str) -> list[dict[str, Any]]:
    template_path = REPO_ROOT / "route_templates" / f"{route_name}.route_template.json"
    template = _load_json(template_path)
    variants: list[dict[str, Any]] = []
    for variant in _list(template.get("variants")):
        if isinstance(variant, dict):
            variants.append(
                {
                    "variantName": variant.get("variantName"),
                    "description": variant.get("description"),
                    "segmentOverrides": variant.get("segmentOverrides"),
                }
            )
    return variants


def build_route_guide(recording_paths: list[str | Path], *, route_name: str | None = None) -> dict[str, Any]:
    path_points: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    route_legs: list[dict[str, Any]] = []
    plane_changes: list[dict[str, Any]] = []
    camera_hints: list[dict[str, Any]] = []
    source_recordings: list[str] = []
    warnings: list[str] = []
    seen_points: set[tuple[int | None, int | None, int | None]] = set()
    guide_route_name = route_name
    start_area = None
    end_area = None

    for raw_path in recording_paths:
        root = Path(raw_path)
        traversal = _load_json(root / "traversal_lifecycle.json")
        if not traversal:
            warnings.append(f"{root}: traversal_lifecycle.json missing")
            continue
        detected_route = _route_name_from_recording(root, traversal)
        if route_name and detected_route and detected_route != route_name:
            continue
        if not guide_route_name:
            guide_route_name = detected_route
        if not guide_route_name or detected_route != guide_route_name:
            continue
        source_recordings.append(str(root))
        camera_summary = _load_json(root / "camera_behavior_summary.json")
        if start_area is None:
            start_area = _dict(traversal.get("start")).get("areaLabel")
        if end_area is None:
            end_area = _dict(traversal.get("end")).get("areaLabel")

        for segment in _list(traversal.get("routeSegments")):
            if not isinstance(segment, dict):
                continue
            route_legs.append(
                {
                    "schema": "route_demonstration_leg.v1",
                    "segmentIndex": segment.get("segmentIndex"),
                    "segmentType": segment.get("segmentType"),
                    "label": segment.get("label"),
                    "startWorld": _tile(segment.get("startWorld")),
                    "endWorld": _tile(segment.get("endWorld")),
                    "primaryAction": segment.get("primaryAction"),
                    "postcondition": segment.get("postcondition"),
                    "confidence": segment.get("confidence"),
                    "sourceRecording": str(root),
                }
            )
            _append_path_point(
                path_points,
                seen_points,
                world=_tile(segment.get("startWorld")),
                source_recording=str(root),
                source_kind="segment_start",
                route_name=guide_route_name,
                segment_index=segment.get("segmentIndex"),
                area_label=segment.get("label"),
            )
            _append_path_point(
                path_points,
                seen_points,
                world=_tile(segment.get("endWorld")),
                source_recording=str(root),
                source_kind="segment_end",
                route_name=guide_route_name,
                segment_index=segment.get("segmentIndex"),
                area_label=segment.get("label"),
            )
            post = _dict(segment.get("postcondition"))
            if post.get("type") == "plane_change":
                plane_changes.append(
                    {
                        "schema": "route_demonstration_plane_change.v1",
                        "segmentIndex": segment.get("segmentIndex"),
                        "label": segment.get("label"),
                        "startPlane": segment.get("startPlane"),
                        "endPlane": segment.get("endPlane"),
                        "startWorld": _tile(segment.get("startWorld")),
                        "endWorld": _tile(segment.get("endWorld")),
                        "sourceRecording": str(root),
                    }
                )

        for step in _list(traversal.get("steps")):
            if not isinstance(step, dict):
                continue
            world = _tile(step.get("world"))
            _append_path_point(
                path_points,
                seen_points,
                world=world,
                source_recording=str(root),
                source_kind=f"step_{step.get('type') or 'unknown'}",
                route_name=guide_route_name,
                step_index=step.get("stepIndex"),
                tick=step.get("endTick") or step.get("startTick"),
                elapsed_seconds=step.get("endTime") or step.get("startTime"),
                area_label=step.get("type"),
            )
            interaction = _interaction_from_step(
                step,
                source_recording=str(root),
                route_name=guide_route_name,
                camera_summary=camera_summary,
            )
            if interaction:
                interaction["orderIndex"] = len(interactions)
                interactions.append(interaction)

        for segment in _list(camera_summary.get("segments")):
            if isinstance(segment, dict):
                camera_hints.append(
                    {
                        "schema": "route_demonstration_camera_hint.v1",
                        "segmentId": segment.get("segmentId"),
                        "startTime": segment.get("startTime"),
                        "endTime": segment.get("endTime"),
                        "deltaYaw": segment.get("deltaYaw"),
                        "deltaPitch": segment.get("deltaPitch"),
                        "durationMs": segment.get("durationMs"),
                        "sourceRecording": str(root),
                    }
                )

    guide_route_name = guide_route_name or route_name or "route_unknown"
    for index, item in enumerate(path_points):
        item["orderIndex"] = index
    status = "PASS" if path_points and (route_legs or interactions) else "WARN"
    if not path_points:
        warnings.append("no path points extracted")
    if not interactions:
        warnings.append("no interaction steps extracted")
    return {
        "schema": SCHEMA,
        "status": status,
        "routeName": guide_route_name,
        "sourceRecordings": source_recordings,
        "startArea": start_area,
        "endArea": end_area,
        "pathPoints": path_points,
        "planeChanges": plane_changes,
        "interactionSteps": interactions,
        "routeLegs": route_legs,
        "cameraHints": camera_hints[:20],
        "postconditions": [
            {"segmentIndex": leg.get("segmentIndex"), "postcondition": leg.get("postcondition"), "sourceRecording": leg.get("sourceRecording")}
            for leg in route_legs
        ],
        "toleratedVariants": _template_variants(guide_route_name),
        "warnings": warnings,
    }


def save_route_guide(guide: dict[str, Any], out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(guide, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def nearest_guide_point(guide: dict[str, Any], current_world: dict[str, Any], *, max_distance: float | None = None) -> dict[str, Any]:
    current = _tile(current_world)
    best: tuple[float, dict[str, Any]] | None = None
    for point in _list(guide.get("pathPoints")):
        if not isinstance(point, dict):
            continue
        distance = tile_distance(current, point.get("world"))
        if distance is None:
            continue
        if max_distance is not None and distance > max_distance:
            continue
        if best is None or distance < best[0]:
            best = (distance, point)
    if best is None:
        return {}
    return {"point": best[1], "distanceTiles": round(best[0], 3)}


def _xy_distance(a: Any, b: Any) -> float | None:
    first = _tile(a)
    second = _tile(b)
    if not first or not second:
        return None
    return math.hypot(float(first["worldX"] - second["worldX"]), float(first["worldY"] - second["worldY"]))


def _nearest_same_plane_item(
    items: list[Any],
    current: dict[str, Any],
    *,
    world_key: str = "world",
    max_distance: float | None = None,
) -> dict[str, Any]:
    best: tuple[float, dict[str, Any]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        world = _tile(item.get(world_key))
        distance = tile_distance(current, world)
        if distance is None:
            continue
        if max_distance is not None and distance > max_distance:
            continue
        if best is None or distance < best[0]:
            best = (distance, item)
    if best is None:
        return {}
    return {"item": best[1], "distanceTiles": round(best[0], 3)}


def _nearest_cross_plane_item(items: list[Any], current: dict[str, Any], *, world_key: str = "world") -> dict[str, Any]:
    best: tuple[float, dict[str, Any]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        world = _tile(item.get(world_key))
        distance = _xy_distance(current, world)
        if distance is None:
            continue
        if best is None or distance < best[0]:
            best = (distance, item)
    if best is None:
        return {}
    return {"item": best[1], "xyDistanceTiles": round(best[0], 3)}


def _reentry_subsegment(guide: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    current_plane = current.get("plane")
    for leg in _list(guide.get("routeLegs")):
        if not isinstance(leg, dict):
            continue
        start = _tile(leg.get("startWorld"))
        end = _tile(leg.get("endWorld"))
        if not start or not end:
            continue
        start_plane = start.get("plane")
        end_plane = end.get("plane")
        if start_plane == current_plane or end_plane == current_plane:
            return {
                "classification": "same_plane_route_leg",
                "segmentIndex": leg.get("segmentIndex"),
                "segmentType": leg.get("segmentType"),
                "label": leg.get("label"),
            }
        if start_plane is not None and end_plane is not None:
            low = min(start_plane, end_plane)
            high = max(start_plane, end_plane)
            if low < current_plane < high:
                return {
                    "classification": "intermediate_floor_between_route_transitions",
                    "segmentIndex": leg.get("segmentIndex"),
                    "segmentType": leg.get("segmentType"),
                    "label": leg.get("label"),
                    "startWorld": start,
                    "endWorld": end,
                }
    return {"classification": "off_demonstrated_route_plane"}


def resolve_reentry(
    guide: dict[str, Any],
    current_world: dict[str, Any],
    *,
    max_same_plane_distance: float | None = None,
    reached_tolerance_tiles: int = 2,
) -> dict[str, Any]:
    current = _tile(current_world)
    if not guide or not current:
        return {
            "schema": "route_guide_reentry.v1",
            "status": "WARN",
            "routeGuideLoaded": bool(guide),
            "routeGuideReentryAttempted": True,
            "blocker": "route_guide_or_position_missing",
        }

    points = _list(guide.get("pathPoints"))
    interactions = _list(guide.get("interactionSteps"))
    nearest_point = _nearest_same_plane_item(points, current, max_distance=max_same_plane_distance)
    nearest_interaction = _nearest_same_plane_item(interactions, current, max_distance=max_same_plane_distance)
    progress = resolve_progress(guide, current, reached_tolerance_tiles=reached_tolerance_tiles) if (nearest_point or nearest_interaction) else {}
    next_point = _dict(progress.get("nextGuidePoint"))
    next_interaction = _dict(progress.get("nextGuideInteraction"))

    recovery_type = None
    next_step: dict[str, Any] | None = None
    if next_interaction:
        recovery_type = "route_guide_interaction"
        next_step = next_interaction
    elif next_point:
        recovery_type = "route_guide_path_point"
        next_step = next_point
    elif nearest_interaction:
        recovery_type = "route_guide_reentry_interaction"
        next_step = _dict(nearest_interaction.get("item"))
    elif nearest_point:
        recovery_type = "route_guide_reentry_point"
        next_step = _dict(nearest_point.get("item"))

    status = "PASS" if next_step else "WARN"
    blocker = None if next_step else "route_guide_no_same_plane_reentry"
    nearest_cross_point = _nearest_cross_plane_item(points, current)
    nearest_cross_interaction = _nearest_cross_plane_item(interactions, current)
    return {
        "schema": "route_guide_reentry.v1",
        "status": status,
        "routeGuideLoaded": True,
        "routeGuideName": guide.get("routeName"),
        "routeGuideReentryAttempted": True,
        "currentWorld": current,
        "currentPlane": current.get("plane"),
        "nearestSamePlaneGuidePoint": nearest_point,
        "nearestSamePlaneInteraction": nearest_interaction,
        "nearestCrossPlaneGuidePoint": nearest_cross_point,
        "nearestCrossPlaneInteraction": nearest_cross_interaction,
        "inferredSubsegment": _reentry_subsegment(guide, current),
        "nextRecoveryStep": next_step,
        "recoveryCandidateType": recovery_type,
        "routeGuideProgress": progress,
        "blocker": blocker,
    }


def resolve_progress(
    guide: dict[str, Any],
    current_world: dict[str, Any],
    *,
    reached_tolerance_tiles: int = 2,
) -> dict[str, Any]:
    current = _tile(current_world)
    if not guide or not current:
        return {
            "schema": "route_guide_progress.v1",
            "status": "WARN",
            "routeGuideLoaded": bool(guide),
            "blocker": "route_guide_or_position_missing",
        }
    nearest = nearest_guide_point(guide, current)
    point = _dict(nearest.get("point"))
    nearest_index = _int(point.get("orderIndex"), None)
    nearest_distance = _float(nearest.get("distanceTiles"))
    reached_indices: list[int] = []
    for item in _list(guide.get("pathPoints")):
        idx = _int(_dict(item).get("orderIndex"), None)
        distance = tile_distance(current, _dict(item).get("world"))
        tolerance = _int(_dict(item).get("reachedToleranceTiles"), reached_tolerance_tiles) or reached_tolerance_tiles
        if idx is not None and distance is not None and distance <= max(reached_tolerance_tiles, tolerance):
            reached_indices.append(idx)
    if reached_indices:
        progress_index = max(reached_indices)
    elif nearest_index is None:
        progress_index = -1
    elif nearest_index > 0 and nearest_distance is not None and nearest_distance > reached_tolerance_tiles:
        # Sparse guides can make a future interaction point the nearest point
        # before the player has reached it. Keep it as the next step instead
        # of silently marking the route complete.
        progress_index = nearest_index - 1
    else:
        progress_index = nearest_index
    next_point = None
    for item in _list(guide.get("pathPoints")):
        idx = _int(_dict(item).get("orderIndex"), None)
        if idx is not None and idx > progress_index:
            next_point = item
            break
    next_interaction = None
    for item in _list(guide.get("interactionSteps")):
        world = _tile(_dict(item).get("world"))
        distance = tile_distance(current, world)
        if distance is not None and distance <= 8:
            next_interaction = dict(item)
            break
    if next_interaction and next_point:
        interaction_world = _tile(next_interaction.get("world"))
        point_world = _tile(_dict(next_point).get("world"))
        interaction_distance = tile_distance(current, interaction_world)
        point_distance = tile_distance(current, point_world)
        if point_distance is not None and interaction_distance is not None and point_distance + 1 < interaction_distance:
            next_interaction = None
    status = "PASS" if next_point or next_interaction else "WARN"
    blocker = None if status == "PASS" else "route_guide_next_step_missing"
    return {
        "schema": "route_guide_progress.v1",
        "status": status,
        "routeGuideLoaded": True,
        "routeGuideName": guide.get("routeName"),
        "currentWorld": current,
        "nearestGuidePoint": nearest,
        "routeGuideProgressIndex": progress_index,
        "skippedReachedGuidePoints": [idx for idx in range(0, max(progress_index, -1) + 1)],
        "nextGuidePoint": next_point,
        "nextGuideInteraction": next_interaction,
        "guideProgressReason": "nearest_reached" if reached_indices else "between_sparse_demonstrated_points",
        "blocker": blocker,
    }


def build_default_guides(*, out_dir: str | Path = DEFAULT_GUIDE_DIR) -> list[Path]:
    specs = {
        "Bank_to_Woodcutting_area": [
            REPO_ROOT / "recordings" / "20260606_094608_manual_route-bank_to_woodcutting_area_v2",
            REPO_ROOT / "recordings" / "20260606_121630_bank_to_WC",
            REPO_ROOT / "recordings" / "20260606_201613_Bank_to_tree_area",
        ],
        "woodcutting_area_to_bank": [
            REPO_ROOT / "recordings" / "20260607_104613_Woodcutting_area_to_bank",
        ],
    }
    written: list[Path] = []
    for name, recordings in specs.items():
        guide = build_route_guide(recordings, route_name=name)
        written.append(save_route_guide(guide, Path(out_dir) / guide_filename(name)))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build route demonstration guides from analyzed recordings.")
    parser.add_argument("--recordings", nargs="*", default=[], help="Recording folders to use.")
    parser.add_argument("--route-name", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--write-default-guides", action="store_true")
    parser.add_argument("--out-dir", default=str(DEFAULT_GUIDE_DIR))
    args = parser.parse_args(argv)
    if args.write_default_guides:
        written = build_default_guides(out_dir=args.out_dir)
        print(json.dumps({"schema": "route_demonstration_write_result.v1", "status": "PASS", "written": [str(p) for p in written]}, indent=2))
        return 0
    if not args.recordings:
        parser.error("--recordings is required unless --write-default-guides is used")
    guide = build_route_guide([Path(item) for item in args.recordings], route_name=args.route_name)
    out = Path(args.out) if args.out else guide_path_for(str(guide.get("routeName") or args.route_name or "route_unknown"), root=args.out_dir)
    save_route_guide(guide, out)
    print(json.dumps({"schema": "route_demonstration_write_result.v1", "status": guide.get("status"), "routeName": guide.get("routeName"), "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import route_template
import telemetry_sources


SCHEMA_VERSION = "route_monitor_status.v1"
SESSION_STATE_SCHEMA = "route_session_state.v1"
SESSION_EVENT_SCHEMA = "route_session_event.v1"
TIMELINE_SCHEMA = "route_progress_timeline.v1"
HISTORY_SUMMARY_SCHEMA = "route_history_summary.v1"
DEFAULT_MAX_SOURCE_AGE_MS = 5000
DEFAULT_CORRIDOR_TOLERANCE_TILES = 18
DEFAULT_ROUTE_HISTORY_ROOT = Path.home() / ".osrs-telemetry" / "route_monitor"
DEFAULT_END_PROXIMITY_TOLERANCE_TILES = 8
DEFAULT_MIN_DISTANCE_AFTER_TRANSITION_FOR_ARRIVAL = 10.0
DEFAULT_MIN_FRESH_END_AREA_SAMPLES = 2
DEFAULT_MIN_FRESH_SAMPLES_NEAR_END_CLUSTER = 1
DEFAULT_MIN_TICKS_AFTER_TRANSITION_FOR_ARRIVAL = 2


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _world(value: Any) -> dict[str, int] | None:
    record = _dict(value)
    nested = _dict(_first(record.get("world"), record.get("worldPoint"), record.get("worldLocation")))
    if nested:
        record = nested
    x = _int(_first(record.get("worldX"), record.get("x")))
    y = _int(_first(record.get("worldY"), record.get("y")))
    plane = _int(record.get("plane"))
    if x is None or y is None:
        return None
    result = {"worldX": x, "worldY": y}
    if plane is not None:
        result["plane"] = plane
    return result


def _public_world(value: Any) -> dict[str, int] | None:
    world = _world(value)
    if not world:
        return None
    result = {"x": world["worldX"], "y": world["worldY"]}
    if world.get("plane") is not None:
        result["plane"] = world.get("plane")
    return result


def _world_key(value: Any) -> tuple[int | None, int | None, int | None] | None:
    world = _world(value)
    if not world:
        return None
    return world.get("worldX"), world.get("worldY"), world.get("plane")


def _distance(a: Any, b: Any) -> float | None:
    aw = _world(a)
    bw = _world(b)
    if not aw or not bw:
        return None
    return round(math.hypot(float(aw["worldX"] - bw["worldX"]), float(aw["worldY"] - bw["worldY"])), 3)


def _name(record: dict[str, Any]) -> str | None:
    value = _first(record.get("name"), record.get("effectiveName"), record.get("targetName"), record.get("objectName"))
    text = str(value or "").strip()
    return text or None


def _required_segments(template: dict[str, Any]) -> list[dict[str, Any]]:
    return [_dict(item) for item in _list(template.get("segments")) if _dict(item).get("required", True)]


def _segment_ref(segment: dict[str, Any]) -> dict[str, Any]:
    action = _dict(segment.get("primaryAction"))
    return {
        "segmentIndex": segment.get("segmentIndex"),
        "segmentType": segment.get("segmentType"),
        "label": segment.get("label"),
        "required": bool(segment.get("required", True)),
        "primaryAction": {
            "option": action.get("option"),
            "target": action.get("target"),
            "targetKind": action.get("targetKind"),
        },
    }


def _load_template(template: str | Path | dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(template, dict):
        return template, None
    resolution = route_template.resolve_route_template(template)
    return _dict(resolution.get("template")), resolution.get("resolvedPath")


def _resolve_template(template: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(template, dict):
        required_count = len(_required_segments(template))
        warnings: list[str] = []
        if not template.get("routeName"):
            warnings.append("route template is missing routeName")
        if template.get("templateRevision") is None:
            warnings.append("route template is missing templateRevision")
        if required_count <= 0:
            warnings.append("route template has no required segments")
        return {
            "schema": route_template.RESOLUTION_SCHEMA,
            "input": "<dict>",
            "resolvedPath": None,
            "exists": True,
            "routeName": template.get("routeName"),
            "templateRevision": template.get("templateRevision"),
            "requiredSegmentCount": required_count,
            "status": "PASS" if not warnings else "FAIL",
            "warnings": warnings,
            "candidatesTried": [],
            "template": template,
        }
    return route_template.resolve_route_template(template)


def _is_auto_template_input(template: Any) -> bool:
    return str(template or "").strip().lower() == "auto"


def _auto_template_unavailable_status(selection: dict[str, Any], *, mode: str, route_state: str = "unknown") -> dict[str, Any]:
    warnings = _list(selection.get("warnings"))
    return {
        "schema": SCHEMA_VERSION,
        "status": "WARN",
        "routeName": selection.get("routeName"),
        "templateInput": "auto",
        "templatePath": None,
        "templateRevision": None,
        "requiredSegmentCount": 0,
        "templateResolution": {},
        "routeTemplateAutoSelection": {key: value for key, value in selection.items() if key != "template"},
        "mode": mode,
        "routeState": route_state,
        "currentArea": selection.get("currentArea") or selection.get("startArea"),
        "startAreaMatched": False,
        "endAreaMatched": False,
        "currentSegmentIndex": None,
        "currentSegmentLabel": None,
        "nextExpectedSegment": None,
        "completedSegments": [],
        "remainingSegments": [],
        "completedSegmentCount": 0,
        "remainingSegmentCount": 0,
        "offRoute": False,
        "offRouteReasons": [],
        "freshness": selection.get("freshness") or {"sourceAgeMs": None, "latestTick": None, "latestExportSeq": None, "status": "unknown"},
        "evidence": _list(selection.get("evidence")),
        "warnings": warnings,
        "missingCapabilities": ["route_template"],
    }


def _template_config_failure(resolution: dict[str, Any], *, mode: str) -> dict[str, Any]:
    warnings = list(resolution.get("warnings") or [])
    warnings.append("Route monitor did not load a valid template; live segment completion is not trustworthy.")
    return {
        "schema": SCHEMA_VERSION,
        "status": "FAIL",
        "routeName": resolution.get("routeName"),
        "templateInput": resolution.get("input"),
        "templatePath": resolution.get("resolvedPath"),
        "templateRevision": resolution.get("templateRevision"),
        "requiredSegmentCount": resolution.get("requiredSegmentCount") or 0,
        "templateResolution": {key: value for key, value in resolution.items() if key != "template"},
        "mode": mode,
        "routeState": "blocked",
        "currentArea": None,
        "startAreaMatched": False,
        "endAreaMatched": False,
        "currentSegmentIndex": None,
        "currentSegmentLabel": None,
        "nextExpectedSegment": None,
        "completedSegments": [],
        "remainingSegments": [],
        "completedSegmentCount": 0,
        "remainingSegmentCount": 0,
        "offRoute": False,
        "offRouteReasons": [],
        "freshness": {"sourceAgeMs": None, "latestTick": None, "latestExportSeq": None, "status": "unknown"},
        "evidence": [],
        "warnings": warnings,
        "missingCapabilities": ["route_template"],
    }


def _source_age_ms_from_path(path: str | Path, *, now: float) -> float | None:
    try:
        return max(0.0, (now - Path(path).stat().st_mtime) * 1000.0)
    except OSError:
        return None


def freshness_from_sources(context: dict[str, Any], *, max_age_ms: int = DEFAULT_MAX_SOURCE_AGE_MS) -> dict[str, Any]:
    now = time.time()
    source_files = _list(context.get("sourceFiles"))
    ages: list[float] = []
    latest_tick = _first(
        _dict(context.get("status")).get("latestTick"),
        _dict(context.get("status")).get("latestTickProcessed"),
        _dict(context.get("baseline")).get("latestTick"),
        context.get("latestTick"),
    )
    latest_export = _first(
        _dict(context.get("status")).get("latestExportSequence"),
        _dict(context.get("status")).get("compactPacketLastSequence"),
        _dict(context.get("status")).get("latestSequence"),
        _dict(context.get("baseline")).get("latestExportSequence"),
        context.get("latestExportSequence"),
    )
    for source in source_files:
        record = _dict(source)
        if not record:
            continue
        age = _first(record.get("ageMillis"), record.get("age_ms"))
        if age is None and record.get("age_seconds") is not None:
            seconds = _float(record.get("age_seconds"))
            age = seconds * 1000.0 if seconds is not None else None
        if age is None and record.get("path"):
            age = _source_age_ms_from_path(record.get("path"), now=now)
        if age is not None:
            ages.append(float(age))
    explicit = _dict(context.get("freshness"))
    if not ages and explicit.get("sourceAgeMs") is not None:
        ages.append(float(explicit.get("sourceAgeMs")))
    latest_age = min(ages) if ages else None
    if latest_age is None:
        status = "unknown"
    elif latest_age <= max_age_ms:
        status = "fresh"
    else:
        status = "stale"
    return {
        "sourceAgeMs": round(latest_age, 1) if latest_age is not None else None,
        "latestTick": _int(latest_tick),
        "latestExportSeq": _int(latest_export),
        "status": status,
        "maxAgeMs": max_age_ms,
    }


def _object_records_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    buckets: list[Any] = [
        context.get("nearby_objects"),
        context.get("nearbyObjects"),
        context.get("route_objects"),
        context.get("routeObjects"),
        _dict(context.get("context")).get("nearby_objects"),
        _dict(context.get("context")).get("nearbyObjects"),
        _dict(context.get("context")).get("route_objects"),
        _dict(context.get("context")).get("routeObjects"),
        _dict(context.get("status")).get("routeObjects"),
        _dict(context.get("status")).get("nearbyObjects"),
        context.get("candidates"),
    ]
    for bucket in buckets:
        for item in _list(bucket):
            if isinstance(item, dict):
                records.append(item)
    return records


def _player_world_from_context(context: dict[str, Any]) -> dict[str, int] | None:
    baseline = _dict(context.get("baseline"))
    player = _dict(_first(baseline.get("player"), context.get("player"), _dict(context.get("status")).get("player")))
    world = _world(player)
    if world:
        return world
    return _world(_first(player.get("worldPoint"), player.get("worldLocation"), baseline.get("playerWorldLocation")))


def _area_from_objects(objects: list[dict[str, Any]]) -> str | None:
    names = [(_name(record) or "").lower() for record in objects]
    bank_score = sum(1 for name in names if "bank booth" in name or "deposit box" in name or name == "bank")
    tree_score = sum(1 for name in names if "tree" in name)
    if bank_score and bank_score >= max(1, tree_score):
        return "bank_area"
    if tree_score:
        return "woodcutting_area"
    return None


def _near_template_area(world: dict[str, Any] | None, area: dict[str, Any]) -> bool:
    if not world:
        return False
    cluster = _dict(area.get("endCluster"))
    target = _world(_first(cluster.get("world"), area.get("world")))
    if not target:
        return False
    tolerance = float(_first(cluster.get("toleranceTiles"), area.get("toleranceTiles"), 8) or 8)
    distance = _distance(world, target)
    return distance is not None and distance <= tolerance


def infer_current_area(context: dict[str, Any], template: dict[str, Any]) -> tuple[str | None, list[str]]:
    evidence: list[str] = []
    world = _player_world_from_context(context)
    objects = _object_records_from_context(context)
    area = _area_from_objects(objects)
    if area:
        evidence.append(f"object evidence matched {area}")
        return area, evidence
    if _near_template_area(world, _dict(template.get("start"))):
        area = _dict(template.get("start")).get("areaLabel")
        evidence.append(f"player world matched template start area {area}")
        return area, evidence
    if _near_template_area(world, _dict(template.get("end"))):
        area = _dict(template.get("end")).get("areaLabel")
        evidence.append(f"player world matched template end area {area}")
        return area, evidence
    if world and world.get("plane") is not None:
        area = f"plane_{world.get('plane')}"
        evidence.append(f"only plane evidence available: {area}")
        return area, evidence
    return None, evidence


def infer_current_area_for_auto_template(context: dict[str, Any]) -> tuple[str | None, list[str]]:
    evidence: list[str] = []
    world = _player_world_from_context(context)
    objects = _object_records_from_context(context)
    area = _area_from_objects(objects)
    if area:
        evidence.append(f"object evidence matched {area}")
        return area, evidence
    for item in route_template.list_route_templates():
        template_payload = _json(item.get("path") or "")
        start = _dict(template_payload.get("start"))
        end = _dict(template_payload.get("end"))
        if _near_template_area(world, start):
            area = start.get("areaLabel")
            evidence.append(f"player world matched template start area {area}")
            return area, evidence
        if _near_template_area(world, end):
            area = end.get("areaLabel")
            evidence.append(f"player world matched template end area {area}")
            return area, evidence
    if world and world.get("plane") is not None:
        evidence.append(f"only plane evidence available: plane_{world.get('plane')}")
    return None, evidence


def resolve_auto_template_for_live_context(context: dict[str, Any], *, max_age_ms: int = DEFAULT_MAX_SOURCE_AGE_MS) -> dict[str, Any]:
    freshness = freshness_from_sources(context, max_age_ms=max_age_ms)
    current_area, evidence = infer_current_area_for_auto_template(context)
    warnings: list[str] = []
    if freshness.get("status") == "stale":
        warnings.append(f"telemetry stale: sourceAgeMs={freshness.get('sourceAgeMs')}")
    if not current_area:
        warnings.append("current area is unknown; auto route template was not selected")
    candidates = route_template.list_route_templates()
    start_matches = [item for item in candidates if item.get("startArea") == current_area]
    selected = start_matches[0] if freshness.get("status") != "stale" and start_matches else None
    if current_area and not selected:
        warnings.append(f"no route template starts at current area {current_area}")
    resolution = route_template.resolve_route_template(selected.get("path")) if selected else {}
    if selected and resolution.get("status") != "PASS":
        warnings.extend(_list(resolution.get("warnings")))
    return {
        "schema": route_template.AUTO_SELECTION_SCHEMA,
        "status": "PASS" if selected and resolution.get("status") == "PASS" else "WARN",
        "routeName": selected.get("routeName") if selected else None,
        "currentArea": current_area,
        "startArea": current_area,
        "endArea": selected.get("endArea") if selected else None,
        "selectedTemplate": selected.get("path") if selected else None,
        "selectedTemplateRouteName": selected.get("routeName") if selected else None,
        "selectedTemplateRevision": selected.get("templateRevision") if selected else None,
        "selectionReason": "current_start_area_match" if selected else "none",
        "alternatives": [item for item in candidates if not selected or item.get("path") != selected.get("path")],
        "warnings": warnings,
        "untemplatedRoute": selected is None,
        "suggestedTemplateName": f"{current_area}_to_unknown" if current_area else "route_unknown",
        "freshness": freshness,
        "evidence": evidence,
        "resolution": {key: value for key, value in resolution.items() if key != "template"},
        "template": _dict(resolution.get("template")),
    }


def _in_route_corridor(world: dict[str, Any] | None, template: dict[str, Any], *, tolerance: int = DEFAULT_CORRIDOR_TOLERANCE_TILES) -> bool:
    if not world:
        return False
    start = _world(_dict(template.get("start")).get("world"))
    end = _world(_dict(template.get("end")).get("world"))
    if not start or not end:
        return False
    x = int(world.get("worldX"))
    y = int(world.get("worldY"))
    min_x = min(start["worldX"], end["worldX"]) - tolerance
    max_x = max(start["worldX"], end["worldX"]) + tolerance
    min_y = min(start["worldY"], end["worldY"]) - tolerance
    max_y = max(start["worldY"], end["worldY"]) + tolerance
    if not (min_x <= x <= max_x and min_y <= y <= max_y):
        return False
    plane = world.get("plane")
    known_planes = {item.get("plane") for item in (start, end) if item.get("plane") is not None}
    return plane is None or not known_planes or plane in known_planes


def _completed_for_live(template: dict[str, Any], *, route_state: str, world: dict[str, Any] | None) -> list[dict[str, Any]]:
    segments = _required_segments(template)
    if route_state == "arrived":
        return [_segment_ref(segment) for segment in segments]
    if route_state == "ready_at_start":
        return [_segment_ref(segments[0])] if segments else []
    if route_state not in {"in_progress", "segment_complete"}:
        return []
    plane = world.get("plane") if world else None
    completed: list[dict[str, Any]] = []
    for segment in segments:
        segment_type = str(segment.get("segmentType") or "")
        completed.append(_segment_ref(segment))
        if segment_type in {"stair_transition", "ladder_transition", "plane_transition"}:
            if plane == _dict(template.get("start")).get("world", {}).get("plane"):
                completed.pop()
            break
    if plane == _dict(template.get("end")).get("world", {}).get("plane"):
        for segment in segments:
            if str(segment.get("segmentType") or "") in {"stair_transition", "ladder_transition", "plane_transition"}:
                required_index = segment.get("segmentIndex")
                completed_indexes = {item.get("segmentIndex") for item in completed}
                if required_index not in completed_indexes:
                    completed.append(_segment_ref(segment))
                break
    return completed


def _remaining_segments(template: dict[str, Any], completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed_indexes = {item.get("segmentIndex") for item in completed}
    return [_segment_ref(segment) for segment in _required_segments(template) if segment.get("segmentIndex") not in completed_indexes]


def _base_status(
    *,
    template: dict[str, Any],
    template_path: str | None,
    template_input: Any = None,
    template_resolution: dict[str, Any] | None = None,
    mode: str,
    route_state: str,
    current_area: str | None,
    completed: list[dict[str, Any]],
    remaining: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    off_route: bool = False,
    off_route_reasons: list[str] | None = None,
    evidence: list[str] | None = None,
    warnings: list[str] | None = None,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    if route_state == "stale":
        status = "WARN"
    elif route_state == "off_route" or off_route:
        status = "FAIL"
    elif route_state in {"unknown", "blocked"}:
        status = "WARN"
    else:
        status = "PASS"
    next_segment = remaining[0] if remaining else None
    current_segment = completed[-1] if completed else None
    start_area = _dict(template.get("start")).get("areaLabel")
    end_area = _dict(template.get("end")).get("areaLabel")
    return {
        "schema": SCHEMA_VERSION,
        "status": status,
        "routeName": template.get("routeName"),
        "templateInput": template_input,
        "templatePath": template_path,
        "templateRevision": template.get("templateRevision"),
        "requiredSegmentCount": len(_required_segments(template)),
        "templateResolution": template_resolution,
        "mode": mode,
        "routeState": route_state,
        "currentArea": current_area,
        "startAreaMatched": bool(current_area and current_area == start_area),
        "endAreaMatched": bool(current_area and current_area == end_area),
        "currentSegmentIndex": current_segment.get("segmentIndex") if current_segment else 0,
        "currentSegmentLabel": current_segment.get("label") if current_segment else None,
        "nextExpectedSegment": next_segment,
        "completedSegments": completed,
        "remainingSegments": remaining,
        "completedSegmentCount": len(completed),
        "remainingSegmentCount": len(remaining),
        "offRoute": bool(off_route),
        "offRouteReasons": off_route_reasons or [],
        "freshness": freshness or {"sourceAgeMs": None, "latestTick": None, "latestExportSeq": None, "status": "unknown"},
        "evidence": evidence or [],
        "warnings": warnings or [],
        "missingCapabilities": missing or [],
    }


def monitor_live_context(
    template: str | Path | dict[str, Any],
    context: dict[str, Any],
    *,
    max_age_ms: int = DEFAULT_MAX_SOURCE_AGE_MS,
) -> dict[str, Any]:
    if _is_auto_template_input(template):
        selection = resolve_auto_template_for_live_context(context, max_age_ms=max_age_ms)
        if selection.get("status") != "PASS" or not selection.get("selectedTemplate"):
            route_state = "stale" if _dict(selection.get("freshness")).get("status") == "stale" else "unknown"
            return _auto_template_unavailable_status(selection, mode="live", route_state=route_state)
        template = str(selection.get("selectedTemplate"))
    resolution = _resolve_template(template)
    if resolution.get("status") != "PASS":
        return _template_config_failure(resolution, mode="live")
    template_payload = _dict(resolution.get("template"))
    template_path = resolution.get("resolvedPath")
    warnings = []
    missing = []
    evidence = [f"loaded template {template_payload.get('routeName')} revision {template_payload.get('templateRevision')}"]
    freshness = freshness_from_sources(context, max_age_ms=max_age_ms)
    world = _player_world_from_context(context)
    if not world:
        missing.append("player_world_point")
        warnings.append("player world point is unavailable")
    else:
        evidence.append(f"player world {world.get('worldX')},{world.get('worldY')},{world.get('plane')}")
    current_area, area_evidence = infer_current_area(context, template_payload)
    evidence.extend(area_evidence)
    start_area = _dict(template_payload.get("start")).get("areaLabel")
    end_area = _dict(template_payload.get("end")).get("areaLabel")
    distance_to_end = _distance_to_end_cluster(template_payload, world)
    requires_end_cluster = _has_end_cluster(template_payload)
    end_tolerance = _end_cluster_tolerance(_dict(template_payload.get("end"))) if requires_end_cluster else _area_tolerance(_dict(template_payload.get("end")))
    near_end_cluster = distance_to_end is not None and distance_to_end <= end_tolerance
    arrival_gate_status = "not_applicable"
    if freshness.get("status") == "stale":
        route_state = "stale"
        warnings.append(f"telemetry stale: sourceAgeMs={freshness.get('sourceAgeMs')}")
    elif not world:
        route_state = "unknown"
    elif current_area == start_area:
        route_state = "ready_at_start"
    elif current_area == end_area:
        if near_end_cluster:
            route_state = "arrived"
            arrival_gate_status = "passed"
            evidence.append("player world is near the template end cluster")
        else:
            route_state = "in_progress"
            arrival_gate_status = "waiting"
            warnings.append("end area label matched, but player is not near the route end cluster")
    elif _in_route_corridor(world, template_payload):
        route_state = "in_progress"
        evidence.append("player world is inside the simple route corridor")
    else:
        route_state = "off_route"
        warnings.append("player is outside the simple route corridor")
    off_route = route_state == "off_route"
    off_route_reasons = ["outside_route_corridor"] if off_route else []
    completed = _completed_for_live(template_payload, route_state=route_state, world=world)
    remaining = _remaining_segments(template_payload, completed)
    payload = _base_status(
        template=template_payload,
        template_path=template_path,
        template_input=resolution.get("input"),
        template_resolution={key: value for key, value in resolution.items() if key != "template"},
        mode="live",
        route_state=route_state,
        current_area=current_area,
        completed=completed,
        remaining=remaining,
        freshness=freshness,
        off_route=off_route,
        off_route_reasons=off_route_reasons,
        evidence=evidence,
        warnings=warnings,
        missing=missing,
    )
    payload["arrivalGateStatus"] = arrival_gate_status
    payload["distanceToEndCluster"] = round(distance_to_end, 3) if distance_to_end is not None else None
    payload["endClusterToleranceTiles"] = round(float(end_tolerance), 3)
    payload["arrivalGateRequiresEndCluster"] = bool(requires_end_cluster)
    payload["nearEndCluster"] = bool(near_end_cluster)
    payload["arrivalGateWarnings"] = ["waiting for end-cluster proximity"] if arrival_gate_status == "waiting" else []
    if "selection" in locals():
        payload["routeTemplateAutoSelection"] = {key: value for key, value in selection.items() if key != "template"}
    return payload


def _completed_from_comparison(template: dict[str, Any], comparison: dict[str, Any]) -> list[dict[str, Any]]:
    matched_statuses = {"matched", "partial", "matched_variant", "variant_candidate"}
    matched_indexes = {
        item.get("templateSegmentIndex")
        for item in _list(comparison.get("segmentMatches"))
        if _dict(item).get("matchStatus") in matched_statuses
    }
    return [_segment_ref(segment) for segment in _required_segments(template) if segment.get("segmentIndex") in matched_indexes]


def monitor_recording(
    template: str | Path | dict[str, Any],
    recording: str | Path,
    *,
    lifecycle: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recording_path = Path(recording)
    lifecycle = lifecycle if isinstance(lifecycle, dict) and lifecycle else _json(recording_path / "traversal_lifecycle.json")
    provided_comparison = isinstance(comparison, dict) and bool(comparison)
    selection: dict[str, Any] | None = None
    if _is_auto_template_input(template):
        selection = route_template.resolve_template_auto(lifecycle)
        if selection.get("status") != "PASS" or not selection.get("selectedTemplate"):
            return _auto_template_unavailable_status(selection, mode="recording")
        template = str(selection.get("selectedTemplate"))
    resolution = _resolve_template(template)
    if resolution.get("status") != "PASS":
        return _template_config_failure(resolution, mode="recording")
    template_payload = _dict(resolution.get("template"))
    template_path = resolution.get("resolvedPath")
    comparison = comparison if provided_comparison else _json(recording_path / "route_template_comparison.json")
    if (
        comparison
        and comparison.get("templateName")
        and template_payload.get("routeName")
        and comparison.get("templateName") != template_payload.get("routeName")
        and not provided_comparison
    ):
        comparison = {}
    if not comparison and lifecycle:
        comparison = route_template.compare_template(template_payload, lifecycle, recording=recording_path)
    warnings = []
    missing = []
    evidence = [f"recording {recording_path}", f"loaded template {template_payload.get('routeName')} revision {template_payload.get('templateRevision')}"]
    if not lifecycle:
        missing.append("traversal_lifecycle")
        warnings.append("traversal_lifecycle.json is unavailable")
    if not comparison:
        missing.append("route_template_comparison")
        warnings.append("route_template_comparison.json is unavailable")
    completed = _completed_from_comparison(template_payload, comparison)
    remaining = _remaining_segments(template_payload, completed)
    end_area = _dict(lifecycle.get("end")).get("areaLabel") if lifecycle else None
    if comparison.get("status") == "FAIL" and not comparison.get("endAreaMatched"):
        route_state = "off_route"
    elif remaining and comparison.get("endAreaMatched"):
        route_state = "arrived"
        warnings.append("endpoint reached with incomplete required segment evidence")
    elif remaining:
        route_state = "blocked"
    elif comparison.get("endAreaMatched") or lifecycle.get("phase") == "arrived":
        route_state = "arrived"
    elif comparison.get("startAreaMatched"):
        route_state = "ready_at_start"
    else:
        route_state = "unknown"
    status_payload = _base_status(
        template=template_payload,
        template_path=template_path,
        template_input=resolution.get("input"),
        template_resolution={key: value for key, value in resolution.items() if key != "template"},
        mode="recording",
        route_state=route_state,
        current_area=end_area or _dict(lifecycle.get("start")).get("areaLabel") if lifecycle else None,
        completed=completed,
        remaining=remaining,
        freshness={"sourceAgeMs": None, "latestTick": None, "latestExportSeq": None, "status": "recording"},
        off_route=route_state == "off_route",
        off_route_reasons=["wrong_endpoint"] if route_state == "off_route" else [],
        evidence=evidence,
        warnings=warnings,
        missing=missing,
    )
    if comparison:
        status_payload["startAreaMatched"] = bool(comparison.get("startAreaMatched"))
        status_payload["endAreaMatched"] = bool(comparison.get("endAreaMatched"))
        status_payload["comparisonStatus"] = comparison.get("status")
        status_payload["comparisonStatusReason"] = comparison.get("statusReason")
        status_payload["comparisonScore"] = comparison.get("score")
        status_payload["missingSegments"] = comparison.get("missingSegments") or []
        status_payload["extraSegments"] = comparison.get("extraSegments") or []
        status_payload["allowedExtraSegments"] = comparison.get("allowedExtraSegments") or []
        status_payload["routeTemplateDirectionMismatch"] = bool(comparison.get("routeTemplateDirectionMismatch") or comparison.get("directionMismatch"))
        if comparison.get("status") == "FAIL":
            status_payload["status"] = "FAIL"
        elif comparison.get("status") == "WARN" or remaining:
            status_payload["status"] = "WARN"
    if selection:
        status_payload["routeTemplateAutoSelection"] = {key: value for key, value in selection.items() if key != "template"}
    return status_payload


def monitor_comparison(template: str | Path | dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    resolution = _resolve_template(template)
    if resolution.get("status") != "PASS":
        return _template_config_failure(resolution, mode="comparison")
    template_payload = _dict(resolution.get("template"))
    template_path = resolution.get("resolvedPath")
    completed = _completed_from_comparison(template_payload, comparison)
    remaining = _remaining_segments(template_payload, completed)
    if comparison.get("status") == "FAIL" and not comparison.get("endAreaMatched"):
        route_state = "off_route"
    elif not remaining and comparison.get("endAreaMatched"):
        route_state = "arrived"
    elif remaining and comparison.get("endAreaMatched"):
        route_state = "arrived"
    elif remaining:
        route_state = "blocked"
    else:
        route_state = "unknown"
    payload = _base_status(
        template=template_payload,
        template_path=template_path,
        template_input=resolution.get("input"),
        template_resolution={key: value for key, value in resolution.items() if key != "template"},
        mode="comparison",
        route_state=route_state,
        current_area=_dict(template_payload.get("end")).get("areaLabel") if comparison.get("endAreaMatched") else None,
        completed=completed,
        remaining=remaining,
        off_route=route_state == "off_route",
        off_route_reasons=["wrong_endpoint"] if route_state == "off_route" else [],
        evidence=[f"comparison status {comparison.get('status')} reason {comparison.get('statusReason')}"],
        warnings=comparison.get("warnings") or [],
    )
    payload["comparisonStatus"] = comparison.get("status")
    payload["comparisonStatusReason"] = comparison.get("statusReason")
    payload["comparisonScore"] = comparison.get("score")
    payload["startAreaMatched"] = bool(comparison.get("startAreaMatched"))
    payload["endAreaMatched"] = bool(comparison.get("endAreaMatched"))
    if comparison.get("status") == "FAIL":
        payload["status"] = "FAIL"
    elif comparison.get("status") == "WARN":
        payload["status"] = "WARN"
    if remaining and comparison.get("endAreaMatched"):
        payload["warnings"] = list(payload.get("warnings") or []) + [
            "endpoint reached with incomplete required segment evidence"
        ]
    return payload


def _safe_name(value: Any) -> str:
    text = str(value or "route").strip() or "route"
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)
    return cleaned.strip("_") or "route"


def default_session_id(route_name: Any = None, *, now: str | None = None) -> str:
    stamp = (now or _utc_now()).replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
    stamp = stamp.split(".")[0]
    return f"route_{stamp}"


def default_history_dir(template: dict[str, Any], session_id: str, *, out_dir: str | Path | None = None) -> Path:
    root = Path(out_dir).expanduser() if out_dir else DEFAULT_ROUTE_HISTORY_ROOT
    return root / _safe_name(template.get("routeName")) / session_id


def history_output_paths(
    base_dir: str | Path,
    *,
    state_out: str | Path | None = None,
    events_out: str | Path | None = None,
    timeline_out: str | Path | None = None,
    summary_out: str | Path | None = None,
) -> dict[str, Path]:
    base = Path(base_dir)
    return {
        "state": Path(state_out) if state_out else base / "route_session_state.json",
        "events": Path(events_out) if events_out else base / "route_session_events.jsonl",
        "timeline": Path(timeline_out) if timeline_out else base / "route_progress_timeline.jsonl",
        "summary": Path(summary_out) if summary_out else base / "route_history_summary.json",
    }


def _append_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=False, default=str) + "\n")


def _history_status_for_state(route_state: str, *, off_route: bool = False) -> str:
    if route_state == "off_route" or off_route:
        return "FAIL"
    if route_state in {"stale", "unknown", "blocked"}:
        return "WARN"
    return "PASS"


def _make_event(
    state: dict[str, Any],
    event_type: str,
    *,
    monotonic_time: float | None = None,
    wall_time_utc: str | None = None,
    route_state_before: str | None = None,
    route_state_after: str | None = None,
    segment: dict[str, Any] | None = None,
    world: dict[str, Any] | None = None,
    evidence: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    event_seq = int(state.get("eventCount") or 0) + 1
    state["eventCount"] = event_seq
    current_world = _world(world or state.get("currentWorld"))
    return {
        "schema": SESSION_EVENT_SCHEMA,
        "sessionId": state.get("sessionId"),
        "eventSeq": event_seq,
        "eventType": event_type,
        "monotonicTime": round(float(monotonic_time if monotonic_time is not None else time.monotonic()), 6),
        "wallTimeUtc": wall_time_utc or _utc_now(),
        "tick": state.get("latestTick"),
        "exportSeq": state.get("latestExportSeq"),
        "world": _public_world(current_world),
        "plane": current_world.get("plane") if current_world else None,
        "routeStateBefore": route_state_before,
        "routeStateAfter": route_state_after or state.get("routeState"),
        "segmentIndex": segment.get("segmentIndex") if segment else None,
        "segmentLabel": segment.get("label") if segment else None,
        "evidence": list(evidence or []),
        "warnings": list(warnings or []),
    }


def _timeline_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": TIMELINE_SCHEMA,
        "sessionId": event.get("sessionId"),
        "eventSeq": event.get("eventSeq"),
        "eventType": event.get("eventType"),
        "monotonicTime": event.get("monotonicTime"),
        "wallTimeUtc": event.get("wallTimeUtc"),
        "tick": event.get("tick"),
        "exportSeq": event.get("exportSeq"),
        "world": event.get("world"),
        "plane": event.get("plane"),
        "routeState": event.get("routeStateAfter"),
        "segmentIndex": event.get("segmentIndex"),
        "segmentLabel": event.get("segmentLabel"),
    }


def create_session_state(
    template: str | Path | dict[str, Any],
    *,
    template_path: str | None = None,
    template_input: Any = None,
    template_resolution: dict[str, Any] | None = None,
    session_id: str | None = None,
    started_at_utc: str | None = None,
) -> dict[str, Any]:
    resolution = template_resolution or _resolve_template(template)
    template_payload = _dict(resolution.get("template")) or _dict(template)
    loaded_path = resolution.get("resolvedPath")
    path_text = template_path or loaded_path
    started = started_at_utc or _utc_now()
    completed: list[dict[str, Any]] = []
    remaining = _remaining_segments(template_payload, completed)
    resolution_public = {key: value for key, value in resolution.items() if key != "template"}
    return {
        "schema": SESSION_STATE_SCHEMA,
        "sessionId": session_id or default_session_id(template_payload.get("routeName"), now=started),
        "routeName": template_payload.get("routeName"),
        "templateInput": template_input if template_input is not None else resolution.get("input"),
        "templatePath": path_text,
        "templateRevision": template_payload.get("templateRevision"),
        "requiredSegmentCount": len(_required_segments(template_payload)),
        "templateResolution": resolution_public,
        "startedAtUtc": started,
        "updatedAtUtc": started,
        "routeState": "unknown",
        "previousNonStaleRouteState": None,
        "currentArea": None,
        "currentWorld": None,
        "latestTick": None,
        "latestExportSeq": None,
        "freshness": {
            "status": "unknown",
            "sourceAgeMs": None,
            "stalePeriodCount": 0,
            "longestStaleMs": 0,
        },
        "activeStaleStartedAtMonotonic": None,
        "currentSegmentIndex": None,
        "currentSegmentLabel": None,
        "nextExpectedSegment": remaining[0] if remaining else None,
        "completedSegments": completed,
        "remainingSegments": remaining,
        "recentPath": [],
        "planeChanges": [],
        "offRoute": False,
        "offRouteReasons": [],
        "offRouteConfidence": 0.0,
        "freshConflictCount": 0,
        "distanceMovedApprox": 0.0,
        "eventCount": 0,
        "arrivalCandidateWorld": None,
        "arrivalCandidateArea": None,
        "arrivalCandidateReason": None,
        "arrivalGateStatus": "not_started",
        "arrivalGateEvidence": [],
        "arrivalGateWarnings": [],
        "distanceToEndCluster": None,
        "endClusterToleranceTiles": None,
        "nearEndCluster": False,
        "nearEndClusterSampleCount": 0,
        "distanceAfterLastTransition": None,
        "arrivalGateRequiresEndCluster": False,
        "distanceOnlyProgressRejected": False,
        "arrivalGateRejectedReason": None,
        "arrivalGatePassedReason": None,
        "freshEndAreaSampleCount": 0,
        "arrivalCompletedAtWorld": None,
        "arrivalCompletedAtTick": None,
        "arrivalCompletedAtMonotonic": None,
        "lastTransitionWorld": None,
        "lastTransitionTick": None,
        "lastTransitionMonotonic": None,
        "secondWalkStartedAt": None,
        "secondWalkCompletedAt": None,
        "secondWalkDistance": 0.0,
        "secondWalkEvidence": [],
        "prematureArrivalPrevented": False,
        "duplicateArrivalEventsSuppressed": 0,
        "repeatedArrivalSamples": 0,
        "warnings": [],
        "evidence": [],
    }


def _completed_indexes(state: dict[str, Any]) -> set[Any]:
    return {item.get("segmentIndex") for item in _list(state.get("completedSegments")) if isinstance(item, dict)}


def _refresh_session_segments(state: dict[str, Any], template: dict[str, Any]) -> None:
    completed = [_dict(item) for item in _list(state.get("completedSegments")) if isinstance(item, dict)]
    remaining = _remaining_segments(template, completed)
    state["completedSegments"] = completed
    state["remainingSegments"] = remaining
    state["nextExpectedSegment"] = remaining[0] if remaining else None
    active = state["nextExpectedSegment"] or (completed[-1] if completed else None)
    state["currentSegmentIndex"] = active.get("segmentIndex") if isinstance(active, dict) else None
    state["currentSegmentLabel"] = active.get("label") if isinstance(active, dict) else None


def _complete_segment(
    state: dict[str, Any],
    template: dict[str, Any],
    segment: dict[str, Any],
    *,
    monotonic_time: float,
    wall_time_utc: str,
    world: dict[str, Any] | None,
    evidence: list[str],
    confidence: float = 0.7,
) -> dict[str, Any] | None:
    index = segment.get("segmentIndex")
    if index in _completed_indexes(state):
        return None
    entry = _segment_ref(segment)
    entry.update(
        {
            "startedAtUtc": state.get("startedAtUtc") if not state.get("completedSegments") else _list(state.get("completedSegments"))[-1].get("completedAtUtc"),
            "completedAtUtc": wall_time_utc,
            "completedAtMonotonic": round(monotonic_time, 6),
            "world": _public_world(world),
            "confidence": confidence,
            "evidence": evidence,
            "warnings": [],
        }
    )
    state.setdefault("completedSegments", []).append(entry)
    _refresh_session_segments(state, template)
    return _make_event(
        state,
        "segment_completed",
        monotonic_time=monotonic_time,
        wall_time_utc=wall_time_utc,
        segment=entry,
        world=world,
        evidence=evidence,
    )


def _complete_required_through(
    state: dict[str, Any],
    template: dict[str, Any],
    segment_index: Any,
    *,
    monotonic_time: float,
    wall_time_utc: str,
    world: dict[str, Any] | None,
    evidence: list[str],
    confidence: float = 0.7,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for segment in _required_segments(template):
        if segment.get("segmentIndex") is None:
            continue
        if int(segment.get("segmentIndex")) > int(segment_index):
            break
        event = _complete_segment(
            state,
            template,
            segment,
            monotonic_time=monotonic_time,
            wall_time_utc=wall_time_utc,
            world=world,
            evidence=evidence,
            confidence=confidence,
        )
        if event:
            events.append(event)
    return events


def _first_segment_by_type(template: dict[str, Any], types: set[str]) -> dict[str, Any] | None:
    for segment in _required_segments(template):
        if str(segment.get("segmentType") or "") in types:
            return segment
    return None


def _walk_segments(template: dict[str, Any]) -> list[dict[str, Any]]:
    return [segment for segment in _required_segments(template) if str(segment.get("segmentType") or "") == "walk_segment"]


def _area_world(area: dict[str, Any]) -> dict[str, int] | None:
    cluster = _dict(area.get("endCluster"))
    return _world(_first(cluster.get("world"), area.get("world"), area.get("centerWorld")))


def _end_cluster_world(area: dict[str, Any]) -> dict[str, int] | None:
    cluster = _dict(area.get("endCluster"))
    return _world(_first(cluster.get("world"), cluster.get("centerWorld"), cluster.get("center")))


def _area_tolerance(area: dict[str, Any], *, default: float = DEFAULT_END_PROXIMITY_TOLERANCE_TILES) -> float:
    cluster = _dict(area.get("endCluster"))
    value = _first(cluster.get("toleranceTiles"), area.get("toleranceTiles"), default)
    return float(_float(value) or default)


def _end_cluster_tolerance(area: dict[str, Any], *, default: float = DEFAULT_END_PROXIMITY_TOLERANCE_TILES) -> float:
    cluster = _dict(area.get("endCluster"))
    value = _first(cluster.get("toleranceTiles"), area.get("endProximityToleranceTiles"), default)
    return float(_float(value) or default)


def _has_end_cluster(template: dict[str, Any]) -> bool:
    return _end_cluster_world(_dict(template.get("end"))) is not None


def _distance_to_end_cluster(template: dict[str, Any], world: dict[str, Any] | None) -> float | None:
    end = _dict(template.get("end"))
    end_world = _end_cluster_world(end)
    return _distance(world, end_world) if world and end_world else None


def _arrival_segment(template: dict[str, Any]) -> dict[str, Any] | None:
    return _first_segment_by_type(template, {"area_arrival"})


def _transition_segment(template: dict[str, Any]) -> dict[str, Any] | None:
    return _first_segment_by_type(template, {"stair_transition", "ladder_transition", "plane_transition"})


def _second_walk_segment(template: dict[str, Any]) -> dict[str, Any] | None:
    walks = _walk_segments(template)
    return walks[-1] if len(walks) > 1 else None


def _set_arrival_gate(
    state: dict[str, Any],
    *,
    status: str,
    world: dict[str, Any] | None,
    area: str | None,
    reason: str | None = None,
    evidence: list[str] | None = None,
    warnings: list[str] | None = None,
    distance_to_end: float | None = None,
    distance_after_transition: float | None = None,
    requires_end_cluster: bool | None = None,
    end_cluster_tolerance: float | None = None,
    near_end_cluster: bool | None = None,
    distance_only_progress_rejected: bool | None = None,
    rejected_reason: str | None = None,
    passed_reason: str | None = None,
    monotonic_time: float | None = None,
    tick: Any = None,
) -> None:
    state["arrivalGateStatus"] = status
    state["arrivalCandidateWorld"] = _public_world(world)
    state["arrivalCandidateArea"] = area
    state["arrivalCandidateReason"] = reason
    state["arrivalGateEvidence"] = evidence or []
    state["arrivalGateWarnings"] = warnings or []
    state["distanceToEndCluster"] = round(distance_to_end, 3) if distance_to_end is not None else None
    state["distanceAfterLastTransition"] = round(distance_after_transition, 3) if distance_after_transition is not None else None
    if requires_end_cluster is not None:
        state["arrivalGateRequiresEndCluster"] = bool(requires_end_cluster)
    if end_cluster_tolerance is not None:
        state["endClusterToleranceTiles"] = round(float(end_cluster_tolerance), 3)
    if near_end_cluster is not None:
        state["nearEndCluster"] = bool(near_end_cluster)
    if near_end_cluster:
        state["nearEndClusterSampleCount"] = int(state.get("nearEndClusterSampleCount") or 0) + 1
    if distance_only_progress_rejected is not None:
        state["distanceOnlyProgressRejected"] = bool(distance_only_progress_rejected)
    state["arrivalGateRejectedReason"] = rejected_reason
    state["arrivalGatePassedReason"] = passed_reason
    if status == "passed":
        state["arrivalCompletedAtWorld"] = _public_world(world)
        state["arrivalCompletedAtTick"] = tick
        state["arrivalCompletedAtMonotonic"] = round(float(monotonic_time), 6) if monotonic_time is not None else None


def _arrival_gate_result(state: dict[str, Any], template: dict[str, Any], *, world: dict[str, Any] | None, current_area: str | None) -> dict[str, Any]:
    end_area = _dict(template.get("end")).get("areaLabel")
    transition = _transition_segment(template)
    second_walk = _second_walk_segment(template)
    transition_completed = bool(transition and transition.get("segmentIndex") in _completed_indexes(state))
    second_walk_started = bool(state.get("secondWalkStartedAt"))
    second_walk_completed = bool(second_walk and second_walk.get("segmentIndex") in _completed_indexes(state))
    transition_world = _world(state.get("lastTransitionWorld"))
    distance_after = _distance(world, transition_world) if world and transition_world else None
    distance_to_end = _distance_to_end_cluster(template, world)
    end = _dict(template.get("end"))
    requires_end_cluster = _has_end_cluster(template)
    tolerance = _end_cluster_tolerance(end) if requires_end_cluster else _area_tolerance(end)
    near_end = distance_to_end is not None and distance_to_end <= tolerance
    fresh_samples = int(state.get("freshEndAreaSampleCount") or 0)
    near_end_samples = int(state.get("nearEndClusterSampleCount") or 0) + (1 if near_end else 0)
    tick = _int(state.get("latestTick"))
    transition_tick = _int(state.get("lastTransitionTick"))
    tick_delta = tick - transition_tick if tick is not None and transition_tick is not None else None
    enough_ticks = tick_delta is None or tick_delta >= DEFAULT_MIN_TICKS_AFTER_TRANSITION_FOR_ARRIVAL
    enough_distance = distance_after is not None and distance_after >= DEFAULT_MIN_DISTANCE_AFTER_TRANSITION_FOR_ARRIVAL
    sustained = fresh_samples >= DEFAULT_MIN_FRESH_END_AREA_SAMPLES
    enough_near_samples = near_end_samples >= DEFAULT_MIN_FRESH_SAMPLES_NEAR_END_CLUSTER

    evidence = [
        f"end area candidate={current_area == end_area}",
        f"transitionCompleted={transition_completed}",
        f"secondWalkStarted={second_walk_started}",
        f"secondWalkCompleted={second_walk_completed}",
        f"arrivalGateRequiresEndCluster={requires_end_cluster}",
        f"nearEndCluster={near_end}",
        f"endClusterToleranceTiles={round(tolerance, 3)}",
        f"distanceToEndCluster={round(distance_to_end, 3) if distance_to_end is not None else None}",
        f"distanceAfterLastTransition={round(distance_after, 3) if distance_after is not None else None}",
        f"freshEndAreaSampleCount={fresh_samples}",
        f"nearEndClusterSampleCount={near_end_samples}",
        f"tickDeltaAfterTransition={tick_delta}",
    ]
    warnings: list[str] = []
    passed = False
    reason = "not_end_area"
    rejected_reason = None
    distance_only_rejected = False
    if current_area == end_area:
        reason = "waiting_for_arrival_gate"
        if not transition_completed:
            rejected_reason = "transition_missing"
            warnings.append("arrival candidate before route transition completed")
        elif not second_walk_started:
            rejected_reason = "second_walk_missing"
            warnings.append("arrival candidate before second walk started")
        elif requires_end_cluster:
            if near_end and enough_near_samples and (enough_ticks or second_walk_completed or enough_distance):
                passed = True
                reason = "near_end_cluster"
            elif near_end:
                rejected_reason = "near_end_cluster_waiting_for_samples_or_ticks"
                reason = rejected_reason
                warnings.append("arrival candidate near end cluster, waiting for sample/tick guard")
            else:
                state["prematureArrivalPrevented"] = True
                distance_only_rejected = bool(second_walk_completed and enough_distance)
                rejected_reason = "distance_only_progress_not_arrival" if distance_only_rejected else "waiting_for_end_cluster"
                reason = rejected_reason
                warnings.append("end area label is broad; waiting for end-cluster proximity")
        elif second_walk_completed and sustained and enough_ticks:
            passed = True
            reason = "passed_without_end_cluster"
            warnings.append("template lacks precise end cluster; arrival inferred from sustained end-area evidence")
        else:
            state["prematureArrivalPrevented"] = True
            rejected_reason = "waiting_for_sustained_end_area"
            warnings.append("template lacks precise end cluster; waiting for sustained end-area evidence")

    return {
        "passed": passed,
        "reason": reason,
        "rejectedReason": rejected_reason,
        "passedReason": reason if passed else None,
        "evidence": evidence,
        "warnings": warnings,
        "distanceToEndCluster": distance_to_end,
        "distanceAfterLastTransition": distance_after,
        "endClusterToleranceTiles": tolerance,
        "nearEndCluster": near_end,
        "arrivalGateRequiresEndCluster": requires_end_cluster,
        "nearEndClusterSampleCount": near_end_samples,
        "distanceOnlyProgressRejected": distance_only_rejected,
        "secondWalkStarted": second_walk_started,
        "secondWalkCompleted": second_walk_completed,
        "transitionCompleted": transition_completed,
        "freshEndAreaSampleCount": fresh_samples,
    }


def _add_recent_path(
    state: dict[str, Any],
    world: dict[str, Any] | None,
    *,
    area: str | None,
    monotonic_time: float,
    wall_time_utc: str,
    max_recent_points: int,
) -> bool:
    if not world:
        return False
    recent = _list(state.get("recentPath"))
    if recent and _world_key(_dict(recent[-1]).get("world")) == _world_key(world):
        return False
    if recent:
        distance = _distance(_dict(recent[-1]).get("world"), world)
        if distance is not None:
            state["distanceMovedApprox"] = round(float(state.get("distanceMovedApprox") or 0.0) + distance, 3)
    recent.append(
        {
            "world": _public_world(world),
            "area": area,
            "monotonicTime": round(monotonic_time, 6),
            "wallTimeUtc": wall_time_utc,
            "tick": state.get("latestTick"),
            "exportSeq": state.get("latestExportSeq"),
        }
    )
    state["recentPath"] = recent[-max(1, int(max_recent_points)) :]
    return True


def _update_stale_period(
    state: dict[str, Any],
    *,
    freshness_status: str,
    monotonic_time: float,
) -> tuple[bool, bool]:
    became_stale = False
    became_fresh = False
    active = _float(state.get("activeStaleStartedAtMonotonic"))
    if freshness_status == "stale" and active is None:
        state["activeStaleStartedAtMonotonic"] = monotonic_time
        freshness = _dict(state.get("freshness"))
        freshness["stalePeriodCount"] = int(freshness.get("stalePeriodCount") or 0) + 1
        state["freshness"] = freshness
        became_stale = True
    elif freshness_status != "stale" and active is not None:
        elapsed_ms = max(0.0, (monotonic_time - active) * 1000.0)
        freshness = _dict(state.get("freshness"))
        freshness["longestStaleMs"] = round(max(float(freshness.get("longestStaleMs") or 0.0), elapsed_ms), 1)
        state["freshness"] = freshness
        state["activeStaleStartedAtMonotonic"] = None
        became_fresh = True
    return became_stale, became_fresh


def update_session_with_context(
    state: dict[str, Any],
    template: str | Path | dict[str, Any],
    context: dict[str, Any],
    *,
    monotonic_time: float | None = None,
    wall_time_utc: str | None = None,
    max_age_ms: int = DEFAULT_MAX_SOURCE_AGE_MS,
    max_stale_ms: int = 3000,
    max_recent_points: int = 100,
) -> list[dict[str, Any]]:
    template_payload, _ = _load_template(template)
    monotonic_time = float(monotonic_time if monotonic_time is not None else time.monotonic())
    wall_time_utc = wall_time_utc or _utc_now()
    events: list[dict[str, Any]] = []
    previous_state = str(state.get("routeState") or "unknown")
    previous_area = state.get("currentArea")
    previous_world = _world(state.get("currentWorld"))
    previous_plane = previous_world.get("plane") if previous_world else None

    snapshot = monitor_live_context(template_payload, context, max_age_ms=max_age_ms)
    world = _player_world_from_context(context)
    current_area = snapshot.get("currentArea")
    freshness = dict(snapshot.get("freshness") or {})
    state["updatedAtUtc"] = wall_time_utc
    state["latestTick"] = freshness.get("latestTick")
    state["latestExportSeq"] = freshness.get("latestExportSeq")
    state["currentArea"] = current_area
    state["currentWorld"] = _public_world(world)
    state["warnings"] = list(snapshot.get("warnings") or [])
    state["evidence"] = list(snapshot.get("evidence") or [])

    state_freshness = _dict(state.get("freshness"))
    state_freshness.update(
        {
            "status": freshness.get("status"),
            "sourceAgeMs": freshness.get("sourceAgeMs"),
            "latestTick": freshness.get("latestTick"),
            "latestExportSeq": freshness.get("latestExportSeq"),
            "maxAgeMs": max_age_ms,
            "maxStaleMs": max_stale_ms,
        }
    )
    state["freshness"] = state_freshness
    became_stale, became_fresh = _update_stale_period(state, freshness_status=str(freshness.get("status") or "unknown"), monotonic_time=monotonic_time)

    significant_snapshot = _add_recent_path(
        state,
        world,
        area=current_area,
        monotonic_time=monotonic_time,
        wall_time_utc=wall_time_utc,
        max_recent_points=max_recent_points,
    )
    if significant_snapshot:
        events.append(_make_event(state, "snapshot", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, world=world, evidence=["world changed"]))

    if previous_area != current_area and current_area:
        events.append(
            _make_event(
                state,
                "area_changed",
                monotonic_time=monotonic_time,
                wall_time_utc=wall_time_utc,
                world=world,
                evidence=[f"area changed {previous_area} -> {current_area}"],
            )
        )

    current_plane = world.get("plane") if world else None
    if previous_plane is not None and current_plane is not None and previous_plane != current_plane:
        change = {
            "fromPlane": previous_plane,
            "toPlane": current_plane,
            "monotonicTime": round(monotonic_time, 6),
            "wallTimeUtc": wall_time_utc,
            "world": _public_world(world),
            "tick": state.get("latestTick"),
            "exportSeq": state.get("latestExportSeq"),
        }
        state.setdefault("planeChanges", []).append(change)
        events.append(_make_event(state, "plane_changed", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, world=world, evidence=[f"plane changed {previous_plane} -> {current_plane}"]))

    if became_stale:
        if previous_state != "stale":
            state["previousNonStaleRouteState"] = previous_state
        state["routeState"] = "stale"
        events.append(_make_event(state, "stale", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, route_state_before=previous_state, route_state_after="stale", world=world, warnings=state.get("warnings") or []))
    elif freshness.get("status") == "stale":
        state["routeState"] = "stale"
    else:
        if became_fresh:
            events.append(_make_event(state, "fresh", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, route_state_before=previous_state, world=world, evidence=["telemetry became fresh"]))

        start_area = _dict(template_payload.get("start")).get("areaLabel")
        end_area = _dict(template_payload.get("end")).get("areaLabel")
        start_world = _world(_dict(template_payload.get("start")).get("world"))
        start_distance = _distance(world, start_world) if world and start_world else None
        route_state = "unknown"
        end_area_candidate = current_area == end_area
        if end_area_candidate:
            state["freshEndAreaSampleCount"] = int(state.get("freshEndAreaSampleCount") or 0) + 1
        else:
            state["freshEndAreaSampleCount"] = 0
        if previous_state == "arrived":
            route_state = "arrived"
            distance_to_end = _distance_to_end_cluster(template_payload, world)
            distance_after_transition = _distance(world, _world(state.get("lastTransitionWorld"))) if world else None
            near_end = distance_to_end is not None and distance_to_end <= _end_cluster_tolerance(_dict(template_payload.get("end")))
            state["distanceToEndCluster"] = round(distance_to_end, 3) if distance_to_end is not None else None
            state["distanceAfterLastTransition"] = round(distance_after_transition, 3) if distance_after_transition is not None else None
            state["nearEndCluster"] = bool(near_end)
            if end_area_candidate:
                state["repeatedArrivalSamples"] = int(state.get("repeatedArrivalSamples") or 0) + 1
                state["duplicateArrivalEventsSuppressed"] = int(state.get("duplicateArrivalEventsSuppressed") or 0) + 1
        elif current_area == start_area:
            events.extend(
                _complete_required_through(
                    state,
                    template_payload,
                    _required_segments(template_payload)[0].get("segmentIndex") if _required_segments(template_payload) else 0,
                    monotonic_time=monotonic_time,
                    wall_time_utc=wall_time_utc,
                    world=world,
                    evidence=[f"current area matched start area {start_area}"],
                    confidence=0.85,
                )
            )
            route_state = "ready_at_start"
        else:
            if not end_area_candidate and snapshot.get("routeState") == "off_route":
                state["freshConflictCount"] = int(state.get("freshConflictCount") or 0) + 1
            else:
                state["freshConflictCount"] = 0
            if int(state.get("freshConflictCount") or 0) >= 2:
                route_state = "off_route"
                state["offRoute"] = True
                state["offRouteConfidence"] = min(1.0, 0.5 + 0.25 * int(state.get("freshConflictCount") or 0))
                state["offRouteReasons"] = list(snapshot.get("offRouteReasons") or ["repeated_conflicting_route_samples"])
                events.append(_make_event(state, "off_route", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, world=world, warnings=state.get("warnings") or []))
            else:
                state["offRoute"] = False
                state["offRouteReasons"] = []
                state["offRouteConfidence"] = 0.0
                if state.get("completedSegments"):
                    route_state = "in_progress"
                elif _in_route_corridor(world, template_payload):
                    route_state = "in_progress"
                else:
                    route_state = "unknown"

            if route_state != "off_route":
                walk_segments = _walk_segments(template_payload)
                if walk_segments and start_distance is not None and start_distance > 1.0:
                    events.extend(
                        _complete_required_through(
                            state,
                            template_payload,
                            walk_segments[0].get("segmentIndex"),
                            monotonic_time=monotonic_time,
                            wall_time_utc=wall_time_utc,
                            world=world,
                            evidence=["player moved away from route start"],
                            confidence=0.72,
                        )
                    )
                    route_state = "in_progress"
                transition = _transition_segment(template_payload)
                if transition and current_plane is not None:
                    expected_delta = _int(_dict(transition.get("expectedPostcondition")).get("planeDelta"))
                    start_plane = _int(_dict(_dict(template_payload.get("start")).get("world")).get("plane"))
                    plane_delta = current_plane - start_plane if start_plane is not None else None
                    if (expected_delta is not None and plane_delta == expected_delta) or (previous_plane is not None and previous_plane != current_plane):
                        transition_was_completed = transition.get("segmentIndex") in _completed_indexes(state)
                        events.extend(
                            _complete_required_through(
                                state,
                                template_payload,
                                transition.get("segmentIndex"),
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                world=world,
                                evidence=["plane changed in route context"],
                                confidence=0.82,
                            )
                        )
                        if not transition_was_completed or not state.get("lastTransitionWorld"):
                            state["lastTransitionWorld"] = _public_world(world)
                            state["lastTransitionTick"] = state.get("latestTick")
                            state["lastTransitionMonotonic"] = round(monotonic_time, 6)
                        route_state = "in_progress"
                second_walk = _second_walk_segment(template_payload)
                if second_walk and transition and transition.get("segmentIndex") in _completed_indexes(state):
                    if not state.get("secondWalkStartedAt"):
                        state["secondWalkStartedAt"] = wall_time_utc
                        state["secondWalkEvidence"] = ["stair transition completed; monitoring second walk"]
                        events.append(
                            _make_event(
                                state,
                                "second_walk_started",
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                segment=_segment_ref(second_walk),
                                world=world,
                                evidence=["second walk started after route transition"],
                            )
                        )
                    distance_after_transition = _distance(world, _world(state.get("lastTransitionWorld"))) if world else None
                    distance_to_end = _distance_to_end_cluster(template_payload, world)
                    near_end = distance_to_end is not None and distance_to_end <= _area_tolerance(_dict(template_payload.get("end")))
                    enough_second_walk_distance = distance_after_transition is not None and distance_after_transition >= DEFAULT_MIN_DISTANCE_AFTER_TRANSITION_FOR_ARRIVAL
                    state["secondWalkDistance"] = round(distance_after_transition, 3) if distance_after_transition is not None else state.get("secondWalkDistance")
                    if second_walk.get("segmentIndex") not in _completed_indexes(state) and (near_end or enough_second_walk_distance):
                        events.extend(
                            _complete_required_through(
                                state,
                                template_payload,
                                second_walk.get("segmentIndex"),
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                world=world,
                                evidence=["second-walk progress toward route end"],
                                confidence=0.72,
                            )
                        )
                        state["secondWalkCompletedAt"] = wall_time_utc
                        state["secondWalkEvidence"] = [
                            f"distanceAfterLastTransition={round(distance_after_transition, 3) if distance_after_transition is not None else None}",
                            f"distanceToEndCluster={round(distance_to_end, 3) if distance_to_end is not None else None}",
                        ]
                        events.append(
                            _make_event(
                                state,
                                "second_walk_completed",
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                segment=_segment_ref(second_walk),
                                world=world,
                                evidence=list(state.get("secondWalkEvidence") or []),
                            )
                        )
                        route_state = "in_progress"
                if end_area_candidate:
                    gate = _arrival_gate_result(state, template_payload, world=world, current_area=current_area)
                    events.append(
                        _make_event(
                            state,
                            "arrival_candidate",
                            monotonic_time=monotonic_time,
                            wall_time_utc=wall_time_utc,
                            world=world,
                            evidence=gate.get("evidence") or [],
                            warnings=gate.get("warnings") or [],
                        )
                    )
                    if gate.get("passed"):
                        _set_arrival_gate(
                            state,
                            status="passed",
                            world=world,
                            area=current_area,
                            reason=str(gate.get("reason") or "arrival_gate_passed"),
                            evidence=list(gate.get("evidence") or []),
                            warnings=list(gate.get("warnings") or []),
                            distance_to_end=gate.get("distanceToEndCluster"),
                            distance_after_transition=gate.get("distanceAfterLastTransition"),
                            requires_end_cluster=gate.get("arrivalGateRequiresEndCluster"),
                            end_cluster_tolerance=gate.get("endClusterToleranceTiles"),
                            near_end_cluster=gate.get("nearEndCluster"),
                            distance_only_progress_rejected=gate.get("distanceOnlyProgressRejected"),
                            rejected_reason=gate.get("rejectedReason"),
                            passed_reason=gate.get("passedReason"),
                            monotonic_time=monotonic_time,
                            tick=state.get("latestTick"),
                        )
                        events.append(
                            _make_event(
                                state,
                                "arrival_gate_passed",
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                world=world,
                                evidence=gate.get("evidence") or [],
                                warnings=gate.get("warnings") or [],
                            )
                        )
                        if gate.get("reason") == "near_end_cluster":
                            events.append(
                                _make_event(
                                    state,
                                    "arrival_gate_passed_near_end_cluster",
                                    monotonic_time=monotonic_time,
                                    wall_time_utc=wall_time_utc,
                                    world=world,
                                    evidence=gate.get("evidence") or [],
                                    warnings=gate.get("warnings") or [],
                                )
                            )
                        arrival = _arrival_segment(template_payload)
                        if arrival:
                            events.extend(
                                _complete_required_through(
                                    state,
                                    template_payload,
                                    arrival.get("segmentIndex"),
                                    monotonic_time=monotonic_time,
                                    wall_time_utc=wall_time_utc,
                                    world=world,
                                    evidence=[f"arrival gate passed: {gate.get('reason')}"],
                                    confidence=0.9,
                                )
                            )
                        route_state = "arrived"
                    else:
                        _set_arrival_gate(
                            state,
                            status="waiting",
                            world=world,
                            area=current_area,
                            reason=str(gate.get("reason") or "waiting_for_arrival_gate"),
                            evidence=list(gate.get("evidence") or []),
                            warnings=list(gate.get("warnings") or []),
                            distance_to_end=gate.get("distanceToEndCluster"),
                            distance_after_transition=gate.get("distanceAfterLastTransition"),
                            requires_end_cluster=gate.get("arrivalGateRequiresEndCluster"),
                            end_cluster_tolerance=gate.get("endClusterToleranceTiles"),
                            near_end_cluster=gate.get("nearEndCluster"),
                            distance_only_progress_rejected=gate.get("distanceOnlyProgressRejected"),
                            rejected_reason=gate.get("rejectedReason"),
                            passed_reason=gate.get("passedReason"),
                            monotonic_time=monotonic_time,
                            tick=state.get("latestTick"),
                        )
                        events.append(
                            _make_event(
                                state,
                                "arrival_gate_waiting",
                                monotonic_time=monotonic_time,
                                wall_time_utc=wall_time_utc,
                                world=world,
                                evidence=gate.get("evidence") or [],
                                warnings=gate.get("warnings") or [],
                            )
                        )
                        if not gate.get("nearEndCluster"):
                            events.append(
                                _make_event(
                                    state,
                                    "arrival_candidate_area_label_only",
                                    monotonic_time=monotonic_time,
                                    wall_time_utc=wall_time_utc,
                                    world=world,
                                    evidence=gate.get("evidence") or [],
                                    warnings=gate.get("warnings") or [],
                                )
                            )
                            events.append(
                                _make_event(
                                    state,
                                    "arrival_gate_waiting_for_end_cluster",
                                    monotonic_time=monotonic_time,
                                    wall_time_utc=wall_time_utc,
                                    world=world,
                                    evidence=gate.get("evidence") or [],
                                    warnings=gate.get("warnings") or [],
                                )
                            )
                        if gate.get("distanceOnlyProgressRejected"):
                            events.append(
                                _make_event(
                                    state,
                                    "arrival_gate_rejected_distance_only",
                                    monotonic_time=monotonic_time,
                                    wall_time_utc=wall_time_utc,
                                    world=world,
                                    evidence=gate.get("evidence") or [],
                                    warnings=gate.get("warnings") or [],
                                )
                            )
                        route_state = "in_progress" if state.get("completedSegments") else "unknown"
                elif route_state != "off_route":
                    _set_arrival_gate(
                        state,
                        status="not_applicable",
                        world=world,
                        area=current_area,
                        reason="not_in_end_area",
                        distance_to_end=_distance_to_end_cluster(template_payload, world),
                        distance_after_transition=_distance(world, _world(state.get("lastTransitionWorld"))) if world else None,
                        requires_end_cluster=_has_end_cluster(template_payload),
                        end_cluster_tolerance=_end_cluster_tolerance(_dict(template_payload.get("end"))),
                        near_end_cluster=False,
                    )
        if route_state != "off_route":
            state["offRoute"] = False
            state["offRouteReasons"] = []
            state["offRouteConfidence"] = 0.0
        state["routeState"] = route_state

    _refresh_session_segments(state, template_payload)
    if previous_state != "arrived" and state.get("routeState") == "arrived" and not any(event.get("eventType") == "arrived" for event in events):
        events.append(_make_event(state, "arrived", monotonic_time=monotonic_time, wall_time_utc=wall_time_utc, world=world, evidence=["route end area reached"]))
    if previous_state != state.get("routeState"):
        events.append(
            _make_event(
                state,
                "state_change",
                monotonic_time=monotonic_time,
                wall_time_utc=wall_time_utc,
                route_state_before=previous_state,
                route_state_after=state.get("routeState"),
                world=world,
                evidence=[f"state changed {previous_state} -> {state.get('routeState')}"],
            )
        )
    return events


def route_history_summary(state: dict[str, Any], *, events_written: int | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    freshness = _dict(state.get("freshness"))
    return {
        "schema": HISTORY_SUMMARY_SCHEMA,
        "status": _history_status_for_state(str(state.get("routeState") or "unknown"), off_route=bool(state.get("offRoute"))),
        "sessionId": state.get("sessionId"),
        "routeName": state.get("routeName"),
        "templateInput": state.get("templateInput"),
        "templatePath": state.get("templatePath"),
        "templateRevision": state.get("templateRevision"),
        "requiredSegmentCount": state.get("requiredSegmentCount"),
        "templateResolution": state.get("templateResolution"),
        "routeState": state.get("routeState"),
        "currentArea": state.get("currentArea"),
        "currentWorld": state.get("currentWorld"),
        "completedSegmentCount": len(_list(state.get("completedSegments"))),
        "remainingSegmentCount": len(_list(state.get("remainingSegments"))),
        "currentSegment": {
            "segmentIndex": state.get("currentSegmentIndex"),
            "label": state.get("currentSegmentLabel"),
        },
        "nextExpectedSegment": state.get("nextExpectedSegment"),
        "offRoute": bool(state.get("offRoute")),
        "offRouteReasons": state.get("offRouteReasons") or [],
        "stalePeriodCount": freshness.get("stalePeriodCount") or 0,
        "longestStaleMs": freshness.get("longestStaleMs") or 0,
        "planeChangeCount": len(_list(state.get("planeChanges"))),
        "recentPathCount": len(_list(state.get("recentPath"))),
        "distanceMovedApprox": state.get("distanceMovedApprox"),
        "arrivalGateStatus": state.get("arrivalGateStatus"),
        "arrivalCandidateWorld": state.get("arrivalCandidateWorld"),
        "arrivalCandidateArea": state.get("arrivalCandidateArea"),
        "arrivalCandidateReason": state.get("arrivalCandidateReason"),
        "arrivalGateEvidence": state.get("arrivalGateEvidence") or [],
        "arrivalGateWarnings": state.get("arrivalGateWarnings") or [],
        "distanceToEndCluster": state.get("distanceToEndCluster"),
        "endClusterToleranceTiles": state.get("endClusterToleranceTiles"),
        "nearEndCluster": bool(state.get("nearEndCluster")),
        "nearEndClusterSampleCount": int(state.get("nearEndClusterSampleCount") or 0),
        "distanceAfterLastTransition": state.get("distanceAfterLastTransition"),
        "arrivalGateRequiresEndCluster": bool(state.get("arrivalGateRequiresEndCluster")),
        "distanceOnlyProgressRejected": bool(state.get("distanceOnlyProgressRejected")),
        "arrivalGateRejectedReason": state.get("arrivalGateRejectedReason"),
        "arrivalGatePassedReason": state.get("arrivalGatePassedReason"),
        "freshEndAreaSampleCount": state.get("freshEndAreaSampleCount"),
        "arrivalCompletedAtWorld": state.get("arrivalCompletedAtWorld"),
        "arrivalCompletedAtTick": state.get("arrivalCompletedAtTick"),
        "arrivalCompletedAtMonotonic": state.get("arrivalCompletedAtMonotonic"),
        "secondWalkStartedAt": state.get("secondWalkStartedAt"),
        "secondWalkCompletedAt": state.get("secondWalkCompletedAt"),
        "secondWalkDistance": state.get("secondWalkDistance"),
        "secondWalkEvidence": state.get("secondWalkEvidence") or [],
        "prematureArrivalPrevented": bool(state.get("prematureArrivalPrevented")),
        "duplicateArrivalEventsSuppressed": int(state.get("duplicateArrivalEventsSuppressed") or 0),
        "repeatedArrivalSamples": int(state.get("repeatedArrivalSamples") or 0),
        "eventCount": int(state.get("eventCount") or 0),
        "eventsWritten": events_written,
        "outputDir": str(output_dir) if output_dir else None,
        "warnings": _list(state.get("warnings"))[:8],
        "updatedAtUtc": state.get("updatedAtUtc"),
    }


def write_history_artifacts(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    paths: dict[str, Path],
    append: bool = True,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    telemetry_sources.atomic_write_json(paths["state"], state, pretty=True)
    timeline = [_timeline_from_event(event) for event in events]
    if append:
        _append_jsonl(paths["events"], events)
        _append_jsonl(paths["timeline"], timeline)
    else:
        paths["events"].parent.mkdir(parents=True, exist_ok=True)
        paths["timeline"].parent.mkdir(parents=True, exist_ok=True)
        paths["events"].write_text("".join(json.dumps(event, separators=(",", ":"), default=str) + "\n" for event in events), encoding="utf-8")
        paths["timeline"].write_text("".join(json.dumps(item, separators=(",", ":"), default=str) + "\n" for item in timeline), encoding="utf-8")
    summary = route_history_summary(state, events_written=len(events), output_dir=output_dir)
    summary["statePath"] = str(paths["state"])
    summary["eventsPath"] = str(paths["events"])
    summary["timelinePath"] = str(paths["timeline"])
    summary["summaryPath"] = str(paths["summary"])
    telemetry_sources.atomic_write_json(paths["summary"], summary, pretty=True)
    return paths


def write_monitor_error(error: dict[str, Any], out_dir: str | Path | None = None) -> Path | None:
    if not out_dir:
        return None
    path = Path(out_dir).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        target = path / "route_monitor_error.json"
        telemetry_sources.atomic_write_json(target, error, pretty=True)
        return target
    except OSError:
        return None


def build_recording_history(
    template: str | Path | dict[str, Any],
    recording: str | Path,
    *,
    lifecycle: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    recording_path = Path(recording)
    lifecycle = lifecycle if isinstance(lifecycle, dict) and lifecycle else _json(recording_path / "traversal_lifecycle.json")
    if _is_auto_template_input(template):
        selection = route_template.resolve_template_auto(lifecycle)
        if selection.get("status") == "PASS" and selection.get("selectedTemplate"):
            template = str(selection.get("selectedTemplate"))
    template_payload, template_path = _load_template(template)
    monitor = monitor_recording(template, recording_path, lifecycle=lifecycle, comparison=comparison)
    state = create_session_state(
        template_payload,
        template_path=template_path or monitor.get("templatePath"),
        template_input=template_path or monitor.get("templatePath") or template,
        session_id=session_id,
    )
    state["mode"] = "recording"
    state["routeState"] = monitor.get("routeState")
    state["currentArea"] = monitor.get("currentArea")
    state["currentWorld"] = _public_world(_dict(_dict(lifecycle.get("end")).get("world"))) if lifecycle else None
    state["completedSegments"] = monitor.get("completedSegments") or []
    state["remainingSegments"] = monitor.get("remainingSegments") or []
    state["nextExpectedSegment"] = monitor.get("nextExpectedSegment")
    state["offRoute"] = bool(monitor.get("offRoute"))
    state["offRouteReasons"] = monitor.get("offRouteReasons") or []
    state["warnings"] = monitor.get("warnings") or []
    state["evidence"] = monitor.get("evidence") or []
    state["freshness"] = {"status": "recording", "sourceAgeMs": None, "stalePeriodCount": 0, "longestStaleMs": 0}
    events: list[dict[str, Any]] = [
        _make_event(state, "session_start", route_state_after="unknown", evidence=[f"recording history from {recording_path}"])
    ]
    route_segments = _list(lifecycle.get("routeSegments")) if lifecycle else []
    for segment in route_segments:
        if not isinstance(segment, dict):
            continue
        world = _world(_first(segment.get("endWorld"), segment.get("world"), segment.get("startWorld")))
        event_type = "segment_completed" if segment.get("segmentIndex") in _completed_indexes(state) else "snapshot"
        events.append(
            _make_event(
                state,
                event_type,
                segment=segment,
                world=world,
                evidence=[f"recording route segment {segment.get('segmentType')} {segment.get('label')}"],
            )
        )
        start_plane = _int(segment.get("startPlane"))
        end_plane = _int(segment.get("endPlane"))
        if start_plane is not None and end_plane is not None and start_plane != end_plane:
            change = {
                "fromPlane": start_plane,
                "toPlane": end_plane,
                "wallTimeUtc": _utc_now(),
                "world": _public_world(world),
            }
            state.setdefault("planeChanges", []).append(change)
            events.append(_make_event(state, "plane_changed", segment=segment, world=world, evidence=[f"recording plane changed {start_plane} -> {end_plane}"]))
        for point in (segment.get("startWorld"), segment.get("endWorld")):
            if _world(point):
                _add_recent_path(
                    state,
                    _world(point),
                    area=None,
                    monotonic_time=time.monotonic(),
                    wall_time_utc=_utc_now(),
                    max_recent_points=100,
                )
    if state.get("routeState") == "arrived":
        events.append(_make_event(state, "arrived", route_state_after="arrived", evidence=["recording route arrived"]))
    events.append(_make_event(state, "session_stop", route_state_after=state.get("routeState"), evidence=["recording history replay completed"]))
    _refresh_session_segments(state, template_payload)
    timeline = [_timeline_from_event(event) for event in events]
    summary = route_history_summary(state, events_written=len(events), output_dir=recording_path)
    return state, events, timeline, summary


def build_live_context_from_sources(
    *,
    session: str | Path | None = None,
    latest_session: bool = False,
    sessions_dir: str | Path | None = None,
    sources_override: str | None = None,
) -> dict[str, Any]:
    discovery = telemetry_sources.discover_sources(
        session=session,
        latest_session=latest_session,
        sessions_dir=sessions_dir,
        sources_override=sources_override,
        include_missing=True,
    )
    reads = telemetry_sources.read_sources(discovery.get("paths") or {})
    payloads = telemetry_sources.parsed_payload_by_source(reads)
    return {
        "session": discovery.get("session_path"),
        "baseline": payloads.get("baseline") or {},
        "context": payloads.get("context") or {},
        "status": payloads.get("status") or {},
        "activity": payloads.get("activity") or {},
        "navigation": payloads.get("navigation") or {},
        "watchValues": payloads.get("watchValues") or {},
        "candidates": payloads.get("candidates") or [],
        "sourceFiles": reads,
        "warnings": [],
        "missingFields": [read.get("name") for read in reads if not read.get("exists")],
    }


def run_follow(args: argparse.Namespace) -> dict[str, Any]:
    if _is_auto_template_input(args.template):
        context = build_live_context_from_sources(
            session=args.session,
            latest_session=args.latest_session,
            sessions_dir=args.sessions_dir,
            sources_override=args.sources,
        )
        selection = resolve_auto_template_for_live_context(context, max_age_ms=args.max_age_ms)
        if selection.get("status") != "PASS" or not selection.get("selectedTemplate"):
            payload = _auto_template_unavailable_status(
                selection,
                mode="live_follow",
                route_state="stale" if _dict(selection.get("freshness")).get("status") == "stale" else "unknown",
            )
            payload["outputDir"] = str(Path(args.out_dir).expanduser()) if args.out_dir else None
            error_path = write_monitor_error(payload, args.out_dir)
            if error_path:
                payload["errorPath"] = str(error_path)
            return payload
        args.template = str(selection.get("selectedTemplate"))
    resolution = _resolve_template(args.template)
    if resolution.get("status") != "PASS":
        error = _template_config_failure(resolution, mode="live_follow")
        error["outputDir"] = str(Path(args.out_dir).expanduser()) if args.out_dir else None
        error_path = write_monitor_error(error, args.out_dir)
        if error_path:
            error["errorPath"] = str(error_path)
        return error
    template_payload = _dict(resolution.get("template"))
    template_path = resolution.get("resolvedPath")
    state = create_session_state(
        template_payload,
        template_path=template_path,
        template_input=resolution.get("input"),
        template_resolution={key: value for key, value in resolution.items() if key != "template"},
        session_id=args.session_id,
    )
    output_dir = default_history_dir(template_payload, str(state.get("sessionId")), out_dir=args.out_dir)
    paths = history_output_paths(
        output_dir,
        state_out=args.state_out,
        events_out=args.events_out,
        timeline_out=args.timeline_out,
        summary_out=args.summary_out,
    )
    start_event = _make_event(state, "session_start", route_state_after="unknown", evidence=["live route monitor session started"])
    write_history_artifacts(state, [start_event], paths=paths, append=True, output_dir=output_dir)
    if args.print_events:
        print(json.dumps(start_event, indent=2, default=str))
    deadline = time.monotonic() + float(args.duration) if args.duration is not None else None
    poll_seconds = max(0.05, float(args.poll_ms or 250) / 1000.0)
    try:
        while True:
            context = build_live_context_from_sources(
                session=args.session,
                latest_session=args.latest_session,
                sessions_dir=args.sessions_dir,
                sources_override=args.sources,
            )
            events = update_session_with_context(
                state,
                template_payload,
                context,
                max_age_ms=args.max_age_ms,
                max_stale_ms=args.max_stale_ms,
                max_recent_points=args.max_recent_points,
            )
            write_history_artifacts(state, events, paths=paths, append=True, output_dir=output_dir)
            if args.print_events:
                for event in events:
                    print(json.dumps(event, indent=2, default=str))
            if args.print_state:
                print(json.dumps(state, indent=2, default=str))
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        state["warnings"] = list(state.get("warnings") or []) + ["route monitor interrupted by user"]
    stop_event = _make_event(state, "session_stop", route_state_after=state.get("routeState"), evidence=["live route monitor session stopped"])
    write_history_artifacts(state, [stop_event], paths=paths, append=True, output_dir=output_dir)
    summary = route_history_summary(state, events_written=int(state.get("eventCount") or 0), output_dir=output_dir)
    summary.update({"statePath": str(paths["state"]), "eventsPath": str(paths["events"]), "timelinePath": str(paths["timeline"]), "summaryPath": str(paths["summary"])})
    telemetry_sources.atomic_write_json(paths["summary"], summary, pretty=True)
    return summary if args.json else state


def write_recording_history(
    template: str | Path | dict[str, Any],
    recording: str | Path,
    *,
    out_dir: str | Path | None = None,
    session_id: str | None = None,
    state_out: str | Path | None = None,
    events_out: str | Path | None = None,
    timeline_out: str | Path | None = None,
    summary_out: str | Path | None = None,
    lifecycle: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    state, events, _timeline, summary = build_recording_history(
        template,
        recording,
        lifecycle=lifecycle,
        comparison=comparison,
        session_id=session_id,
    )
    output_dir = Path(out_dir) if out_dir else Path(recording)
    paths = history_output_paths(
        output_dir,
        state_out=state_out,
        events_out=events_out,
        timeline_out=timeline_out,
        summary_out=summary_out,
    )
    write_history_artifacts(state, events, paths=paths, append=False, output_dir=output_dir)
    summary.update({"statePath": str(paths["state"]), "eventsPath": str(paths["events"]), "timelinePath": str(paths["timeline"]), "summaryPath": str(paths["summary"])})
    telemetry_sources.atomic_write_json(paths["summary"], summary, pretty=True)
    return state, paths, summary


def compact_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": status.get("schema") or SCHEMA_VERSION,
        "status": status.get("status"),
        "routeName": status.get("routeName"),
        "templateRevision": status.get("templateRevision"),
        "requiredSegmentCount": status.get("requiredSegmentCount"),
        "templateResolution": status.get("templateResolution"),
        "mode": status.get("mode"),
        "routeState": status.get("routeState"),
        "currentArea": status.get("currentArea"),
        "currentSegment": {
            "segmentIndex": status.get("currentSegmentIndex"),
            "label": status.get("currentSegmentLabel"),
        },
        "nextExpectedSegment": status.get("nextExpectedSegment"),
        "completedSegmentCount": status.get("completedSegmentCount"),
        "remainingSegmentCount": status.get("remainingSegmentCount"),
        "offRoute": status.get("offRoute"),
        "arrivalGateStatus": status.get("arrivalGateStatus"),
        "distanceToEndCluster": status.get("distanceToEndCluster"),
        "endClusterToleranceTiles": status.get("endClusterToleranceTiles"),
        "nearEndCluster": status.get("nearEndCluster"),
        "nearEndClusterSampleCount": status.get("nearEndClusterSampleCount"),
        "distanceAfterLastTransition": status.get("distanceAfterLastTransition"),
        "arrivalGateRequiresEndCluster": status.get("arrivalGateRequiresEndCluster"),
        "distanceOnlyProgressRejected": status.get("distanceOnlyProgressRejected"),
        "arrivalGateRejectedReason": status.get("arrivalGateRejectedReason"),
        "arrivalGatePassedReason": status.get("arrivalGatePassedReason"),
        "arrivalGateWarnings": _list(status.get("arrivalGateWarnings"))[:5],
        "freshness": status.get("freshness"),
        "comparisonStatusReason": status.get("comparisonStatusReason"),
        "warnings": _list(status.get("warnings"))[:5],
        "missingCapabilities": _list(status.get("missingCapabilities"))[:8],
    }


def write_status(status: dict[str, Any], target: str | Path, *, pretty: bool = True) -> Path:
    path = Path(target)
    if path.suffix.lower() == ".json":
        out_path = path
    else:
        out_path = path / "route_monitor_status.json"
    telemetry_sources.atomic_write_json(out_path, status, pretty=pretty)
    return out_path


def summary_text(status: dict[str, Any]) -> str:
    next_segment = _dict(status.get("nextExpectedSegment"))
    lines = [
        f"Route Monitor: {status.get('status')} {status.get('routeState')}",
        f"Route: {status.get('routeName')} templateRevision={status.get('templateRevision')}",
        f"Current area: {status.get('currentArea')}",
        f"Completed / remaining: {status.get('completedSegmentCount')} / {status.get('remainingSegmentCount')}",
        f"Next: {next_segment.get('segmentIndex')} {next_segment.get('label')}" if next_segment else "Next: none",
        f"Arrival gate: {status.get('arrivalGateStatus')} nearEnd={status.get('nearEndCluster')} distanceToEnd={status.get('distanceToEndCluster')} distanceAfterTransition={status.get('distanceAfterLastTransition')}",
        f"Off route: {status.get('offRoute')}",
        f"Freshness: {_dict(status.get('freshness')).get('status')} ageMs={_dict(status.get('freshness')).get('sourceAgeMs')}",
    ]
    warnings = _list(status.get("warnings"))
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings[:8])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate route readiness/progress against a route template.")
    parser.add_argument("--template", help="Route template JSON path or route name.")
    parser.add_argument("--route-name", help="Route name to use when resolving an automatic template.")
    parser.add_argument("--list-route-templates", action="store_true", help="List available route templates.")
    parser.add_argument("--default-route-template", help="Default route template name or path to use when --template is omitted.")
    parser.add_argument("--validate-template", help="Resolve and validate a route template path or route name.")
    parser.add_argument("--recording", help="Recording folder to monitor.")
    parser.add_argument("--live", action="store_true", help="Evaluate live telemetry.")
    parser.add_argument("--follow", action="store_true", help="Persistently sample live telemetry until stopped.")
    parser.add_argument("--poll-ms", type=int, default=250, help="Follow-mode poll interval.")
    parser.add_argument("--duration", type=float, help="Follow-mode duration in seconds.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest live session for --live.")
    parser.add_argument("--session", help="Telemetry session path for --live.")
    parser.add_argument("--sessions-dir", help="Override sessions directory for --latest-session.")
    parser.add_argument("--sources", help="Explicit live source override for --live.")
    parser.add_argument("--json", action="store_true", help="Print JSON status.")
    parser.add_argument("--out", help="Optional status JSON output path.")
    parser.add_argument("--out-dir", help="Route history output directory.")
    parser.add_argument("--session-id", help="Route history session id.")
    parser.add_argument("--state-out", help="Override route_session_state.json path.")
    parser.add_argument("--events-out", help="Override route_session_events.jsonl path.")
    parser.add_argument("--timeline-out", help="Override route_progress_timeline.jsonl path.")
    parser.add_argument("--summary-out", help="Override route_history_summary.json path.")
    parser.add_argument("--max-recent-points", type=int, default=100, help="Maximum recent path points to keep in state.")
    parser.add_argument("--max-stale-ms", type=int, default=3000, help="Maximum tolerated stale window before stale state is emitted.")
    parser.add_argument("--print-events", action="store_true", help="Print route history events as they are emitted.")
    parser.add_argument("--print-state", action="store_true", help="Print route session state each follow update.")
    parser.add_argument("--write-history", action="store_true", help="Write route history artifacts for recording mode.")
    parser.add_argument("--max-age-ms", type=int, default=DEFAULT_MAX_SOURCE_AGE_MS, help="Live telemetry stale threshold.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_route_templates:
        payload = {
            "schema": "route_template_list.v1",
            "status": "PASS",
            "templates": route_template.list_route_templates(),
        }
        print(
            json.dumps(payload, indent=2, default=str)
            if args.json
            else "\n".join(
                f"{item.get('routeName')} rev {item.get('templateRevision')} {item.get('startArea')} -> {item.get('endArea')} - {item.get('path')}"
                for item in payload["templates"]
            )
        )
        return 0
    if args.validate_template:
        resolution = route_template.resolve_route_template(args.validate_template, default_template=args.default_route_template)
        print(json.dumps({key: value for key, value in resolution.items() if key != "template"}, indent=2, default=str))
        return 0 if resolution.get("status") == "PASS" else 1
    args.template = args.template or args.default_route_template
    if args.route_name and (not args.template or _is_auto_template_input(args.template)):
        match = route_template.find_template_for_route_name(args.route_name)
        if match:
            args.template = str(match.get("path"))
    if not args.template:
        raise SystemExit("--template is required unless --list-route-templates or --validate-template is used")
    if args.live and args.follow:
        payload = run_follow(args)
        print(json.dumps(payload, indent=2, default=str) if args.json else summary_text(payload if payload.get("schema") == SCHEMA_VERSION else {"status": payload.get("status"), "routeState": payload.get("routeState"), "routeName": payload.get("routeName"), "templateRevision": payload.get("templateRevision"), "currentArea": payload.get("currentArea"), "completedSegmentCount": payload.get("completedSegmentCount"), "remainingSegmentCount": payload.get("remainingSegmentCount"), "nextExpectedSegment": payload.get("nextExpectedSegment"), "offRoute": payload.get("offRoute"), "freshness": {"status": payload.get("routeState")}, "warnings": payload.get("warnings") or []}))
        return 0 if payload.get("status") != "FAIL" else 1
    if args.live:
        context = build_live_context_from_sources(
            session=args.session,
            latest_session=args.latest_session,
            sessions_dir=args.sessions_dir,
            sources_override=args.sources,
        )
        status = monitor_live_context(args.template, context, max_age_ms=args.max_age_ms)
    elif args.recording:
        status = monitor_recording(args.template, args.recording)
        if args.write_history:
            _state, _paths, summary = write_recording_history(
                args.template,
                args.recording,
                out_dir=args.out_dir,
                session_id=args.session_id,
                state_out=args.state_out,
                events_out=args.events_out,
                timeline_out=args.timeline_out,
                summary_out=args.summary_out,
            )
            if args.json:
                print(json.dumps(summary, indent=2, default=str))
            else:
                print(summary_text(status))
            return 0 if summary.get("status") != "FAIL" else 1
    else:
        raise SystemExit("--recording or --live is required")
    if args.recording and not args.out:
        write_status(status, args.recording, pretty=True)
    if args.out:
        write_status(status, args.out, pretty=True)
    print(json.dumps(status, indent=2, default=str) if args.json else summary_text(status))
    return 0 if status.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

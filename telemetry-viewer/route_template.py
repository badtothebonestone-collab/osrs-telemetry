from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_sources


TEMPLATE_SCHEMA = "route_template.v1"
TEMPLATE_SEGMENT_SCHEMA = "route_template_segment.v1"
VARIANT_SCHEMA = "route_template_variant.v1"
COMPARISON_SCHEMA = "route_template_comparison.v1"
RESOLUTION_SCHEMA = "route_template_resolution.v1"
AUTO_SELECTION_SCHEMA = "route_template_auto_selection.v1"
QUALITY_ORDER = {"unmatched": 0, "weak": 1, "medium": 2, "strong": 3}
NAVIGATION_OPTIONS = {"walk", "walk here", "world_walk_click", "minimap_click", "navigation_click"}
DOOR_TARGET_HINTS = {"door", "large door", "gate", "large gate"}
DEFAULT_ROUTE_NAME = "Bank_to_Woodcutting_area"
REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTE_TEMPLATE_DIR = REPO_ROOT / "route_templates"
ROUTE_SEMANTICS: dict[str, dict[str, Any]] = {
    "Bank_to_Woodcutting_area": {
        "templateRevision": 3,
        "optionalDoorTransitions": True,
        "requiredSegmentTypes": {"area_start", "area_arrival", "walk_segment", "stair_transition", "ladder_transition", "plane_transition"},
        "templateNotes": [
            "User confirmed this route does not require opening a door.",
            "Door/Open and Large door events are treated as navigation/review evidence, not required route progress.",
            "area_arrival requires proximity to the woodcutting end cluster; broad woodcutting_area labels and distance-only second-walk progress are not final arrival.",
        ],
    },
    "woodcutting_area_to_bank": {
        "templateRevision": 1,
        "optionalDoorTransitions": False,
        "requiredSegmentTypes": {"area_start", "area_arrival", "walk_segment", "stair_transition", "ladder_transition", "plane_transition"},
        "templateNotes": [
            "Extracted from Tree_area_to_Bank recording.",
            "Deposit Box interaction is endpoint/task evidence, not required route progress for the traversal route.",
        ],
    },
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def route_template_dir(root: str | Path | None = None) -> Path:
    return (Path(root).expanduser() if root is not None else REPO_ROOT) / "route_templates"


def route_template_filename(route_name: Any) -> str:
    text = str(route_name or DEFAULT_ROUTE_NAME).strip()
    if text.lower().endswith(".route_template.json"):
        return text
    if text.lower().endswith(".json"):
        return text
    return f"{text}.route_template.json"


def default_template_path(root: str | Path | None = None) -> Path:
    return route_template_dir(root) / route_template_filename(DEFAULT_ROUTE_NAME)


def _route_name_from_input(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    name = Path(text).name
    lowered = name.lower()
    if lowered.endswith(".route_template.json"):
        return name[: -len(".route_template.json")]
    if lowered.endswith(".json"):
        return name[: -len(".json")]
    if any(sep in text for sep in ("/", "\\")) or ":" in text:
        return None
    return text


def _add_candidate(candidates: list[Path], seen: set[str], path: Path) -> None:
    expanded = path.expanduser()
    try:
        key = str(expanded.resolve(strict=False)).lower()
    except OSError:
        key = str(expanded).lower()
    if key not in seen:
        candidates.append(expanded)
        seen.add(key)


def list_route_templates(root: str | Path | None = None) -> list[dict[str, Any]]:
    directory = route_template_dir(root)
    templates: list[dict[str, Any]] = []
    try:
        paths = sorted(directory.glob("*.route_template.json"))
    except OSError:
        paths = []
    for path in paths:
        payload = _json(path)
        start = _dict(payload.get("start"))
        end = _dict(payload.get("end"))
        templates.append(
            {
                "path": str(path.resolve()),
                "filename": path.name,
                "routeName": payload.get("routeName"),
                "templateRevision": payload.get("templateRevision"),
                "startArea": start.get("areaLabel"),
                "endArea": end.get("areaLabel"),
                "aliases": _list(payload.get("aliases")) + _list(_dict(payload.get("routeIdentity")).get("aliases")),
                "requiredSegmentCount": len([item for item in _list(payload.get("segments")) if _dict(item).get("required", True)]),
                "status": "PASS" if payload.get("routeName") and payload.get("templateRevision") else "WARN",
            }
        )
    return templates


def _template_matches_route_name(template: dict[str, Any], route_name: Any) -> bool:
    wanted = _norm(route_name)
    if not wanted:
        return False
    names = [template.get("routeName"), *_list(template.get("aliases"))]
    return any(_norm(name) == wanted for name in names)


def _template_matches_start_end(template: dict[str, Any], start_area: Any, end_area: Any) -> bool:
    return bool(start_area and end_area and template.get("startArea") == start_area and template.get("endArea") == end_area)


def _suggested_route_name(route_name: Any = None, start_area: Any = None, end_area: Any = None) -> str:
    if str(route_name or "").strip():
        return str(route_name).strip()
    if str(start_area or "").strip() and str(end_area or "").strip():
        return f"{str(start_area).strip()}_to_{str(end_area).strip()}"
    return "route_unknown"


def find_template_for_route_name(route_name: Any, *, root: str | Path | None = None) -> dict[str, Any] | None:
    matches = [item for item in list_route_templates(root) if _template_matches_route_name(item, route_name)]
    return matches[0] if matches else None


def find_template_for_start_end(start_area: Any, end_area: Any, *, root: str | Path | None = None) -> dict[str, Any] | None:
    matches = [item for item in list_route_templates(root) if _template_matches_start_end(item, start_area, end_area)]
    return matches[0] if matches else None


def resolve_template_auto(lifecycle: dict[str, Any], *, root: str | Path | None = None) -> dict[str, Any]:
    route_name = lifecycle.get("routeName")
    start_area = _dict(lifecycle.get("start")).get("areaLabel")
    end_area = _dict(lifecycle.get("end")).get("areaLabel")
    candidates = list_route_templates(root)
    warnings: list[str] = []
    selected: dict[str, Any] | None = None
    reason = "none"

    route_matches = [item for item in candidates if _template_matches_route_name(item, route_name)]
    if route_matches:
        selected = route_matches[0]
        reason = "route_name_match"
    else:
        area_matches = [item for item in candidates if _template_matches_start_end(item, start_area, end_area)]
        if area_matches:
            selected = area_matches[0]
            reason = "start_end_match"
        else:
            alias_matches = [item for item in candidates if any(_norm(alias) == _norm(route_name) for alias in _list(item.get("aliases")))]
            if alias_matches:
                selected = alias_matches[0]
                reason = "alias_match"

    alternatives = [item for item in candidates if selected is None or item.get("path") != selected.get("path")]
    suggested = _suggested_route_name(route_name, start_area, end_area)
    resolution: dict[str, Any] = {}
    template: dict[str, Any] = {}
    if selected:
        resolution = resolve_route_template(selected.get("path"), root=root)
        template = _dict(resolution.get("template"))
        status = "PASS" if resolution.get("status") == "PASS" else "FAIL"
        warnings.extend(_list(resolution.get("warnings")))
    else:
        status = "WARN"
        warnings.append(f"no route template found for {suggested}")

    return {
        "schema": AUTO_SELECTION_SCHEMA,
        "status": status,
        "routeName": route_name,
        "startArea": start_area,
        "endArea": end_area,
        "selectedTemplate": selected.get("path") if selected else None,
        "selectedTemplateRouteName": selected.get("routeName") if selected else None,
        "selectedTemplateRevision": selected.get("templateRevision") if selected else None,
        "selectionReason": reason,
        "alternatives": alternatives,
        "warnings": warnings,
        "untemplatedRoute": selected is None,
        "suggestedTemplateName": suggested,
        "resolution": {key: value for key, value in resolution.items() if key != "template"},
        "template": template,
    }


def resolve_route_template(
    value: Any = None,
    *,
    root: str | Path | None = None,
    default_template: str | Path | None = None,
) -> dict[str, Any]:
    repo = Path(root).expanduser() if root is not None else REPO_ROOT
    raw = str(value or "").strip()
    default_value = str(default_template or "").strip()
    candidates: list[Path] = []
    seen: set[str] = set()
    warnings: list[str] = []

    if raw:
        supplied = Path(raw)
        if supplied.is_absolute():
            _add_candidate(candidates, seen, supplied)
        else:
            _add_candidate(candidates, seen, repo / supplied)
            _add_candidate(candidates, seen, route_template_dir(repo) / supplied.name)
            route_name = _route_name_from_input(raw)
            if route_name:
                _add_candidate(candidates, seen, route_template_dir(repo) / route_template_filename(route_name))
    else:
        warnings.append("route template input was empty; using configured default")

    if default_value:
        default_path = Path(default_value)
        if default_path.is_absolute():
            _add_candidate(candidates, seen, default_path)
        else:
            _add_candidate(candidates, seen, repo / default_path)
            _add_candidate(candidates, seen, route_template_dir(repo) / default_path.name)
            default_route_name = _route_name_from_input(default_value)
            if default_route_name:
                _add_candidate(candidates, seen, route_template_dir(repo) / route_template_filename(default_route_name))
    if not raw and not default_value:
        _add_candidate(candidates, seen, default_template_path(repo))

    resolved: Path | None = None
    payload: dict[str, Any] = {}
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            resolved = candidate.resolve()
            payload = _json(resolved)
            break

    route_name = payload.get("routeName")
    revision = payload.get("templateRevision")
    required_count = len([item for item in _list(payload.get("segments")) if _dict(item).get("required", True)])
    if resolved is None:
        status = "FAIL"
        warnings.append("route template could not be resolved")
    elif not route_name:
        status = "FAIL"
        warnings.append("route template is missing routeName")
    elif revision is None:
        status = "FAIL"
        warnings.append("route template is missing templateRevision")
    elif required_count <= 0:
        status = "FAIL"
        warnings.append("route template has no required segments")
    else:
        status = "PASS"

    return {
        "schema": RESOLUTION_SCHEMA,
        "input": raw,
        "resolvedPath": str(resolved) if resolved else None,
        "exists": bool(resolved),
        "routeName": route_name,
        "templateRevision": revision,
        "requiredSegmentCount": required_count,
        "status": status,
        "warnings": warnings,
        "candidatesTried": [str(path) for path in candidates],
        "template": payload,
    }


def _world(value: Any) -> dict[str, int] | None:
    record = _dict(value)
    x = _first(record.get("worldX"), record.get("x"))
    y = _first(record.get("worldY"), record.get("y"))
    plane = record.get("plane")
    try:
        if x is None or y is None:
            return None
        result = {"worldX": int(x), "worldY": int(y)}
        if plane is not None:
            result["plane"] = int(plane)
        return result
    except (TypeError, ValueError):
        return None


def _distance(a: Any, b: Any) -> float | None:
    aw = _world(a)
    bw = _world(b)
    if not aw or not bw:
        return None
    return round(math.hypot(float(aw["worldX"] - bw["worldX"]), float(aw["worldY"] - bw["worldY"])), 3)


def _segment_distance(segment: dict[str, Any]) -> float | None:
    return _distance(segment.get("startWorld"), segment.get("endWorld"))


def _utc_now() -> str:
    try:
        return telemetry_sources.utc_now()
    except Exception:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _quality_rank(value: Any) -> int:
    return QUALITY_ORDER.get(str(value or "").lower(), 0)


def _post_result(segment: dict[str, Any]) -> str:
    return _norm(_dict(segment.get("postcondition")).get("result"))


def _post_type(segment: dict[str, Any]) -> str:
    return _norm(_dict(segment.get("postcondition")).get("type"))


def _action(segment: dict[str, Any]) -> dict[str, Any]:
    return _dict(segment.get("primaryAction"))


def _option(segment: dict[str, Any]) -> str:
    return _norm(_action(segment).get("option"))


def _target(segment: dict[str, Any]) -> str:
    return _norm(_action(segment).get("target"))


def _is_navigation_option(option: str) -> bool:
    return option in NAVIGATION_OPTIONS


def _is_door_target(target: str) -> bool:
    return target in DOOR_TARGET_HINTS or any(hint in target for hint in DOOR_TARGET_HINTS)


def _route_semantics(route_name: Any) -> dict[str, Any]:
    return _dict(ROUTE_SEMANTICS.get(str(route_name or "")))


def _is_door_or_open_segment(segment: dict[str, Any]) -> bool:
    segment_type = str(segment.get("segmentType") or "")
    option = _option(segment)
    target = _target(segment)
    return segment_type == "door_transition" or option in {"open", "close"} or _is_door_target(target)


def _is_cancel_segment(segment: dict[str, Any]) -> bool:
    return _option(segment) == "cancel" or _norm(segment.get("label")) == "cancel"


def _segment_semantic_role(segment: dict[str, Any], *, route_name: Any) -> str:
    semantics = _route_semantics(route_name)
    segment_type = str(segment.get("segmentType") or "")
    post = _dict(segment.get("postcondition"))
    post_result = str(post.get("result") or "")
    option = _option(segment)
    if _is_cancel_segment(segment):
        return "incidental_menu_evidence"
    if segment_type in {"bank_context", "task_context"}:
        return "route_context"
    if semantics.get("optionalDoorTransitions") and _is_door_or_open_segment(segment):
        return "navigation_support"
    if segment_type == "walk_segment" and _is_navigation_option(option) and option != "walk":
        return "navigation_support"
    if post_result != "success":
        return "review_only"
    required_types = semantics.get("requiredSegmentTypes")
    if isinstance(required_types, set) and segment_type not in required_types:
        return "review_only"
    return "route_progress"


def _is_required_route_segment(segment: dict[str, Any], *, route_name: Any) -> bool:
    post = _dict(segment.get("postcondition"))
    if post.get("result") != "success":
        return False
    return _segment_semantic_role(segment, route_name=route_name) == "route_progress"


def _annotate_template_segment(item: dict[str, Any], *, role: str, route_name: Any) -> dict[str, Any]:
    item["routeRole"] = role
    notes = _list(item.get("notes"))
    if route_name == "Bank_to_Woodcutting_area" and role == "navigation_support" and _is_door_or_open_segment(item):
        notes.append("Door/Open is not required for Bank_to_Woodcutting_area; keep as navigation support only.")
    if role == "incidental_menu_evidence":
        notes.append("Incidental menu evidence; not required route progress.")
    item["notes"] = notes
    return item


def _movement_strong(segment: dict[str, Any], *, minimum: float = 3.0) -> bool:
    if _post_type(segment) != "movement" or _post_result(segment) != "success":
        return False
    distance = _segment_distance(segment)
    return distance is not None and distance >= minimum


def _target_kind_from_segment(segment: dict[str, Any]) -> str | None:
    kind = _dict(segment.get("primaryAction")).get("targetKind")
    if kind:
        return kind
    segment_type = str(segment.get("segmentType") or "")
    if segment_type in {"door_transition", "stair_transition", "ladder_transition", "plane_transition"}:
        return "object"
    if segment_type == "walk_segment":
        return "ground"
    return None


def _plane_delta(segment: dict[str, Any]) -> int | None:
    start = segment.get("startPlane")
    end = segment.get("endPlane")
    try:
        if start is None or end is None:
            return None
        return int(end) - int(start)
    except (TypeError, ValueError):
        return None


def _min_distance_moved(segment: dict[str, Any]) -> float | None:
    distance = _distance(segment.get("startWorld"), segment.get("endWorld"))
    if distance is None:
        return None
    if str(_dict(segment.get("postcondition")).get("type") or "") == "movement":
        return min(distance, 1.0) if distance > 0 else None
    return None


def _quality_requirement(segment: dict[str, Any]) -> str | None:
    action = _dict(segment.get("primaryAction"))
    quality = action.get("targetQuality")
    if quality:
        return "medium"
    return None


def _segment_tolerances(segment: dict[str, Any]) -> dict[str, Any]:
    segment_type = str(segment.get("segmentType") or "")
    if segment_type in {"stair_transition", "ladder_transition", "plane_transition"}:
        return {"timeWindowMs": 5000, "tickWindow": 8, "positionToleranceTiles": 8}
    if segment_type == "door_transition":
        return {"timeWindowMs": 4000, "tickWindow": 6, "positionToleranceTiles": 8}
    if segment_type == "walk_segment":
        return {"timeWindowMs": 5000, "tickWindow": 8, "positionToleranceTiles": 12}
    return {"timeWindowMs": 5000, "tickWindow": 8, "positionToleranceTiles": 8}


def _template_segment(segment: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    action = _dict(segment.get("primaryAction"))
    post = _dict(segment.get("postcondition"))
    segment_type = str(segment.get("segmentType") or "unknown")
    return {
        "schema": TEMPLATE_SEGMENT_SCHEMA,
        "segmentIndex": segment.get("segmentIndex"),
        "segmentType": segment_type,
        "label": segment.get("label"),
        "required": bool(required),
        "order": "flexible" if segment_type == "walk_segment" else "strict",
        "primaryAction": {
            "option": action.get("option"),
            "target": action.get("target"),
            "targetKind": _target_kind_from_segment(segment),
        },
        "expectedPostcondition": {
            "type": post.get("type"),
            "planeDelta": _plane_delta(segment),
            "minDistanceMoved": _min_distance_moved(segment),
        },
        "qualityRequirements": {
            "minTargetQuality": _quality_requirement(segment),
            "allowMissingRowGeometry": True,
        },
        "tolerances": _segment_tolerances(segment),
        "notes": [],
    }


def extract_template(
    lifecycle: dict[str, Any],
    *,
    created_from_recording: str | Path | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    route_segments = _list(lifecycle.get("routeSegments"))
    status = str(lifecycle.get("status") or "").upper()
    warnings: list[str] = []
    if status not in {"PASS", "WARN"}:
        warnings.append(f"template extracted from non-passing traversal status {status or 'unknown'}")
    if not route_segments:
        warnings.append("no routeSegments available; template has no required segments")
    route_name = lifecycle.get("routeName") or "route_unknown"
    semantics = _route_semantics(route_name)
    start = _dict(lifecycle.get("start"))
    end = _dict(lifecycle.get("end"))
    required_segments: list[dict[str, Any]] = []
    optional_segments: list[dict[str, Any]] = []
    for segment in route_segments:
        segment_record = _dict(segment)
        role = _segment_semantic_role(segment_record, route_name=route_name)
        required = _is_required_route_segment(segment_record, route_name=route_name)
        item = _annotate_template_segment(_template_segment(segment_record, required=required), role=role, route_name=route_name)
        if required:
            item["segmentIndex"] = len(required_segments) + 1
            required_segments.append(item)
        else:
            item["segmentIndex"] = len(optional_segments) + 1
            optional_segments.append(item)
            if role in {"navigation_support", "incidental_menu_evidence"}:
                warnings.append(f"{item.get('label') or item.get('segmentType')} kept as {role}, not required route progress")
    if lifecycle.get("reviewEvidence"):
        warnings.append(f"{len(_list(lifecycle.get('reviewEvidence')))} review evidence item(s) were kept as notes, not required segments")
    template = {
        "schema": TEMPLATE_SCHEMA,
        "templateVersion": 1,
        "routeName": route_name,
        "createdFromRecording": str(created_from_recording) if created_from_recording else lifecycle.get("recordingPath"),
        "createdAtUtc": created_at_utc or _utc_now(),
        "start": {
            "areaLabel": start.get("areaLabel"),
            "world": _world(start.get("world")),
            "toleranceTiles": 8,
        },
        "end": {
            "areaLabel": end.get("areaLabel"),
            "world": _world(end.get("world")),
            "toleranceTiles": 8,
        },
        "segments": required_segments,
        "optionalSegments": optional_segments,
        "reviewEvidenceNotes": [
            {
                "evidenceId": item.get("evidenceId"),
                "type": item.get("type"),
                "action": item.get("action"),
                "targetName": item.get("targetName"),
                "reviewReason": item.get("reviewReason"),
            }
            for item in _list(lifecycle.get("reviewEvidence"))
            if isinstance(item, dict)
        ],
        "warnings": warnings,
    }
    if semantics:
        template["templateRevision"] = semantics.get("templateRevision")
        template["templateNotes"] = _list(semantics.get("templateNotes"))
        template["templateSemantics"] = {
            "optionalDoorTransitions": bool(semantics.get("optionalDoorTransitions")),
            "requiredSegmentTypes": sorted(semantics.get("requiredSegmentTypes") or []),
        }
    return template


def load_lifecycle_from_recording(recording: str | Path) -> dict[str, Any]:
    path = Path(recording)
    if path.is_dir():
        return _json(path / "traversal_lifecycle.json")
    return _json(path)


def template_path_for(route_name: str, out_dir: str | Path) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(route_name or "route_unknown"))
    return Path(out_dir) / f"{safe}.route_template.json"


def write_template(template: dict[str, Any], out_dir: str | Path, *, pretty: bool = True) -> Path:
    out_path = template_path_for(str(template.get("routeName") or "route_unknown"), out_dir)
    telemetry_sources.atomic_write_json(out_path, template, pretty=pretty)
    return out_path


def extract_template_from_recording(recording: str | Path, out_dir: str | Path, *, pretty: bool = True) -> tuple[dict[str, Any], Path]:
    lifecycle = load_lifecycle_from_recording(recording)
    template = extract_template(lifecycle, created_from_recording=recording)
    return template, write_template(template, out_dir, pretty=pretty)


def _area_match(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    expected_area = expected.get("areaLabel")
    actual_area = actual.get("areaLabel")
    if expected_area and actual_area == expected_area:
        reasons.append(f"area matched {expected_area}")
        return True, reasons, warnings
    if expected_area and actual_area and actual_area != expected_area:
        warnings.append(f"expected area {expected_area}, saw {actual_area}")
        return False, reasons, warnings
    expected_world = _world(expected.get("world"))
    actual_world = _world(actual.get("world"))
    tolerance = float(expected.get("toleranceTiles") or 8)
    distance = _distance(expected_world, actual_world)
    if distance is not None and distance <= tolerance:
        reasons.append(f"world matched within {tolerance:g} tiles")
        return True, reasons, warnings
    warnings.append(f"expected area {expected_area}, saw {actual_area}")
    return False, reasons, warnings


def _action_matches(template_action: dict[str, Any], recording_action: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    expected_option = _clean(template_action.get("option"))
    expected_target = _clean(template_action.get("target"))
    actual_option = _clean(recording_action.get("option"))
    actual_target = _clean(recording_action.get("target"))
    score = 0.0
    checks = 0
    if expected_option:
        checks += 1
        if actual_option and actual_option.lower() == expected_option.lower():
            score += 1.0
            reasons.append(f"option matched {expected_option}")
        elif expected_option.lower() == "walk" and actual_option and _is_navigation_option(actual_option.lower()):
            score += 1.0
            reasons.append(f"navigation option {actual_option} satisfied Walk")
        else:
            warnings.append(f"expected option {expected_option}, saw {actual_option}")
    if expected_target:
        checks += 1
        if actual_target and actual_target.lower() == expected_target.lower():
            score += 1.0
            reasons.append(f"target matched {expected_target}")
        else:
            warnings.append(f"expected target {expected_target}, saw {actual_target}")
    if checks == 0:
        return 1.0, ["no primary action required"], warnings
    return score / checks, reasons, warnings


def _segment_score(template_segment: dict[str, Any], recording_segment: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0.0
    expected_type = template_segment.get("segmentType")
    actual_type = recording_segment.get("segmentType")
    if expected_type == actual_type:
        score += 0.35
        reasons.append(f"segment type matched {expected_type}")
    elif expected_type == "walk_segment" and actual_type == "walk_segment":
        score += 0.35
    else:
        warnings.append(f"expected segment type {expected_type}, saw {actual_type}")

    expected_post = _dict(template_segment.get("expectedPostcondition"))
    actual_post = _dict(recording_segment.get("postcondition"))
    if expected_post.get("type") == actual_post.get("type"):
        score += 0.2
        reasons.append(f"postcondition matched {expected_post.get('type')}")
    else:
        warnings.append(f"expected postcondition {expected_post.get('type')}, saw {actual_post.get('type')}")
    if expected_post.get("planeDelta") is not None and _plane_delta(recording_segment) != expected_post.get("planeDelta"):
        warnings.append(f"expected plane delta {expected_post.get('planeDelta')}, saw {_plane_delta(recording_segment)}")
    elif expected_post.get("planeDelta") is not None:
        score += 0.1
        reasons.append("plane delta matched")
    if expected_post.get("minDistanceMoved") is not None:
        actual_distance = _distance(recording_segment.get("startWorld"), recording_segment.get("endWorld"))
        if actual_distance is not None and actual_distance >= float(expected_post.get("minDistanceMoved") or 0):
            score += 0.1
            reasons.append("movement distance met minimum")
        else:
            warnings.append("movement distance below expectation")

    action_score, action_reasons, action_warnings = _action_matches(_dict(template_segment.get("primaryAction")), _dict(recording_segment.get("primaryAction")))
    score += 0.25 * action_score
    reasons.extend(action_reasons)
    warnings.extend(action_warnings)

    min_quality = _dict(template_segment.get("qualityRequirements")).get("minTargetQuality")
    actual_quality = _dict(recording_segment.get("primaryAction")).get("targetQuality")
    if min_quality:
        if _quality_rank(actual_quality) >= _quality_rank(min_quality):
            score += 0.1
            reasons.append(f"target quality met {min_quality}")
        else:
            warnings.append(f"target quality below {min_quality}: {actual_quality}")
    else:
        score += 0.1
    return round(min(score, 1.0), 3), reasons, warnings


def _match_status(score: float, warnings: list[str]) -> str:
    if score >= 0.78 and not any("below" in item or "expected" in item for item in warnings):
        return "matched"
    if score >= 0.55:
        return "partial"
    return "missing"


def _alternative_score(alternative: dict[str, Any], recording_segment: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0.0
    expected_type = alternative.get("segmentType")
    if expected_type and expected_type == recording_segment.get("segmentType"):
        score += 0.25
        reasons.append(f"alternative segment type matched {expected_type}")
    elif expected_type:
        warnings.append(f"expected alternative type {expected_type}, saw {recording_segment.get('segmentType')}")
    else:
        score += 0.25

    action_score, action_reasons, action_warnings = _action_matches(_dict(alternative.get("primaryAction")), _action(recording_segment))
    score += 0.25 * action_score
    reasons.extend(action_reasons)
    warnings.extend(action_warnings)

    requirement = _dict(alternative.get("requiresPostcondition"))
    post = _dict(recording_segment.get("postcondition"))
    if requirement:
        if requirement.get("type") == post.get("type"):
            score += 0.2
            reasons.append(f"alternative postcondition matched {requirement.get('type')}")
        else:
            warnings.append(f"expected alternative postcondition {requirement.get('type')}, saw {post.get('type')}")
        min_distance = requirement.get("minDistanceMoved")
        if min_distance is not None:
            distance = _segment_distance(recording_segment)
            if distance is not None and distance >= float(min_distance):
                score += 0.2
                reasons.append(f"movement distance {distance:g} met alternative minimum {float(min_distance):g}")
            else:
                warnings.append(f"movement distance below alternative minimum {min_distance}")
    else:
        score += 0.4

    min_quality = _dict(alternative.get("qualityRequirements")).get("minTargetQuality")
    actual_quality = _action(recording_segment).get("targetQuality")
    if min_quality:
        if _quality_rank(actual_quality) >= _quality_rank(min_quality):
            score += 0.1
            reasons.append(f"target quality met {min_quality}")
        elif _movement_strong(recording_segment):
            score += 0.05
            reasons.append("strong movement evidence compensated for target quality")
        else:
            warnings.append(f"target quality below {min_quality}: {actual_quality}")
    else:
        score += 0.1
    return round(min(score, 1.0), 3), reasons, warnings


def _variant_overrides_for(template: dict[str, Any], template_segment: dict[str, Any]) -> list[dict[str, Any]]:
    index = template_segment.get("segmentIndex")
    overrides: list[dict[str, Any]] = []
    for variant in _list(template.get("variants")):
        if not isinstance(variant, dict):
            continue
        for override in _list(variant.get("segmentOverrides")):
            override_record = _dict(override)
            if override_record.get("baseSegmentIndex") == index:
                item = dict(override_record)
                item["_variantName"] = variant.get("variantName")
                item["_variantDescription"] = variant.get("description")
                overrides.append(item)
    return overrides


def _registered_variant_match(
    template: dict[str, Any],
    template_segment: dict[str, Any],
    recording_segment: dict[str, Any],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for override in _variant_overrides_for(template, template_segment):
        for alternative in _list(override.get("allowedAlternatives")):
            alternative_record = _dict(alternative)
            if not alternative_record.get("satisfiesBaseSegment", True):
                continue
            score, reasons, warnings = _alternative_score(alternative_record, recording_segment)
            if score < 0.75 or any("expected" in item or "below" in item for item in warnings):
                continue
            candidate = {
                "schema": "route_navigation_support_substitution.v1",
                "registered": True,
                "variantName": override.get("_variantName"),
                "baseSegmentIndex": template_segment.get("segmentIndex"),
                "baseSegmentLabel": template_segment.get("label"),
                "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                "recordingSegmentLabel": recording_segment.get("label"),
                "alternative": alternative_record,
                "score": score,
                "reasons": reasons,
                "warnings": warnings,
            }
            if best is None or float(candidate["score"]) > float(best.get("score") or 0):
                best = candidate
    return best


def _door_navigation_substitution(
    template_segment: dict[str, Any],
    recording_segment: dict[str, Any],
    *,
    endpoints_match: bool,
) -> dict[str, Any] | None:
    base_action = _action(template_segment)
    base_option = _norm(base_action.get("option"))
    base_target = _norm(base_action.get("target"))
    option = _option(recording_segment)
    target = _target(recording_segment)
    if not endpoints_match:
        return None
    if template_segment.get("segmentType") != "door_transition" or base_option != "open" or not _is_door_target(base_target):
        return None
    if recording_segment.get("segmentType") != "walk_segment" or not _is_navigation_option(option):
        return None
    if not _is_door_target(target):
        return None
    if not _movement_strong(recording_segment, minimum=3.0):
        return None
    quality = _action(recording_segment).get("targetQuality")
    if _quality_rank(quality) < _quality_rank("medium") and not _movement_strong(recording_segment, minimum=6.0):
        return None
    distance = _segment_distance(recording_segment)
    return {
        "schema": "route_navigation_support_substitution.v1",
        "registered": False,
        "variantName": None,
        "baseSegmentIndex": template_segment.get("segmentIndex"),
        "baseSegmentLabel": template_segment.get("label"),
        "recordingSegmentIndex": recording_segment.get("segmentIndex"),
        "recordingSegmentLabel": recording_segment.get("label"),
        "alternative": {
            "segmentType": recording_segment.get("segmentType"),
            "primaryAction": {
                "option": _action(recording_segment).get("option"),
                "target": _action(recording_segment).get("target"),
            },
            "satisfiesBaseSegment": True,
            "requiresPostcondition": {"type": "movement", "minDistanceMoved": 3},
            "qualityRequirements": {"minTargetQuality": "medium"},
            "notes": [
                "Navigation-support click near/through door can satisfy door transition when route progress is confirmed."
            ],
        },
        "score": 0.92,
        "movementDistance": distance,
        "reasons": [
            "door/open base segment can be satisfied by navigation support",
            f"recording used {_action(recording_segment).get('option')} {_action(recording_segment).get('target')}",
            f"movement distance {distance:g} confirmed route progress" if distance is not None else "movement confirmed route progress",
            "route start/end areas matched",
        ],
        "warnings": [],
    }


def _navigation_substitution(
    template: dict[str, Any],
    template_segment: dict[str, Any],
    recording_segment: dict[str, Any],
    *,
    endpoints_match: bool,
) -> dict[str, Any] | None:
    registered = _registered_variant_match(template, template_segment, recording_segment)
    if registered:
        return registered
    return _door_navigation_substitution(template_segment, recording_segment, endpoints_match=endpoints_match)


def _is_harmless_extra_segment(segment: dict[str, Any], lifecycle: dict[str, Any], *, end_matched: bool) -> bool:
    if not end_matched:
        return False
    post = _dict(segment.get("postcondition"))
    route_name = lifecycle.get("routeName")
    role = _segment_semantic_role(segment, route_name=route_name)
    if role in {"navigation_support", "route_context", "incidental_menu_evidence", "review_only"}:
        return True
    if post.get("result") == "failed":
        return False
    end_area = _dict(lifecycle.get("end")).get("areaLabel")
    area_before = post.get("areaBefore")
    area_after = post.get("areaAfter")
    option = _option(segment)
    end_world = _world(_dict(lifecycle.get("end")).get("world"))
    segment_end = _world(segment.get("endWorld"))
    if end_world and segment_end:
        distance_to_end = _distance(end_world, segment_end)
        if distance_to_end is not None and distance_to_end <= float(_dict(lifecycle.get("end")).get("toleranceTiles") or 8):
            return True
    if segment.get("segmentType") == "walk_segment" and _is_navigation_option(option):
        return True
    if end_area and (area_before == end_area or area_after == end_area):
        return True
    if str(segment.get("segmentType") or "") in {"task_context", "bank_context"}:
        return True
    return False


def compare_template(template: dict[str, Any], lifecycle: dict[str, Any], *, recording: str | Path | None = None) -> dict[str, Any]:
    route_segments = [_dict(item) for item in _list(lifecycle.get("routeSegments"))]
    required_segments = [_dict(item) for item in _list(template.get("segments")) if _dict(item).get("required", True)]
    optional_segments = [_dict(item) for item in _list(template.get("optionalSegments"))]
    warnings: list[str] = []
    missing_segments: list[dict[str, Any]] = []
    extra_segments: list[dict[str, Any]] = []
    out_of_order: list[dict[str, Any]] = []
    weak_segments: list[dict[str, Any]] = []
    failed_postconditions: list[dict[str, Any]] = []
    segment_matches: list[dict[str, Any]] = []
    optional_segment_matches: list[dict[str, Any]] = []
    navigation_substitutions: list[dict[str, Any]] = []
    allowed_extra_segments: list[dict[str, Any]] = []
    navigation_support_evidence: list[dict[str, Any]] = []
    review_evidence_segments: list[dict[str, Any]] = []
    matched_variant_names: set[str] = set()
    valid_unregistered_variant = False

    template_start_area = _dict(template.get("start")).get("areaLabel")
    template_end_area = _dict(template.get("end")).get("areaLabel")
    lifecycle_start_area = _dict(lifecycle.get("start")).get("areaLabel")
    lifecycle_end_area = _dict(lifecycle.get("end")).get("areaLabel")
    direction_mismatch = bool(
        template_start_area
        and template_end_area
        and lifecycle_start_area
        and lifecycle_end_area
        and template_start_area == lifecycle_end_area
        and template_end_area == lifecycle_start_area
    )

    route_name_matched = template.get("routeName") == lifecycle.get("routeName")
    if not route_name_matched:
        warnings.append(f"route name mismatch: expected {template.get('routeName')}, saw {lifecycle.get('routeName')}")
    if direction_mismatch:
        warnings.append(
            "route_template_direction_mismatch: template start/end areas are reversed relative to this traversal; use the matching one-way template or auto-selection"
        )
    start_matched, start_reasons, start_warnings = _area_match(_dict(template.get("start")), _dict(lifecycle.get("start")))
    end_matched, end_reasons, end_warnings = _area_match(_dict(template.get("end")), _dict(lifecycle.get("end")))
    warnings.extend(start_warnings + end_warnings)

    used_recording_indexes: set[int] = set()
    search_start = 0
    for template_segment in required_segments:
        best_index: int | None = None
        best_score = -1.0
        best_reasons: list[str] = []
        best_warnings: list[str] = []
        best_substitution: dict[str, Any] | None = None
        order = template_segment.get("order") or "strict"
        candidates = enumerate(route_segments)
        for index, recording_segment in candidates:
            if index in used_recording_indexes:
                continue
            if order == "strict" and index < search_start:
                continue
            score, reasons, candidate_warnings = _segment_score(template_segment, recording_segment)
            if score > best_score:
                best_index = index
                best_score = score
                best_reasons = reasons
                best_warnings = candidate_warnings
            substitution = _navigation_substitution(template, template_segment, recording_segment, endpoints_match=bool(start_matched and end_matched))
            if substitution and (
                best_substitution is None
                or float(substitution.get("score") or 0.0) > float(best_substitution.get("score") or 0.0)
            ):
                best_substitution = substitution
        if best_substitution and (best_score < 0.78 or bool(best_substitution.get("registered"))):
            subst_index = None
            for index, recording_segment in enumerate(route_segments):
                if recording_segment.get("segmentIndex") == best_substitution.get("recordingSegmentIndex") and index not in used_recording_indexes:
                    subst_index = index
                    break
            if subst_index is not None:
                used_recording_indexes.add(subst_index)
                if order == "strict":
                    search_start = subst_index + 1
                navigation_substitutions.append(best_substitution)
                if best_substitution.get("registered"):
                    if best_substitution.get("variantName"):
                        matched_variant_names.add(str(best_substitution.get("variantName")))
                    match_status = "matched_variant"
                else:
                    valid_unregistered_variant = True
                    match_status = "variant_candidate"
                segment_matches.append(
                    {
                        "templateSegmentIndex": template_segment.get("segmentIndex"),
                        "recordingSegmentIndex": best_substitution.get("recordingSegmentIndex"),
                        "matchStatus": match_status,
                        "score": best_substitution.get("score"),
                        "reasons": best_substitution.get("reasons") or [],
                        "warnings": best_substitution.get("warnings") or [],
                        "substitution": best_substitution,
                    }
                )
                continue
        if best_index is None or best_score < 0.55:
            out_index: int | None = None
            out_score = -1.0
            out_reasons: list[str] = []
            out_warnings: list[str] = []
            if order == "strict":
                for index, recording_segment in enumerate(route_segments):
                    if index in used_recording_indexes or index >= search_start:
                        continue
                    score, reasons, candidate_warnings = _segment_score(template_segment, recording_segment)
                    if score > out_score:
                        out_index = index
                        out_score = score
                        out_reasons = reasons
                        out_warnings = candidate_warnings
                if out_index is not None and out_score >= 0.55:
                    recording_segment = route_segments[out_index]
                    used_recording_indexes.add(out_index)
                    out_of_order.append(
                        {
                            "templateSegmentIndex": template_segment.get("segmentIndex"),
                            "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                            "label": template_segment.get("label"),
                        }
                    )
                    segment_matches.append(
                        {
                            "templateSegmentIndex": template_segment.get("segmentIndex"),
                            "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                            "matchStatus": "out_of_order",
                            "score": out_score,
                            "reasons": out_reasons,
                            "warnings": out_warnings + ["matched before expected order"],
                        }
                    )
                    continue
            missing = {
                "templateSegmentIndex": template_segment.get("segmentIndex"),
                "segmentType": template_segment.get("segmentType"),
                "label": template_segment.get("label"),
                "reasons": best_reasons,
                "warnings": best_warnings or ["no plausible recording segment found"],
            }
            missing_segments.append(missing)
            segment_matches.append(
                {
                    "templateSegmentIndex": template_segment.get("segmentIndex"),
                    "recordingSegmentIndex": None,
                    "matchStatus": "missing",
                    "score": max(0.0, best_score),
                    "reasons": best_reasons,
                    "warnings": missing["warnings"],
                }
            )
            continue
        recording_segment = route_segments[best_index]
        used_recording_indexes.add(best_index)
        if order == "strict":
            if best_index < search_start:
                out_of_order.append({"templateSegmentIndex": template_segment.get("segmentIndex"), "recordingSegmentIndex": recording_segment.get("segmentIndex")})
            search_start = best_index + 1
        match_status = _match_status(best_score, best_warnings)
        if match_status == "partial":
            weak_segments.append(
                {
                    "templateSegmentIndex": template_segment.get("segmentIndex"),
                    "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                    "label": template_segment.get("label"),
                    "warnings": best_warnings,
                }
            )
        if _dict(recording_segment.get("postcondition")).get("result") not in {"success", "area_start", "area_arrival"}:
            failed_postconditions.append({"recordingSegmentIndex": recording_segment.get("segmentIndex"), "label": recording_segment.get("label")})
        segment_matches.append(
            {
                "templateSegmentIndex": template_segment.get("segmentIndex"),
                "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                "matchStatus": match_status,
                "score": best_score,
                "reasons": best_reasons,
                "warnings": best_warnings,
            }
        )

    for template_segment in optional_segments:
        best_index: int | None = None
        best_score = -1.0
        best_reasons: list[str] = []
        best_warnings: list[str] = []
        for index, recording_segment in enumerate(route_segments):
            if index in used_recording_indexes:
                continue
            score, reasons, candidate_warnings = _segment_score(template_segment, recording_segment)
            if score > best_score:
                best_index = index
                best_score = score
                best_reasons = reasons
                best_warnings = candidate_warnings
        if best_index is not None and best_score >= 0.55:
            recording_segment = route_segments[best_index]
            used_recording_indexes.add(best_index)
            optional_segment_matches.append(
                {
                    "templateSegmentIndex": template_segment.get("segmentIndex"),
                    "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                    "matchStatus": _match_status(best_score, best_warnings),
                    "score": best_score,
                    "reasons": best_reasons,
                    "warnings": best_warnings,
                }
            )

    for index, recording_segment in enumerate(route_segments):
        if index not in used_recording_indexes:
            if _is_harmless_extra_segment(recording_segment, lifecycle, end_matched=bool(end_matched)):
                role = _segment_semantic_role(recording_segment, route_name=lifecycle.get("routeName"))
                if role == "navigation_support":
                    navigation_support_evidence.append(
                        {
                            "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                            "segmentType": recording_segment.get("segmentType"),
                            "label": recording_segment.get("label"),
                            "postcondition": recording_segment.get("postcondition"),
                        }
                    )
                elif role in {"incidental_menu_evidence", "review_only"}:
                    review_evidence_segments.append(
                        {
                            "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                            "segmentType": recording_segment.get("segmentType"),
                            "label": recording_segment.get("label"),
                            "postcondition": recording_segment.get("postcondition"),
                            "role": role,
                        }
                    )
                allowed_extra_segments.append(
                    {
                        "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                        "segmentType": recording_segment.get("segmentType"),
                        "label": recording_segment.get("label"),
                        "postcondition": recording_segment.get("postcondition"),
                        "reason": role if role != "route_progress" else "harmless_navigation_or_post_arrival_evidence",
                    }
                )
                continue
            extra_segments.append(
                {
                    "recordingSegmentIndex": recording_segment.get("segmentIndex"),
                    "segmentType": recording_segment.get("segmentType"),
                    "label": recording_segment.get("label"),
                    "postcondition": recording_segment.get("postcondition"),
                }
            )

    matched_statuses = {"matched", "partial", "matched_variant", "variant_candidate"}
    matched_count = sum(1 for item in segment_matches if item.get("matchStatus") in matched_statuses)
    matched_strong = sum(1 for item in segment_matches if item.get("matchStatus") in {"matched", "matched_variant", "variant_candidate"})
    required_count = len(required_segments)
    score_parts = [float(item.get("score") or 0.0) for item in segment_matches]
    base_score = sum(score_parts) / required_count if required_count else 0.0
    if route_name_matched:
        base_score += 0.05
    if start_matched:
        base_score += 0.05
    if end_matched:
        base_score += 0.1
    score = round(min(base_score, 1.0), 3)

    status_reason = "PASS_BASE_TEMPLATE"
    if not end_matched:
        status = "FAIL"
        status_reason = "FAIL_WRONG_ENDPOINT"
    elif failed_postconditions:
        status = "FAIL"
        status_reason = "FAIL_FAILED_POSTCONDITION"
    elif missing_segments or out_of_order or failed_postconditions:
        status = "WARN"
        if missing_segments:
            status_reason = "WARN_PARTIAL_BUT_ENDPOINT_REACHED"
        elif out_of_order:
            status_reason = "FAIL_OUT_OF_ORDER_REQUIRED_SEGMENT"
    elif weak_segments or extra_segments:
        status = "WARN"
        status_reason = "WARN_EXTRA_REVIEW_EVIDENCE" if extra_segments else "WARN_PARTIAL_BUT_ENDPOINT_REACHED"
    elif valid_unregistered_variant:
        status = "WARN"
        status_reason = "WARN_VALID_UNREGISTERED_VARIANT"
    elif matched_variant_names:
        status = "PASS"
        status_reason = "PASS_REGISTERED_VARIANT"
    else:
        status = "PASS"

    if _list(lifecycle.get("reviewEvidence")):
        warnings.append(f"{len(_list(lifecycle.get('reviewEvidence')))} review evidence item(s) present; not treated as template failures")
    return {
        "schema": COMPARISON_SCHEMA,
        "status": status,
        "statusReason": status_reason,
        "templateName": template.get("routeName"),
        "recording": str(recording) if recording else lifecycle.get("recordingPath"),
        "routeNameMatched": route_name_matched,
        "startAreaMatched": start_matched,
        "endAreaMatched": end_matched,
        "startMatchReasons": start_reasons,
        "endMatchReasons": end_reasons,
        "optionalSegmentCount": len(optional_segments),
        "templateRevision": template.get("templateRevision"),
        "routeTemplateDirectionMismatch": direction_mismatch,
        "directionMismatch": direction_mismatch,
        "detectedRouteName": lifecycle.get("routeName"),
        "detectedStartArea": lifecycle_start_area,
        "detectedEndArea": lifecycle_end_area,
        "matchedSegmentCount": matched_count,
        "matchedRequiredSegmentCount": matched_strong,
        "requiredSegmentCount": required_count,
        "missingSegments": missing_segments,
        "extraSegments": extra_segments,
        "allowedExtraSegments": allowed_extra_segments,
        "navigationSupportEvidence": navigation_support_evidence,
        "reviewEvidenceSegments": review_evidence_segments,
        "outOfOrderSegments": out_of_order,
        "weakSegments": weak_segments,
        "failedPostconditions": failed_postconditions,
        "score": score,
        "warnings": warnings,
        "segmentMatches": segment_matches,
        "optionalSegmentMatches": optional_segment_matches,
        "navigationSupportSubstitutions": navigation_substitutions,
        "matchedVariantName": sorted(matched_variant_names)[0] if matched_variant_names else None,
        "matchedVariantNames": sorted(matched_variant_names),
        "validUnregisteredVariant": bool(valid_unregistered_variant),
        "reviewEvidenceCount": len(_list(lifecycle.get("reviewEvidence"))),
    }


def compare_template_files(template_path: str | Path, recording_or_lifecycle: str | Path | dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_route_template(template_path)
    template = _dict(resolution.get("template"))
    if isinstance(recording_or_lifecycle, dict):
        lifecycle = recording_or_lifecycle
        recording = lifecycle.get("recordingPath")
    else:
        path = Path(recording_or_lifecycle)
        lifecycle = load_lifecycle_from_recording(path)
        recording = path
    return compare_template(template, lifecycle, recording=recording)


def compact_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": comparison.get("schema") or COMPARISON_SCHEMA,
        "status": comparison.get("status"),
        "statusReason": comparison.get("statusReason"),
        "templateName": comparison.get("templateName"),
        "score": comparison.get("score"),
        "templateRevision": comparison.get("templateRevision"),
        "routeTemplateDirectionMismatch": bool(comparison.get("routeTemplateDirectionMismatch") or comparison.get("directionMismatch")),
        "detectedRouteName": comparison.get("detectedRouteName"),
        "detectedStartArea": comparison.get("detectedStartArea"),
        "detectedEndArea": comparison.get("detectedEndArea"),
        "matchedVariantName": comparison.get("matchedVariantName"),
        "validUnregisteredVariant": bool(comparison.get("validUnregisteredVariant")),
        "matchedSegmentCount": comparison.get("matchedSegmentCount"),
        "requiredSegmentCount": comparison.get("requiredSegmentCount"),
        "missingSegmentCount": len(_list(comparison.get("missingSegments"))),
        "extraSegmentCount": len(_list(comparison.get("extraSegments"))),
        "allowedExtraSegmentCount": len(_list(comparison.get("allowedExtraSegments"))),
        "navigationSupportSubstitutionCount": len(_list(comparison.get("navigationSupportSubstitutions"))),
        "navigationSupportEvidenceCount": len(_list(comparison.get("navigationSupportEvidence"))),
        "reviewEvidenceCount": comparison.get("reviewEvidenceCount"),
        "reviewEvidenceSegmentCount": len(_list(comparison.get("reviewEvidenceSegments"))),
        "weakSegmentCount": len(_list(comparison.get("weakSegments"))),
        "failedPostconditionCount": len(_list(comparison.get("failedPostconditions"))),
        "warningCount": len(_list(comparison.get("warnings"))),
        "warnings": _list(comparison.get("warnings"))[:5],
    }


def write_comparison(comparison: dict[str, Any], recording: str | Path, *, pretty: bool = True) -> Path:
    recording_path = Path(recording)
    if recording_path.is_file():
        recording_path = recording_path.parent
    out_path = recording_path / "route_template_comparison.json"
    telemetry_sources.atomic_write_json(out_path, comparison, pretty=pretty)
    return out_path


def extract_variant(
    template: dict[str, Any],
    lifecycle: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    *,
    variant_name: str | None = None,
    description: str | None = None,
    source_recording: str | Path | None = None,
) -> dict[str, Any]:
    comparison = comparison if isinstance(comparison, dict) and comparison else compare_template(template, lifecycle, recording=source_recording)
    substitutions = [item for item in _list(comparison.get("navigationSupportSubstitutions")) if isinstance(item, dict)]
    grouped: dict[Any, dict[str, Any]] = {}
    for substitution in substitutions:
        base_index = substitution.get("baseSegmentIndex")
        override = grouped.setdefault(
            base_index,
            {
                "baseSegmentIndex": base_index,
                "baseSegmentLabel": substitution.get("baseSegmentLabel"),
                "allowedAlternatives": [],
            },
        )
        alternative = _dict(substitution.get("alternative"))
        if alternative:
            override["allowedAlternatives"].append(alternative)
    warnings: list[str] = []
    if not grouped:
        warnings.append("no navigation-support substitutions were available for variant extraction")
    variant = {
        "schema": VARIANT_SCHEMA,
        "variantName": variant_name or "route_variant",
        "sourceRecording": str(source_recording) if source_recording else comparison.get("recording") or lifecycle.get("recordingPath"),
        "routeName": template.get("routeName") or lifecycle.get("routeName"),
        "description": description
        or "Navigation-support substitutions may satisfy route progress when movement and endpoint evidence are strong.",
        "segmentOverrides": list(grouped.values()),
        "allowedExtraSegments": _list(comparison.get("allowedExtraSegments")),
        "allowedReviewEvidence": [],
        "sharedEndpointRequirements": True,
        "warnings": warnings,
    }
    return variant


def add_variant_to_template(template_path: str | Path, variant: dict[str, Any], *, pretty: bool = True) -> tuple[dict[str, Any], Path]:
    resolution = resolve_route_template(template_path)
    path = Path(str(resolution.get("resolvedPath") or template_path))
    template = _json(path)
    variants = [item for item in _list(template.get("variants")) if _dict(item).get("variantName") != variant.get("variantName")]
    variants.append(variant)
    template["variants"] = variants
    telemetry_sources.atomic_write_json(path, template, pretty=pretty)
    return template, path


def write_variant(variant: dict[str, Any], recording: str | Path, *, pretty: bool = True) -> Path:
    recording_path = Path(recording)
    if recording_path.is_file():
        recording_path = recording_path.parent
    out_path = recording_path / "route_template_variant.json"
    telemetry_sources.atomic_write_json(out_path, variant, pretty=pretty)
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and compare traversal route templates.")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--recording", required=True)
    extract.add_argument("--out", default="route_templates")
    extract.add_argument("--compact", action="store_true")
    compare = sub.add_parser("compare")
    compare.add_argument("--recording", required=True)
    compare.add_argument("--template", required=True)
    compare.add_argument("--write", action="store_true")
    compare.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "extract":
        template, path = extract_template_from_recording(args.recording, args.out, pretty=not args.compact)
        payload = {"templatePath": str(path), "routeTemplate": template}
    else:
        comparison = compare_template_files(args.template, args.recording)
        if args.write:
            path = write_comparison(comparison, args.recording, pretty=not args.compact)
            payload = {"comparisonPath": str(path), "routeTemplateComparison": comparison}
        else:
            payload = comparison
    print(json.dumps(payload, indent=None if getattr(args, "compact", False) else 2, separators=(",", ":") if getattr(args, "compact", False) else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

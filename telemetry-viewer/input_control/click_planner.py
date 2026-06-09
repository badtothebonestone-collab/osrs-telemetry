from __future__ import annotations

import math
import re
from typing import Any


SCHEMA_VERSION = "human_click_plan.v1"
CONTEXT_SCHEMA = "click_planning_context.v1"
TAG_RE = re.compile(r"<[^>]+>")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(values: Any) -> list[str]:
    items: list[str] = []
    for value in _list(values):
        if isinstance(value, dict):
            text = _first_present(value.get("code"), value.get("reason"), value.get("message"))
            items.append(str(text if text is not None else value))
        elif value is not None:
            items.append(str(value))
    return items


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "yes", "1", "pass", "confirmed"}:
            return True
        if lower in {"false", "no", "0", "fail", "missing"}:
            return False
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _clean_label(value: Any) -> str:
    text = TAG_RE.sub("", str(value or "")).strip().lower()
    text = text.replace("_", " ")
    return " ".join(text.split())


def _point(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _num(_first_present(value.get("x"), value.get("canvasX"), value.get("screenX")))
    y = _num(_first_present(value.get("y"), value.get("canvasY"), value.get("screenY")))
    if x is None or y is None:
        return None
    return {"x": int(round(x)), "y": int(round(y))}


def _nested_point(value: dict[str, Any], paths: list[list[str]]) -> dict[str, int] | None:
    for path in paths:
        current: Any = value
        for key in path:
            current = _dict(current).get(key)
        point = _point(current)
        if point is not None:
            return point
    return None


def _rect(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _num(_first_present(value.get("x"), value.get("left"), value.get("minX")))
    y = _num(_first_present(value.get("y"), value.get("top"), value.get("minY")))
    width = _num(value.get("width"))
    height = _num(value.get("height"))
    if width is None and value.get("right") is not None and x is not None:
        width = (_num(value.get("right")) or x) - x
    if height is None and value.get("bottom") is not None and y is not None:
        height = (_num(value.get("bottom")) or y) - y
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"x": int(round(x)), "y": int(round(y)), "width": int(round(width)), "height": int(round(height))}


def _clamp_point(point: dict[str, int], bounds: dict[str, int] | None, *, margin: int = 2) -> dict[str, int]:
    if not bounds:
        return dict(point)
    min_x = int(bounds["x"]) + margin
    min_y = int(bounds["y"]) + margin
    max_x = int(bounds["x"]) + int(bounds["width"]) - margin
    max_y = int(bounds["y"]) + int(bounds["height"]) - margin
    if min_x > max_x:
        min_x = max_x = int(bounds["x"]) + int(bounds["width"]) // 2
    if min_y > max_y:
        min_y = max_y = int(bounds["y"]) + int(bounds["height"]) // 2
    return {"x": max(min_x, min(max_x, int(point["x"]))), "y": max(min_y, min(max_y, int(point["y"])))}


def _stable_sign(text: str, salt: str) -> int:
    total = sum(ord(ch) for ch in f"{text}|{salt}")
    return -1 if total % 2 else 1


def normalize_activity(activity: str | None, source: dict[str, Any] | None = None) -> str:
    value = str(activity or "").strip().lower()
    if value in {"route_traversal", "traversal", "route"}:
        return "route_traversal"
    if value in {"woodcutting", "banking", "menu_interaction", "camera_input_sample", "generic"}:
        return value
    source = source or {}
    loop = _dict(source.get("woodcuttingLoopLifecycle") or source.get("woodcuttingLoop"))
    if loop:
        return "woodcutting"
    if source.get("bankingLifecycle") or source.get("bankState") or source.get("depositResult"):
        return "banking"
    if source.get("routeMonitor") or source.get("route_monitor"):
        return "route_traversal"
    return "generic"


def _compact_profile(profile: dict[str, Any] | None, activity: str) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    task_profiles = _dict(profile.get("taskProfiles"))
    task_profile = _dict(profile.get("taskProfile")) or _dict(task_profiles.get(activity))
    landing = _dict(profile.get("landing"))
    camera = _dict(profile.get("camera"))
    if not landing and task_profile:
        click_distance = _dict(task_profile.get("clickLandingDistancePx"))
        landing = {
            "medianAimDistancePx": click_distance.get("median"),
            "p75AimDistancePx": click_distance.get("p75"),
            "p90AimDistancePx": click_distance.get("p90"),
            "aimDistanceBucketsPx": task_profile.get("aimDistanceBucketsPx"),
        }
    return {
        "profileLoaded": bool(profile),
        "activityBucket": activity,
        "status": profile.get("status"),
        "recordingCount": profile.get("recordingCount"),
        "medianAimDistancePx": landing.get("medianAimDistancePx"),
        "p75AimDistancePx": landing.get("p75AimDistancePx"),
        "p90AimDistancePx": landing.get("p90AimDistancePx"),
        "aimDistanceBucketsPx": landing.get("aimDistanceBucketsPx"),
        "cameraBeforeClickRate": task_profile.get("cameraBeforeClickFrequency"),
        "menuRowSelectionCount": _first_present(
            _dict(profile.get("clicks")).get("menuRowSelectionCount"),
            task_profile.get("menuRowSelectionCount"),
        ),
        "rightClickMenuOpenCount": _first_present(
            _dict(profile.get("clicks")).get("rightClickMenuOpenCount"),
            task_profile.get("rightClickMenuOpenCount"),
        ),
        "middleMouseDragCount": camera.get("middleMouseDragCount"),
        "warnings": _list(profile.get("warnings"))[:5],
        "missingCapabilities": _list(profile.get("missingCapabilities"))[:5],
    }


def _target_from_source(source: dict[str, Any]) -> dict[str, Any]:
    visibility = _dict(source.get("actionInputVisibility") or source.get("actionInputVisibilitySummary"))
    planned = _dict(visibility.get("plannedTarget"))
    if planned:
        return planned
    proposal = _dict(source.get("proposal"))
    explanation = _dict(proposal.get("targetExplanation"))
    if explanation:
        return explanation
    return _dict(source.get("target"))


def _readiness_from_source(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    visibility = _dict(source.get("actionInputVisibility") or source.get("actionInputVisibilitySummary"))
    readiness = _dict(source.get("readiness") or visibility.get("readiness"))
    action_readiness = _dict(readiness.get("actionReadiness") or source.get("actionReadiness"))
    hover = _dict(source.get("hover") or visibility.get("hoverConfirmationEvidence"))
    menu = _dict(source.get("menu") or visibility.get("menuOptionClickedEvidence"))
    visible = _bool(_first_present(target.get("targetVisible"), target.get("visible"), target.get("onScreen")))
    geometry_available = _bool(_first_present(target.get("geometryAvailable"), _dict(target.get("safeAimPoint")).get("actionable")))
    return {
        "hoverConfirmed": bool(_bool(hover.get("confirmed")) is True or hover.get("hoverMenu") or hover.get("latestMatch")),
        "menuConfirmed": bool(_bool(menu.get("confirmed")) is True or menu.get("option") or menu.get("selectedOption")),
        "targetVisible": visible is True,
        "geometryAvailable": geometry_available is True,
        "executionAllowed": _bool(action_readiness.get("executionAllowed")),
        "blockedReasons": _string_list(action_readiness.get("blockers")) + _string_list(readiness.get("blockers")),
        "raw": {"readiness": readiness, "hover": hover, "menu": menu},
    }


def _target_actions(target: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for key in ("action", "targetAction", "option", "primaryAction"):
        value = _clean_label(target.get(key))
        if value:
            actions.append(value)
    for key in ("actions", "effectiveActions", "targetActions"):
        for item in _list(target.get(key)):
            value = _clean_label(item)
            if value:
                actions.append(value)
    return list(dict.fromkeys(actions))


def _label_conflicts(left: str, right: str) -> bool:
    left = _clean_label(left)
    right = _clean_label(right)
    if not left or not right or left in {"unknown", "none"} or right in {"unknown", "none"}:
        return False
    return left not in right and right not in left


def _evidence_labels(readiness: dict[str, Any]) -> tuple[str, str]:
    raw = _dict(readiness.get("raw"))
    hover = _dict(raw.get("hover"))
    menu = _dict(raw.get("menu"))
    evidence_action = _clean_label(
        _first_present(
            hover.get("hoverOption"),
            hover.get("topOption"),
            hover.get("option"),
            menu.get("option"),
            menu.get("selectedOption"),
        )
    )
    evidence_target = _clean_label(
        _first_present(
            hover.get("hoverTarget"),
            hover.get("topTarget"),
            hover.get("target"),
            menu.get("target"),
            menu.get("selectedTarget"),
        )
    )
    return evidence_action, evidence_target


def _base_point_and_bounds(target: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, int] | None, dict[str, int] | None, str]:
    row_geometry = _dict(target.get("rowCanvasGeometry") or target.get("menuRowGeometry") or source.get("rowCanvasGeometry"))
    row_point = _point(row_geometry.get("point"))
    if row_point is not None:
        return row_point, _rect(row_geometry.get("menuBounds") or row_geometry.get("bounds")), "menu_row_geometry"
    base = _nested_point(
        target,
        [
            ["basePoint"],
            ["safeAimPoint", "point"],
            ["safeAimPoint"],
            ["suggestedClickPoint"],
            ["resolvedScreenClickPoint"],
            ["canvasAimPoint"],
            ["aimPoint"],
            ["geometry", "aimPoint"],
            ["aimPointContext"],
            ["geometry", "canvas"],
            ["canvasLocation"],
            ["center"],
            ["point"],
        ],
    )
    if base is None:
        base = _nested_point(source, [["basePoint"], ["plannedPoint"], ["plannedScreenPoint"], ["plannedCanvasPoint"]])
    bounds = _rect(
        _first_present(
            target.get("clickboxBounds"),
            target.get("bounds"),
            target.get("canvasBounds"),
            target.get("menuBounds"),
            _dict(target.get("geometry")).get("bounds"),
            _dict(target.get("geometry")).get("clickbox"),
        )
    )
    return base, bounds, "target_geometry" if base else "none"


def _target_quality(target: dict[str, Any]) -> str:
    quality = str(
        _first_present(
            target.get("targetQuality"),
            target.get("quality"),
            target.get("matchQuality"),
            target.get("qualityTier"),
            target.get("targetQualityTier"),
        )
        or "unknown"
    ).strip().lower()
    if quality in {"excellent", "high"}:
        return "strong"
    if quality in {"ok", "fair"}:
        return "medium"
    if quality in {"strong", "medium", "weak"}:
        return quality
    return "unknown"


def _plan_offset(profile: dict[str, Any], *, action: str, target_name: str, source: str) -> tuple[dict[str, int], str, str | None]:
    median = _num(profile.get("medianAimDistancePx"))
    p75 = _num(profile.get("p75AimDistancePx"))
    if median is None:
        return {"dx": 0, "dy": 0}, "none", None
    if source == "menu_row_geometry":
        magnitude = max(2.0, min(6.0, median * 0.08))
    else:
        magnitude = max(3.0, min(14.0, median * 0.18))
    vertical = max(1.0, min(8.0, (p75 or median) * 0.08))
    label = f"{action}|{target_name}"
    offset = {
        "dx": int(round(magnitude * _stable_sign(label, "x"))),
        "dy": int(round(vertical * _stable_sign(label, "y"))),
    }
    bucket = "profile_median" if median <= 30 else "profile_capped_large_distance"
    return offset, "human_click_profile", bucket


def _loop_block(source: dict[str, Any], action: str) -> tuple[str | None, str | None]:
    loop = _dict(source.get("woodcuttingLoopLifecycle") or source.get("woodcuttingLoop"))
    deposit = _dict(source.get("depositResult") or source.get("deposit_result"))
    banking = _dict(source.get("banking") or source.get("bankingLifecycle") or source.get("banking_lifecycle"))
    woodcutting = _dict(loop.get("woodcutting") or source.get("woodcutting") or source.get("woodcuttingLifecycle") or source.get("woodcutting_lifecycle"))
    next_phase_raw = loop.get("nextExpectedPhase")
    next_phase = _dict(next_phase_raw).get("phase") if isinstance(next_phase_raw, dict) else next_phase_raw
    next_phase = str(next_phase or "").strip().lower()
    loop_state = str(loop.get("loopState") or loop.get("state") or "").strip().lower()
    action_text = action.strip().lower()
    inventory_full = (
        next_phase == "route_to_bank"
        or loop_state == "inventory_full"
        or woodcutting.get("inventoryFull") is True
        or woodcutting.get("inventory_full") is True
    )
    if inventory_full and action_text in {"chop down", "chop", "cut", "woodcutting"}:
        return "inventory_full_route_to_bank_required", "route_to_bank"
    deposit_complete = (
        next_phase == "route_to_woodcutting_area"
        or deposit.get("depositComplete") is True
        or deposit.get("complete") is True
        or banking.get("depositComplete") is True
    )
    if deposit_complete and "deposit" in action_text:
        return "deposit_complete_route_to_trees_required", "route_to_woodcutting_area"
    return None, None


def build_click_plan(
    source: dict[str, Any] | None = None,
    *,
    target: dict[str, Any] | None = None,
    action: str | None = None,
    activity: str | None = None,
    human_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    target = target if isinstance(target, dict) else _target_from_source(source)
    action = str(action or source.get("action") or _dict(source.get("proposal")).get("proposedAction") or "unknown")
    activity_bucket = normalize_activity(activity, source)
    profile = _compact_profile(human_profile or _dict(source.get("humanClickProfile") or source.get("human_click_profile")), activity_bucket)
    readiness = _readiness_from_source(source, target)
    route_monitor = _dict(source.get("routeMonitor") or source.get("route_monitor"))
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    status = "PASS"

    next_block, replacement_action = _loop_block(source, action)
    if next_block:
        status = "WARN"
        blockers.append(next_block)
        warnings.append(next_block)
        action = replacement_action or action

    off_route = _bool(route_monitor.get("offRoute"))
    if off_route is True or str(route_monitor.get("routeState") or "").lower() == "off_route":
        status = "FAIL"
        blockers.append("route_monitor_off_route")
        warnings.append("route monitor reports off-route; normal click planning is blocked")

    target_quality = _target_quality(target)
    target_name = str(_first_present(target.get("name"), target.get("targetName"), target.get("effectiveName"), "unknown"))
    target_kind = str(_first_present(target.get("kind"), target.get("targetKind"), target.get("targetType"), "unknown"))
    base_point, bounds, base_source = _base_point_and_bounds(target, source)
    geometry_available = readiness["geometryAvailable"] or base_point is not None
    if not target:
        status = "WARN" if status != "FAIL" else status
        blockers.append("target_missing")
        warnings.append("target/readiness evidence is missing")
    if target_quality == "weak" and not (readiness["hoverConfirmed"] or readiness["menuConfirmed"]):
        status = "WARN" if status != "FAIL" else status
        blockers.append("weak_target_without_hover_or_menu")
        warnings.append("weak target quality without hover/menu confirmation")
    if base_point is None:
        status = "WARN" if status != "FAIL" else status
        blockers.append("geometry_missing")
        warnings.append("target geometry or aim point is missing; no fake coordinates generated")

    evidence_action, evidence_target = _evidence_labels(readiness)
    if evidence_target and _label_conflicts(target_name, evidence_target):
        status = "WARN" if status != "FAIL" else status
        blockers.append("target_evidence_conflict")
        warnings.append(f"target evidence conflict: geometry target={target_name} hover/menu target={evidence_target}")
    target_actions = _target_actions(target)
    if evidence_action and target_actions and not any(not _label_conflicts(evidence_action, target_action) for target_action in target_actions):
        status = "WARN" if status != "FAIL" else status
        blockers.append("target_action_conflict")
        warnings.append(f"target action conflict: geometry actions={target_actions} hover/menu action={evidence_action}")

    camera_rate = _num(profile.get("cameraBeforeClickRate"))
    if camera_rate is not None and camera_rate >= 0.5 and (not readiness["targetVisible"] or not geometry_available):
        status = "WARN" if status != "FAIL" else status
        blockers.append("camera_adjust_first")
        warnings.append("profile often uses camera before clicks and target visibility/geometry is weak")

    world = _dict(_first_present(target.get("world"), target.get("worldLocation"))) or {
        key: target.get(key) for key in ("worldX", "worldY", "plane") if target.get(key) is not None
    }
    offset, offset_source, bucket = _plan_offset(profile, action=action, target_name=target_name, source=base_source)
    planned_point = None
    if base_point is not None:
        planned_point = _clamp_point({"x": base_point["x"] + offset["dx"], "y": base_point["y"] + offset["dy"]}, bounds)
        offset = {"dx": planned_point["x"] - base_point["x"], "dy": planned_point["y"] - base_point["y"]}
        if offset["dx"] or offset["dy"]:
            reasons.append("profile_informed_non_center_point")
        else:
            reasons.append("profile_offset_clamped_to_available_geometry")

    confidence = 0.2
    if target_quality == "strong":
        confidence += 0.25
        reasons.append("strong_target_quality")
    elif target_quality == "medium":
        confidence += 0.18
        reasons.append("medium_target_quality")
    elif target_quality == "weak":
        confidence += 0.05
    if geometry_available:
        confidence += 0.15
        reasons.append("geometry_available")
    if readiness["targetVisible"]:
        confidence += 0.1
        reasons.append("target_visible")
    if readiness["hoverConfirmed"]:
        confidence += 0.15
        reasons.append("hover_evidence_available")
    if readiness["menuConfirmed"]:
        confidence += 0.12
        reasons.append("menu_evidence_available")
    if status == "FAIL":
        confidence = min(confidence, 0.25)
    elif status == "WARN":
        confidence = min(confidence, 0.65)
    else:
        confidence = min(confidence, 0.95)

    return {
        "schema": SCHEMA_VERSION,
        "status": status,
        "task": "route" if activity_bucket == "route_traversal" else activity_bucket,
        "action": action,
        "target": {
            "name": target_name,
            "kind": target_kind,
            "ref": _first_present(target.get("ref"), target.get("objectKey"), target.get("hash")),
            "world": world,
            "targetQuality": target_quality,
        },
        "readiness": {
            "hoverConfirmed": readiness["hoverConfirmed"],
            "menuConfirmed": readiness["menuConfirmed"],
            "targetVisible": readiness["targetVisible"],
            "geometryAvailable": bool(geometry_available),
            "blockedReasons": list(dict.fromkeys(blockers + readiness["blockedReasons"])),
        },
        "aim": {
            "basePoint": base_point or {"x": None, "y": None},
            "plannedPoint": planned_point or {"x": None, "y": None},
            "offset": offset,
            "offsetSource": offset_source,
            "distanceBucket": bucket,
            "basePointSource": base_source,
            "bounds": bounds,
        },
        "humanProfile": profile,
        "confidence": round(confidence, 3),
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": list(dict.fromkeys(warnings + profile.get("warnings", []))),
        "missingCapabilities": list(dict.fromkeys(profile.get("missingCapabilities", []) + (["target_geometry"] if base_point is None else []))),
        "recommendedPreAction": "camera_adjust_first" if "camera_adjust_first" in blockers else None,
        "execution": {
            "advisoryOnly": True,
            "clickNowAllowedByPlanner": status == "PASS" and not blockers,
            "rule": "Planner output must still pass live readiness, hover/menu proof, and executor safety before any click is sent.",
        },
    }


def compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    aim = _dict(plan.get("aim"))
    target = _dict(plan.get("target"))
    return {
        "schema": plan.get("schema") or SCHEMA_VERSION,
        "status": plan.get("status"),
        "task": plan.get("task"),
        "action": plan.get("action"),
        "target": target.get("name"),
        "targetQuality": target.get("targetQuality"),
        "plannedPoint": aim.get("plannedPoint"),
        "centerPoint": aim.get("basePoint"),
        "offset": aim.get("offset"),
        "confidence": plan.get("confidence"),
        "reasons": _list(plan.get("reasons"))[:6],
        "warnings": _list(plan.get("warnings"))[:6],
        "blockedReasons": _list(_dict(plan.get("readiness")).get("blockedReasons"))[:6],
    }


def compare_center_click_vs_profile_click(plan: dict[str, Any]) -> dict[str, Any]:
    aim = _dict(plan.get("aim"))
    center = _point(aim.get("basePoint"))
    profile_point = _point(aim.get("plannedPoint"))
    offset = _dict(aim.get("offset"))
    dx = _num(offset.get("dx")) or 0.0
    dy = _num(offset.get("dy")) or 0.0
    return {
        "schema": "center_vs_profile_click.v1",
        "status": "PASS" if center and profile_point else "WARN",
        "centerPoint": center or {"x": None, "y": None},
        "profilePoint": profile_point or {"x": None, "y": None},
        "offset": {"dx": int(round(dx)), "dy": int(round(dy))},
        "distancePx": round(math.hypot(dx, dy), 3),
        "differentFromCenter": bool(center and profile_point and (dx or dy)),
        "offsetSource": aim.get("offsetSource"),
        "warnings": [] if center and profile_point else ["center/profile point comparison requires both points"],
    }

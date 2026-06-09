from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import menu_interaction_model


QUALITY_SCHEMA = "target_match_quality.v1"
SUMMARY_SCHEMA = "target_match_summary.v1"
TARGET_QUALITIES = ("strong", "medium", "weak", "unmatched")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _event_seq(event: dict[str, Any]) -> int:
    value = _number(event.get("event_seq") or event.get("eventSeq"))
    return int(value or 0)


def _classification_seq(classification: dict[str, Any]) -> int:
    value = _number(classification.get("eventSeq") or classification.get("event_seq"))
    return int(value or 0)


def _event_time(event: dict[str, Any]) -> float | None:
    return _float(event.get("elapsed_seconds") or _dict(event.get("time")).get("elapsedSeconds"))


def _snapshot_time(snapshot: dict[str, Any] | None) -> float | None:
    return _float(_dict(snapshot).get("elapsed_seconds"))


def _point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x", value.get("canvasX", value.get("canvas_x", value.get("client_x", value.get("screen_x")))))
    y = value.get("y", value.get("canvasY", value.get("canvas_y", value.get("client_y", value.get("screen_y")))))
    try:
        return {"x": float(x), "y": float(y)}
    except (TypeError, ValueError):
        return None


def click_point(input_event: dict[str, Any], click_analysis: dict[str, Any] | None = None) -> dict[str, float] | None:
    analysis_point = _point(_dict(click_analysis).get("clickPoint"))
    if analysis_point:
        return analysis_point
    for x_key, y_key in (("canvas_x", "canvas_y"), ("client_x", "client_y"), ("screen_x", "screen_y"), ("x", "y")):
        if input_event.get(x_key) is None or input_event.get(y_key) is None:
            continue
        try:
            return {"x": float(input_event[x_key]), "y": float(input_event[y_key])}
        except (TypeError, ValueError):
            continue
    return None


def aim_point(target: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(target, dict):
        return None
    geometry = _dict(target.get("geometry"))
    for value in (
        target.get("aimPoint"),
        geometry.get("aimPoint"),
        target.get("canvas"),
        target.get("canvasLocation"),
        geometry.get("canvas"),
    ):
        point = _point(value)
        if point:
            return point
    return None


def aim_point_source(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict):
        return None
    geometry = _dict(target.get("geometry"))
    for value in (
        target.get("aimPoint"),
        geometry.get("aimPoint"),
        target.get("canvas"),
        target.get("canvasLocation"),
        geometry.get("canvas"),
    ):
        if isinstance(value, dict) and _point(value):
            return str(value.get("source") or value.get("space") or "aimPoint")
    return None


def clickbox_bounds(target: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(target, dict):
        return None
    geometry = _dict(target.get("geometry"))
    bounds = target.get("clickboxBounds") or target.get("bounds") or geometry.get("clickbox")
    if not isinstance(bounds, dict):
        return None
    left = bounds.get("left", bounds.get("x"))
    top = bounds.get("top", bounds.get("y"))
    width = bounds.get("width", bounds.get("w"))
    height = bounds.get("height", bounds.get("h"))
    right = bounds.get("right", (left + width) if left is not None and width is not None else None)
    bottom = bounds.get("bottom", (top + height) if top is not None and height is not None else None)
    try:
        return {"left": float(left), "top": float(top), "right": float(right), "bottom": float(bottom)}
    except (TypeError, ValueError):
        return None


def inside_clickbox(click: dict[str, float] | None, target: dict[str, Any] | None) -> bool | None:
    bounds = clickbox_bounds(target)
    if click is None or bounds is None:
        return None
    return bounds["left"] <= click["x"] <= bounds["right"] and bounds["top"] <= click["y"] <= bounds["bottom"]


def distance_from_aim(click: dict[str, float] | None, target: dict[str, Any] | None) -> float | None:
    aim = aim_point(target)
    if click is None or aim is None:
        return None
    return round(math.hypot(click["x"] - aim["x"], click["y"] - aim["y"]), 3)


def target_actions(target: dict[str, Any] | None) -> list[str]:
    if not isinstance(target, dict):
        return []
    actions = target.get("effectiveActions") or target.get("actions") or []
    if isinstance(actions, list):
        return [str(action) for action in actions if action is not None]
    if actions:
        return [str(actions)]
    return []


def target_name(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict):
        return None
    value = target.get("effectiveName") or target.get("name")
    return str(value) if value not in (None, "") else None


def target_id(target: dict[str, Any] | None) -> Any:
    if not isinstance(target, dict):
        return None
    return target.get("effectiveId", target.get("rawId", target.get("id")))


def _clean_menu_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    while "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if end < start:
            break
        text = text[:start] + text[end + 1 :]
    text = " ".join(text.replace("\u00a0", " ").split())
    return text or None


def _normalized_text(value: Any) -> str:
    return str(_clean_menu_text(value) or "").strip().lower()


def _menu_hover_target(classification: dict[str, Any]) -> dict[str, Any] | None:
    menu_context = _dict(classification.get("menuContext"))
    option = _clean_menu_text(menu_context.get("hoverOption"))
    target = _clean_menu_text(menu_context.get("hoverTarget"))
    if not option:
        return None
    option_l = option.lower()
    if option_l in {"walk here", "cancel", "examine"}:
        return None
    if not target and option_l not in {"chop down", "open", "bank", "deposit-all", "climb-up", "climb-down"}:
        return None
    label = target or option
    return {
        "ref": f"menu_hover:{option_l}:{str(label).lower()}",
        "kind": "menu_hover",
        "effectiveName": label,
        "effectiveActions": [option],
        "menuActionAvailable": True,
        "source": "menu_hover_context",
    }


def _target_conflict_reasons(left: dict[str, Any], right: dict[str, Any] | None) -> list[str]:
    if not isinstance(right, dict):
        return []
    left_name = _normalized_text(target_name(left))
    right_name = _normalized_text(target_name(right))
    left_actions = [_normalized_text(action) for action in target_actions(left)]
    right_actions = [_normalized_text(action) for action in target_actions(right)]
    name_conflict = bool(left_name and right_name and left_name != right_name)
    action_conflict = bool(left_actions and right_actions and not any(left == right or left in right or right in left for left in left_actions for right in right_actions))
    reasons: list[str] = []
    if name_conflict:
        reasons.append("target_name_conflict")
    if action_conflict:
        reasons.append("target_action_conflict")
    right_kind = _normalized_text(right.get("kind"))
    if (left_name in {"tree", "oak", "oak tree"} or any("chop" in action for action in left_actions)) and (
        right_name in {"gate", "door"}
        or right_kind in {"route", "wall_object", "wall object"}
        or right.get("routeObjectCandidate")
        or right.get("routeObjectKind")
    ):
        reasons.append("route_or_gate_geometry_conflicts_with_woodcutting_action")
    return reasons


def _target_conflicts(left: dict[str, Any], right: dict[str, Any] | None) -> bool:
    return bool(_target_conflict_reasons(left, right))


def _woodcutting_name(value: Any) -> bool:
    text = _normalized_text(value)
    return text in {"tree", "oak", "oak tree", "dead tree"} or text.endswith(" tree")


def _woodcutting_action(actions: list[str]) -> bool:
    return any("chop" in _normalized_text(action) for action in actions)


def normalize_target(target: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {
            "ref": None,
            "kind": "unknown",
            "name": None,
            "action": None,
            "rawId": None,
            "effectiveId": None,
            "world": None,
            "distanceToPlayer": None,
        }
    actions = target_actions(target)
    return {
        "ref": target.get("ref"),
        "kind": target.get("kind") or "unknown",
        "name": target_name(target),
        "action": actions[0] if actions else None,
        "rawId": target.get("rawId"),
        "effectiveId": target.get("effectiveId") or target.get("id"),
        "world": target.get("worldPoint"),
        "distanceToPlayer": target.get("distance"),
    }


def _target_from_classification(classification: dict[str, Any]) -> dict[str, Any] | None:
    context = _dict(classification.get("targetContext"))
    matched = context.get("matchedTarget")
    if isinstance(matched, dict):
        target = dict(matched)
        if context.get("targetName") and not target.get("effectiveName") and not target.get("name"):
            target["effectiveName"] = context.get("targetName")
        if context.get("targetAction") and not target.get("effectiveActions") and not target.get("actions"):
            target["effectiveActions"] = [context.get("targetAction")]
        return target
    linked = _dict(classification.get("linkedGameTarget")) or _dict(_dict(classification.get("menuSelection")).get("linkedGameTarget"))
    if linked:
        target = dict(linked)
        if target.get("action") and not target.get("effectiveActions") and not target.get("actions"):
            target["effectiveActions"] = [target.get("action")]
        if target.get("name") and not target.get("effectiveName"):
            target["effectiveName"] = target.get("name")
        if target.get("effectiveId") and not target.get("id"):
            target["id"] = target.get("effectiveId")
        return target
    return None


def _target_from_click_analysis(click_analysis: dict[str, Any]) -> dict[str, Any] | None:
    target = click_analysis.get("nearestTarget")
    if isinstance(target, dict):
        return dict(target)
    relative_target = _dict(_dict(click_analysis.get("targetRelative")).get("target"))
    if relative_target:
        return dict(relative_target)
    return None


def _association_method(target: dict[str, Any] | None, *, geometry_source: bool = False) -> str:
    if not isinstance(target, dict):
        return "unresolved"
    if target.get("source") == "menu_hover_context":
        return "hover_menu_identity"
    if target.get("source") == "menu_selection_context":
        return "menu_selection_identity"
    if geometry_source:
        return "geometry_nearest"
    return "click_history_identity"


def _intended_target_class(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict):
        return None
    name = _normalized_text(target_name(target))
    actions = [_normalized_text(action) for action in target_actions(target)]
    if _woodcutting_name(name) or _woodcutting_action(actions):
        return "woodcutting_target"
    if any("bank" in action for action in actions) or "bank" in name:
        return "banking_target"
    if any("climb" in action for action in actions) or any(word in name for word in ("stair", "ladder")):
        return "route_transition"
    return None


def resolve_target_identity(classification: dict[str, Any], click_analysis: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    hover_target = _menu_hover_target(classification)
    classification_target = _target_from_classification(classification)
    geometry_target = _target_from_click_analysis(_dict(click_analysis))
    selected = merged_target(classification, click_analysis)
    selected_source_is_geometry = selected is geometry_target and selected is not None
    rejected: list[dict[str, Any]] = []
    conflict_reasons: list[str] = []
    for source, candidate in (("geometry_nearest", geometry_target), ("classification_target", classification_target)):
        if not isinstance(candidate, dict) or not isinstance(selected, dict) or _same_target(candidate, selected):
            continue
        reasons = _target_conflict_reasons(selected, candidate)
        if not reasons:
            continue
        normalized = normalize_target(candidate)
        normalized["source"] = source
        normalized["reasons"] = reasons
        if normalized not in rejected:
            rejected.append(normalized)
        conflict_reasons.extend(reasons)
    intended_actions = target_actions(selected)
    association = {
        "schema": "target_association.v1",
        "intendedAction": intended_actions[0] if intended_actions else _dict(classification.get("targetContext")).get("targetAction"),
        "intendedTargetName": target_name(selected) or _dict(classification.get("targetContext")).get("targetName"),
        "intendedTargetClass": _intended_target_class(selected),
        "selectedCandidate": normalize_target(selected),
        "rejectedCandidates": rejected,
        "conflictReasons": sorted(set(conflict_reasons)),
        "identityConfidence": 0.9 if hover_target and selected and _same_target(hover_target, selected) else (0.7 if selected and not selected_source_is_geometry else 0.35),
        "geometryConfidence": 0.0,
        "associationMethod": _association_method(selected, geometry_source=selected_source_is_geometry),
    }
    return selected, association


def merged_target(classification: dict[str, Any], click_analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    hover_target = _menu_hover_target(classification)
    base = _target_from_click_analysis(_dict(click_analysis)) or _target_from_classification(classification)
    other = _target_from_classification(classification)
    if base and other:
        merged = dict(other)
        merged.update({key: value for key, value in base.items() if value not in (None, "", [], {})})
        base = merged
    else:
        base = base or other
    if hover_target and (not base or _target_conflicts(hover_target, base)):
        return hover_target
    return base


def _same_target(candidate: dict[str, Any], target: dict[str, Any]) -> bool:
    candidate_ref = candidate.get("ref")
    target_ref = target.get("ref")
    if candidate_ref and target_ref and candidate_ref == target_ref:
        return True
    candidate_id = target_id(candidate)
    current_id = target_id(target)
    candidate_name = str(target_name(candidate) or "").lower()
    current_name = str(target_name(target) or "").lower()
    if candidate_id is not None and current_id is not None and candidate_id == current_id and candidate_name == current_name:
        return True
    return bool(candidate_name and current_name and candidate_name == current_name and any(a in target_actions(target) for a in target_actions(candidate)))


def _candidate_scene_coords(candidate: dict[str, Any]) -> tuple[int | None, int | None]:
    local = _dict(candidate.get("localPoint"))
    scene_x = _number(local.get("sceneX") or candidate.get("sceneX"))
    scene_y = _number(local.get("sceneY") or candidate.get("sceneY"))
    return (int(scene_x) if scene_x is not None else None, int(scene_y) if scene_y is not None else None)


def _candidate_matches_hover_ref(candidate: dict[str, Any], snapshot: dict[str, Any], target: dict[str, Any]) -> bool:
    hover = _dict(snapshot.get("hover"))
    entries = _list(hover.get("entries"))
    if hover.get("topOption") or hover.get("topTarget"):
        entries = [
            {
                "option": hover.get("topOption"),
                "target": hover.get("topTarget"),
                "identifier": hover.get("topIdentifier"),
                "param0": hover.get("topParam0"),
                "param1": hover.get("topParam1"),
            },
            *entries,
        ]
    candidate_id = target_id(candidate)
    scene_x, scene_y = _candidate_scene_coords(candidate)
    target_name_l = _normalized_text(target_name(target))
    target_actions_l = [_normalized_text(action) for action in target_actions(target)]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        option = _normalized_text(entry.get("option"))
        target_text = _normalized_text(entry.get("target"))
        if target_name_l and target_name_l not in target_text and target_text not in target_name_l:
            continue
        if target_actions_l and option and not any(option == action or option in action or action in option for action in target_actions_l):
            continue
        identifier = _number(entry.get("identifier"))
        param0 = _number(entry.get("param0"))
        param1 = _number(entry.get("param1"))
        id_match = identifier is None or candidate_id is None or int(identifier) == int(candidate_id)
        scene_match = (
            param0 is None
            or param1 is None
            or scene_x is None
            or scene_y is None
            or (int(param0) == scene_x and int(param1) == scene_y)
        )
        if id_match and scene_match:
            return True
    return False


def _target_identity_match_reasons(candidate: dict[str, Any], target: dict[str, Any]) -> list[str]:
    if _same_target(candidate, target):
        return ["same_target_ref_or_action"]
    candidate_name = _normalized_text(target_name(candidate))
    target_name_l = _normalized_text(target_name(target))
    candidate_actions = [_normalized_text(action) for action in target_actions(candidate)]
    target_actions_l = [_normalized_text(action) for action in target_actions(target)]
    if not candidate_name or not target_name_l:
        return []
    name_match = candidate_name == target_name_l or candidate_name in target_name_l or target_name_l in candidate_name
    action_match = bool(
        candidate_actions
        and target_actions_l
        and any(left == right or left in right or right in left for left in candidate_actions for right in target_actions_l)
    )
    action_missing = not candidate_actions
    woodcutting_compatible = (
        (_woodcutting_name(candidate_name) or _woodcutting_name(target_name_l))
        and (_woodcutting_action(target_actions_l) or _intended_target_class(target) == "woodcutting_target")
    )
    if name_match and (action_match or (action_missing and woodcutting_compatible)):
        reasons = ["target_name_match"]
        if action_match:
            reasons.append("target_action_match")
        elif action_missing:
            reasons.append("candidate_action_missing_but_woodcutting_identity_matches")
        return reasons
    return []


def _snapshot_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (_list(snapshot.get("nearby_objects")) + _list(snapshot.get("route_objects")) + _list(snapshot.get("nearby_npcs")))
        if isinstance(item, dict)
    ]


def matching_target_observations(target: dict[str, Any] | None, snapshots: list[dict[str, Any]], click_time: float | None, *, window_seconds: float = 8.0, pre_window_seconds: float = 8.0) -> list[dict[str, Any]]:
    if not target or click_time is None:
        return []
    observations: list[dict[str, Any]] = []
    for snapshot in snapshots:
        elapsed = _snapshot_time(snapshot)
        if elapsed is None or elapsed < click_time - pre_window_seconds or elapsed > click_time + window_seconds:
            continue
        for candidate in _snapshot_candidates(snapshot):
            match_reasons = _target_identity_match_reasons(candidate, target)
            if match_reasons:
                item = dict(candidate)
                item["_snapshotElapsedSeconds"] = elapsed
                item["_snapshotTick"] = snapshot.get("latest_tick")
                item["_snapshotExportSequence"] = snapshot.get("latest_export_sequence")
                item["_identityMatchReasons"] = match_reasons
                if _candidate_matches_hover_ref(candidate, snapshot, target):
                    item["_hoverLinkedCandidate"] = True
                    item["_identityMatchReasons"] = sorted(set([*match_reasons, "hover_ref_match"]))
                observations.append(item)
    return observations


def _candidate_time_delta(input_event: dict[str, Any], candidate: dict[str, Any]) -> float:
    input_time = _event_time(input_event)
    candidate_time = _float(candidate.get("_snapshotElapsedSeconds"))
    if input_time is None or candidate_time is None:
        return 999999.0
    return abs(input_time - candidate_time)


def _candidate_tick_delta(input_event: dict[str, Any], candidate: dict[str, Any]) -> int:
    input_tick = _number(input_event.get("nearest_tick") or input_event.get("latest_tick"))
    candidate_tick = _number(candidate.get("_snapshotTick"))
    if input_tick is None or candidate_tick is None:
        return 999999
    return abs(int(input_tick) - int(candidate_tick))


def choose_geometry_target(
    input_event: dict[str, Any],
    click_analysis: dict[str, Any] | None,
    target: dict[str, Any] | None,
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    click = click_point(input_event, click_analysis)
    candidates = [item for item in [target, *observations] if isinstance(item, dict)]
    best: dict[str, Any] | None = None
    ranked_candidates: list[dict[str, Any]] = []
    best_rank: tuple[int, int, int, int, float, float] | None = None
    for candidate in candidates:
        inside = inside_clickbox(click, candidate)
        distance = distance_from_aim(click, candidate)
        time_delta = _candidate_time_delta(input_event, candidate)
        tick_delta = _candidate_tick_delta(input_event, candidate)
        freshness_rank = 0 if tick_delta <= 1 or time_delta <= 2.0 else (1 if time_delta <= 8.0 else 2)
        hover_geometry_plausible = inside is True or distance is None or distance <= 160
        hover_rank = 0 if candidate.get("_hoverLinkedCandidate") and hover_geometry_plausible else 1
        if inside is True:
            geometry_rank = 0
        elif distance is not None:
            geometry_rank = 1 if distance <= 80 else 2
        elif clickbox_bounds(candidate) is not None:
            geometry_rank = 3
        else:
            geometry_rank = 4
        rank = (
            hover_rank,
            freshness_rank,
            geometry_rank,
            tick_delta,
            time_delta,
            distance if distance is not None else 999999.0,
        )
        ranked_candidates.append(
            {
                "rank": rank,
                "candidate": candidate,
                "distanceFromAimPointPx": distance,
                "insideClickbox": inside,
                "timeDeltaSeconds": time_delta,
                "tickDelta": tick_delta,
            }
        )
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = candidate
    best = best or target
    geometry = {
        "clickCanvas": click,
        "aimPoint": aim_point(best),
        "aimPointSource": aim_point_source(best),
        "distanceFromAimPointPx": distance_from_aim(click, best),
        "insideClickbox": inside_clickbox(click, best),
        "clickboxAvailable": clickbox_bounds(best) is not None,
        "targetOnScreen": best.get("onScreen") if isinstance(best, dict) else None,
        "geometrySourceElapsedSeconds": best.get("_snapshotElapsedSeconds") if isinstance(best, dict) else None,
        "geometrySourceTick": best.get("_snapshotTick") if isinstance(best, dict) else None,
        "geometryTargetRef": best.get("ref") if isinstance(best, dict) else None,
        "geometryTargetName": target_name(best),
        "geometryMatchReasons": best.get("_identityMatchReasons") if isinstance(best, dict) else None,
        "geometryHoverLinked": bool(_dict(best).get("_hoverLinkedCandidate")),
        "geometryCandidateCount": len(ranked_candidates),
        "geometryCandidateAlternatives": [
            {
                "target": normalize_target(item["candidate"]),
                "distanceFromAimPointPx": item["distanceFromAimPointPx"],
                "insideClickbox": item["insideClickbox"],
                "timeDeltaSeconds": round(float(item["timeDeltaSeconds"]), 3)
                if isinstance(item["timeDeltaSeconds"], (int, float)) and item["timeDeltaSeconds"] < 999999
                else None,
                "tickDelta": item["tickDelta"] if item["tickDelta"] < 999999 else None,
            }
            for item in sorted(ranked_candidates, key=lambda item: item["rank"])[:5]
        ],
    }
    return best, geometry


def _attach_intended_identity(target: dict[str, Any] | None, association: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(target, dict):
        return target
    enriched = dict(target)
    intended_action = association.get("intendedAction")
    intended_name = association.get("intendedTargetName")
    if intended_action and not target_actions(enriched):
        enriched["effectiveActions"] = [intended_action]
    if intended_name and not target_name(enriched):
        enriched["effectiveName"] = intended_name
    return enriched


def _finalize_association(association: dict[str, Any], target: dict[str, Any] | None, geometry: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(association)
    finalized["selectedCandidate"] = normalize_target(target)
    if finalized.get("intendedAction") in (None, ""):
        actions = target_actions(target)
        finalized["intendedAction"] = actions[0] if actions else None
    if finalized.get("intendedTargetName") in (None, ""):
        finalized["intendedTargetName"] = target_name(target)
    if finalized.get("intendedTargetClass") in (None, ""):
        finalized["intendedTargetClass"] = _intended_target_class(target)
    if geometry.get("clickboxAvailable") is True or geometry.get("distanceFromAimPointPx") is not None:
        finalized["geometryConfidence"] = 0.75
    elif geometry.get("targetOnScreen") is True:
        finalized["geometryConfidence"] = 0.35
    else:
        finalized["geometryConfidence"] = 0.0
    if not target:
        finalized["associationMethod"] = "unresolved"
    return finalized


def _text_blob(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        try:
            parts.append(json.dumps(value, sort_keys=True, default=str))
        except TypeError:
            parts.append(str(value))
    return " ".join(parts).lower()


def _action_matches(action: str | None, actions: list[str]) -> bool:
    if not action:
        return False
    action_l = action.lower()
    return any(action_l == item.lower() or action_l in item.lower() or item.lower() in action_l for item in actions)


def _world_point(snapshot: dict[str, Any] | None) -> tuple[Any, Any, Any] | None:
    point = _dict(_dict(snapshot).get("player_world_point") or _dict(snapshot).get("playerWorldPoint"))
    if not point:
        return None
    return (point.get("worldX"), point.get("worldY"), point.get("plane"))


def _inventory(snapshot: dict[str, Any] | None) -> Any:
    return _dict(snapshot).get("inventory")


def post_click_result(before: dict[str, Any] | None, snapshots: list[dict[str, Any]], click_time: float | None, target: dict[str, Any] | None) -> dict[str, Any]:
    before_point = _world_point(before)
    before_inventory = _inventory(before)
    future = [
        snapshot
        for snapshot in snapshots
        if click_time is not None and _snapshot_time(snapshot) is not None and click_time < float(_snapshot_time(snapshot) or 0.0) <= click_time + 8.0
    ]
    future_points = [_world_point(snapshot) for snapshot in future]
    future_points = [point for point in future_points if point is not None]
    plane_changed = bool(before_point and any(point[2] != before_point[2] for point in future_points))
    position_changed = bool(before_point and any(point != before_point for point in future_points))
    animation_started = False
    widget_opened = False
    inventory_changed = False
    menu_closed = False
    before_menu = _dict(before).get("menu")
    for snapshot in future:
        raw = _dict(snapshot.get("raw_event"))
        high = _dict(raw.get("high_value_fields"))
        player = _dict(high.get("player"))
        if player.get("animation") not in (None, -1, "-1", 0, "0"):
            animation_started = True
        if high.get("widgets"):
            widget_opened = True
        if before_inventory is not None and snapshot.get("inventory") != before_inventory:
            inventory_changed = True
        if before_menu and snapshot.get("menu") in (False, None, {}, []):
            menu_closed = True
    name = str(target_name(target) or "").lower()
    action = str((target_actions(target) or [""])[0]).lower()
    matched = False
    if "climb" in action or any(word in name for word in ("ladder", "stair", "staircase")):
        matched = plane_changed
    elif "open" in action or "door" in name:
        matched = position_changed or widget_opened or menu_closed
    elif "bank" in action or "bank" in name:
        matched = widget_opened
    elif "chop" in action or "tree" in name:
        matched = animation_started or inventory_changed
    elif action in {"walk", "walk here"}:
        matched = position_changed
    else:
        matched = position_changed or animation_started or inventory_changed or widget_opened
    return {
        "planeChanged": plane_changed,
        "positionChanged": position_changed,
        "animationStarted": animation_started,
        "inventoryChanged": inventory_changed,
        "widgetOpened": widget_opened,
        "menuClosed": menu_closed,
        "matchedExpectedOutcome": matched,
    }


def menu_selection_quality_payload(classification: dict[str, Any]) -> dict[str, Any]:
    selection = _dict(classification.get("menuSelection"))
    inside = selection.get("insideRowBounds")
    row_distance = _float(selection.get("rowCenterDistancePx"))
    option = selection.get("selectedOption")
    target = selection.get("selectedTarget")
    row_index = selection.get("selectedRowIndex")
    row_bounds = selection.get("rowBounds")
    transform = selection.get("coordinateTransformUsed") or classification.get("coordinateTransformUsed")
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []
    if inside is True:
        score += 0.46
        reasons.append("menu_row_click_inside_bounds")
    elif row_bounds:
        score += 0.18
        warnings.append("menu_row_bounds_present_but_click_outside")
    else:
        warnings.append("menu_row_bounds_missing")
    if row_distance is not None:
        if row_distance <= 8:
            score += 0.24
            reasons.append("menu_row_center_distance_le_8px")
        elif row_distance <= 20:
            score += 0.16
            reasons.append("menu_row_center_distance_le_20px")
        else:
            warnings.append(f"menu_row_center_distance_px={row_distance}")
    if option:
        score += 0.13
        reasons.append("selected_option_present")
    if target:
        score += 0.13
        reasons.append("selected_target_present")
    if row_index is not None:
        score += 0.06
        reasons.append("selected_row_index_present")
    if transform:
        score += 0.08
        reasons.append(f"coordinate_transform={transform}")
    if not selection:
        warnings.append("menu_selection_payload_missing")
    score = round(max(0.0, min(1.0, score)), 3)
    return {
        "insideRowBounds": inside,
        "rowCenterDistancePx": row_distance,
        "rowGeometryQuality": quality_from_score(score),
        "rowGeometryScore": score,
        "selectedOption": option,
        "selectedTarget": target,
        "selectedRowIndex": row_index,
        "rowBounds": row_bounds,
        "normalizedClickPoint": selection.get("normalizedClickPoint") or classification.get("normalizedMenuPoint"),
        "coordinateTransformUsed": transform,
        "coordinateTransformConfidence": selection.get("coordinateTransformConfidence") or classification.get("coordinateTransformConfidence"),
        "inputPathClassification": selection.get("inputPathClassification") or classification.get("inputPathClassification"),
        "mirrorVerificationStatus": selection.get("mirrorVerificationStatus") or classification.get("mirrorVerificationStatus"),
        "reasons": reasons,
        "warnings": sorted(set(warnings + list(selection.get("warnings") or []))),
    }


def game_target_quality_payload(quality: str, score: float, warnings: list[str]) -> dict[str, Any]:
    return {
        "quality": quality,
        "score": round(float(score), 3),
        "warnings": warnings,
    }


def freshness_payload(input_event: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
    elapsed = _event_time(input_event)
    before_elapsed = _snapshot_time(before)
    age_ms = round((elapsed - before_elapsed) * 1000.0, 3) if elapsed is not None and before_elapsed is not None else None
    input_tick = _number(input_event.get("nearest_tick") or input_event.get("latest_tick"))
    snapshot_tick = _number(_dict(before).get("latest_tick") or _dict(before).get("tick"))
    tick_delta = abs(int(input_tick) - int(snapshot_tick)) if input_tick is not None and snapshot_tick is not None else None
    input_seq = _number(input_event.get("nearest_export_sequence") or input_event.get("latest_export_sequence"))
    snapshot_seq = _number(_dict(before).get("latest_export_sequence") or _dict(before).get("export_sequence"))
    seq_delta = abs(int(input_seq) - int(snapshot_seq)) if input_seq is not None and snapshot_seq is not None else None
    return {
        "nearestTelemetryAgeMs": age_ms,
        "nearestTickDelta": tick_delta,
        "nearestExportSeqDelta": seq_delta,
    }


def score_target_match(
    input_event: dict[str, Any],
    classification: dict[str, Any],
    click_analysis: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshots = snapshots or []
    event_seq = _event_seq(input_event) or _classification_seq(classification)
    label = str(classification.get("classification") or "unknown")
    target, target_association = resolve_target_identity(classification, click_analysis)
    click_time = _event_time(input_event) or _float(_dict(classification.get("time")).get("elapsedSeconds"))
    observations = matching_target_observations(target, snapshots, click_time)
    geometry_target, geometry = choose_geometry_target(input_event, click_analysis, target, observations)
    target = geometry_target or target
    target = _attach_intended_identity(target, target_association)
    target_association = _finalize_association(target_association, target, geometry)
    actions = target_actions(target)
    action = actions[0] if actions else target_association.get("intendedAction") or _dict(classification.get("targetContext")).get("targetAction")
    fresh = freshness_payload(input_event, before)
    post = post_click_result(before, snapshots, click_time, target)
    evidence = {
        "hoverConfirmed": False,
        "menuConfirmed": False,
        "telemetryClickHistoryConfirmed": False,
        "objectIdentityConfirmed": bool(target_name(target) and target_id(target) is not None),
        "actionConfirmed": bool(action and _action_matches(str(action), actions)),
        "freshSnapshot": bool(
            (fresh.get("nearestTelemetryAgeMs") is not None and float(fresh["nearestTelemetryAgeMs"]) <= 1500.0)
            or (fresh.get("nearestTickDelta") is not None and int(fresh["nearestTickDelta"]) <= 1)
        ),
        "postClickResultConfirmed": bool(post.get("matchedExpectedOutcome")),
    }
    before_blob = _text_blob(_dict(before).get("hover"), _dict(before).get("menu"))
    history = _dict(click_analysis).get("telemetryObservedClickHistory")
    history_blob = _text_blob(history)
    name_l = str(target_name(target) or "").lower()
    action_l = str(action or "").lower()
    if name_l and name_l in before_blob:
        evidence["hoverConfirmed"] = True
    if action_l and action_l in before_blob:
        evidence["menuConfirmed"] = True
    if (name_l and name_l in history_blob) or (action_l and action_l in history_blob):
        evidence["telemetryClickHistoryConfirmed"] = True

    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []
    severe_conflict = False
    max_quality: str | None = None
    menu_quality: dict[str, Any] | None = menu_selection_quality_payload(classification) if label == "menu_selection_click" else None
    if target_association.get("rejectedCandidates"):
        warnings.append("target_identity_conflicting_geometry_rejected")
        reasons.append("identity_action_preferred_over_nearby_geometry")

    if not classification.get("targetRelativeEligible"):
        warnings.append("not_target_relative_eligible")
        return _quality_payload(
            input_event,
            classification,
            target,
            geometry,
            evidence,
            post,
            fresh,
            "unmatched",
            0.0,
            ["click was not target-relative eligible"],
            warnings,
            menu_selection_quality=menu_quality,
            target_association=target_association,
        )
    if not target:
        warnings.append("no_plausible_target")
        return _quality_payload(
            input_event,
            classification,
            target,
            geometry,
            evidence,
            post,
            fresh,
            "unmatched",
            0.0,
            ["no plausible target was available"],
            warnings,
            menu_selection_quality=menu_quality,
            target_association=target_association,
        )

    distance = geometry.get("distanceFromAimPointPx")
    inside = geometry.get("insideClickbox")
    if label == "menu_selection_click":
        if menu_quality:
            row_score = _float(menu_quality.get("rowGeometryScore")) or 0.0
            if row_score >= 0.8:
                score += 0.28
                transform_name = menu_quality.get("coordinateTransformUsed")
                reasons.append(f"menu_row_geometry_confirmed_using_{transform_name}" if transform_name else "menu_row_geometry_confirmed")
            elif row_score >= 0.35:
                score += 0.12
                reasons.append("menu_row_selection_partially_confirmed")
            else:
                warnings.extend(menu_quality.get("warnings") or ["menu_row_geometry_missing"])
            if not menu_quality.get("rowBounds"):
                warnings.append("menu_row_geometry_missing")
            if menu_quality.get("selectedOption") or menu_quality.get("selectedTarget"):
                score += 0.16
                reasons.append("menu_row_option_target_present")
        reasons.append("underlying_game_target_confirmed_by_menu_row_action")
        reasons.append("object_clickbox_proximity_not_required_for_menu_row_selection")
    elif inside is True:
        score += 0.36
        reasons.append("click_inside_clickbox")
    elif inside is False:
        warnings.append("click_outside_clickbox")
        score -= 0.08
    if isinstance(distance, (int, float)):
        if label == "menu_selection_click":
            reasons.append("underlying_target_geometry_not_used_for_menu_row_click")
        elif distance <= 12:
            score += 0.32
            reasons.append("distance_from_aim_le_12px")
        elif distance <= 30:
            score += 0.22
            reasons.append("distance_from_aim_le_30px")
        elif distance <= 80:
            score += 0.12
            reasons.append("distance_from_aim_le_80px")
        else:
            warnings.append(f"large_distance_from_aim_px={distance}")
            max_quality = "medium" if distance <= 160 else "weak"
    elif geometry.get("clickboxAvailable"):
        reasons.append("clickbox_available")
    else:
        warnings.append("target_geometry_missing")
        if not post.get("matchedExpectedOutcome"):
            max_quality = "medium"
    if geometry.get("targetOnScreen") is True:
        score += 0.08
        reasons.append("target_on_screen")
    elif geometry.get("targetOnScreen") is False:
        warnings.append("target_not_on_screen")
        if label != "menu_selection_click":
            max_quality = max_quality or "medium"

    if target_name(target):
        score += 0.12
        reasons.append("target_name_present")
    if target_id(target) is not None:
        score += 0.08
        reasons.append("target_id_present")
    if target.get("ref"):
        score += 0.08
        reasons.append("stable_target_ref_present")
    if target.get("kind"):
        score += 0.04
        reasons.append(f"target_kind={target.get('kind')}")
    if target.get("routeObjectCandidate") or target.get("routeObjectKind") or target.get("confidence"):
        score += 0.06
        reasons.append("route_or_candidate_confidence_present")
    if target.get("source") == "menu_hover_context":
        score += 0.14
        reasons.append("menu_hover_target_used")

    if evidence["actionConfirmed"]:
        score += 0.18
        reasons.append("target_action_confirmed")
    elif action:
        score += 0.08
        reasons.append("target_action_present")
    if target.get("menuActionAvailable"):
        score += 0.06
        reasons.append("menu_action_available")
    if label in {"object_action_click", "npc_action_click"}:
        score += 0.08
        reasons.append(f"classification={label}")
    if label == "menu_selection_click":
        score += 0.12
        reasons.append("classification=menu_selection_click")
        if "left_click_after_recent_right_click_or_open_menu" in (classification.get("reasons") or []):
            score += 0.08
            reasons.append("linked_to_recent_right_click_or_open_menu")
        if _dict(classification.get("linkedGameTarget")).get("name") or _dict(classification.get("menuSelection")).get("linkedGameTarget"):
            score += 0.08
            reasons.append("menu_selection_linked_to_game_target")
    if evidence["hoverConfirmed"]:
        score += 0.16
        reasons.append("hover_confirmed_target")
    if evidence["menuConfirmed"]:
        score += 0.16
        reasons.append("menu_confirmed_action_or_target")
    if evidence["telemetryClickHistoryConfirmed"]:
        score += 0.14
        reasons.append("telemetry_click_history_confirmed")

    if evidence["freshSnapshot"]:
        score += 0.1
        reasons.append("fresh_telemetry_snapshot")
    else:
        warnings.append("nearest_telemetry_snapshot_stale_or_unknown")
        score -= 0.05
        if not post.get("matchedExpectedOutcome"):
            max_quality = "medium"

    if post.get("matchedExpectedOutcome"):
        score += 0.22
        reasons.append("post_click_expected_outcome_confirmed")
    elif post.get("positionChanged"):
        score += 0.1
        reasons.append("post_click_position_changed")
    if ("climb" in action_l or "ladder" in name_l or "stair" in name_l) and not post.get("planeChanged"):
        warnings.append("climb_target_without_plane_change_in_window")

    score = max(0.0, min(1.0, round(score, 3)))
    quality = quality_from_score(score, severe_conflict=severe_conflict)
    if max_quality:
        quality = cap_quality(quality, max_quality)
    if quality == "unmatched" and target:
        warnings.append("target_match_score_below_threshold")
    return _quality_payload(
        input_event,
        classification,
        target,
        geometry,
        evidence,
        post,
        fresh,
        quality,
        score,
        reasons,
        warnings,
        menu_selection_quality=menu_quality,
        game_target_quality=game_target_quality_payload(quality, score, warnings),
        target_association=target_association,
    )


def cap_quality(quality: str, maximum: str) -> str:
    order = {"unmatched": 0, "weak": 1, "medium": 2, "strong": 3}
    reverse = {value: key for key, value in order.items()}
    return reverse[min(order.get(quality, 0), order.get(maximum, 0))]


def quality_from_score(score: float, *, severe_conflict: bool = False) -> str:
    if severe_conflict:
        return "unmatched" if score < 0.55 else "weak"
    if score >= 0.8:
        return "strong"
    if score >= 0.55:
        return "medium"
    if score >= 0.25:
        return "weak"
    return "unmatched"


def _quality_payload(
    input_event: dict[str, Any],
    classification: dict[str, Any],
    target: dict[str, Any] | None,
    geometry: dict[str, Any],
    evidence: dict[str, Any],
    post: dict[str, Any],
    fresh: dict[str, Any],
    quality: str,
    score: float,
    reasons: list[str],
    warnings: list[str],
    *,
    menu_selection_quality: dict[str, Any] | None = None,
    game_target_quality: dict[str, Any] | None = None,
    target_association: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": QUALITY_SCHEMA,
        "clickId": classification.get("clickId") or f"click_{_event_seq(input_event):03d}",
        "eventSeq": _event_seq(input_event) or _classification_seq(classification),
        "classification": classification.get("classification"),
        "quality": quality,
        "score": round(float(score), 3),
        "matchedTarget": normalize_target(target),
        "geometry": geometry,
        "evidence": evidence,
        "postClickResult": post,
        "freshness": fresh,
        "targetAssociation": target_association or {"schema": "target_association.v1", "associationMethod": "unresolved", "rejectedCandidates": [], "conflictReasons": []},
        "reasons": reasons,
        "warnings": warnings,
        "inputPathClassification": classification.get("inputPathClassification"),
        "mirrorVerificationStatus": classification.get("mirrorVerificationStatus"),
        "coordinateTransformUsed": classification.get("coordinateTransformUsed"),
    }
    if menu_selection_quality is not None:
        payload["menuSelectionQuality"] = menu_selection_quality
        payload["gameTargetQuality"] = game_target_quality or game_target_quality_payload(quality, score, warnings)
    return payload


def score_joined_rows(joined_rows: list[dict[str, Any]], snapshots: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in joined_rows:
        classification = _dict(row.get("inputActionClassification"))
        input_event = _dict(row.get("inputEvent"))
        if input_event.get("kind") not in {"click", "double_click"}:
            continue
        if not classification.get("targetRelativeEligible"):
            continue
        rows.append(
            score_target_match(
                input_event,
                classification,
                _dict(row.get("clickAnalysis")),
                _dict(row.get("nearestTelemetryBefore")),
                _dict(row.get("nearestTelemetryAfter")),
                snapshots or [],
            )
        )
    return rows, summarize_quality(rows)


def _distance_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "average": None, "median": None}
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    median = ordered[midpoint] if len(ordered) % 2 else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    return {
        "count": len(values),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "average": round(sum(values) / len(values), 3),
        "median": round(median, 3),
    }


def click_landing_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clickbox_counts = {"inside": 0, "outside": 0, "unknown": 0, "unavailable": 0}
    distance_buckets = {"le12": 0, "le30": 0, "le80": 0, "gt80": 0, "unknown": 0}
    distances: list[float] = []
    menu_row_counts = {"inside": 0, "outside": 0, "unknown": 0, "missingBounds": 0}
    row_distances: list[float] = []
    examples: list[dict[str, Any]] = []
    for row in rows:
        geometry = _dict(row.get("geometry"))
        menu_quality = _dict(row.get("menuSelectionQuality"))
        classification = str(row.get("classification") or "")
        target = _dict(row.get("matchedTarget"))
        clickbox_available = geometry.get("clickboxAvailable")
        inside = geometry.get("insideClickbox")
        if clickbox_available is False:
            clickbox_counts["unavailable"] += 1
        elif inside is True:
            clickbox_counts["inside"] += 1
        elif inside is False:
            clickbox_counts["outside"] += 1
        else:
            clickbox_counts["unknown"] += 1
        distance = _float(geometry.get("distanceFromAimPointPx"))
        if distance is None:
            distance_buckets["unknown"] += 1
        else:
            distances.append(distance)
            if distance <= 12:
                distance_buckets["le12"] += 1
            elif distance <= 30:
                distance_buckets["le30"] += 1
            elif distance <= 80:
                distance_buckets["le80"] += 1
            else:
                distance_buckets["gt80"] += 1
        if classification == "menu_selection_click" or menu_quality:
            if menu_quality.get("rowBounds"):
                if menu_quality.get("insideRowBounds") is True:
                    menu_row_counts["inside"] += 1
                elif menu_quality.get("insideRowBounds") is False:
                    menu_row_counts["outside"] += 1
                else:
                    menu_row_counts["unknown"] += 1
            else:
                menu_row_counts["missingBounds"] += 1
            row_distance = _float(menu_quality.get("rowCenterDistancePx"))
            if row_distance is not None:
                row_distances.append(row_distance)
        warning_values = row.get("warnings") or []
        if (
            inside is False
            or (distance is not None and distance > 80)
            or "menu_row_geometry_missing" in warning_values
            or ("target_geometry_missing" in warning_values and classification != "menu_selection_click")
            or (clickbox_available is False and classification != "menu_selection_click")
        ):
            examples.append(
                {
                    "eventSeq": row.get("eventSeq"),
                    "classification": classification,
                    "targetName": target.get("name"),
                    "targetAction": target.get("action"),
                    "insideClickbox": inside,
                    "clickboxAvailable": clickbox_available,
                    "distanceFromAimPointPx": distance,
                    "quality": row.get("quality"),
                    "warnings": row.get("warnings") or [],
                }
            )
    return {
        "schema": "click_landing_summary.v1",
        "targetRelativeClickCount": len(rows),
        "clickboxCounts": clickbox_counts,
        "aimDistancePx": _distance_stats(distances),
        "aimDistanceBuckets": distance_buckets,
        "menuRowCounts": menu_row_counts,
        "menuRowCenterDistancePx": _distance_stats(row_distances),
        "imperfectButUsefulClickCount": len(examples),
        "examples": examples[:8],
    }


def summarize_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("quality") or "unmatched") for row in rows)
    for quality in TARGET_QUALITIES:
        counts.setdefault(quality, 0)
    warnings = sorted({str(warning) for row in rows for warning in (row.get("warnings") or [])})
    association_examples: list[dict[str, Any]] = []
    rejected_count = 0
    for row in rows:
        association = _dict(row.get("targetAssociation"))
        rejected = _list(association.get("rejectedCandidates"))
        if not rejected:
            continue
        rejected_count += len(rejected)
        association_examples.append(
            {
                "eventSeq": row.get("eventSeq"),
                "classification": row.get("classification"),
                "associationMethod": association.get("associationMethod"),
                "intendedAction": association.get("intendedAction"),
                "intendedTargetName": association.get("intendedTargetName"),
                "selectedCandidate": association.get("selectedCandidate"),
                "rejectedCandidates": rejected[:4],
                "conflictReasons": association.get("conflictReasons") or [],
                "quality": row.get("quality"),
                "warnings": row.get("warnings") or [],
            }
        )
    examples = []
    for row in sorted(rows, key=lambda item: (-float(item.get("score") or 0.0), int(item.get("eventSeq") or 0)))[:8]:
        target = _dict(row.get("matchedTarget"))
        association = _dict(row.get("targetAssociation"))
        examples.append(
            {
                "eventSeq": row.get("eventSeq"),
                "classification": row.get("classification"),
                "targetName": target.get("name"),
                "targetAction": target.get("action"),
                "quality": row.get("quality"),
                "score": row.get("score"),
                "associationMethod": association.get("associationMethod"),
                "rejectedCandidateCount": len(_list(association.get("rejectedCandidates"))),
                "reasons": row.get("reasons") or [],
                "warnings": row.get("warnings") or [],
            }
        )
    return {
        "schema": SUMMARY_SCHEMA,
        "status": "PASS" if rows else "WARN",
        "generated_at_utc": utc_now(),
        "targetRelativeClickCount": len(rows),
        "strongMatchCount": counts.get("strong", 0),
        "mediumMatchCount": counts.get("medium", 0),
        "weakMatchCount": counts.get("weak", 0),
        "unmatchedCount": counts.get("unmatched", 0),
        "qualityCounts": {quality: counts.get(quality, 0) for quality in TARGET_QUALITIES},
        "clickLandingSummary": click_landing_summary(rows),
        "targetAssociation": {
            "schema": "target_association_summary.v1",
            "conflictCount": len(association_examples),
            "rejectedCandidateCount": rejected_count,
            "examples": association_examples[:8],
        },
        "warnings": warnings,
        "examples": examples,
    }

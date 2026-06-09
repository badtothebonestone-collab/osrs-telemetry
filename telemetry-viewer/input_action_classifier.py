from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import coordinate_spaces
import menu_interaction_model


CLASSIFICATION_SCHEMA = "input_action_classification.v1"
SUMMARY_SCHEMA = "input_action_summary.v1"

CLICK_LIKE_KINDS = {"mouse_down", "mouse_up", "click", "double_click"}
OS_CLICK_KINDS = {"click", "double_click"}
TARGET_ELIGIBLE_CLASSIFICATIONS = {"object_action_click", "npc_action_click", "game_action_click", "menu_selection_click"}
TARGET_INELIGIBLE_CLASSIFICATIONS = {
    "camera_drag_click",
    "camera_drag_release",
    "minimap_click",
    "ui_control_click",
    "inventory_click",
    "sidebar_click",
    "chatbox_click",
    "window_chrome_click",
    "external_click",
    "ambiguous_click",
    "right_click_menu_open",
}


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


def _event_time(event: dict[str, Any]) -> float | None:
    return _float(event.get("elapsed_seconds"))


def _event_seq(event: dict[str, Any]) -> int:
    value = _number(event.get("event_seq"))
    return int(value or 0)


def _point_from_event(event: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    x = event.get(f"{prefix}_x")
    y = event.get(f"{prefix}_y")
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def event_point(event: dict[str, Any]) -> dict[str, float] | None:
    for key, space in (("normalizedCanvas", "normalized_canvas"), ("normalizedMenuPoint", "normalized_menu")):
        point = _dict(event.get(key))
        if point:
            try:
                return {"x": float(point["x"]), "y": float(point["y"]), "space": space}
            except (KeyError, TypeError, ValueError):
                pass
    for prefix in ("canvas", "client", "screen"):
        point = _point_from_event(event, prefix)
        if not point:
            continue
        try:
            return {"x": float(point["x"]), "y": float(point["y"]), "space": prefix}
        except (TypeError, ValueError):
            continue
    return None


def candidate_point(candidate: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        candidate.get("aimPoint"),
        candidate.get("canvas"),
        candidate.get("canvasLocation"),
        _dict(candidate.get("geometry")).get("canvas"),
    ):
        if not isinstance(value, dict):
            continue
        x = value.get("x", value.get("canvasX", value.get("screen_x")))
        y = value.get("y", value.get("canvasY", value.get("screen_y")))
        if x is None or y is None:
            continue
        return {"x": x, "y": y}
    return None


def distance_to_candidate(event: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    click = event_point(event)
    aim = candidate_point(candidate)
    if click is None or aim is None:
        return None
    try:
        return math.hypot(float(click["x"]) - float(aim["x"]), float(click["y"]) - float(aim["y"]))
    except (TypeError, ValueError):
        return None


def nearest_target(event: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    pools = (
        ("object", _list(snapshot.get("nearby_objects")) + _list(snapshot.get("route_objects"))),
        ("npc", _list(snapshot.get("nearby_npcs"))),
    )
    for kind, items in pools:
        for item in items:
            if not isinstance(item, dict):
                continue
            distance = distance_to_candidate(event, item)
            if distance is None:
                continue
            candidate = dict(item)
            candidate["kind"] = candidate.get("kind") or kind
            candidate["clickDistance"] = round(distance, 3)
            candidates.append((distance, kind, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][2]


def normalize_region(event: dict[str, Any]) -> str:
    raw = str(event.get("region") or "unknown").lower()
    client_x = _number(event.get("client_x"))
    client_y = _number(event.get("client_y"))
    if client_x is None or client_y is None:
        client_x = _number(event.get("canvas_x"))
        client_y = _number(event.get("canvas_y"))
    if raw in {"minimap", "chatbox", "viewport", "external", "topbar", "window_chrome"}:
        return raw
    if "inventory" in raw:
        return "inventory"
    if "sidebar" in raw:
        return "sidebar"
    if client_x is not None and client_y is not None:
        x = float(client_x)
        y = float(client_y)
        if x < 0 or y < 0:
            return "window_chrome"
        if x <= 765 and y <= 503:
            return "viewport"
        if x > 765 and y <= 170:
            return "minimap"
        if x > 765 and 170 < y <= 520:
            return "sidebar"
        if y > 503:
            return "chatbox"
    return "unknown"


def _position_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "screen": _point_from_event(event, "screen"),
        "client": _point_from_event(event, "client"),
        "canvas": _point_from_event(event, "canvas"),
        "normalizedCanvas": event.get("normalizedCanvas"),
        "normalizedMenuPoint": event.get("normalizedMenuPoint"),
    }


def _menu_context(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before_menu = _dict(_dict(before or {}).get("menu"))
    after_menu = _dict(_dict(after or {}).get("menu"))
    before_hover = _dict(_dict(before or {}).get("hover"))
    menu_snapshot = menu_interaction_model.normalize_menu_snapshot(before or after or {})
    bounds = menu_snapshot.get("bounds") if isinstance(menu_snapshot, dict) else None
    normalized = _dict(event_point(_dict(before or {}).get("inputEvent") or {}))
    return {
        "menuOpenBefore": bool(before_menu.get("menuOpen") or before_menu.get("open") or menu_snapshot.get("isOpen")),
        "menuOpenAfter": bool(after_menu.get("menuOpen") or after_menu.get("open")),
        "insideOpenMenuBounds": coordinate_spaces.point_in_bounds(normalized, bounds) if normalized else False,
        "hoverOption": before_hover.get("topOption") or before_menu.get("topOption"),
        "hoverTarget": before_hover.get("topTarget") or before_menu.get("topTarget"),
        "menuBounds": bounds,
        "menuRowCount": len(menu_snapshot.get("rowsVisualOrder") or []),
        "menuWarnings": menu_snapshot.get("warnings") or [],
    }


def _target_context(target: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
    if not target:
        return {
            "matchedTarget": None,
            "targetName": None,
            "targetAction": None,
            "targetRef": None,
            "distanceFromAimPointPx": None,
        }
    actions = target.get("effectiveActions") or target.get("actions") or []
    if not isinstance(actions, list):
        actions = [str(actions)]
    distance = distance_to_candidate(event, target)
    return {
        "matchedTarget": {
            "kind": target.get("kind"),
            "ref": target.get("ref"),
            "id": target.get("effectiveId") or target.get("id") or target.get("rawId"),
            "name": target.get("effectiveName") or target.get("name"),
            "actions": actions,
            "worldPoint": target.get("worldPoint"),
            "distance": target.get("distance"),
            "clickDistance": target.get("clickDistance"),
        },
        "targetName": target.get("effectiveName") or target.get("name"),
        "targetAction": actions[0] if actions else None,
        "targetRef": target.get("ref"),
        "distanceFromAimPointPx": round(distance, 3) if distance is not None else None,
    }


def _world_point(snapshot: dict[str, Any] | None) -> tuple[Any, Any, Any] | None:
    point = _dict(_dict(snapshot or {}).get("player_world_point") or _dict(snapshot or {}).get("playerWorldPoint"))
    if not point:
        return None
    return (point.get("worldX"), point.get("worldY"), point.get("plane"))


def _movement_after(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    before_point = _world_point(before)
    after_point = _world_point(after)
    return bool(before_point and after_point and before_point != after_point)


def _drag_context_default() -> dict[str, Any]:
    return {"partOfDrag": False, "dragButton": None, "dragDistancePx": 0, "dragDurationMs": 0}


def build_drag_context(input_events: list[dict[str, Any]], *, drag_threshold_px: float = 6.0) -> dict[int, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    result: dict[int, dict[str, Any]] = {}
    sorted_events = sorted(input_events, key=lambda event: (_event_time(event) or 0.0, _event_seq(event)))

    def position(event: dict[str, Any]) -> tuple[float, float] | None:
        point = event_point(event)
        if not point:
            return None
        return (point["x"], point["y"])

    for event in sorted_events:
        kind = event.get("kind")
        button = str(event.get("button") or "unknown")
        if kind == "mouse_down":
            pos = position(event)
            active[button] = {
                "button": button,
                "down_event": event,
                "down_pos": pos,
                "last_pos": pos,
                "max_distance": 0.0,
                "saw_drag_event": False,
            }
            continue
        if kind in {"mouse_move", "drag_move", "drag_start", "drag_end"}:
            buttons = [button] if event.get("button") else list(active)
            pos = position(event)
            for item_button in buttons:
                item = active.get(item_button)
                if not item or pos is None:
                    continue
                down_pos = item.get("down_pos")
                if down_pos is not None:
                    item["max_distance"] = max(float(item.get("max_distance") or 0.0), math.hypot(pos[0] - down_pos[0], pos[1] - down_pos[1]))
                item["last_pos"] = pos
                if kind.startswith("drag_"):
                    item["saw_drag_event"] = True
            continue
        if kind == "mouse_up":
            item = active.pop(button, None)
            if not item:
                continue
            pos = position(event)
            down_event = _dict(item.get("down_event"))
            down_pos = item.get("down_pos")
            distance = float(item.get("max_distance") or 0.0)
            if pos is not None and down_pos is not None:
                distance = max(distance, math.hypot(pos[0] - down_pos[0], pos[1] - down_pos[1]))
            duration_ms = 0
            if _event_time(event) is not None and _event_time(down_event) is not None:
                duration_ms = int(round((float(_event_time(event) or 0.0) - float(_event_time(down_event) or 0.0)) * 1000.0))
            part_of_drag = bool(distance >= drag_threshold_px)
            context = {
                "partOfDrag": part_of_drag,
                "dragButton": button,
                "dragDistancePx": round(distance, 3),
                "dragDurationMs": duration_ms,
                "sawDragEvent": bool(item.get("saw_drag_event")),
            }
            for seq in (_event_seq(down_event), _event_seq(event)):
                if seq:
                    result[seq] = dict(context)
            event_time = _event_time(event)
            for maybe_click in sorted_events:
                if maybe_click.get("kind") not in OS_CLICK_KINDS or str(maybe_click.get("button") or "unknown") != button:
                    continue
                click_time = _event_time(maybe_click)
                if event_time is None or click_time is None:
                    continue
                if 0 <= click_time - event_time <= 0.25:
                    result[_event_seq(maybe_click)] = dict(context)
                    break
    return result


def classify_input_actions(
    input_events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]] | None = None,
    *,
    drag_threshold_px: float = 6.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshots = sorted(snapshots or [], key=lambda item: float(item.get("elapsed_seconds") or 0.0))
    drag_lookup = build_drag_context(input_events, drag_threshold_px=drag_threshold_px)
    classifications: list[dict[str, Any]] = []
    previous_right_click: dict[str, Any] | None = None

    for event in sorted(input_events, key=lambda item: (_event_time(item) or 0.0, _event_seq(item))):
        if event.get("kind") not in CLICK_LIKE_KINDS:
            continue
        elapsed = _event_time(event)
        before, after = nearest_snapshots(snapshots, elapsed)
        classification = classify_event(
            event,
            before,
            after,
            drag_context=drag_lookup.get(_event_seq(event), _drag_context_default()),
            previous_right_click=previous_right_click,
        )
        if event.get("kind") in OS_CLICK_KINDS and classification.get("classification") == "right_click_menu_open":
            previous_right_click = classification
        elif event.get("kind") in OS_CLICK_KINDS and classification.get("button") == "left":
            previous_right_click = None
        classifications.append(classification)

    return classifications, summarize_classifications(classifications)


def nearest_snapshots(snapshots: list[dict[str, Any]], elapsed: float | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if elapsed is None:
        return None, None
    before = [item for item in snapshots if _float(item.get("elapsed_seconds")) is not None and float(item["elapsed_seconds"]) <= elapsed]
    after = [item for item in snapshots if _float(item.get("elapsed_seconds")) is not None and float(item["elapsed_seconds"]) >= elapsed]
    return (before[-1] if before else None, after[0] if after else None)


def classify_event(
    event: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    drag_context: dict[str, Any] | None = None,
    previous_right_click: dict[str, Any] | None = None,
) -> dict[str, Any]:
    drag_context = dict(drag_context or _drag_context_default())
    kind = str(event.get("kind") or "unknown")
    button = str(event.get("button") or "unknown")
    region = normalize_region(event)
    reasons: list[str] = []
    warnings: list[str] = []
    target = None
    menu_selection: dict[str, Any] | None = None
    classification = "ambiguous_click"
    confidence = 0.35
    eligible = False
    target_eligible = False

    title = str(event.get("foreground_window_title") or "")
    runelite = event.get("runelite_window_match")
    if runelite is False:
        if "telemetry control" in title.lower() or "osrs telemetry" in title.lower():
            classification = "ui_control_click"
            confidence = 0.95
            reasons.append("foreground_window_is_telemetry_ui")
        else:
            classification = "external_click"
            confidence = 0.85
            reasons.append("foreground_window_is_not_runelite")
        if drag_context.get("partOfDrag"):
            reasons.append(f"drag_distance_px={drag_context.get('dragDistancePx')}")
            warnings.append("non-runelite click-like event exceeded drag threshold")
        region = "external" if classification == "external_click" else region
    elif drag_context.get("partOfDrag"):
        reasons.append(f"drag_distance_px={drag_context.get('dragDistancePx')}")
        if button == "middle":
            classification = "camera_drag_click" if kind == "mouse_down" else "camera_drag_release"
            confidence = 0.96
            reasons.append("middle_mouse_drag")
        else:
            classification = "ambiguous_click"
            confidence = 0.45
            warnings.append("click-like event exceeded drag threshold")
            reasons.append("drag_threshold_exceeded")
    elif region == "window_chrome":
        classification = "window_chrome_click"
        confidence = 0.85
        reasons.append("client_point_outside_game_client")
    elif region == "minimap":
        classification = "minimap_click"
        confidence = 0.86
        reasons.append("client_region_minimap")
    elif region == "inventory":
        classification = "inventory_click"
        confidence = 0.84
        reasons.append("client_region_inventory")
    elif region == "sidebar":
        classification = "sidebar_click"
        confidence = 0.82
        reasons.append("client_region_sidebar")
    elif region == "chatbox":
        classification = "chatbox_click"
        confidence = 0.82
        reasons.append("client_region_chatbox")
    elif button == "right" and region == "viewport" and kind in OS_CLICK_KINDS:
        classification = "right_click_menu_open"
        confidence = 0.84
        reasons.append("right_click_in_viewport")
    elif button == "left" and region == "viewport":
        menu_context = _menu_context(before, after)
        recent_right = False
        if previous_right_click:
            previous_time = _float(_dict(previous_right_click.get("time")).get("elapsedSeconds"))
            current_time = _event_time(event)
            recent_right = previous_time is not None and current_time is not None and 0.0 <= current_time - previous_time <= 3.0
        target = nearest_target(event, before)
        if recent_right or menu_context.get("menuOpenBefore"):
            menu_snapshot = menu_interaction_model.normalize_menu_snapshot(before or after or {}, opened_event=_dict(previous_right_click).get("inputEvent"))
            menu_selection = menu_interaction_model.resolve_menu_selection(event, menu_snapshot, fallback_target=target)
            classification = "menu_selection_click"
            confidence = 0.78
            eligible = True
            target_eligible = bool(_dict(menu_selection.get("linkedGameTarget")).get("name") or target is not None)
            reasons.append("left_click_after_recent_right_click_or_open_menu")
            if menu_selection.get("insideRowBounds") is True:
                reasons.append("left_click_hit_normalized_menu_row_bounds")
                confidence = 0.88
            elif menu_selection.get("rowBounds"):
                reasons.append("menu_row_geometry_available")
            else:
                warnings.extend(menu_selection.get("warnings") or [])
        elif target is not None:
            target_kind = str(target.get("kind") or "object")
            classification = "npc_action_click" if target_kind == "npc" else "object_action_click"
            confidence = 0.82
            eligible = True
            target_eligible = True
            reasons.append(f"matched_{target_kind}_target")
        elif _movement_after(before, after):
            classification = "world_walk_click"
            confidence = 0.7
            eligible = True
            target_eligible = False
            reasons.append("player_position_changed_after_click")
        else:
            classification = "ambiguous_click"
            confidence = 0.38
            reasons.append("left_viewport_click_without_target_or_movement_evidence")
    else:
        reasons.append("no_rule_matched")

    if classification in TARGET_INELIGIBLE_CLASSIFICATIONS:
        target_eligible = False
    if classification in TARGET_ELIGIBLE_CLASSIFICATIONS and classification != "menu_selection_click":
        eligible = True
    if classification == "menu_selection_click" and target is None:
        reasons.append("menu_selection_without_matched_target")

    payload = {
        "schema": CLASSIFICATION_SCHEMA,
        "clickId": f"click_{len(str(_event_seq(event))):02d}_{_event_seq(event)}",
        "eventSeq": _event_seq(event),
        "eventKind": kind,
        "time": {
            "monotonic": event.get("monotonic_time"),
            "elapsedSeconds": event.get("elapsed_seconds"),
            "wallTimeUtc": event.get("wall_time_utc"),
        },
        "button": button,
        "classification": classification,
        "eligibleForTargetMatching": bool(eligible),
        "targetRelativeEligible": bool(target_eligible),
        "confidence": round(float(confidence), 3),
        "region": region,
        "position": _position_payload(event),
        "dragContext": drag_context,
        "menuContext": _menu_context(before, after),
        "targetContext": _target_context(target, event),
        "cameraContext": {
            "nearCameraSegment": bool(classification in {"camera_drag_click", "camera_drag_release"}),
            "cameraSegmentId": None,
            "timeSinceCameraMs": None,
        },
        "inputPathClassification": event.get("inputPathClassification"),
        "mirrorVerificationStatus": event.get("mirrorVerificationStatus"),
        "reasons": reasons,
        "warnings": warnings,
    }
    if menu_selection is not None:
        payload["menuSelection"] = menu_selection
        payload["selectedRowIndex"] = menu_selection.get("selectedRowIndex")
        payload["selectedOption"] = menu_selection.get("selectedOption")
        payload["selectedTarget"] = menu_selection.get("selectedTarget")
        payload["menuRowBounds"] = menu_selection.get("rowBounds")
        payload["insideMenuRowBounds"] = menu_selection.get("insideRowBounds")
        payload["rowCenterDistancePx"] = menu_selection.get("rowCenterDistancePx")
        payload["linkedGameTarget"] = menu_selection.get("linkedGameTarget")
        payload["normalizedMenuPoint"] = menu_selection.get("normalizedClickPoint")
        payload["coordinateTransformUsed"] = menu_selection.get("coordinateTransformUsed")
        payload["coordinateTransformConfidence"] = menu_selection.get("coordinateTransformConfidence")
        payload["coordinateTransformReasons"] = menu_selection.get("coordinateTransformReasons")
        payload["inputPathClassification"] = menu_selection.get("inputPathClassification") or payload.get("inputPathClassification")
        payload["mirrorVerificationStatus"] = menu_selection.get("mirrorVerificationStatus") or payload.get("mirrorVerificationStatus")
    return payload


def summarize_classifications(classifications: list[dict[str, Any]]) -> dict[str, Any]:
    click_rows = [row for row in classifications if row.get("eventKind") in OS_CLICK_KINDS]
    classification_counts = Counter(str(row.get("classification") or "unknown") for row in click_rows)
    button_classification_counts = Counter(str(row.get("classification") or "unknown") for row in classifications)
    excluded = [row for row in click_rows if not row.get("targetRelativeEligible")]
    exclusion_reasons = Counter()
    for row in excluded:
        for reason in row.get("reasons") or ["not_target_relative_eligible"]:
            exclusion_reasons[str(reason)] += 1
    examples = []
    seen: set[str] = set()
    for row in click_rows:
        label = str(row.get("classification") or "unknown")
        if label in seen:
            continue
        seen.add(label)
        examples.append(
            {
                "eventSeq": row.get("eventSeq"),
                "classification": label,
                "button": row.get("button"),
                "elapsedSeconds": _dict(row.get("time")).get("elapsedSeconds"),
                "target": _dict(row.get("targetContext")).get("targetName"),
                "reasons": row.get("reasons") or [],
            }
        )
    target_relative_clicks = [row for row in click_rows if row.get("targetRelativeEligible")]
    eligible_game_action = [
        row
        for row in click_rows
        if row.get("classification") in {"game_action_click", "object_action_click", "npc_action_click", "world_walk_click", "menu_selection_click"}
    ]
    return {
        "schema": SUMMARY_SCHEMA,
        "status": "PASS" if click_rows else "WARN",
        "generated_at_utc": utc_now(),
        "rawOsClickCount": len(click_rows),
        "clickLikeEventCount": len(classifications),
        "classifiedClickCount": len(click_rows),
        "classifiedButtonEventCount": len(classifications),
        "eligibleGameActionClickCount": len(eligible_game_action),
        "worldObjectClickCount": sum(classification_counts.get(label, 0) for label in ("object_action_click", "npc_action_click")),
        "targetRelativeClickCount": len(target_relative_clicks),
        "cameraDragClickCount": classification_counts.get("camera_drag_click", 0),
        "cameraDragReleaseCount": classification_counts.get("camera_drag_release", 0),
        "rightClickMenuOpenCount": classification_counts.get("right_click_menu_open", 0),
        "menuSelectionClickCount": classification_counts.get("menu_selection_click", 0),
        "minimapClickCount": classification_counts.get("minimap_click", 0),
        "uiControlClickCount": classification_counts.get("ui_control_click", 0),
        "inventoryClickCount": classification_counts.get("inventory_click", 0),
        "sidebarClickCount": classification_counts.get("sidebar_click", 0),
        "chatboxClickCount": classification_counts.get("chatbox_click", 0),
        "windowChromeClickCount": classification_counts.get("window_chrome_click", 0),
        "externalClickCount": classification_counts.get("external_click", 0),
        "ambiguousClickCount": classification_counts.get("ambiguous_click", 0),
        "excludedClickCount": len(excluded),
        "classificationCounts": dict(sorted(classification_counts.items())),
        "buttonEventClassificationCounts": dict(sorted(button_classification_counts.items())),
        "exclusionReasons": dict(sorted(exclusion_reasons.items())),
        "examples": examples[:12],
        "warnings": [] if click_rows else ["No OS click events were available to classify."],
    }

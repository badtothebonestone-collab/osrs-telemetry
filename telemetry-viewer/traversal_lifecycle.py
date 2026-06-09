from __future__ import annotations

import json
import math
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_sources


SCHEMA_VERSION = "traversal_lifecycle.v1"
GROUPING_SCHEMA_VERSION = "traversal_step_grouping.v1"
DEFAULT_POSTCONDITION_TIME_WINDOW_MS = 4000
DEFAULT_POSTCONDITION_TICK_WINDOW = 8
CLIMB_POSTCONDITION_TIME_WINDOW_MS = 5000
DOOR_POSTCONDITION_TIME_WINDOW_MS = 4000
DOOR_POSTCONDITION_TICK_WINDOW = 6
WALK_POSTCONDITION_TIME_WINDOW_MS = 5000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
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


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return records


def _json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _world(value: Any) -> dict[str, int] | None:
    record = _dict(value)
    x = _int(_first(record.get("worldX"), record.get("x")))
    y = _int(_first(record.get("worldY"), record.get("y")))
    plane = _int(record.get("plane"))
    if x is None or y is None:
        return None
    result = {"worldX": x, "worldY": y}
    if plane is not None:
        result["plane"] = plane
    return result


def _distance(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    aw = _world(a)
    bw = _world(b)
    if not aw or not bw:
        return None
    return round(math.hypot(float(aw["worldX"] - bw["worldX"]), float(aw["worldY"] - bw["worldY"])), 3)


def _name(record: dict[str, Any]) -> str | None:
    return _clean(_first(record.get("name"), record.get("effectiveName"), record.get("targetName"), record.get("objectName")))


def _actions(record: dict[str, Any]) -> list[str]:
    value = _first(record.get("actions"), record.get("effectiveActions"), record.get("menuActions"))
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value or [] if item not in (None, "")]


def _target_id(record: dict[str, Any]) -> Any:
    return _first(record.get("effectiveId"), record.get("rawId"), record.get("id"), record.get("identifier"))


def _record_target(record: dict[str, Any]) -> dict[str, Any]:
    target = _dict(record.get("matchedTarget")) or _dict(record.get("target")) or record
    return {
        "kind": _first(target.get("kind"), record.get("targetKind"), "object"),
        "name": _first(target.get("name"), target.get("effectiveName"), record.get("targetName")),
        "action": _first(target.get("action"), record.get("targetAction")),
        "rawId": _first(target.get("rawId"), target.get("id"), record.get("rawId")),
        "effectiveId": _first(target.get("effectiveId"), record.get("effectiveId")),
        "ref": _first(target.get("ref"), record.get("targetRef")),
        "world": _world(_first(target.get("world"), target.get("worldPoint"), record.get("world"))),
        "distanceToPlayer": _first(target.get("distanceToPlayer"), target.get("distance")),
    }


def _snapshot_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("event_type") != "source_snapshot":
        return None
    high = _dict(event.get("high_value_fields"))
    player = _dict(high.get("player"))
    raw = event
    return {
        "elapsedSeconds": _float(event.get("elapsed_seconds")),
        "wallTimeUtc": event.get("wall_time_utc"),
        "tick": _int(_first(event.get("latest_tick"), high.get("latest_tick"))),
        "exportSequence": _int(_first(event.get("latest_export_sequence"), high.get("latest_export_sequence"))),
        "world": _world(player.get("worldPoint")),
        "plane": _int(_dict(player.get("worldPoint")).get("plane")),
        "player": player,
        "inventory": _dict(high.get("inventory")),
        "bank": _dict(high.get("bank")),
        "nearbyObjects": _list(high.get("nearby_objects")),
        "routeObjects": _list(high.get("route_objects")),
        "nearbyNpcs": _list(high.get("nearby_npcs")),
        "rawEvent": raw,
    }


def snapshots_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = [item for item in (_snapshot_from_event(event) for event in events) if item]
    return sorted(snapshots, key=lambda item: (_float(item.get("elapsedSeconds")) if _float(item.get("elapsedSeconds")) is not None else -1.0))


def _dedupe_world_path(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    last_key: tuple[int | None, int | None, int | None] | None = None
    for snapshot in snapshots:
        world = _world(snapshot.get("world"))
        if not world:
            continue
        key = (world.get("worldX"), world.get("worldY"), world.get("plane"))
        if key == last_key:
            continue
        last_key = key
        path.append(
            {
                "elapsedSeconds": snapshot.get("elapsedSeconds"),
                "tick": snapshot.get("tick"),
                "world": world,
            }
        )
    return path


def _movement_summary(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    world_path = _dedupe_world_path(snapshots)
    position_changes: list[dict[str, Any]] = []
    plane_changes: list[dict[str, Any]] = []
    distance_total = 0.0
    for before, after in zip(world_path, world_path[1:]):
        bw = _world(before.get("world"))
        aw = _world(after.get("world"))
        if not bw or not aw:
            continue
        distance = _distance(bw, aw) or 0.0
        distance_total += distance
        change = {
            "startTime": before.get("elapsedSeconds"),
            "endTime": after.get("elapsedSeconds"),
            "startTick": before.get("tick"),
            "endTick": after.get("tick"),
            "from": bw,
            "to": aw,
            "distance": distance,
        }
        position_changes.append(change)
        if bw.get("plane") != aw.get("plane"):
            plane_changes.append(change)
    return {
        "worldPath": world_path,
        "planeChanges": plane_changes,
        "positionChanges": position_changes,
        "distanceApprox": round(distance_total, 3),
    }


def _object_names(snapshots: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for snapshot in snapshots:
        for record in _list(snapshot.get("nearbyObjects")) + _list(snapshot.get("routeObjects")):
            name = _name(_dict(record))
            if name:
                counts[name.lower()] += 1
    return counts


def area_label(snapshots: list[dict[str, Any]]) -> str | None:
    counts = _object_names(snapshots)
    bank_score = sum(count for name, count in counts.items() if "bank booth" in name or "deposit box" in name or name == "bank")
    tree_score = sum(count for name, count in counts.items() if "tree" in name)
    if bank_score and bank_score >= max(1, tree_score):
        return "bank_area"
    if tree_score:
        return "woodcutting_area"
    planes = [_int(snapshot.get("plane")) for snapshot in snapshots if _int(snapshot.get("plane")) is not None]
    if planes:
        return f"plane_{planes[-1]}"
    return None


def _inventory_full(snapshot: dict[str, Any] | None) -> bool | None:
    inventory = _dict(_dict(snapshot).get("inventory"))
    if inventory.get("inventoryFull") is not None:
        return bool(inventory.get("inventoryFull"))
    free = _int(inventory.get("freeSlots"))
    if free is not None:
        return free <= 0
    return None


def _route_name(start_label: str | None, end_label: str | None) -> str | None:
    if start_label == "bank_area" and end_label == "woodcutting_area":
        return "Bank_to_Woodcutting_area"
    if start_label == "woodcutting_area" and end_label == "bank_area":
        return "woodcutting_area_to_bank"
    if start_label and end_label and start_label != end_label:
        return f"{start_label}_to_{end_label}"
    return "route_unknown"


def _nearest_snapshot_before(snapshots: list[dict[str, Any]], elapsed: float | None) -> dict[str, Any] | None:
    if elapsed is None:
        return None
    before = [snapshot for snapshot in snapshots if (_float(snapshot.get("elapsedSeconds")) is not None and float(snapshot["elapsedSeconds"]) <= elapsed)]
    return before[-1] if before else None


def _snapshots_after(
    snapshots: list[dict[str, Any]],
    elapsed: float | None,
    tick: int | None,
    *,
    window_ms: int = DEFAULT_POSTCONDITION_TIME_WINDOW_MS,
    tick_window: int = DEFAULT_POSTCONDITION_TICK_WINDOW,
) -> list[dict[str, Any]]:
    if elapsed is None and tick is None:
        return []
    result: list[dict[str, Any]] = []
    for snapshot in snapshots:
        snap_elapsed = _float(snapshot.get("elapsedSeconds"))
        snap_tick = _int(snapshot.get("tick"))
        if elapsed is not None and snap_elapsed is not None:
            delta_ms = (snap_elapsed - elapsed) * 1000.0
            if 0 <= delta_ms <= window_ms:
                result.append(snapshot)
                continue
        if tick is not None and snap_tick is not None:
            delta_tick = snap_tick - tick
            if 0 <= delta_tick <= tick_window:
                result.append(snapshot)
    return sorted(result, key=lambda item: (_float(item.get("elapsedSeconds")) if _float(item.get("elapsedSeconds")) is not None else 0.0))


def _bank_open(snapshot: dict[str, Any]) -> bool | None:
    bank = _dict(snapshot.get("bank"))
    for key in ("bankOpen", "open", "isOpen"):
        if bank.get(key) is not None:
            return bool(bank.get(key))
    raw = _dict(snapshot.get("rawEvent"))
    text = json.dumps(_dict(raw.get("high_value_fields")).get("bank") or {}, default=str).lower()
    if "bank" in text and "open" in text:
        return True
    return None


def _semantic_type(action: str | None, target_name: str | None, classification: str | None = None) -> str:
    action_l = str(action or "").lower()
    name_l = str(target_name or "").lower()
    if classification == "world_walk_click" or action_l in {"walk", "walk here"}:
        return "walk"
    if "bank" in action_l or "bank booth" in name_l or "deposit box" in name_l:
        return "bank_interaction"
    if "tree" in name_l and "chop" in action_l:
        return "task_interaction"
    if any(word in name_l for word in ("ladder", "staircase", "stairs", "trapdoor", "door")) or action_l in {"open", "close", "climb", "climb-up", "climb-down", "enter", "exit"}:
        return "object_action"
    if classification == "menu_selection_click":
        return "menu_selection"
    return "unknown"


def _is_climb(action: str | None, target_name: str | None) -> bool:
    text = f"{action or ''} {target_name or ''}".lower()
    return any(word in text for word in ("climb", "ladder", "staircase", "stairs", "trapdoor"))


def _is_door(action: str | None, target_name: str | None) -> bool:
    text = f"{action or ''} {target_name or ''}".lower()
    return "door" in text or "open" in text


def _is_bank(action: str | None, target_name: str | None) -> bool:
    text = f"{action or ''} {target_name or ''}".lower()
    return "bank" in text or "deposit box" in text


def _postcondition(
    step: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    window_ms: int = DEFAULT_POSTCONDITION_TIME_WINDOW_MS,
    tick_window: int = DEFAULT_POSTCONDITION_TICK_WINDOW,
) -> tuple[dict[str, Any], str, float, list[str], list[str]]:
    warnings: list[str] = []
    evidence: list[str] = []
    elapsed = _float(step.get("startTime"))
    tick = _int(step.get("startTick"))
    action = step.get("action")
    target_name = step.get("targetName")
    if window_ms == DEFAULT_POSTCONDITION_TIME_WINDOW_MS and tick_window == DEFAULT_POSTCONDITION_TICK_WINDOW:
        if _is_climb(action, target_name):
            window_ms = CLIMB_POSTCONDITION_TIME_WINDOW_MS
        elif _is_door(action, target_name):
            window_ms = DOOR_POSTCONDITION_TIME_WINDOW_MS
            tick_window = DOOR_POSTCONDITION_TICK_WINDOW
        elif step.get("type") == "walk":
            window_ms = WALK_POSTCONDITION_TIME_WINDOW_MS
    before = _nearest_snapshot_before(snapshots, elapsed)
    afters = _snapshots_after(snapshots, elapsed, tick, window_ms=window_ms, tick_window=tick_window)
    before_world = _world(_dict(before).get("world"))
    after_world = _world(_dict(afters[-1]).get("world")) if afters else None
    plane_changed = bool(before_world and any((_world(item.get("world")) or {}).get("plane") != before_world.get("plane") for item in afters))
    position_changed = bool(before_world and any((_distance(before_world, _world(item.get("world"))) or 0.0) >= 1.0 for item in afters))
    distance_moved = 0.0
    if before_world and afters:
        distances = [_distance(before_world, _world(item.get("world"))) or 0.0 for item in afters]
        distance_moved = max(distances) if distances else 0.0
    target_world = _world(_dict(step.get("target") or {}).get("world") or step.get("world"))
    arrived_near = bool(target_world and any((_distance(target_world, _world(item.get("world"))) or 999.0) <= 2.0 for item in afters))
    bank_open = bool(any(_bank_open(item) is True for item in afters))
    inventory_changed = False
    if before and afters:
        before_inv = _dict(before.get("inventory"))
        after_inv = _dict(afters[-1].get("inventory"))
        inventory_changed = bool(before_inv and after_inv and before_inv != after_inv)

    post = {
        "positionChanged": position_changed,
        "planeChanged": plane_changed,
        "arrivedNearTarget": arrived_near,
        "widgetOpened": bank_open,
        "inventoryChanged": inventory_changed,
        "tickDelta": (_int(_dict(afters[-1]).get("tick")) - _int(_dict(before).get("tick"))) if afters and _int(_dict(afters[-1]).get("tick")) is not None and _int(_dict(before).get("tick")) is not None else None,
        "timeDeltaMs": round(((_float(_dict(afters[-1]).get("elapsedSeconds")) or 0.0) - (_float(_dict(before).get("elapsedSeconds")) or 0.0)) * 1000.0, 3) if afters and _float(_dict(before).get("elapsedSeconds")) is not None and _float(_dict(afters[-1]).get("elapsedSeconds")) is not None else None,
        "distanceMoved": round(distance_moved, 3),
        "areaBefore": area_label([before]) if before else None,
        "areaAfter": area_label([afters[-1]]) if afters else None,
        "beforeSnapshot": {
            "elapsedSeconds": _dict(before).get("elapsedSeconds"),
            "tick": _dict(before).get("tick"),
            "world": before_world,
        },
        "afterSnapshot": {
            "elapsedSeconds": _dict(afters[-1]).get("elapsedSeconds") if afters else None,
            "tick": _dict(afters[-1]).get("tick") if afters else None,
            "world": after_world,
        },
    }

    quality = str(_dict(step.get("targetQuality")).get("quality") or "").lower()
    base_conf = {"strong": 0.82, "medium": 0.66, "weak": 0.42}.get(quality, 0.45)
    result = "unknown"
    confidence = base_conf
    if _is_climb(action, target_name):
        if plane_changed:
            result = "success"
            confidence = max(confidence, 0.92)
            evidence.append("plane changed after climb-style action")
        elif position_changed:
            result = "partial"
            confidence = max(confidence, 0.62)
            warnings.append("climb-style action had position change but no plane change in window")
        else:
            result = "partial"
            confidence = min(confidence, 0.5)
            warnings.append("climb-style action lacked plane/position postcondition")
    elif _is_bank(action, target_name):
        if bank_open:
            result = "success"
            confidence = max(confidence, 0.9)
            evidence.append("bank/widget state opened after bank interaction")
        else:
            result = "partial" if quality in {"strong", "medium"} else "unknown"
            confidence = min(max(confidence, 0.58), 0.7)
            warnings.append("bank state/widget proof missing")
    elif _is_door(action, target_name):
        if plane_changed or position_changed or arrived_near:
            result = "success"
            confidence = max(confidence, 0.78)
            evidence.append("world position or plane changed after door/open action")
        else:
            result = "partial" if quality in {"strong", "medium"} else "unknown"
            warnings.append("door/open action lacks clear movement-through evidence")
    elif step.get("type") == "walk":
        if position_changed:
            result = "success"
            confidence = max(confidence, 0.75)
            evidence.append("player position changed during walk segment")
        else:
            result = "partial"
            warnings.append("walk step lacks position delta")
    elif step.get("type") == "task_interaction":
        result = "partial"
        confidence = max(confidence, 0.55)
        warnings.append("task interaction observed inside traversal recording")
    else:
        if plane_changed or position_changed:
            result = "success"
            confidence = max(confidence, 0.72)
            evidence.append("world position/plane changed after action")
        elif quality in {"strong", "medium"}:
            result = "partial"
            warnings.append("target evidence is strong but postcondition is unclear")

    return post, result, round(confidence, 3), evidence, warnings


def _target_quality_by_seq(rows: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {row.get("eventSeq"): row for row in rows if row.get("eventSeq") is not None}


def _menu_by_seq(rows: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {row.get("eventSeq"): row for row in rows if row.get("eventSeq") is not None}


def _joined_by_seq(rows: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    result = {}
    for row in rows:
        seq = _dict(row.get("inputEvent")).get("event_seq")
        if seq is not None:
            result[seq] = row
    return result


def _action_steps(
    target_quality_rows: list[dict[str, Any]],
    menu_interactions: list[dict[str, Any]],
    joined_rows: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    menu_by_seq = _menu_by_seq(menu_interactions)
    joined_by_seq = _joined_by_seq(joined_rows)
    steps: list[dict[str, Any]] = []
    for row in target_quality_rows:
        seq = row.get("eventSeq")
        menu = _dict(menu_by_seq.get(seq))
        joined = _dict(joined_by_seq.get(seq))
        target = _record_target(row)
        menu_selection = _dict(menu.get("menuSelection"))
        selected_option = _first(menu.get("option"), menu_selection.get("selectedOption"), _dict(row.get("menuSelectionQuality")).get("selectedOption"))
        selected_target = _first(menu.get("target"), menu_selection.get("selectedTarget"), _dict(row.get("menuSelectionQuality")).get("selectedTarget"))
        action = _clean(_first(selected_option, target.get("action"), row.get("targetAction")))
        target_name = _clean(_first(selected_target, target.get("name"), row.get("targetName")))
        classification = _clean(row.get("classification") or _dict(joined.get("classification")).get("classification"))
        event = _dict(joined.get("inputEvent")) or _dict(menu.get("clickEvent"))
        elapsed = _float(_first(event.get("elapsed_seconds"), joined.get("elapsedSeconds"), row.get("elapsedSeconds")))
        before = _nearest_snapshot_before(snapshots, elapsed)
        step = {
            "stepIndex": 0,
            "type": _semantic_type(action, target_name, classification),
            "startTime": elapsed,
            "endTime": None,
            "startTick": _int(_first(event.get("nearest_tick"), row.get("tick"), _dict(before).get("tick"))),
            "endTick": None,
            "action": action,
            "targetName": target_name,
            "targetKind": target.get("kind"),
            "targetId": _first(target.get("effectiveId"), target.get("rawId")),
            "target": target,
            "world": target.get("world"),
            "inputEventSeq": seq,
            "inputClassification": classification,
            "clickPolicyUsed": _first(_dict(joined.get("inputPathIntegrity")).get("clickPolicyUsed"), _dict(joined.get("inputEvent")).get("clickPolicyUsed")),
            "menuSelection": {
                "present": bool(menu),
                "selectedRowIndex": _first(menu.get("selectedRowIndex"), menu_selection.get("selectedRowIndex")),
                "option": action,
                "target": target_name,
                "rowBoundsPresent": bool(menu.get("rowBoundsPresent") or menu_selection.get("rowBounds")),
                "insideRowBounds": _first(menu.get("insideRowBounds"), menu_selection.get("insideRowBounds")),
                "rowGeometrySource": _first(menu.get("rowGeometrySource"), menu_selection.get("rowGeometrySource")),
                "selectedSnapshotId": _first(menu.get("selectedSnapshotId"), menu_selection.get("selectedSnapshotId")),
            },
            "targetQuality": {
                "quality": row.get("quality"),
                "score": row.get("score"),
                "warnings": row.get("warnings") or [],
                "evidence": row.get("evidence") or {},
            },
            "coordinateTransform": _first(
                joined.get("coordinateTransformUsed"),
                _dict(joined.get("classification")).get("coordinateTransformUsed"),
                menu_selection.get("coordinateTransformUsed"),
            ),
            "postcondition": {},
            "result": "unknown",
            "confidence": 0.0,
            "evidence": [],
            "warnings": [],
        }
        post, result, confidence, evidence, warnings = _postcondition(step, snapshots)
        step["postcondition"] = post
        step["endTime"] = _dict(post.get("afterSnapshot")).get("elapsedSeconds")
        step["endTick"] = _dict(post.get("afterSnapshot")).get("tick")
        step["result"] = result
        step["confidence"] = confidence
        step["evidence"] = evidence
        step["warnings"] = sorted(set(warnings + list(row.get("warnings") or [])))
        steps.append(step)
    return steps


def _nearest_route_object_for_plane_change(change: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    start_time = _float(change.get("startTime"))
    before = _nearest_snapshot_before(snapshots, start_time)
    if not before:
        return None
    before_plane = _int(_dict(change.get("from")).get("plane"))
    after_plane = _int(_dict(change.get("to")).get("plane"))
    direction = "up" if before_plane is not None and after_plane is not None and after_plane > before_plane else "down"
    candidates = []
    for record in _list(before.get("routeObjects")) + _list(before.get("nearbyObjects")):
        item = _dict(record)
        name = (_name(item) or "").lower()
        actions = [action.lower() for action in _actions(item)]
        if not any(word in name for word in ("stair", "ladder", "trapdoor")):
            continue
        action_match = any(direction in action or "climb" in action for action in actions)
        if not action_match:
            continue
        distance = _float(item.get("distance"))
        candidates.append((distance if distance is not None else 999.0, item))
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1] if candidates else None


def _plane_transition_steps(movement: dict[str, Any], snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for change in _list(movement.get("planeChanges")):
        route_object = _nearest_route_object_for_plane_change(_dict(change), snapshots)
        actions = _actions(_dict(route_object))
        before_plane = _int(_dict(change.get("from")).get("plane"))
        after_plane = _int(_dict(change.get("to")).get("plane"))
        direction = "up" if before_plane is not None and after_plane is not None and after_plane > before_plane else "down"
        action = next((item for item in actions if direction in item.lower()), None) or next((item for item in actions if "climb" in item.lower()), None) or f"plane_{direction}"
        target_name = _name(_dict(route_object))
        steps.append(
            {
                "stepIndex": 0,
                "type": "plane_transition",
                "startTime": change.get("startTime"),
                "endTime": change.get("endTime"),
                "startTick": change.get("startTick"),
                "endTick": change.get("endTick"),
                "action": action,
                "targetName": target_name,
                "targetKind": "object" if route_object else "unknown",
                "targetId": _target_id(_dict(route_object)),
                "world": _world(_dict(route_object).get("worldPoint")),
                "inputEventSeq": None,
                "menuSelection": {},
                "targetQuality": {},
                "coordinateTransform": None,
                "postcondition": {
                    "positionChanged": bool((_distance(change.get("from"), change.get("to")) or 0.0) > 0),
                    "planeChanged": True,
                    "arrivedNearTarget": False,
                    "widgetOpened": False,
                    "inventoryChanged": False,
                    "beforeSnapshot": {"world": change.get("from"), "tick": change.get("startTick"), "elapsedSeconds": change.get("startTime")},
                    "afterSnapshot": {"world": change.get("to"), "tick": change.get("endTick"), "elapsedSeconds": change.get("endTime")},
                },
                "result": "success",
                "confidence": 0.86 if route_object else 0.74,
                "evidence": [f"player plane changed {before_plane} -> {after_plane}"],
                "warnings": [] if route_object else ["plane transition target inferred from movement only"],
            }
        )
    return steps


def _walk_steps(movement: dict[str, Any]) -> list[dict[str, Any]]:
    changes = _list(movement.get("positionChanges"))
    if not changes:
        return []
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for change in changes:
        before = _dict(change.get("from"))
        after = _dict(change.get("to"))
        if before.get("plane") != after.get("plane"):
            if current:
                steps.append(current)
                current = None
            continue
        if current is None:
            current = {
                "stepIndex": 0,
                "type": "walk",
                "startTime": change.get("startTime"),
                "endTime": change.get("endTime"),
                "startTick": change.get("startTick"),
                "endTick": change.get("endTick"),
                "action": "Walk",
                "targetName": None,
                "targetKind": "ground",
                "targetId": None,
                "world": after,
                "inputEventSeq": None,
                "menuSelection": {},
                "targetQuality": {},
                "coordinateTransform": None,
                "postcondition": {
                    "positionChanged": True,
                    "planeChanged": False,
                    "arrivedNearTarget": False,
                    "widgetOpened": False,
                    "inventoryChanged": False,
                    "beforeSnapshot": {"world": before, "tick": change.get("startTick"), "elapsedSeconds": change.get("startTime")},
                    "afterSnapshot": {"world": after, "tick": change.get("endTick"), "elapsedSeconds": change.get("endTime")},
                },
                "result": "success",
                "confidence": 0.72,
                "evidence": ["player world position changed"],
                "warnings": [],
                "_distance": 0.0,
            }
        current["endTime"] = change.get("endTime")
        current["endTick"] = change.get("endTick")
        current["world"] = after
        current["postcondition"]["afterSnapshot"] = {"world": after, "tick": change.get("endTick"), "elapsedSeconds": change.get("endTime")}
        current["_distance"] = float(current.get("_distance") or 0.0) + float(change.get("distance") or 0.0)
    if current:
        steps.append(current)
    for step in steps:
        step["distanceApprox"] = round(float(step.pop("_distance", 0.0)), 3)
    return steps


def _phase(status: str, steps: list[dict[str, Any]], movement: dict[str, Any], start_label: str | None, end_label: str | None) -> str:
    if status == "FAIL":
        return "failed"
    if start_label and end_label and start_label != end_label:
        return "arrived" if status == "PASS" else "partial"
    if any(step.get("type") == "plane_transition" for step in steps):
        return "traversing" if status == "PASS" else "partial"
    if movement.get("positionChanges"):
        return "moving"
    return "idle" if steps else "unknown"


def _step_time(step: dict[str, Any]) -> float:
    value = _float(step.get("startTime"))
    return value if value is not None else 1e12


def _step_tick(step: dict[str, Any]) -> int | None:
    return _int(_first(step.get("startTick"), step.get("endTick")))


def _quality_tier(step: dict[str, Any]) -> str:
    return str(_dict(step.get("targetQuality")).get("quality") or "").lower()


def _has_progress_postcondition(step: dict[str, Any]) -> bool:
    post = _dict(step.get("postcondition"))
    return bool(
        post.get("positionChanged")
        or post.get("planeChanged")
        or post.get("arrivedNearTarget")
        or post.get("widgetOpened")
        or post.get("inventoryChanged")
    )


def _is_task(step: dict[str, Any]) -> bool:
    return step.get("type") == "task_interaction" or ("tree" in str(step.get("targetName") or "").lower() and "chop" in str(step.get("action") or "").lower())


def _is_route_target(step: dict[str, Any]) -> bool:
    return _is_climb(step.get("action"), step.get("targetName")) or _is_door(step.get("action"), step.get("targetName")) or _is_bank(step.get("action"), step.get("targetName"))


def _is_route_context(route_name: str | None) -> bool:
    return route_name in {"Bank_to_Woodcutting_area", "woodcutting_area_to_bank"}


def _assign_raw_step_ids(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        item = deepcopy(step)
        item["stepIndex"] = index
        item["rawStepId"] = item.get("rawStepId") or f"raw_step_{index:03d}"
        raw_steps.append(item)
    return raw_steps


def _support_ref(step: dict[str, Any], role: str = "supporting") -> dict[str, Any]:
    return {
        "evidenceId": step.get("rawStepId"),
        "role": role,
        "type": step.get("type"),
        "startTime": step.get("startTime"),
        "endTime": step.get("endTime"),
        "inputEventSeq": step.get("inputEventSeq"),
        "action": step.get("action"),
        "targetName": step.get("targetName"),
        "result": step.get("result"),
        "confidence": step.get("confidence"),
    }


def _review_ref(step: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "evidenceId": step.get("rawStepId"),
        "rawStepId": step.get("rawStepId"),
        "progressRole": "review_only",
        "type": step.get("type"),
        "startTime": step.get("startTime"),
        "endTime": step.get("endTime"),
        "inputEventSeq": step.get("inputEventSeq"),
        "action": step.get("action"),
        "targetName": step.get("targetName"),
        "targetQuality": _dict(step.get("targetQuality")),
        "menuSelection": _dict(step.get("menuSelection")),
        "result": step.get("result"),
        "confidence": step.get("confidence"),
        "reviewReason": reason,
        "warnings": _list(step.get("warnings")),
    }


def _review_reason(step: dict[str, Any], *, route_name: str | None, has_proven_route_progress: bool) -> str | None:
    quality = _quality_tier(step)
    if quality in {"weak", "unmatched"}:
        return "weak or unmatched target quality"
    if _is_task(step):
        return "task interaction is not route progress for this route"
    result = str(step.get("result") or "unknown")
    has_postcondition = _has_progress_postcondition(step)
    if _is_bank(step.get("action"), step.get("targetName")) and result != "success" and (_is_route_context(route_name) or has_proven_route_progress):
        return "bank context did not produce a route-progress postcondition"
    if step.get("type") == "menu_selection" and not _is_route_target(step) and result != "success":
        return "menu selection is supporting/review evidence, not route progress"
    if _is_route_target(step) and result in {"partial", "unknown"} and has_proven_route_progress and not has_postcondition:
        return "route target evidence lacked a matching movement, plane, or widget postcondition"
    return None


def _nearby_plane_support(action_step: dict[str, Any], raw_steps: list[dict[str, Any]], used_raw_ids: set[str]) -> dict[str, Any] | None:
    if action_step.get("type") == "plane_transition":
        return None
    if not _is_climb(action_step.get("action"), action_step.get("targetName")) or action_step.get("result") != "success":
        return None
    action_time = _float(action_step.get("startTime"))
    action_tick = _step_tick(action_step)
    action_text = str(action_step.get("action") or "").lower()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for step in raw_steps:
        raw_id = str(step.get("rawStepId") or "")
        if raw_id in used_raw_ids or step.get("type") != "plane_transition":
            continue
        before_plane = _int(_dict(_dict(step.get("postcondition")).get("beforeSnapshot")).get("world", {}).get("plane"))
        after_plane = _int(_dict(_dict(step.get("postcondition")).get("afterSnapshot")).get("world", {}).get("plane"))
        if before_plane is not None and after_plane is not None:
            if "up" in action_text and after_plane <= before_plane:
                continue
            if "down" in action_text and after_plane >= before_plane:
                continue
        step_end_time = _float(step.get("endTime"))
        if action_time is not None and step_end_time is not None and step_end_time < action_time - 0.1:
            continue
        time_delta = None
        step_time = _float(step.get("startTime"))
        if action_time is not None and step_time is not None:
            time_delta = abs(step_time - action_time) * 1000.0
            if time_delta <= CLIMB_POSTCONDITION_TIME_WINDOW_MS:
                candidates.append((time_delta, step))
                continue
        step_tick = _step_tick(step)
        if action_tick is not None and step_tick is not None:
            tick_delta = abs(step_tick - action_tick)
            if tick_delta <= DEFAULT_POSTCONDITION_TICK_WINDOW:
                candidates.append((float(tick_delta * 600), step))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else None


def _make_grouped_step(raw_step: dict[str, Any], index: int, reason: str) -> dict[str, Any]:
    step = deepcopy(raw_step)
    before_confidence = float(step.get("confidence") or 0.0)
    after_confidence = before_confidence
    if step.get("result") == "success":
        after_confidence = min(1.0, max(after_confidence, before_confidence + 0.03))
    step.update(
        {
            "stepId": f"route_step_{index:03d}",
            "rawStepIds": [step.get("rawStepId")],
            "primaryEvidenceId": step.get("rawStepId"),
            "supportingEvidence": [_support_ref(raw_step, "primary")],
            "reviewEvidence": [],
            "groupingReason": reason,
            "progressRole": "route_progress",
            "stepConfidenceBeforeGrouping": round(before_confidence, 3),
            "stepConfidenceAfterGrouping": round(after_confidence, 3),
            "confidence": round(after_confidence, 3),
        }
    )
    return step


def _group_route_steps(raw_steps: list[dict[str, Any]], *, route_name: str | None) -> dict[str, Any]:
    raw_steps = sorted(raw_steps, key=lambda item: (_step_time(item), str(item.get("type") or "")))
    proven_progress = any(step.get("result") == "success" and (step.get("type") in {"walk", "plane_transition"} or _has_progress_postcondition(step)) for step in raw_steps)
    planned_plane_support: dict[str, dict[str, Any]] = {}
    planned_support_ids: set[str] = set()
    for raw in raw_steps:
        support = _nearby_plane_support(raw, raw_steps, set())
        if support:
            planned_plane_support[str(raw.get("rawStepId") or "")] = support
            planned_support_ids.add(str(support.get("rawStepId") or ""))
    grouped: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    used_raw_ids: set[str] = set()
    groups_created = 0
    for raw in raw_steps:
        raw_id = str(raw.get("rawStepId") or "")
        if raw_id in used_raw_ids:
            continue
        if raw_id in planned_support_ids:
            continue
        reason = _review_reason(raw, route_name=route_name, has_proven_route_progress=proven_progress)
        if reason:
            review.append(_review_ref(raw, reason))
            used_raw_ids.add(raw_id)
            continue
        group_reason = "retained as route-progress evidence"
        if raw.get("type") == "walk":
            group_reason = "world movement grouped as a walk segment"
        elif raw.get("type") == "plane_transition":
            group_reason = "plane change retained as route transition"
        elif _is_door(raw.get("action"), raw.get("targetName")) and raw.get("result") == "success":
            group_reason = "door/open action grouped with movement postcondition"
        elif _is_climb(raw.get("action"), raw.get("targetName")) and raw.get("result") == "success":
            group_reason = "climb action grouped with plane/position postcondition"
        groups_created += 1
        grouped_step = _make_grouped_step(raw, groups_created, group_reason)
        used_raw_ids.add(raw_id)
        plane_support = planned_plane_support.get(raw_id) or _nearby_plane_support(raw, raw_steps, used_raw_ids)
        if plane_support:
            support_id = str(plane_support.get("rawStepId") or "")
            used_raw_ids.add(support_id)
            grouped_step["rawStepIds"].append(support_id)
            grouped_step["supportingEvidence"].append(_support_ref(plane_support, "postcondition"))
            grouped_step["groupingReason"] = "climb action and plane transition grouped into one route step"
            grouped_step["stepConfidenceAfterGrouping"] = min(1.0, max(float(grouped_step.get("stepConfidenceAfterGrouping") or 0.0), 0.94))
            grouped_step["confidence"] = grouped_step["stepConfidenceAfterGrouping"]
        grouped.append(grouped_step)

    for index, step in enumerate(grouped, start=1):
        step["stepIndex"] = index
        step["stepId"] = f"route_step_{index:03d}"

    raw_partial = sum(1 for step in raw_steps if step.get("result") in {"partial", "unknown"})
    grouped_partial = sum(1 for step in grouped if step.get("result") in {"partial", "unknown"})
    grouping_warnings = []
    if review:
        grouping_warnings.append(f"{len(review)} raw evidence item(s) kept for review instead of route progress")
    return {
        "groupedSteps": grouped,
        "reviewEvidence": review,
        "supportingEvidenceCount": sum(len(_list(step.get("supportingEvidence"))) for step in grouped),
        "grouping": {
            "schema": GROUPING_SCHEMA_VERSION,
            "status": "PASS" if grouped else ("WARN" if raw_steps else "FAIL"),
            "groupsCreated": len(grouped),
            "stepsMerged": max(0, len(raw_steps) - len(grouped)),
            "partialStepsResolved": max(0, raw_partial - grouped_partial),
            "warnings": grouping_warnings,
        },
    }


def _world_label(world: Any) -> dict[str, Any] | None:
    value = _world(world)
    return value if value else None


def _segment_type(step: dict[str, Any]) -> str:
    step_type = str(step.get("type") or "")
    action = step.get("action")
    target = step.get("targetName")
    text = f"{action or ''} {target or ''}".lower()
    if step_type == "walk":
        return "walk_segment"
    if "door" in text or "open" in text:
        return "door_transition"
    if "ladder" in text:
        return "ladder_transition"
    if "stair" in text or "trapdoor" in text or "climb" in text or step_type == "plane_transition":
        return "stair_transition" if "stair" in text or "climb" in text else "plane_transition"
    if _is_bank(action, target):
        return "bank_context"
    if _is_task(step):
        return "task_context"
    return step_type or "unknown"


def _postcondition_type(post: dict[str, Any]) -> str:
    if post.get("planeChanged"):
        return "plane_change"
    if post.get("positionChanged") or post.get("arrivedNearTarget"):
        return "movement"
    if post.get("widgetOpened"):
        return "widget_open"
    if post.get("inventoryChanged"):
        return "inventory_change"
    return "none"


def _route_segments(steps: list[dict[str, Any]], lifecycle_start: dict[str, Any], lifecycle_end: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    start_area = lifecycle_start.get("areaLabel")
    end_area = lifecycle_end.get("areaLabel")
    segments.append(
        {
            "segmentIndex": 1,
            "segmentType": "area_start",
            "label": f"Start: {start_area or 'unknown'}",
            "startWorld": lifecycle_start.get("world"),
            "endWorld": lifecycle_start.get("world"),
            "startPlane": lifecycle_start.get("plane"),
            "endPlane": lifecycle_start.get("plane"),
            "primaryAction": None,
            "postcondition": {"type": "area_start", "result": "success" if lifecycle_start.get("world") else "unknown"},
            "evidenceRefs": [],
            "confidence": 0.85 if lifecycle_start.get("world") else 0.25,
            "warnings": [],
        }
    )
    for step in steps:
        post = _dict(step.get("postcondition"))
        before = _dict(post.get("beforeSnapshot"))
        after = _dict(post.get("afterSnapshot"))
        target_quality = _dict(step.get("targetQuality"))
        label_bits = [str(item) for item in (step.get("action"), step.get("targetName")) if item not in (None, "")]
        segments.append(
            {
                "segmentIndex": len(segments) + 1,
                "segmentType": _segment_type(step),
                "label": " ".join(label_bits) if label_bits else str(step.get("type") or "Route step"),
                "startWorld": _world_label(before.get("world")),
                "endWorld": _world_label(after.get("world")),
                "startPlane": _int(_dict(before.get("world")).get("plane")),
                "endPlane": _int(_dict(after.get("world")).get("plane")),
                "primaryAction": {
                    "option": step.get("action"),
                    "target": step.get("targetName"),
                    "targetQuality": target_quality.get("quality"),
                },
                "postcondition": {"type": _postcondition_type(post), "result": step.get("result")},
                "evidenceRefs": _list(step.get("rawStepIds")),
                "confidence": step.get("confidence"),
                "warnings": _list(step.get("warnings")),
            }
        )
    segments.append(
        {
            "segmentIndex": len(segments) + 1,
            "segmentType": "area_arrival",
            "label": f"Arrive: {end_area or 'unknown'}",
            "startWorld": lifecycle_end.get("world"),
            "endWorld": lifecycle_end.get("world"),
            "startPlane": lifecycle_end.get("plane"),
            "endPlane": lifecycle_end.get("plane"),
            "primaryAction": None,
            "postcondition": {"type": "area_arrival", "result": "success" if lifecycle_end.get("world") else "unknown"},
            "evidenceRefs": [],
            "confidence": 0.88 if lifecycle_end.get("world") else 0.25,
            "warnings": [],
        }
    )
    for index, segment in enumerate(segments, start=1):
        segment["segmentIndex"] = index
    return segments


def analyze_data(
    *,
    events: list[dict[str, Any]] | None = None,
    joined_input_telemetry: list[dict[str, Any]] | None = None,
    input_action_classifications: list[dict[str, Any]] | None = None,
    target_match_quality: list[dict[str, Any]] | None = None,
    menu_interactions: list[dict[str, Any]] | None = None,
    summaries: dict[str, Any] | None = None,
    recording_path: str | Path | None = None,
) -> dict[str, Any]:
    events = events or []
    summaries = summaries or {}
    snapshots = snapshots_from_events(events)
    movement = _movement_summary(snapshots)
    start_snapshots = snapshots[:3] if snapshots else []
    end_snapshots = snapshots[-3:] if snapshots else []
    start_snapshot = snapshots[0] if snapshots else {}
    end_snapshot = snapshots[-1] if snapshots else {}
    start_label = area_label(start_snapshots)
    end_label = area_label(end_snapshots)
    route_name = _route_name(start_label, end_label)
    raw_candidates = []
    raw_candidates.extend(_walk_steps(movement))
    raw_candidates.extend(_action_steps(target_match_quality or [], menu_interactions or [], joined_input_telemetry or [], snapshots))
    raw_candidates.extend(_plane_transition_steps(movement, snapshots))
    raw_candidates.sort(key=lambda item: (_float(item.get("startTime")) if _float(item.get("startTime")) is not None else 1e12, str(item.get("type") or "")))
    raw_steps = _assign_raw_step_ids(raw_candidates)
    grouping_result = _group_route_steps(raw_steps, route_name=route_name)
    steps = _list(grouping_result.get("groupedSteps"))
    review_evidence = _list(grouping_result.get("reviewEvidence"))
    grouping = _dict(grouping_result.get("grouping"))
    supporting_evidence_count = int(grouping_result.get("supportingEvidenceCount") or 0)

    result_counts = Counter(str(step.get("result") or "unknown") for step in steps)
    warnings: list[str] = []
    if not snapshots:
        warnings.append("no source snapshots available")
    if not steps and movement.get("positionChanges"):
        warnings.append("movement observed but no traversal steps extracted")
    if result_counts.get("failed"):
        warnings.append("one or more traversal steps failed")
    partial_count = result_counts.get("partial", 0) + result_counts.get("unknown", 0)
    if partial_count:
        warnings.append(f"{partial_count} traversal step(s) have partial or unknown postconditions")
    if _dict(summaries.get("menu_interaction_summary")).get("menuSelectionsMissingRowGeometryCount"):
        warnings.append("some menu selections lack row geometry, but traversal can still pass with target/postcondition evidence")
    if _dict(summaries.get("target_match_summary")).get("weakMatchCount"):
        warnings.append("weak target quality matches present")

    if not snapshots:
        status = "FAIL"
    elif steps and not result_counts.get("failed") and (movement.get("positionChanges") or result_counts.get("success")):
        status = "PASS" if result_counts.get("success", 0) >= max(1, partial_count) else "WARN"
    elif movement.get("positionChanges"):
        status = "PASS"
    else:
        status = "WARN"

    confidences = [float(step.get("confidence") or 0.0) for step in steps if step.get("confidence") is not None]
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else (0.55 if movement.get("positionChanges") else 0.0)
    evidence = []
    if route_name and route_name != "route_unknown":
        evidence.append(f"route inferred from {start_label} to {end_label}")
    if movement.get("planeChanges"):
        evidence.append(f"{len(movement.get('planeChanges') or [])} plane transition(s) observed")
    if movement.get("distanceApprox"):
        evidence.append(f"player moved about {movement.get('distanceApprox')} tile(s)")

    ticks = [_int(snapshot.get("tick")) for snapshot in snapshots if _int(snapshot.get("tick")) is not None]
    duration = None
    if snapshots:
        duration = _first(
            _dict(summaries.get("summary")).get("duration_seconds"),
            (_float(snapshots[-1].get("elapsedSeconds")) or 0.0) - (_float(snapshots[0].get("elapsedSeconds")) or 0.0),
        )
    start_info = {
        "world": _world(start_snapshot.get("world")),
        "plane": _int(start_snapshot.get("plane")),
        "areaLabel": start_label,
        "inventoryFull": _inventory_full(start_snapshot),
    }
    end_info = {
        "world": _world(end_snapshot.get("world")),
        "plane": _int(end_snapshot.get("plane")),
        "areaLabel": end_label,
        "inventoryFull": _inventory_full(end_snapshot),
    }
    route_segments = _route_segments(steps, start_info, end_info) if snapshots else []
    segment_counts = Counter(str(_dict(segment.get("postcondition")).get("result") or "unknown") for segment in route_segments)

    lifecycle = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "routeName": route_name,
        "phase": _phase(status, steps, movement, start_label, end_label),
        "confidence": confidence,
        "tickRange": {"start": min(ticks) if ticks else None, "end": max(ticks) if ticks else None},
        "durationSeconds": round(float(duration), 3) if isinstance(duration, (int, float)) else duration,
        "start": start_info,
        "end": end_info,
        "movement": movement,
        "rawSteps": raw_steps,
        "steps": steps,
        "groupedSteps": steps,
        "routeSegments": route_segments,
        "reviewEvidence": review_evidence,
        "rawStepCount": len(raw_steps),
        "groupedStepCount": len(steps),
        "supportingEvidenceCount": supporting_evidence_count,
        "reviewEvidenceCount": len(review_evidence),
        "routeSegmentCount": len(route_segments),
        "successfulSegmentCount": segment_counts.get("success", 0),
        "partialSegmentCount": segment_counts.get("partial", 0),
        "unknownSegmentCount": segment_counts.get("unknown", 0),
        "grouping": grouping,
        "stepCount": len(steps),
        "successfulStepCount": result_counts.get("success", 0),
        "partialStepCount": result_counts.get("partial", 0),
        "failedStepCount": result_counts.get("failed", 0),
        "unknownStepCount": result_counts.get("unknown", 0),
        "warnings": sorted(set(warnings)),
        "evidence": evidence,
    }
    if recording_path:
        lifecycle["recordingPath"] = str(Path(recording_path))
    return lifecycle


def analyze_events(events: list[dict[str, Any]], *, summaries: dict[str, Any] | None = None) -> dict[str, Any]:
    return analyze_data(events=events, summaries=summaries)


def analyze_recording(path: str | Path) -> dict[str, Any]:
    recording = Path(path)
    events = _jsonl(recording / "events.jsonl")
    joined = _jsonl(recording / "joined_input_telemetry.jsonl")
    classifications = _jsonl(recording / "input_action_classifications.jsonl")
    target_quality = _jsonl(recording / "target_match_quality.jsonl")
    menu_rows = _jsonl(recording / "menu_interactions.jsonl")
    summaries = {
        "summary": _json(recording / "summary.json"),
        "input_action_summary": _json(recording / "input_action_summary.json"),
        "target_match_summary": _json(recording / "target_match_summary.json"),
        "menu_interaction_summary": _json(recording / "menu_interaction_summary.json"),
        "coordinate_alignment_summary": _json(recording / "coordinate_alignment_summary.json"),
        "camera_behavior_summary": _json(recording / "camera_behavior_summary.json"),
        "vm_mouse_arduino_mapping": _json(recording / "vm_mouse_arduino_mapping.json"),
    }
    lifecycle = analyze_data(
        events=events,
        joined_input_telemetry=joined,
        input_action_classifications=classifications,
        target_match_quality=target_quality,
        menu_interactions=menu_rows,
        summaries=summaries,
        recording_path=recording,
    )
    lifecycle["generatedAtUtc"] = telemetry_sources.utc_now()
    return lifecycle


def compact_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    route_segments = _list(lifecycle.get("routeSegments"))
    return {
        "schema": lifecycle.get("schema") or SCHEMA_VERSION,
        "status": lifecycle.get("status"),
        "routeName": lifecycle.get("routeName"),
        "phase": lifecycle.get("phase"),
        "confidence": lifecycle.get("confidence"),
        "start": lifecycle.get("start"),
        "end": lifecycle.get("end"),
        "stepCount": lifecycle.get("stepCount"),
        "rawStepCount": lifecycle.get("rawStepCount"),
        "groupedStepCount": lifecycle.get("groupedStepCount"),
        "routeSegmentCount": lifecycle.get("routeSegmentCount") or len(route_segments),
        "successfulStepCount": lifecycle.get("successfulStepCount"),
        "partialStepCount": lifecycle.get("partialStepCount"),
        "failedStepCount": lifecycle.get("failedStepCount"),
        "successfulSegmentCount": lifecycle.get("successfulSegmentCount"),
        "partialSegmentCount": lifecycle.get("partialSegmentCount"),
        "reviewEvidenceCount": lifecycle.get("reviewEvidenceCount"),
        "planeChangeCount": len(_list(_dict(lifecycle.get("movement")).get("planeChanges"))),
        "routeSegments": route_segments[:8],
        "grouping": lifecycle.get("grouping"),
        "warnings": _list(lifecycle.get("warnings"))[:8],
    }


def write_recording_summary(path: str | Path, *, pretty: bool = True) -> dict[str, Any]:
    recording = Path(path)
    lifecycle = analyze_recording(recording)
    telemetry_sources.atomic_write_json(recording / "traversal_lifecycle.json", lifecycle, pretty=pretty)
    return lifecycle

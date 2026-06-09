from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import coordinate_spaces


MENU_SNAPSHOT_SCHEMA = "menu_snapshot.v1"
MENU_ROW_SCHEMA = "menu_row.v1"
MENU_SELECTION_SCHEMA = "menu_selection.v1"
MENU_INTERACTION_SCHEMA = "menu_interaction_event.v1"
MENU_INTERACTION_SUMMARY_SCHEMA = "menu_interaction_summary.v1"

DEFAULT_MENU_ROW_HEIGHT_PX = 15.0
DEFAULT_MENU_HEADER_HEIGHT_PX = 18.0
DEFAULT_MENU_OPEN_PAIR_WINDOW_MS = 2500
DEFAULT_MENU_SELECTION_PAIR_WINDOW_MS = 3000
DEFAULT_MENU_SNAPSHOT_RETENTION_MS = 5000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _float(value: Any) -> float | None:
    number = _number(value)
    return float(number) if number is not None else None


def _clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    out: list[str] = []
    in_tag = False
    for char in text:
        if char == "<":
            in_tag = True
            continue
        if char == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(char)
    cleaned = " ".join("".join(out).split())
    return cleaned or None


def _point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x", value.get("canvasX", value.get("client_x", value.get("screen_x"))))
    y = value.get("y", value.get("canvasY", value.get("client_y", value.get("screen_y"))))
    try:
        return {"x": float(x), "y": float(y)}
    except (TypeError, ValueError):
        return None


def click_point(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "screen": _event_point(event, "screen"),
        "client": _event_point(event, "client"),
        "canvas": _event_point(event, "canvas"),
    }


def _event_point(event: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    x = event.get(f"{prefix}_x")
    y = event.get(f"{prefix}_y")
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def preferred_click_point(event: dict[str, Any]) -> dict[str, float] | None:
    normalized = _point(event.get("normalizedMenuPoint") or event.get("normalizedCanvas"))
    if normalized:
        normalized["space"] = "normalized_menu"
        return normalized
    for prefix in ("canvas", "client", "screen"):
        point = _event_point(event, prefix)
        if not point:
            continue
        try:
            return {"x": float(point["x"]), "y": float(point["y"]), "space": prefix}
        except (TypeError, ValueError):
            continue
    return None


def normalize_bounds(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = _number(value.get("x", value.get("left")))
    y = _number(value.get("y", value.get("top")))
    width = _number(value.get("width", value.get("w")))
    height = _number(value.get("height", value.get("h")))
    if width is None:
        right = _number(value.get("right"))
        width = right - x if right is not None and x is not None else None
    if height is None:
        bottom = _number(value.get("bottom"))
        height = bottom - y if bottom is not None and y is not None else None
    if x is None or y is None or width is None or height is None:
        return None
    if float(width) <= 0 or float(height) <= 0:
        return None
    return {
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }


def bounds_center(bounds: dict[str, Any] | None) -> dict[str, float] | None:
    clean = normalize_bounds(bounds)
    if not clean:
        return None
    return {"x": clean["x"] + clean["width"] / 2.0, "y": clean["y"] + clean["height"] / 2.0}


def point_in_bounds(point: dict[str, Any] | None, bounds: dict[str, Any] | None) -> bool | None:
    clean_point = _point(point)
    clean_bounds = normalize_bounds(bounds)
    if not clean_point or not clean_bounds:
        return None
    return (
        clean_bounds["x"] <= clean_point["x"] <= clean_bounds["x"] + clean_bounds["width"]
        and clean_bounds["y"] <= clean_point["y"] <= clean_bounds["y"] + clean_bounds["height"]
    )


def distance(point_a: dict[str, Any] | None, point_b: dict[str, Any] | None) -> float | None:
    a = _point(point_a)
    b = _point(point_b)
    if not a or not b:
        return None
    return round(math.hypot(a["x"] - b["x"], a["y"] - b["y"]), 3)


def menu_entries_display_order(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    entries = sample.get("entries")
    if not isinstance(entries, list):
        return []
    source = str(sample.get("sourceEvent") or sample.get("sampleSource") or "")
    order = str(sample.get("entriesDisplayOrder") or sample.get("entryOrder") or "").strip().lower()
    reverse_raw_order = False
    if order in {"top_to_bottom", "display_top_to_bottom", "display_order"}:
        reverse_raw_order = False
    elif order in {"client_order", "raw_client_order", "bottom_to_top"}:
        reverse_raw_order = True
    elif source == "MenuOpened":
        reverse_raw_order = True
    indexed = list(enumerate(entries))
    if reverse_raw_order:
        indexed = list(reversed(indexed))
    rows: list[dict[str, Any]] = []
    for display_index, (source_index, entry) in enumerate(indexed):
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["sourceEntryIndex"] = source_index
        row["displayEntryIndex"] = display_index
        row["entryIndex"] = display_index
        row["entriesDisplayOrder"] = "top_to_bottom"
        rows.append(row)
    return rows


def compute_row_bounds(sample: dict[str, Any], row_index: int, *, row_count: int | None = None) -> dict[str, float] | None:
    menu_bounds = normalize_bounds(sample.get("menuBounds") or sample.get("bounds"))
    if not menu_bounds:
        return None
    displayed_count = _number(sample.get("entryCount", sample.get("menuEntryCount")))
    count = int(max(1, row_count or 0, displayed_count or 0, len(menu_entries_display_order(sample))))
    if row_index < 0 or row_index >= count:
        return None
    explicit_row_height = _float(sample.get("rowHeight"))
    explicit_header = _float(sample.get("headerHeight"))
    if explicit_row_height and explicit_row_height > 0:
        row_height = explicit_row_height
        header_height = explicit_header if explicit_header is not None else max(0.0, menu_bounds["height"] - row_height * count)
    elif menu_bounds["height"] > DEFAULT_MENU_ROW_HEIGHT_PX * count:
        natural_header_height = menu_bounds["height"] - (DEFAULT_MENU_ROW_HEIGHT_PX * count)
        header_height = min(24.0, max(DEFAULT_MENU_HEADER_HEIGHT_PX, natural_header_height))
        row_height = max(1.0, (menu_bounds["height"] - header_height) / count)
    else:
        header_height = 0.0
        row_height = menu_bounds["height"] / count
    return {
        "x": menu_bounds["x"],
        "y": menu_bounds["y"] + header_height + row_index * row_height,
        "width": menu_bounds["width"],
        "height": row_height,
    }


def _menu_sample_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for candidate in (
            value,
            value.get("menu"),
            value.get("hover"),
            value.get("hoverMenu"),
            value.get("postMenuSort"),
            _dict(value.get("clientTickHot")).get("hoverMenu"),
            _dict(value.get("clientTickHot")).get("postMenuSort"),
            _dict(_dict(value.get("raw_event")).get("high_value_fields")).get("hover"),
            _dict(_dict(value.get("raw_event")).get("high_value_fields")).get("menu"),
        ):
            if isinstance(candidate, dict):
                candidates.append(candidate)
    return candidates


def _choose_menu_sample(value: Any) -> dict[str, Any]:
    candidates = _menu_sample_candidates(value)
    scored: list[tuple[int, dict[str, Any]]] = []
    for sample in candidates:
        score = 0
        if sample.get("sourceEvent") == "MenuOpened":
            score += 4
        if sample.get("menuOpen") is True:
            score += 3
        if isinstance(sample.get("menuBounds"), dict) or isinstance(sample.get("bounds"), dict):
            score += 3
        if isinstance(sample.get("entries"), list) and sample.get("entries"):
            score += 3
        if sample.get("topOption") or sample.get("topTarget"):
            score += 1
        if score:
            scored.append((score, sample))
    if not scored:
        return candidates[0] if candidates else {}
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def kind_from_menu_type(value: Any) -> str:
    text = str(value or "").lower()
    if "object" in text:
        return "object"
    if "npc" in text:
        return "npc"
    if "widget" in text or "cc_op" in text:
        return "widget"
    if "item" in text:
        return "item"
    if "walk" in text:
        return "world"
    return "unknown"


def linked_target_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    kind = kind_from_menu_type(entry.get("type"))
    identifier = entry.get("identifier")
    return {
        "kind": kind,
        "name": _clean_text(entry.get("target")),
        "action": _clean_text(entry.get("option")),
        "ref": entry.get("objectRef") or entry.get("ref"),
        "rawId": identifier if kind in {"object", "npc", "world"} and identifier not in (None, -1) else entry.get("rawId"),
        "effectiveId": entry.get("effectiveId") or (identifier if kind in {"object", "npc"} and identifier not in (None, -1) else None),
        "world": entry.get("world") or entry.get("worldPoint"),
    }


def normalize_menu_row(entry: dict[str, Any], row_index: int, sample: dict[str, Any]) -> dict[str, Any]:
    row_bounds = normalize_bounds(entry.get("rowBounds") or entry.get("bounds")) or compute_row_bounds(sample, row_index)
    center = bounds_center(row_bounds)
    option = _clean_text(entry.get("option"))
    target = _clean_text(entry.get("target"))
    reasons: list[str] = []
    confidence = 0.15
    if option:
        confidence += 0.15
        reasons.append("option_present")
    if target:
        confidence += 0.15
        reasons.append("target_present")
    if entry.get("type"):
        confidence += 0.08
        reasons.append("menu_type_present")
    if entry.get("identifier") not in (None, -1, ""):
        confidence += 0.08
        reasons.append("identifier_present")
    if row_bounds:
        confidence += 0.28
        reasons.append("row_bounds_present")
    else:
        reasons.append("row_bounds_missing")
    linked = linked_target_from_entry({"option": option, "target": target, **entry})
    return {
        "schema": MENU_ROW_SCHEMA,
        "rowIndex": row_index,
        "sourceEntryIndex": entry.get("sourceEntryIndex"),
        "displayEntryIndex": entry.get("displayEntryIndex", row_index),
        "option": option,
        "target": target,
        "rawOption": entry.get("option"),
        "rawTarget": entry.get("target"),
        "type": entry.get("type"),
        "identifier": entry.get("identifier"),
        "param0": entry.get("param0"),
        "param1": entry.get("param1"),
        "itemId": entry.get("itemId"),
        "widgetId": entry.get("widgetId"),
        "npcIndex": entry.get("npcIndex"),
        "actorName": entry.get("actorName"),
        "objectId": entry.get("objectId") or (linked.get("rawId") if linked.get("kind") == "object" else None),
        "objectRef": entry.get("objectRef") or entry.get("ref"),
        "world": entry.get("world") or entry.get("worldPoint"),
        "bounds": row_bounds,
        "center": center,
        "linkedTarget": linked,
        "confidence": round(min(confidence, 1.0), 3),
        "reasons": reasons,
    }


def normalize_menu_snapshot(value: Any, *, opened_event: dict[str, Any] | None = None) -> dict[str, Any]:
    sample = _choose_menu_sample(value)
    rows = [normalize_menu_row(entry, index, sample) for index, entry in enumerate(menu_entries_display_order(sample))]
    if not rows and (sample.get("topOption") or sample.get("topTarget")):
        rows = [
            normalize_menu_row(
                {
                    "option": sample.get("topOption"),
                    "target": sample.get("topTarget"),
                    "type": sample.get("topType"),
                    "identifier": sample.get("topIdentifier"),
                    "param0": sample.get("topParam0"),
                    "param1": sample.get("topParam1"),
                },
                0,
                sample,
            )
        ]
    bounds = normalize_bounds(sample.get("menuBounds") or sample.get("bounds"))
    warnings: list[str] = []
    if not bounds:
        warnings.append("menu_bounds_missing")
    if not rows:
        warnings.append("menu_rows_missing")
    open_event = _dict(opened_event)
    is_open = bool(sample.get("menuOpen") is True or sample.get("sourceEvent") == "MenuOpened" or (bounds and rows))
    return {
        "schema": MENU_SNAPSHOT_SCHEMA,
        "isOpen": is_open,
        "openedAt": {
            "eventSeq": open_event.get("eventSeq") or open_event.get("event_seq"),
            "tick": sample.get("gameTickAtSample") or _dict(value).get("latest_tick"),
            "monotonicTime": open_event.get("monotonic_time") or sample.get("monotonicTimeNanos"),
            "elapsedSeconds": open_event.get("elapsed_seconds") or _dict(value).get("elapsed_seconds"),
            "wallTimeUtc": open_event.get("wall_time_utc") or sample.get("timestampUtc"),
        },
        "bounds": bounds,
        "rowCount": len(rows),
        "entryCount": sample.get("entryCount", sample.get("menuEntryCount")),
        "sourceEvent": sample.get("sourceEvent") or sample.get("sampleSource"),
        "rowsVisualOrder": rows,
        "warnings": warnings,
    }


def _elapsed_seconds(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("elapsed_seconds", "elapsedSeconds"):
            number = _float(value.get(key))
            if number is not None:
                return number
        opened_at = _dict(value.get("openedAt"))
        for key in ("elapsedSeconds", "elapsed_seconds"):
            number = _float(opened_at.get(key))
            if number is not None:
                return number
    return None


def _event_seq(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in ("event_seq", "eventSeq"):
        number = _number(value.get(key))
        if number is not None:
            return int(number)
    return None


def _row_action_target(row: dict[str, Any]) -> tuple[str | None, str | None]:
    return _clean_text(row.get("option")), _clean_text(row.get("target"))


def _target_action_name(target: dict[str, Any] | None) -> tuple[str | None, str | None]:
    target = _dict(target)
    action = _clean_text(target.get("action"))
    if not action:
        actions = target.get("effectiveActions") or target.get("actions") or []
        if isinstance(actions, list) and actions:
            action = _clean_text(actions[0])
    name = _clean_text(target.get("name") or target.get("effectiveName") or target.get("targetName"))
    return action, name


def row_matches_target(row: dict[str, Any], target: dict[str, Any] | None) -> bool:
    action, name = _target_action_name(target)
    row_action, row_name = _row_action_target(row)
    action_ok = not action or not row_action or action.lower() == row_action.lower()
    name_ok = not name or not row_name or name.lower() in row_name.lower() or row_name.lower() in name.lower()
    return bool((action or name) and action_ok and name_ok)


def snapshot_has_matching_entry(snapshot: dict[str, Any], target: dict[str, Any] | None) -> bool:
    return any(row_matches_target(row, target) for row in _list(_dict(snapshot).get("rowsVisualOrder")) if isinstance(row, dict))


def _snapshot_brief(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshotId": snapshot.get("snapshotId"),
        "elapsedSeconds": snapshot.get("elapsedSeconds"),
        "tick": snapshot.get("tick"),
        "exportSequence": snapshot.get("exportSequence"),
        "sourceEvent": snapshot.get("sourceEvent"),
        "boundsPresent": bool(snapshot.get("bounds")),
        "rowCount": snapshot.get("rowCount"),
        "entryCount": snapshot.get("entryCount"),
        "warnings": snapshot.get("warnings") or [],
    }


def snapshot_brief(snapshot: dict[str, Any]) -> dict[str, Any]:
    return _snapshot_brief(_dict(snapshot))


def build_menu_snapshot_buffer(snapshots: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize every menu-like telemetry snapshot so selection pairing can look past nearest before/after."""
    buffer: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots or []):
        normalized = normalize_menu_snapshot(snapshot)
        rows = normalized.get("rowsVisualOrder") or []
        has_signal = bool(normalized.get("isOpen") or normalized.get("bounds") or rows)
        if not has_signal:
            continue
        elapsed = _elapsed_seconds(snapshot) if _elapsed_seconds(snapshot) is not None else _elapsed_seconds(normalized)
        normalized["snapshotId"] = f"menu_snapshot_{index + 1:04d}"
        normalized["elapsedSeconds"] = elapsed
        normalized["wallTimeUtc"] = snapshot.get("wall_time_utc") or snapshot.get("wallTimeUtc") or _dict(normalized.get("openedAt")).get("wallTimeUtc")
        normalized["tick"] = snapshot.get("latest_tick") or snapshot.get("tick") or _dict(normalized.get("openedAt")).get("tick")
        normalized["exportSequence"] = snapshot.get("latest_export_sequence") or snapshot.get("export_sequence") or snapshot.get("exportSequence")
        normalized["sourceSnapshotIndex"] = index
        normalized["sourceFile"] = "events.jsonl"
        normalized["freshness"] = {
            "elapsedDeltaMs": None,
            "stale": False,
        }
        buffer.append(normalized)
    buffer.sort(key=lambda item: (_elapsed_seconds(item) if _elapsed_seconds(item) is not None else -1.0, str(item.get("snapshotId") or "")))
    return buffer


def _row_geometry_source(selection: dict[str, Any], chosen_transform: dict[str, Any], target: dict[str, Any] | None) -> str:
    if selection.get("insideRowBounds") is True and selection.get("rowBounds"):
        warnings = set(str(item) for item in chosen_transform.get("warnings") or [])
        if "target_row_anchor_transform_used" in warnings:
            return "option_target_match"
        return "direct_row_hit"
    if selection.get("rowBounds") and row_matches_target(
        {"option": selection.get("selectedOption"), "target": selection.get("selectedTarget")},
        target,
    ):
        return "option_target_match"
    if _dict(selection.get("linkedGameTarget")).get("name"):
        return "fallback_target_link"
    return "unknown"


def _score_snapshot_candidate(
    click_event: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    fallback_target: dict[str, Any] | None,
    right_click_elapsed: float | None,
    selection_elapsed: float | None,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    selection = resolve_menu_selection(click_event, snapshot, fallback_target=fallback_target)
    chosen = _dict(_dict(coordinate_spaces.infer_best_transform_for_menu_hit(click_event, snapshot, fallback_target=fallback_target)).get("chosen"))
    snapshot_elapsed = _elapsed_seconds(snapshot)
    delta_ms = None
    if selection_elapsed is not None and snapshot_elapsed is not None:
        delta_ms = round((snapshot_elapsed - selection_elapsed) * 1000.0, 3)
    matching_entry = snapshot_has_matching_entry(snapshot, fallback_target)
    selected_matches = row_matches_target(
        {
            "option": selection.get("selectedOption"),
            "target": selection.get("selectedTarget"),
        },
        fallback_target,
    )
    score = 0.0
    reasons: list[str] = []
    if snapshot.get("bounds"):
        score += 2.0
        reasons.append("menu_bounds_present")
    if matching_entry:
        score += 3.0
        reasons.append("snapshot_entries_match_target")
    if selected_matches:
        score += 2.0
        reasons.append("selected_row_matches_target")
    if selection.get("rowBounds"):
        score += 1.4
        reasons.append("selected_row_bounds_present")
    if selection.get("insideRowBounds") is True:
        score += 1.8
        reasons.append("click_inside_selected_row_bounds")
    if str(snapshot.get("sourceEvent") or "") in {"MenuOpened", "PostMenuSort"}:
        score += 0.45
        reasons.append("menu_source_event_preferred")
    if right_click_elapsed is not None and snapshot_elapsed is not None and snapshot_elapsed >= right_click_elapsed:
        score += 0.35
        reasons.append("snapshot_after_right_click")
    if delta_ms is not None:
        score -= min(abs(delta_ms) / 10000.0, 0.8)
        reasons.append("time_proximity_scored")
    if fallback_target and not matching_entry:
        score -= 2.0
        reasons.append("snapshot_entries_do_not_match_target")
    if not snapshot.get("bounds") and selection.get("rowBounds") is None:
        score -= 0.6
        reasons.append("snapshot_lacks_row_geometry")
    brief = {
        **_snapshot_brief(snapshot),
        "deltaFromSelectionMs": delta_ms,
        "matchingEntry": matching_entry,
        "selectedOption": selection.get("selectedOption"),
        "selectedTarget": selection.get("selectedTarget"),
        "selectedRowIndex": selection.get("selectedRowIndex"),
        "rowBoundsPresent": bool(selection.get("rowBounds")),
        "insideRowBounds": selection.get("insideRowBounds"),
        "coordinateTransformUsed": selection.get("coordinateTransformUsed"),
        "score": round(score, 3),
        "reasons": reasons,
    }
    return score, selection, brief


def select_menu_snapshot_for_selection(
    click_event: dict[str, Any],
    menu_snapshots: list[dict[str, Any]] | None,
    *,
    fallback_target: dict[str, Any] | None = None,
    previous_right_click: dict[str, Any] | None = None,
    menu_open_pair_window_ms: int = DEFAULT_MENU_OPEN_PAIR_WINDOW_MS,
    menu_selection_pair_window_ms: int = DEFAULT_MENU_SELECTION_PAIR_WINDOW_MS,
    menu_snapshot_retention_ms: int = DEFAULT_MENU_SNAPSHOT_RETENTION_MS,
) -> dict[str, Any]:
    selection_elapsed = _elapsed_seconds(click_event)
    right_click_elapsed = _elapsed_seconds(previous_right_click)
    open_window = float(menu_open_pair_window_ms) / 1000.0
    selection_window = float(menu_selection_pair_window_ms) / 1000.0
    retention = float(menu_snapshot_retention_ms) / 1000.0
    candidates: list[tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for snapshot in menu_snapshots or []:
        snapshot_elapsed = _elapsed_seconds(snapshot)
        if selection_elapsed is not None and snapshot_elapsed is not None:
            lower = (right_click_elapsed - 0.1) if right_click_elapsed is not None else selection_elapsed - retention
            upper = selection_elapsed + selection_window
            if snapshot_elapsed < lower or snapshot_elapsed > upper:
                continue
            if right_click_elapsed is not None and snapshot_elapsed > right_click_elapsed + open_window and snapshot_elapsed > selection_elapsed:
                continue
        score, selection, brief = _score_snapshot_candidate(
            click_event,
            snapshot,
            fallback_target=fallback_target,
            right_click_elapsed=right_click_elapsed,
            selection_elapsed=selection_elapsed,
        )
        candidates.append((score, snapshot, selection, brief))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return {
            "selectedSnapshot": None,
            "selection": resolve_menu_selection(click_event, None, fallback_target=fallback_target),
            "diagnostics": {
                "candidateSnapshotCount": 0,
                "candidateSnapshots": [],
                "selectedSnapshotId": None,
                "selectedSnapshotReason": "missing_snapshot",
                "selectedSnapshotScore": 0.0,
                "rowGeometrySource": "missing_snapshot",
            },
        }
    score, snapshot, selection, _brief = candidates[0]
    candidate_briefs = [brief for _score, _snapshot, _selection, brief in candidates[:8]]
    alignment = coordinate_spaces.infer_best_transform_for_menu_hit(click_event, snapshot, fallback_target=fallback_target)
    chosen = _dict(alignment.get("chosen"))
    source = _row_geometry_source(selection, chosen, fallback_target)
    if not selection.get("rowBounds") and snapshot.get("bounds"):
        source = "option_target_match" if snapshot_has_matching_entry(snapshot, fallback_target) else source
    diagnostics = {
        "candidateSnapshotCount": len(candidates),
        "candidateSnapshots": candidate_briefs,
        "selectedSnapshotId": snapshot.get("snapshotId"),
        "selectedSnapshotReason": source,
        "selectedSnapshotScore": round(score, 3),
        "rowGeometrySource": source,
        "menuOpenPairWindowMs": menu_open_pair_window_ms,
        "menuSelectionPairWindowMs": menu_selection_pair_window_ms,
        "menuSnapshotRetentionMs": menu_snapshot_retention_ms,
    }
    selection = dict(selection)
    selection.update(
        {
            "candidateSnapshotCount": len(candidates),
            "selectedSnapshotId": snapshot.get("snapshotId"),
            "selectedSnapshotReason": source,
            "selectedSnapshotScore": round(score, 3),
            "candidateSnapshots": candidate_briefs,
            "rowGeometrySource": source,
        }
    )
    if source in {"direct_row_hit", "option_target_match"}:
        selection["rowGeometryProven"] = bool(selection.get("rowBounds"))
    else:
        selection["rowGeometryProven"] = False
    return {"selectedSnapshot": snapshot, "selection": selection, "diagnostics": diagnostics}


def fallback_row_from_target(target: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    action = target.get("action")
    if action is None:
        actions = target.get("effectiveActions") or target.get("actions") or []
        if isinstance(actions, list) and actions:
            action = actions[0]
    name = target.get("name") or target.get("effectiveName") or target.get("targetName")
    if not action and not name:
        return None
    entry = {
        "option": action,
        "target": name,
        "type": "inferred",
        "identifier": target.get("rawId") or target.get("effectiveId") or target.get("id"),
        "objectRef": target.get("ref"),
        "world": target.get("world") or target.get("worldPoint"),
    }
    row = normalize_menu_row(entry, 0, {})
    row["confidence"] = min(row.get("confidence") or 0.0, 0.45)
    row.setdefault("reasons", []).append("inferred_from_linked_target")
    return row


def resolve_menu_selection(
    click_event: dict[str, Any],
    menu_snapshot: dict[str, Any] | None,
    *,
    fallback_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = _dict(menu_snapshot)
    point_payload = click_point(click_event)
    alignment = coordinate_spaces.infer_best_transform_for_menu_hit(click_event, snapshot, fallback_target=fallback_target)
    chosen_transform = _dict(alignment.get("chosen"))
    normalized_click_payload = _dict(chosen_transform.get("normalizedPoint"))
    click = _point(normalized_click_payload) or preferred_click_point(click_event)
    rows = [row for row in _list(snapshot.get("rowsVisualOrder")) if isinstance(row, dict)]
    selected: dict[str, Any] | None = None
    inside_bounds = False
    if chosen_transform.get("insideRowBounds") is True and isinstance(chosen_transform.get("selectedRow"), dict):
        selected = _dict(chosen_transform.get("selectedRow"))
        inside_bounds = True
    elif rows and click:
        for row in rows:
            inside = point_in_bounds(click, row.get("bounds"))
            if inside is True:
                selected = row
                inside_bounds = True
                break
    if selected is None and len(rows) == 1 and not normalize_bounds(rows[0].get("bounds")):
        selected = rows[0]
    if selected is None:
        selected = fallback_row_from_target(fallback_target)
    row_bounds = normalize_bounds(_dict(selected).get("bounds"))
    row_center = bounds_center(row_bounds)
    row_distance = distance(click, row_center)
    linked = _dict(_dict(selected).get("linkedTarget")) or linked_target_from_entry(_dict(selected))
    if fallback_target:
        linked = {**linked, **{k: v for k, v in fallback_target.items() if k in {"ref", "world", "worldPoint", "rawId", "effectiveId", "id"} and v not in (None, "", {}, [])}}
        if not linked.get("name"):
            linked["name"] = fallback_target.get("name") or fallback_target.get("effectiveName")
        if not linked.get("action"):
            actions = fallback_target.get("effectiveActions") or fallback_target.get("actions") or []
            linked["action"] = actions[0] if isinstance(actions, list) and actions else fallback_target.get("action")
        if not linked.get("rawId"):
            linked["rawId"] = fallback_target.get("rawId") or fallback_target.get("id")
        if not linked.get("effectiveId"):
            linked["effectiveId"] = fallback_target.get("effectiveId") or fallback_target.get("id")
    warnings: list[str] = []
    confidence = 0.2
    if selected:
        confidence += 0.25
    else:
        warnings.append("menu_row_unresolved")
    if row_bounds:
        confidence += 0.25
        if inside_bounds:
            confidence += 0.18
        else:
            warnings.append("click_not_inside_resolved_menu_row")
    else:
        warnings.append("menu_row_bounds_missing")
    if linked.get("name") or linked.get("action"):
        confidence += 0.2
    if fallback_target and not row_bounds:
        warnings.append("selection_inferred_from_game_target_without_row_geometry")
    if chosen_transform:
        if chosen_transform.get("name"):
            confidence += 0.08
        for warning in chosen_transform.get("warnings") or []:
            if warning not in warnings and warning not in {"point_outside_menu_bounds", "menu_row_hit_does_not_match_target_action"}:
                warnings.append(str(warning))
    return {
        "schema": MENU_SELECTION_SCHEMA,
        "clickEventSeq": click_event.get("event_seq") or click_event.get("eventSeq"),
        "button": click_event.get("button"),
        "clickedPoint": point_payload,
        "normalizedClickPoint": normalized_click_payload or None,
        "coordinateTransformUsed": chosen_transform.get("name"),
        "coordinateTransformConfidence": chosen_transform.get("confidence"),
        "coordinateTransformReasons": chosen_transform.get("reasons") or [],
        "coordinateTransformWarnings": chosen_transform.get("warnings") or [],
        "selectedRowIndex": _dict(selected).get("rowIndex"),
        "selectedOption": _dict(selected).get("option"),
        "selectedTarget": _dict(selected).get("target"),
        "rowBounds": row_bounds,
        "insideRowBounds": True if inside_bounds else (None if not row_bounds else False),
        "rowCenterDistancePx": row_distance,
        "linkedGameTarget": linked,
        "inputPathClassification": click_event.get("inputPathClassification"),
        "mirrorVerificationStatus": click_event.get("mirrorVerificationStatus"),
        "selectionConfidence": round(min(confidence, 1.0), 3),
        "warnings": warnings,
    }


def target_from_quality_or_classification(classification: dict[str, Any], quality: dict[str, Any] | None = None) -> dict[str, Any] | None:
    matched = _dict(_dict(quality).get("matchedTarget"))
    if matched:
        return matched
    context = _dict(classification.get("targetContext"))
    matched = _dict(context.get("matchedTarget"))
    if matched:
        target = dict(matched)
        if context.get("targetName") and not target.get("name"):
            target["name"] = context.get("targetName")
        if context.get("targetAction") and not target.get("action"):
            target["action"] = context.get("targetAction")
        return target
    return None


def build_menu_interactions_from_joined(
    joined_rows: list[dict[str, Any]],
    target_quality_rows: list[dict[str, Any]] | None = None,
    *,
    menu_snapshots: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quality_by_seq = {row.get("eventSeq"): row for row in target_quality_rows or []}
    interactions: list[dict[str, Any]] = []
    last_open_session: dict[str, Any] | None = None
    session_index = 0
    for row in joined_rows:
        classification = _dict(row.get("inputActionClassification"))
        label = classification.get("classification")
        input_event = _dict(row.get("inputEvent"))
        if not label or input_event.get("kind") not in {"click", "double_click"}:
            continue
        seq = input_event.get("event_seq") or classification.get("eventSeq")
        before = _dict(row.get("nearestTelemetryBefore"))
        after = _dict(row.get("nearestTelemetryAfter"))
        quality = quality_by_seq.get(seq)
        if label == "right_click_menu_open":
            menu_snapshot = normalize_menu_snapshot(after or before, opened_event=input_event)
            session_index += 1
            session_id = f"menu_session_{session_index:03d}"
            last_open_session = {
                "menuSessionId": session_id,
                "rightClickEventSeq": seq,
                "rightClickTime": input_event.get("elapsed_seconds"),
                "openSnapshotIds": [menu_snapshot.get("snapshotId")] if menu_snapshot.get("snapshotId") else [],
                "menuSnapshot": menu_snapshot,
            }
            interactions.append(
                {
                    "schema": MENU_INTERACTION_SCHEMA,
                    "interactionType": "menu_open",
                    "menuSessionId": session_id,
                    "eventSeq": seq,
                    "rightClickEventSeq": seq,
                    "rightClickTime": input_event.get("elapsed_seconds"),
                    "openSnapshotIds": last_open_session.get("openSnapshotIds"),
                    "clickEvent": input_event,
                    "menuSnapshot": menu_snapshot,
                    "closeReason": None,
                    "warnings": menu_snapshot.get("warnings") or [],
                }
            )
        elif label == "menu_selection_click":
            fallback_target = target_from_quality_or_classification(classification, quality)
            selected_snapshot = _dict(classification.get("selectedMenuSnapshot"))
            selected_snapshot_id = selected_snapshot.get("snapshotId")
            menu_snapshot = {}
            if selected_snapshot_id:
                menu_snapshot = _dict(next((item for item in menu_snapshots or [] if item.get("snapshotId") == selected_snapshot_id), {}))
            if not menu_snapshot:
                menu_snapshot = _dict(_dict(last_open_session).get("menuSnapshot")) or normalize_menu_snapshot(before or after, opened_event=input_event)
            selection = _dict(classification.get("menuSelection")) or resolve_menu_selection(input_event, menu_snapshot, fallback_target=fallback_target)
            if not selection.get("candidateSnapshotCount") and menu_snapshots:
                pairing = select_menu_snapshot_for_selection(
                    input_event,
                    menu_snapshots,
                    fallback_target=fallback_target,
                    previous_right_click={"elapsed_seconds": _dict(last_open_session).get("rightClickTime")} if last_open_session else None,
                )
                selection = _dict(pairing.get("selection")) or selection
                menu_snapshot = _dict(pairing.get("selectedSnapshot")) or menu_snapshot
            if last_open_session:
                session_id = str(last_open_session.get("menuSessionId") or "")
                right_seq = last_open_session.get("rightClickEventSeq")
                right_time = last_open_session.get("rightClickTime")
                open_snapshot_ids = list(last_open_session.get("openSnapshotIds") or [])
            else:
                session_index += 1
                session_id = f"menu_session_implicit_{session_index:03d}"
                right_seq = None
                right_time = None
                open_snapshot_ids = []
            if menu_snapshot.get("snapshotId") and menu_snapshot.get("snapshotId") not in open_snapshot_ids:
                open_snapshot_ids.append(menu_snapshot.get("snapshotId"))
            interactions.append(
                {
                    "schema": MENU_INTERACTION_SCHEMA,
                    "interactionType": "menu_selection",
                    "menuSessionId": session_id,
                    "rightClickEventSeq": right_seq,
                    "rightClickTime": right_time,
                    "openSnapshotIds": open_snapshot_ids,
                    "eventSeq": seq,
                    "selectedClickEventSeq": seq,
                    "selectedOption": selection.get("selectedOption"),
                    "selectedTarget": selection.get("selectedTarget"),
                    "selectedRowIndex": selection.get("selectedRowIndex"),
                    "rowBounds": selection.get("rowBounds"),
                    "rowGeometryProven": bool(selection.get("rowBounds")),
                    "rowGeometrySource": selection.get("rowGeometrySource"),
                    "clickEvent": input_event,
                    "menuSnapshot": menu_snapshot,
                    "menuSelection": selection,
                    "linkedTarget": selection.get("linkedGameTarget") or fallback_target,
                    "targetMatchQuality": _dict(quality).get("quality"),
                    "targetMatchScore": _dict(quality).get("score"),
                    "selectedSnapshotId": selection.get("selectedSnapshotId") or menu_snapshot.get("snapshotId"),
                    "selectedSnapshotReason": selection.get("selectedSnapshotReason"),
                    "selectedSnapshotScore": selection.get("selectedSnapshotScore"),
                    "candidateSnapshotCount": selection.get("candidateSnapshotCount"),
                    "candidateSnapshots": selection.get("candidateSnapshots") or [],
                    "closeReason": "selection_click",
                    "warnings": list(selection.get("warnings") or []) + list(_dict(quality).get("warnings") or []),
                }
            )
            last_open_session = None
        elif label != "right_click_menu_open":
            if input_event.get("button") == "left":
                last_open_session = None
    return interactions, summarize_menu_interactions(interactions)


def summarize_menu_interactions(interactions: list[dict[str, Any]]) -> dict[str, Any]:
    menu_opens = [row for row in interactions if row.get("interactionType") == "menu_open"]
    selections = [row for row in interactions if row.get("interactionType") == "menu_selection"]
    rows_resolved = 0
    with_row_geometry = 0
    linked_targets = 0
    examples: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    warnings = Counter()
    for row in interactions:
        for warning in row.get("warnings") or []:
            warnings[str(warning)] += 1
        selection = _dict(row.get("menuSelection"))
        if selection:
            if selection.get("selectedOption") or selection.get("selectedTarget"):
                rows_resolved += 1
            if selection.get("rowBounds"):
                with_row_geometry += 1
            if _dict(selection.get("linkedGameTarget")).get("name") or _dict(row.get("linkedTarget")).get("name"):
                linked_targets += 1
            if len(examples) < 8:
                example = {
                    "eventSeq": row.get("eventSeq"),
                    "menuSessionId": row.get("menuSessionId"),
                    "rightClickEventSeq": row.get("rightClickEventSeq"),
                    "option": selection.get("selectedOption"),
                    "target": selection.get("selectedTarget"),
                    "selectedRowIndex": selection.get("selectedRowIndex"),
                    "rowBoundsPresent": bool(selection.get("rowBounds")),
                    "insideRowBounds": selection.get("insideRowBounds"),
                    "rowGeometrySource": selection.get("rowGeometrySource") or row.get("rowGeometrySource"),
                    "selectedSnapshotId": selection.get("selectedSnapshotId") or row.get("selectedSnapshotId"),
                    "selectedSnapshotReason": selection.get("selectedSnapshotReason") or row.get("selectedSnapshotReason"),
                    "selectedSnapshotScore": selection.get("selectedSnapshotScore") or row.get("selectedSnapshotScore"),
                    "candidateSnapshotCount": selection.get("candidateSnapshotCount") or row.get("candidateSnapshotCount"),
                    "linkedTarget": selection.get("linkedGameTarget"),
                    "targetMatchQuality": row.get("targetMatchQuality"),
                    "targetMatchScore": row.get("targetMatchScore"),
                }
                examples.append(example)
                diagnostics.append(
                    {
                        **example,
                        "candidateSnapshots": selection.get("candidateSnapshots") or row.get("candidateSnapshots") or [],
                        "missingRowGeometryReason": None if selection.get("rowBounds") else _list(selection.get("warnings")) or _list(row.get("warnings")),
                    }
                )
    status = (
        "PASS"
        if selections and linked_targets == len(selections) and with_row_geometry == len(selections)
        else ("WARN" if interactions else "WARN")
    )
    return {
        "schema": MENU_INTERACTION_SUMMARY_SCHEMA,
        "status": status,
        "generated_at_utc": utc_now(),
        "rightClickMenuOpenCount": len(menu_opens),
        "menuSelectionCount": len(selections),
        "menuRowsResolvedCount": rows_resolved,
        "menuSelectionsWithRowGeometryCount": with_row_geometry,
        "menuSelectionsLinkedToTargetsCount": linked_targets,
        "menuSelectionsMissingRowGeometryCount": max(0, len(selections) - with_row_geometry),
        "warningCounts": dict(sorted(warnings.items())),
        "warnings": sorted(warnings),
        "examples": examples,
        "menuRowDiagnostics": diagnostics,
    }


def dumps_compact(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)

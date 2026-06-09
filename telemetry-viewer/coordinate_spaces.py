from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any


COORDINATE_SPACE_REPORT_SCHEMA = "coordinate_space_report.v1"
COORDINATE_TRANSFORM_SCHEMA = "coordinate_transform.v1"
NORMALIZED_POINT_SCHEMA = "normalized_point.v1"
COORDINATE_ALIGNMENT_SUMMARY_SCHEMA = "coordinate_alignment_summary.v1"

COMMON_DPI_SCALES = (1.0, 1.25, 4.0 / 3.0, 1.4, 1.425, 1.5, 1.75, 2.0)


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


def point(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x", value.get("client_x", value.get("canvas_x", value.get("screen_x"))))
    y = value.get("y", value.get("client_y", value.get("canvas_y", value.get("screen_y"))))
    try:
        return {"x": float(x), "y": float(y)}
    except (TypeError, ValueError):
        return None


def bounds(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = _float(value.get("x", value.get("left")))
    y = _float(value.get("y", value.get("top")))
    width = _float(value.get("width", value.get("w")))
    height = _float(value.get("height", value.get("h")))
    if width is None:
        right = _float(value.get("right"))
        width = right - x if right is not None and x is not None else None
    if height is None:
        bottom = _float(value.get("bottom"))
        height = bottom - y if bottom is not None and y is not None else None
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def point_in_bounds(candidate: Any, area: Any) -> bool | None:
    pt = point(candidate)
    clean = bounds(area)
    if pt is None or clean is None:
        return None
    return clean["x"] <= pt["x"] <= clean["x"] + clean["width"] and clean["y"] <= pt["y"] <= clean["y"] + clean["height"]


def distance(point_a: Any, point_b: Any) -> float | None:
    a = point(point_a)
    b = point(point_b)
    if a is None or b is None:
        return None
    return round(math.hypot(a["x"] - b["x"], a["y"] - b["y"]), 3)


def scale_point(value: Any, scale_x: float, scale_y: float | None = None) -> dict[str, float] | None:
    pt = point(value)
    if pt is None:
        return None
    sy = scale_x if scale_y is None else scale_y
    return {"x": pt["x"] * float(scale_x), "y": pt["y"] * float(sy)}


def offset_point(value: Any, dx: float, dy: float) -> dict[str, float] | None:
    pt = point(value)
    if pt is None:
        return None
    return {"x": pt["x"] + float(dx), "y": pt["y"] + float(dy)}


def normalized_point(value: Any, *, source_space: str, target_space: str, transform: str, confidence: float, reasons: list[str] | None = None) -> dict[str, Any] | None:
    pt = point(value)
    if pt is None:
        return None
    return {
        "schema": NORMALIZED_POINT_SCHEMA,
        "x": round(pt["x"], 3),
        "y": round(pt["y"], 3),
        "sourceSpace": source_space,
        "targetSpace": target_space,
        "transform": transform,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "reasons": reasons or [],
    }


def normalize_point(value: Any, from_space: str, to_space: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    context = dict(context or {})
    pt = point(value)
    if pt is None:
        return None
    if from_space == to_space:
        return normalized_point(pt, source_space=from_space, target_space=to_space, transform="identity", confidence=1.0)
    scale_x = _float(context.get("scale_x", context.get("dpi_scale_x"))) or 1.0
    scale_y = _float(context.get("scale_y", context.get("dpi_scale_y"))) or scale_x
    offset_x = _float(context.get("offset_x")) or 0.0
    offset_y = _float(context.get("offset_y")) or 0.0
    mode = str(context.get("mode") or "scale_then_offset")
    if mode == "inverse_scale":
        converted = {"x": pt["x"] / scale_x + offset_x, "y": pt["y"] / scale_y + offset_y}
    else:
        converted = {"x": pt["x"] * scale_x + offset_x, "y": pt["y"] * scale_y + offset_y}
    return normalized_point(
        converted,
        source_space=from_space,
        target_space=to_space,
        transform=str(context.get("name") or mode),
        confidence=float(context.get("confidence") or 0.6),
        reasons=list(context.get("reasons") or []),
    )


def event_point(event: dict[str, Any], space: str) -> dict[str, float] | None:
    if space == "normalized_menu":
        return point(event.get("normalizedMenuPoint"))
    if space == "normalized_canvas":
        return point(event.get("normalizedCanvas"))
    x = event.get(f"{space}_x")
    y = event.get(f"{space}_y")
    if x is not None and y is not None:
        return point({"x": x, "y": y})
    return None


def best_raw_event_point(event: dict[str, Any]) -> tuple[str, dict[str, float]] | None:
    for space in ("canvas", "client", "screen"):
        pt = event_point(event, space)
        if pt is not None:
            return space, pt
    return None


def infer_dpi_scale(*, event: dict[str, Any] | None = None, window: dict[str, Any] | None = None) -> float | None:
    event = _dict(event)
    window = _dict(window)
    for value in (
        event.get("dpi_scale_x"),
        event.get("dpiScaleX"),
        window.get("dpi_scale_x"),
        window.get("dpiScaleX"),
    ):
        parsed = _float(value)
        if parsed and parsed > 0:
            return parsed
    for value in (event.get("dpi"), window.get("dpi")):
        parsed = _float(value)
        if parsed and parsed > 0:
            return round(parsed / 96.0, 4)
    return None


def row_center(row: dict[str, Any]) -> dict[str, float] | None:
    center = point(row.get("center"))
    if center:
        return center
    clean = bounds(row.get("bounds"))
    if not clean:
        return None
    return {"x": clean["x"] + clean["width"] / 2.0, "y": clean["y"] + clean["height"] / 2.0}


def _target_action_name(target: dict[str, Any] | None) -> tuple[str | None, str | None]:
    target = _dict(target)
    name = _clean_text(target.get("name") or target.get("effectiveName") or target.get("targetName"))
    action = _clean_text(target.get("action"))
    if not action:
        actions = target.get("effectiveActions") or target.get("actions") or []
        if isinstance(actions, list) and actions:
            action = _clean_text(actions[0])
    return action, name


def row_matches_target(row: dict[str, Any], target: dict[str, Any] | None) -> bool:
    action, name = _target_action_name(target)
    row_option = _clean_text(row.get("option"))
    row_target = _clean_text(row.get("target"))
    action_ok = not action or not row_option or action.lower() == row_option.lower()
    name_ok = not name or not row_target or name.lower() in row_target.lower() or row_target.lower() in name.lower()
    return bool((action or name) and action_ok and name_ok)


def _candidate_points(event: dict[str, Any], *, dpi_scale: float | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(name: str, source_space: str, pt: dict[str, float] | None, *, scale_x: float = 1.0, scale_y: float | None = None, confidence: float = 0.5, reasons: list[str] | None = None) -> None:
        if pt is None:
            return
        sy = scale_x if scale_y is None else scale_y
        candidates.append(
            {
                "name": name,
                "sourceSpace": source_space,
                "targetSpace": "menu",
                "point": {"x": round(pt["x"], 3), "y": round(pt["y"], 3)},
                "scaleX": round(float(scale_x), 5),
                "scaleY": round(float(sy), 5),
                "confidence": confidence,
                "reasons": reasons or [],
            }
        )

    for space in ("canvas", "client", "screen"):
        raw = event_point(event, space)
        if raw:
            add(f"raw_{space}", space, raw, confidence=0.55 if space != "screen" else 0.25, reasons=[f"{space}_point_present"])
    client = event_point(event, "client")
    if client:
        scales = list(COMMON_DPI_SCALES)
        detected = dpi_scale or infer_dpi_scale(event=event)
        if detected and all(abs(detected - item) > 0.02 for item in scales):
            scales.append(detected)
        for scale in sorted(set(round(item, 5) for item in scales)):
            if scale <= 0:
                continue
            add(
                f"client_inverse_dpi_{str(round(scale, 3)).replace('.', '_')}",
                "client",
                {"x": client["x"] / scale, "y": client["y"] / scale},
                scale_x=scale,
                confidence=0.6 + (0.1 if detected and abs(detected - scale) <= 0.02 else 0.0),
                reasons=["client_point_inverse_dpi_candidate"],
            )
    screen = event_point(event, "screen")
    origin_x = _float(event.get("client_origin_screen_x"))
    origin_y = _float(event.get("client_origin_screen_y"))
    if screen and origin_x is not None and origin_y is not None:
        add(
            "screen_minus_client_origin",
            "screen",
            {"x": screen["x"] - origin_x, "y": screen["y"] - origin_y},
            confidence=0.66,
            reasons=["screen_point_minus_client_origin"],
        )
    return candidates


def _add_target_row_anchor_candidates(
    candidates: list[dict[str, Any]],
    event: dict[str, Any],
    rows: list[dict[str, Any]],
    fallback_target: dict[str, Any] | None,
) -> None:
    client = event_point(event, "client")
    if client is None:
        return
    matching_rows = [row for row in rows if row_matches_target(row, fallback_target)]
    for row in matching_rows:
        center = row_center(row)
        if not center or not center["x"] or not center["y"]:
            continue
        scale_x = client["x"] / center["x"]
        scale_y = client["y"] / center["y"]
        if not (1.05 <= scale_x <= 2.5 and 1.05 <= scale_y <= 2.5):
            continue
        candidates.append(
            {
                "name": "client_inverse_scale_target_row_anchor",
                "sourceSpace": "client",
                "targetSpace": "menu",
                "point": {"x": round(center["x"], 3), "y": round(center["y"], 3)},
                "scaleX": round(scale_x, 5),
                "scaleY": round(scale_y, 5),
                "confidence": 0.35,
                "reasons": ["target_row_anchor_scale_inferred", "row_option_target_matches_click_target"],
                "anchoredRowIndex": row.get("rowIndex"),
            }
        )


def _score_candidate(candidate: dict[str, Any], menu_bounds: dict[str, Any] | None, rows: list[dict[str, Any]], fallback_target: dict[str, Any] | None) -> dict[str, Any]:
    pt = point(candidate.get("point"))
    inside_menu = point_in_bounds(pt, menu_bounds)
    selected_row: dict[str, Any] | None = None
    inside_row = False
    for row in rows:
        if point_in_bounds(pt, row.get("bounds")) is True:
            selected_row = row
            inside_row = True
            break
    target_match = bool(selected_row and row_matches_target(selected_row, fallback_target))
    score = 0.0
    reasons = list(candidate.get("reasons") or [])
    warnings: list[str] = []
    if inside_menu is True:
        score += 1.0
        reasons.append("point_inside_menu_bounds")
    elif inside_menu is False:
        warnings.append("point_outside_menu_bounds")
    if inside_row:
        score += 1.5
        reasons.append("point_inside_menu_row_bounds")
    if target_match:
        score += 1.25
        reasons.append("menu_row_matches_target_action")
    elif selected_row is not None and fallback_target:
        score -= 0.35
        warnings.append("menu_row_hit_does_not_match_target_action")
    if candidate.get("anchoredRowIndex") is not None:
        warnings.append("target_row_anchor_transform_used")
    score += float(candidate.get("confidence") or 0.0)
    center_distance = distance(pt, row_center(selected_row or {}))
    if center_distance is not None:
        if center_distance <= 8:
            score += 0.3
        elif center_distance <= 20:
            score += 0.15
    result = dict(candidate)
    result.update(
        {
            "schema": COORDINATE_TRANSFORM_SCHEMA,
            "score": round(score, 3),
            "insideMenuBounds": inside_menu,
            "insideRowBounds": inside_row if selected_row else None,
            "selectedRow": selected_row,
            "selectedRowIndex": _dict(selected_row).get("rowIndex"),
            "rowCenterDistancePx": center_distance,
            "warnings": warnings,
            "reasons": reasons,
        }
    )
    result["normalizedPoint"] = normalized_point(
        result.get("point"),
        source_space=str(result.get("sourceSpace") or "unknown"),
        target_space="menu",
        transform=str(result.get("name") or "unknown"),
        confidence=float(result.get("confidence") or 0.0),
        reasons=reasons,
    )
    return result


def infer_best_transform_for_menu_hit(
    click_event: dict[str, Any],
    menu_snapshot: dict[str, Any] | None,
    *,
    fallback_target: dict[str, Any] | None = None,
    dpi_scale: float | None = None,
) -> dict[str, Any]:
    snapshot = _dict(menu_snapshot)
    rows = [row for row in _list(snapshot.get("rowsVisualOrder")) if isinstance(row, dict)]
    menu_bounds = bounds(snapshot.get("bounds") or snapshot.get("menuBounds"))
    candidates = _candidate_points(click_event, dpi_scale=dpi_scale)
    _add_target_row_anchor_candidates(candidates, click_event, rows, fallback_target)
    scored = [_score_candidate(candidate, menu_bounds, rows, fallback_target) for candidate in candidates]
    scored.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            bool(item.get("insideRowBounds")),
            bool(item.get("insideMenuBounds")),
        ),
        reverse=True,
    )
    best = scored[0] if scored else {}
    status = "PASS" if best.get("insideRowBounds") else ("WARN" if best.get("insideMenuBounds") else "WARN")
    warnings = list(best.get("warnings") or [])
    if not rows:
        warnings.append("menu_rows_missing")
    if not menu_bounds:
        warnings.append("menu_bounds_missing")
    if not best.get("insideRowBounds"):
        warnings.append("menu_row_hit_not_confirmed")
    return {
        "schema": COORDINATE_TRANSFORM_SCHEMA,
        "status": status,
        "chosen": best,
        "alternatives": scored[:8],
        "rawPoint": {
            "client": event_point(click_event, "client"),
            "canvas": event_point(click_event, "canvas"),
            "screen": event_point(click_event, "screen"),
        },
        "menuBounds": menu_bounds,
        "rowCount": len(rows),
        "warnings": sorted(set(warnings)),
    }


def build_coordinate_space_report(input_events: list[dict[str, Any]], snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    dpi_values = []
    spaces = Counter()
    methods = Counter()
    for event in input_events:
        for space in ("screen", "client", "canvas"):
            if event_point(event, space):
                spaces[space] += 1
        scale = infer_dpi_scale(event=event)
        if scale:
            dpi_values.append(scale)
        if event.get("coordinate_capture_method"):
            methods[str(event.get("coordinate_capture_method"))] += 1
    detected = None
    if dpi_values:
        detected = round(sum(dpi_values) / len(dpi_values), 4)
    return {
        "schema": COORDINATE_SPACE_REPORT_SCHEMA,
        "generated_at_utc": utc_now(),
        "observedSpaces": dict(sorted(spaces.items())),
        "coordinateCaptureMethods": dict(sorted(methods.items())),
        "detectedDpiScale": detected,
        "inputEventCount": len(input_events),
        "telemetrySnapshotCount": len(snapshots or []),
        "warnings": [] if input_events else ["input_events_missing"],
    }


def build_coordinate_alignment_summary(
    input_events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]] | None = None,
    *,
    menu_snapshots_by_event: dict[int, dict[str, Any]] | None = None,
    transform_results: list[dict[str, Any]] | None = None,
    input_path_integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_coordinate_space_report(input_events, snapshots)
    results = list(transform_results or [])
    raw_hits = sum(1 for result in results if _dict(result.get("rawHitTest")).get("insideRowBounds") is True)
    normalized_hits = sum(1 for result in results if _dict(_dict(result.get("chosen")).get("chosen")).get("insideRowBounds") is True)
    chosen_names = Counter(str(_dict(_dict(result.get("chosen")).get("chosen")).get("name") or "unknown") for result in results)
    warnings = set(report.get("warnings") or [])
    for result in results:
        warnings.update(str(item) for item in _list(result.get("warnings")))
        warnings.update(str(item) for item in _list(_dict(result.get("chosen")).get("warnings")))
    return {
        "schema": COORDINATE_ALIGNMENT_SUMMARY_SCHEMA,
        "status": "PASS" if normalized_hits and normalized_hits >= raw_hits else ("WARN" if input_events else "WARN"),
        "generated_at_utc": utc_now(),
        "coordinateSpaceReport": report,
        "detectedDpiScale": report.get("detectedDpiScale"),
        "inputPathClassification": _dict(input_path_integrity).get("inputPathClassification"),
        "mirrorVerificationStatus": _dict(input_path_integrity).get("mirrorVerificationStatus"),
        "menuSelectionCandidateCount": len(results),
        "rawMenuRowHitCount": raw_hits,
        "normalizedMenuRowHitCount": normalized_hits,
        "menuHitTestSuccessCountByTransform": dict(sorted(chosen_names.items())),
        "chosenTransform": chosen_names.most_common(1)[0][0] if chosen_names else None,
        "candidateTransformsConsidered": sorted({str(_dict(_dict(result.get("chosen")).get("chosen")).get("name") or "unknown") for result in results}),
        "examples": results[:8],
        "warnings": sorted(warnings),
    }

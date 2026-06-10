from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import client_tick_core
from execute_next_action import (
    _configure_live_arduino_movement_safety,
    _load_pointer_calibration_for_live_movement,
    _restore_post_test_focus,
)
from input_control.backend_arduino_hid import DEFAULT_COMMAND_TIMEOUT_MS
from input_control.executor import (
    backend_from_options,
    fetch_plugin_snapshot,
    _finish_live_input_session,
    _input_controller_from_options,
    _start_live_input_session,
)
from input_control.input_geometry import (
    input_geometry_from_status,
    resolve_screen_click_point,
    source_canvas_size_from_status,
    validate_screen_point_inside_geometry,
)
from input_control.mouse_movement import MousePoint, MouseTarget, plan_mouse_movement


SCHEMA = "plane1_staircase_recovery_probe.v1"
ATTEMPT_SCHEMA = "plane1_staircase_recovery_probe_attempt.v1"
ROUTE_RELEVANT_OPTIONS = {"bottom floor", "middle floor", "top floor", "climb-down", "climb down", "climb-up", "climb up"}
STALE_MENU_MAX_AGE_MS = 5000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int | None = None) -> int | None:
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


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).lower().replace("-", " ").replace("_", " ").split())


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def _tile(value: Any) -> dict[str, int] | None:
    value = _dict(value)
    x = _int(value.get("worldX", value.get("x")))
    y = _int(value.get("worldY", value.get("y")))
    plane = _int(value.get("plane"))
    if x is None or y is None or plane is None:
        return None
    return {"worldX": x, "worldY": y, "plane": plane}


def _distance(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    first = _tile(a)
    second = _tile(b)
    if not first or not second or first["plane"] != second["plane"]:
        return None
    return math.hypot(first["worldX"] - second["worldX"], first["worldY"] - second["worldY"])


def _world_from_object(obj: dict[str, Any]) -> dict[str, int] | None:
    return _tile(obj.get("worldLocation")) or _tile(obj.get("world")) or _tile(obj) or _tile(_dict(obj.get("candidate")))


def _object_id(obj: dict[str, Any]) -> int | None:
    for key in ("objectId", "id", "rawId"):
        value = _int(obj.get(key))
        if value is not None:
            return value
    candidate = _dict(obj.get("candidate"))
    for key in ("objectId", "id", "rawId"):
        value = _int(candidate.get(key))
        if value is not None:
            return value
    target = _dict(candidate.get("target"))
    for key in ("id", "rawId"):
        value = _int(target.get(key))
        if value is not None:
            return value
    return None


def _object_name(obj: dict[str, Any]) -> str:
    candidate = _dict(obj.get("candidate"))
    target = _dict(candidate.get("target"))
    return _text(obj.get("name") or candidate.get("name") or candidate.get("targetName") or target.get("name"))


def _object_actions(obj: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for source in (obj, _dict(obj.get("candidate")), _dict(_dict(obj.get("candidate")).get("target"))):
        for key in ("actions", "menuActions", "actionNames", "expectedOptions"):
            for action in _list(source.get(key)):
                text = _text(action)
                if text:
                    actions.append(text)
    return list(dict.fromkeys(actions))


def _aim_point(obj: dict[str, Any]) -> dict[str, int] | None:
    for source in (obj, _dict(obj.get("candidate"))):
        for key in ("aimPoint", "safeAimPoint"):
            point = _dict(source.get(key))
            x = _int(point.get("canvasX", point.get("x")))
            y = _int(point.get("canvasY", point.get("y")))
            if x is not None and y is not None:
                return {"x": x, "y": y, "source": _text(point.get("source") or key)}
        projection = _dict(source.get("projectionStatus"))
        canvas_point = _dict(projection.get("canvasPoint"))
        x = _int(canvas_point.get("canvasX", canvas_point.get("x")))
        y = _int(canvas_point.get("canvasY", canvas_point.get("y")))
        if x is not None and y is not None:
            return {"x": x, "y": y, "source": "projectionStatus.canvasPoint"}
    return None


def _bounds(obj: dict[str, Any]) -> dict[str, int] | None:
    for source in (obj, _dict(obj.get("geometry")), _dict(obj.get("candidate")), _dict(_dict(obj.get("candidate")).get("geometry"))):
        for key in ("bounds", "clickboxBounds", "aimBounds"):
            bounds = _dict(source.get(key))
            x = _int(bounds.get("x"))
            y = _int(bounds.get("y"))
            w = _int(bounds.get("w", bounds.get("width")))
            h = _int(bounds.get("h", bounds.get("height")))
            if x is not None and y is not None and w and h:
                return {"x": x, "y": y, "w": w, "h": h}
    summary = _dict(_dict(obj.get("candidate")).get("geometrySummary"))
    return _bounds({"bounds": summary.get("bounds")}) if summary else None


def _attempt_points(obj: dict[str, Any], max_points: int) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    aim = _aim_point(obj)
    if aim:
        points.append(dict(aim))
    bounds = _bounds(obj)
    if bounds:
        x = bounds["x"]
        y = bounds["y"]
        w = bounds["w"]
        h = bounds["h"]
        candidates = [
            {"x": x + w / 2, "y": y + h / 2, "source": "clickbox_center"},
            {"x": x + w * 0.38, "y": y + h * 0.38, "source": "clickbox_upper_left_interior"},
            {"x": x + w * 0.62, "y": y + h * 0.38, "source": "clickbox_upper_right_interior"},
            {"x": x + w * 0.50, "y": y + h * 0.62, "source": "clickbox_lower_center_interior"},
        ]
        for point in candidates:
            points.append({"x": int(round(point["x"])), "y": int(round(point["y"])), "source": point["source"]})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for point in points:
        key = (_int(point.get("x"), -1) or -1, _int(point.get("y"), -1) or -1)
        if key in seen:
            continue
        seen.add(key)
        unique.append(point)
        if len(unique) >= max_points:
            break
    return unique


def _candidate_summary(obj: dict[str, Any]) -> dict[str, Any]:
    candidate = _dict(obj.get("candidate"))
    return {
        "name": _object_name(obj),
        "objectId": _object_id(obj),
        "world": _world_from_object(obj),
        "actions": _object_actions(obj),
        "objectKey": obj.get("objectKey") or candidate.get("objectKey"),
        "hash": obj.get("hash") or candidate.get("hash"),
        "distanceToPlayer": obj.get("distanceToPlayer") or candidate.get("distanceTiles"),
        "routeRelevance": obj.get("routeRelevance") or candidate.get("routeRelevance"),
        "projectionStatus": obj.get("projectionStatus") or candidate.get("projectionStatus"),
        "geometry": {
            "aimPoint": _aim_point(obj),
            "bounds": _bounds(obj),
            "availableGeometryTypes": _list(_dict(candidate.get("geometrySummary")).get("availableGeometryTypes")),
        },
    }


def collect_staircases(status: dict[str, Any], player: dict[str, int] | None, *, radius_tiles: int) -> list[dict[str, Any]]:
    sources = [
        ("serviceRouteObjectCensus", _list(_dict(status.get("serviceRouteObjectCensus")).get("topRouteObjects"))),
        (
            "brain.serviceRouteContext.routeObjectCensus",
            _list(_dict(_dict(_dict(status.get("brain")).get("serviceRouteContext")).get("routeObjectCensus")).get("topRouteObjects")),
        ),
        (
            "brain.returnRouteContext.routeObjectCensus",
            _list(_dict(_dict(_dict(status.get("brain")).get("returnRouteContext")).get("routeObjectCensus")).get("topRouteObjects")),
        ),
    ]
    objects: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for source, values in sources:
        for raw in values:
            obj = _dict(raw)
            name = _object_name(obj)
            object_id = _object_id(obj)
            world = _world_from_object(obj)
            if not world:
                continue
            distance = _distance(player, world) if player else None
            is_staircase = "stair" in _norm(name) or object_id in {16672, 56230, 56231}
            if not is_staircase:
                continue
            if distance is not None and distance > radius_tiles:
                continue
            key = (object_id, world["worldX"], world["worldY"], world["plane"], obj.get("objectKey"))
            if key in seen:
                continue
            seen.add(key)
            item = _candidate_summary(obj)
            item["source"] = source
            objects.append(item)
    objects.sort(
        key=lambda item: (
            0 if item.get("objectId") == 16672 and _tile(item.get("world")) == {"worldX": 3204, "worldY": 3229, "plane": 1} else 1,
            float(item.get("distanceToPlayer") if item.get("distanceToPlayer") is not None else 999),
            item.get("objectId") or 999999,
        )
    )
    return objects


def _entries_from_menu(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    entries = []
    for index, entry in enumerate(_list(sample.get("entries"))):
        item = _dict(entry)
        if not item:
            continue
        row = _dict(item.get("rowBounds") or item.get("rowCanvasGeometry") or item.get("bounds"))
        entries.append(
            {
                "index": index,
                "option": _text(item.get("option")),
                "target": _text(item.get("target")),
                "type": _text(item.get("type")),
                "identifier": _int(item.get("identifier")),
                "param0": _int(item.get("param0")),
                "param1": _int(item.get("param1")),
                "rowBounds": row or None,
            }
        )
    return entries


def _menu_summary(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    sample = client_tick_core.latest_hover_menu_sample(snapshot)
    hot = client_tick_core.compact_hot_explanation(snapshot)
    menu_age_ms = _int(_dict(hot).get("postMenuSortAgeMillis"))
    stale_reasons: list[str] = []
    if menu_age_ms is not None and menu_age_ms > STALE_MENU_MAX_AGE_MS:
        stale_reasons.append("post_menu_sort_stale")
    if bool(_dict(_dict(snapshot).get("freshness")).get("allCachedPacketsStale")):
        stale_reasons.append("plugin_all_packets_stale")
    return {
        "sample": sample,
        "topOption": _text(_dict(sample).get("topOption") if _dict(sample).get("topOption") is not None else _dict(sample).get("option")),
        "topTarget": _text(_dict(sample).get("topTarget") if _dict(sample).get("topTarget") is not None else _dict(sample).get("target")),
        "menuOpen": bool(_dict(sample).get("menuOpen")),
        "menuBounds": _dict(sample).get("menuBounds"),
        "entries": _entries_from_menu(sample),
        "clientTickHot": hot,
        "menuEvidenceStale": bool(stale_reasons),
        "staleReasons": stale_reasons,
    }


def _menu_is_stale(menu: dict[str, Any]) -> bool:
    menu = _dict(menu)
    return bool(menu.get("menuEvidenceStale"))


def _contains_option(menu: dict[str, Any], options: set[str]) -> bool:
    for entry in _list(menu.get("entries")):
        if _norm(_dict(entry).get("option")) in options:
            return True
    top = _norm(menu.get("topOption"))
    return top in options


def _matching_menu_entries(menu: dict[str, Any], target_name: str, object_id: int | None) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for entry in _list(menu.get("entries")):
        item = _dict(entry)
        option = _norm(item.get("option"))
        target = _norm(item.get("target"))
        if option not in ROUTE_RELEVANT_OPTIONS:
            continue
        if option in {"bottom floor", "middle floor", "top floor"}:
            matches.append(item)
            continue
        if target_name and _norm(target_name) and _norm(target_name) not in target:
            continue
        identifier = _int(item.get("identifier"))
        if object_id is not None and identifier not in {None, object_id}:
            continue
        matches.append(item)
    return matches


def _schema_gap(attempts: list[dict[str, Any]]) -> str:
    missing_row_bounds = any(
        _list(_dict(attempt.get("rightClickMenu")).get("entries"))
        and not any(_dict(entry).get("rowBounds") for entry in _list(_dict(attempt.get("rightClickMenu")).get("entries")))
        for attempt in attempts
    )
    stale_menu = any(
        _menu_is_stale(_dict(attempt.get("hoverMenu"))) or _menu_is_stale(_dict(attempt.get("rightClickMenu")))
        for attempt in attempts
    )
    lines = ["# Plane-1 Staircase Recovery Probe Schema Gap Report", ""]
    if stale_menu:
        lines.append("- WARN: Hover/right-click menu evidence was stale. Do not use these menu rows to enrich a route guide.")
    else:
        lines.append("- PASS: Captured menu evidence was not marked stale by the probe.")
    if missing_row_bounds:
        lines.append("- WARN: Menu entries were captured without per-row bounds. This is acceptable for evidence, but not enough for automatic row selection without a trusted fallback.")
    else:
        lines.append("- PASS: No missing menu-row-bound issue was observed in the captured attempts.")
    lines.append("")
    lines.append("- The probe does not perform a route transition; postcondition evidence still requires a future validation click if the route guide is enriched.")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Focused plane-1 Staircase hover/menu evidence probe.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:8893")
    parser.add_argument("--out-dir")
    parser.add_argument("--arduino-port", default="COM6")
    parser.add_argument("--arduino-baud", type=int, default=115200)
    parser.add_argument("--arduino-handshake-timeout-ms", type=int, default=2000)
    parser.add_argument("--arduino-command-timeout-ms", type=int, default=DEFAULT_COMMAND_TIMEOUT_MS)
    parser.add_argument("--arduino-session-token", default="auto")
    parser.add_argument("--arduino-fail-closed", action="store_true", default=True)
    parser.add_argument("--no-arduino-fail-closed", dest="arduino_fail_closed", action="store_false")
    parser.add_argument("--arduino-pointer-calibration-path")
    parser.add_argument("--arduino-pointer-calibration-max-age-hours", type=float, default=24.0)
    parser.add_argument("--allow-uncalibrated-arduino-movement", action="store_true")
    parser.add_argument("--arduino-move-settle-ms", type=int, default=80)
    parser.add_argument("--arduino-move-poll-ms", type=int, default=10)
    parser.add_argument("--arduino-move-noeffect-timeout-ms", type=int, default=200)
    parser.add_argument("--arduino-move-noeffect-retries", type=int, default=2)
    parser.add_argument("--arduino-min-effective-move-px", type=int, default=2)
    parser.add_argument("--arduino-retry-scale", type=float, default=1.25)
    parser.add_argument("--arduino-move-max-consecutive-noeffect", type=int, default=3)
    parser.add_argument("--input-profile", default="instant_debug")
    parser.add_argument("--movement-profile", default="linear_debug")
    parser.add_argument("--backend", default="arduino")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--focus-runelite", action="store_true", default=True)
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--target-world-x", type=int, default=3204)
    parser.add_argument("--target-world-y", type=int, default=3229)
    parser.add_argument("--target-plane", type=int, default=1)
    parser.add_argument("--player-world-x", type=int, default=3206)
    parser.add_argument("--player-world-y", type=int, default=3229)
    parser.add_argument("--player-plane", type=int, default=1)
    parser.add_argument("--near-radius-tiles", type=int, default=8)
    parser.add_argument("--max-targets", type=int, default=3)
    parser.add_argument("--max-points-per-target", type=int, default=4)
    parser.add_argument("--hover-settle-ms", type=int, default=250)
    parser.add_argument("--post-right-click-ms", type=int, default=250)
    parser.add_argument("--snapshot-timeout", type=float, default=1.5)
    parser.add_argument("--json", action="store_true")
    return parser


def _prepare_live_input_args(args: argparse.Namespace) -> None:
    args.hover_only = True
    args.execute = False
    args.camera_self_test = False
    args.dry_run = False
    args.allow_software_input = False
    args.unsafe_allow_pyautogui_live = False
    args.unsafe_allow_software_live = False
    args.arduino_require_monitor = bool(getattr(args, "arduino_require_monitor", False))
    args.arduino_monitor_status_path = getattr(args, "arduino_monitor_status_path", None)
    args.input_integrity_status_path = getattr(args, "input_integrity_status_path", None)
    args.input_integrity_backend_status_path = getattr(args, "input_integrity_backend_status_path", None)
    args.arduino_monitor_max_age_ms = int(getattr(args, "arduino_monitor_max_age_ms", 5000) or 5000)
    args.arduino_vid = getattr(args, "arduino_vid", None)
    args.arduino_pid = getattr(args, "arduino_pid", None)


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    _prepare_live_input_args(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("recordings") / f"{stamp}_plane1_staircase_recovery_probe"
    summary_path = out_dir / "plane1_staircase_recovery_probe.json"
    attempts_path = out_dir / "plane1_staircase_recovery_probe.jsonl"
    schema_gap_path = out_dir / "schema_gap_report.md"
    status = fetch_json(daemon_status_url(args.daemon_url), timeout=20.0)
    player = _tile(status.get("playerLocation") or _dict(_dict(status.get("brain")).get("currentContextSummary")).get("player"))
    desired_player = {"worldX": args.player_world_x, "worldY": args.player_world_y, "plane": args.player_plane}
    near_distance = _distance(player, desired_player) if player else None
    near_plane1_state = bool(player and player["plane"] == args.player_plane and near_distance is not None and near_distance <= args.near_radius_tiles)
    input_geometry = input_geometry_from_status(status)
    source_canvas_size = source_canvas_size_from_status(status)
    staircases = collect_staircases(status, player, radius_tiles=args.near_radius_tiles)
    target_world = {"worldX": args.target_world_x, "worldY": args.target_world_y, "plane": args.target_plane}
    blockers: list[str] = []
    warnings: list[str] = []
    if not near_plane1_state:
        blockers.append("player_not_near_plane1_recovery_state")
    if input_geometry.get("status") != "PASS":
        blockers.append("input_geometry_unavailable")
    if not staircases:
        blockers.append("plane1_staircase_objects_missing")
    selected_staircases = staircases[: max(1, int(args.max_targets or 1))]
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "FAIL" if blockers else "WARN",
        "generatedAtUtc": _now_utc(),
        "daemonUrl": args.daemon_url,
        "snapshotUrl": args.snapshot_url,
        "probeFolder": str(out_dir.resolve()),
        "player": player,
        "expectedPlayerState": desired_player,
        "nearPlane1RecoveryState": near_plane1_state,
        "nearDistanceTiles": near_distance,
        "targetWorld": target_world,
        "expectedRouteLeg": "Bank_to_Woodcutting_area",
        "expectedRecoveryOption": "Climb-down",
        "inputGeometry": input_geometry,
        "sourceCanvasSize": source_canvas_size,
        "staircaseObjects": staircases,
        "attempts": [],
        "safeSamePlaneRecoveryAction": False,
        "capturedOptions": [],
        "matchingMenuEntries": [],
        "routeGuideUpdated": False,
        "blockers": blockers,
        "warnings": warnings,
    }
    if blockers:
        _write_json(summary_path, payload)
        schema_gap_path.write_text(_schema_gap([]), encoding="utf-8")
        return payload

    backend = backend_from_options(args)
    pre_action_focus = _restore_post_test_focus("runelite", window_title_filter=args.window_title_filter) if args.focus_runelite else None
    payload["preProbeFocus"] = pre_action_focus
    movement_safety = None
    if args.backend == "arduino" and not args.allow_uncalibrated_arduino_movement:
        calibration_status = _load_pointer_calibration_for_live_movement(args)
        payload["pointerCalibration"] = calibration_status
        if calibration_status.get("status") != "PASS":
            payload["status"] = "FAIL"
            payload["blockers"].append("arduino_pointer_calibration_required")
            _write_json(summary_path, payload)
            schema_gap_path.write_text(_schema_gap([]), encoding="utf-8")
            return payload
        movement_safety = _configure_live_arduino_movement_safety(args, backend, calibration_status)
        payload["movementSafety"] = movement_safety
        if movement_safety.get("status") != "PASS":
            payload["status"] = "FAIL"
            payload["blockers"].append("arduino_live_movement_safety_unavailable")
            _write_json(summary_path, payload)
            schema_gap_path.write_text(_schema_gap([]), encoding="utf-8")
            return payload

    live_input_status = None
    attempts: list[dict[str, Any]] = []
    try:
        live_input_status = _start_live_input_session(args, backend)
        payload["liveInputStatusBefore"] = live_input_status
        controller = _input_controller_from_options(backend, args)
        for target_index, staircase in enumerate(selected_staircases):
            points = _attempt_points(staircase, max_points=max(1, int(args.max_points_per_target or 1)))
            for point_index, canvas_point in enumerate(points):
                resolution = resolve_screen_click_point(
                    {"x": canvas_point["x"], "y": canvas_point["y"]},
                    click_point_space="canvas",
                    input_geometry=input_geometry,
                    source_canvas_size=source_canvas_size,
                )
                validation = validate_screen_point_inside_geometry(resolution.get("screenClickPoint"), input_geometry)
                attempt = {
                    "schema": ATTEMPT_SCHEMA,
                    "generatedAtUtc": _now_utc(),
                    "targetIndex": target_index,
                    "pointIndex": point_index,
                    "staircase": staircase,
                    "canvasPoint": canvas_point,
                    "screenPointResolution": resolution,
                    "screenPointValidation": validation,
                    "hoverMenu": None,
                    "rightClickMenu": None,
                    "rightClickAttempted": False,
                    "escapeSent": False,
                    "status": "PENDING",
                    "blockers": [],
                    "warnings": [],
                }
                if resolution.get("status") == "FAIL" or validation.get("status") == "FAIL":
                    attempt["status"] = "FAIL"
                    attempt["blockers"].append(str(validation.get("reason") or resolution.get("clickFailureBucket") or "point_unusable"))
                    attempts.append(attempt)
                    _append_jsonl(attempts_path, attempt)
                    continue
                screen = _dict(resolution.get("screenClickPoint"))
                current = backend.current_position() if callable(getattr(backend, "current_position", None)) else (screen["x"], screen["y"])
                geometry = _dict(staircase.get("geometry"))
                bounds = _dict(geometry.get("bounds"))
                plan = plan_mouse_movement(
                    MousePoint(int(current[0]), int(current[1])),
                    MouseTarget(
                        int(screen["x"]),
                        int(screen["y"]),
                        radius_px=4,
                        width_px=_int(bounds.get("w"), 8) or 8,
                        height_px=_int(bounds.get("h"), 8) or 8,
                        label="plane1_staircase_probe",
                        source=str(canvas_point.get("source") or "probe"),
                    ),
                    args.movement_profile,
                )
                try:
                    controller.move_mouse(plan)
                    time.sleep(max(0, int(args.hover_settle_ms or 0)) / 1000.0)
                    hover_snapshot = fetch_plugin_snapshot(
                        args.snapshot_url,
                        timeout=float(args.snapshot_timeout),
                        client_tick_tail=10,
                        menu_entry_limit=20,
                    )
                    attempt["hoverMenu"] = _menu_summary(hover_snapshot)
                    controller.click_current_position(button="right", hold_ms=0)
                    attempt["rightClickAttempted"] = True
                    time.sleep(max(0, int(args.post_right_click_ms or 0)) / 1000.0)
                    right_snapshot = fetch_plugin_snapshot(
                        args.snapshot_url,
                        timeout=float(args.snapshot_timeout),
                        client_tick_tail=10,
                        menu_entry_limit=20,
                    )
                    attempt["rightClickMenu"] = _menu_summary(right_snapshot)
                    try:
                        backend.press("Escape")
                        attempt["escapeSent"] = True
                    except Exception as error:  # noqa: BLE001
                        attempt["warnings"].append(f"escape_close_failed: {type(error).__name__}: {error}")
                    stale_menu_evidence = _menu_is_stale(_dict(attempt.get("hoverMenu"))) or _menu_is_stale(_dict(attempt.get("rightClickMenu")))
                    entries = [] if stale_menu_evidence else _matching_menu_entries(
                        _dict(attempt.get("rightClickMenu")),
                        _object_name(staircase),
                        _object_id(staircase),
                    )
                    if stale_menu_evidence:
                        attempt["warnings"].append("plugin_menu_evidence_stale")
                    if entries:
                        attempt["status"] = "PASS"
                        attempt["matchingMenuEntries"] = entries
                    else:
                        attempt["status"] = "WARN"
                        if stale_menu_evidence:
                            attempt["blockers"].append("plugin_menu_evidence_stale")
                        attempt["blockers"].append("route_relevant_menu_option_not_captured")
                    attempts.append(attempt)
                    _append_jsonl(attempts_path, attempt)
                except Exception as error:  # noqa: BLE001
                    attempt["status"] = "FAIL"
                    attempt["blockers"].append(f"{type(error).__name__}: {error}")
                    attempts.append(attempt)
                    _append_jsonl(attempts_path, attempt)
    finally:
        if live_input_status is not None:
            _finish_live_input_session(backend, live_input_status, options=args)
            payload["liveInputStatusAfter"] = live_input_status
        close = getattr(backend, "close", None)
        if callable(close):
            close()

    captured_options: list[str] = []
    stale_captured_options: list[str] = []
    matching_entries: list[dict[str, Any]] = []
    menu_evidence_stale = False
    for attempt in attempts:
        for menu_key in ("hoverMenu", "rightClickMenu"):
            menu = _dict(attempt.get(menu_key))
            for entry in _list(menu.get("entries")):
                option = _text(_dict(entry).get("option"))
                if not option:
                    continue
                if _menu_is_stale(menu):
                    menu_evidence_stale = True
                    stale_captured_options.append(option)
                else:
                    captured_options.append(option)
            matching_entries.extend(_list(attempt.get("matchingMenuEntries")))
    payload["attempts"] = attempts
    payload["capturedOptions"] = list(dict.fromkeys(captured_options))
    payload["staleCapturedOptions"] = list(dict.fromkeys(stale_captured_options))
    payload["menuEvidenceStale"] = menu_evidence_stale
    payload["matchingMenuEntries"] = matching_entries
    payload["safeSamePlaneRecoveryAction"] = bool(matching_entries)
    payload["bottomFloorCaptured"] = any(_norm(entry.get("option")) == "bottom floor" for entry in matching_entries)
    payload["climbDownCaptured"] = any(_norm(entry.get("option")) in {"climb-down", "climb down"} for entry in matching_entries)
    payload["climbUpCaptured"] = any(_norm(entry.get("option")) in {"climb-up", "climb up"} for entry in matching_entries)
    payload["menuRowBoundsCaptured"] = any(_dict(entry).get("rowBounds") for entry in matching_entries)
    payload["status"] = "PASS" if payload["safeSamePlaneRecoveryAction"] else "WARN"
    if menu_evidence_stale:
        payload["blockers"].append("plugin_menu_evidence_stale")
    if not payload["safeSamePlaneRecoveryAction"]:
        payload["blockers"].append("route_relevant_menu_option_not_captured")
    _write_json(summary_path, payload)
    schema_gap_path.write_text(_schema_gap(attempts), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_probe(args)
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else f"Probe {payload.get('status')} -> {payload.get('probeFolder')}\n", end="")
    return 0 if payload.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

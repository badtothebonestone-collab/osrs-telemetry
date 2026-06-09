from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import telemetry_schema
import telemetry_sources
import route_template
import route_monitor
import banking_lifecycle
import traversal_lifecycle
import woodcutting_lifecycle
import input_trace_joiner
import human_click_profile
import interruption_lifecycle
import combat_damage_summary
import woodcutting_loop_lifecycle


ANALYZER_SCHEMA_VERSION = "manual_telemetry_analysis.v1"
GAP_REPORT_SCHEMA_VERSION = "manual_telemetry_schema_gap_report.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _load_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events_path = path / "events.jsonl" if path.is_dir() else path
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"events.jsonl line {line_number} JSONDecodeError: {error.msg}")
                    continue
                if isinstance(value, dict):
                    events.append(value)
                else:
                    warnings.append(f"events.jsonl line {line_number} was not an object")
    except FileNotFoundError:
        warnings.append(f"events file missing: {events_path}")
    except OSError as error:
        warnings.append(f"events file unreadable: {type(error).__name__}: {error}")
    return events, warnings


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _ticks(events: list[dict[str, Any]]) -> list[Any]:
    ticks: list[Any] = []
    for event in events:
        tick = _first(event.get("latest_tick"), _dict(event.get("high_value_fields")).get("latest_tick"))
        if tick is not None:
            ticks.append(tick)
    return ticks


def _field_sets(events: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    present: set[str] = set()
    missing: set[str] = set()
    for event in events:
        presence = _dict(event.get("field_presence"))
        present.update(str(item) for item in presence.get("available_fields") or [])
        missing.update(str(item) for item in presence.get("missing_fields") or [])
        present.update(str(item) for item in event.get("available_fields") or [])
        missing.update(str(item) for item in event.get("missing_fields") or [])
    missing -= present
    return present, missing


def _source_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    parse_failures: list[dict[str, Any]] = []
    stale_counts: Counter[str] = Counter()
    for event in events:
        for source in event.get("sources") or []:
            if not isinstance(source, dict):
                continue
            name = str(source.get("name") or source.get("path") or "unknown")
            item = by_source.setdefault(
                name,
                {
                    "name": name,
                    "path": source.get("path"),
                    "url": source.get("url"),
                    "source_kind": source.get("source_kind"),
                    "observations": 0,
                    "latest_modified_utc": None,
                    "latest_age_seconds": None,
                    "latest_parse_status": None,
                    "latest_size_bytes": None,
                    "stale_observations": 0,
                    "parse_failures": 0,
                },
            )
            item["observations"] += 1
            item["path"] = source.get("path") or item.get("path")
            item["url"] = source.get("url") or item.get("url")
            item["source_kind"] = source.get("source_kind") or item.get("source_kind")
            item["latest_modified_utc"] = source.get("modified_utc") or item.get("latest_modified_utc")
            item["latest_age_seconds"] = source.get("age_seconds")
            item["latest_parse_status"] = source.get("parse_status")
            item["latest_size_bytes"] = source.get("size_bytes")
            if source.get("stale"):
                item["stale_observations"] += 1
                stale_counts[name] += 1
            status = source.get("parse_status")
            if status not in {None, "ok", "missing"} or source.get("read_error"):
                item["parse_failures"] += 1
                parse_failures.append(
                    {
                        "source": name,
                        "status": status,
                        "error": source.get("read_error"),
                        "time": event.get("wall_time_utc"),
                    }
                )
    return {
        "source_files_observed": sorted(by_source.values(), key=lambda item: item["name"]),
        "stale_sources": dict(stale_counts),
        "parse_failures": parse_failures,
    }


def _value_at(event: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    values, _paths = telemetry_schema.lookup_any(event, list(aliases))
    return values[0] if values else None


def _changed_fields(snapshot_events: list[dict[str, Any]]) -> list[str]:
    paths = {
        "tick": ("high_value_fields.latest_tick",),
        "game_state": ("high_value_fields.game_state",),
        "player_animation": ("high_value_fields.player.animation",),
        "run_energy": ("high_value_fields.player.runEnergy",),
        "inventory_count": ("high_value_fields.inventory.itemCount", "high_value_fields.inventory.freeSlots"),
        "equipment": ("high_value_fields.equipment.items",),
        "bank_state": ("high_value_fields.bank.bankOpen", "high_value_fields.bank.open"),
        "hover": ("high_value_fields.hover.topTarget", "high_value_fields.hover.topOption"),
        "menu": ("high_value_fields.menu.menuOpen",),
        "nearby_objects": ("high_value_fields.nearby_objects",),
        "route_objects": ("high_value_fields.route_objects",),
        "nearby_npcs": ("high_value_fields.nearby_npcs",),
        "widgets": ("high_value_fields.widgets",),
    }
    changed: list[str] = []
    previous: dict[str, Any] = {}
    for event in snapshot_events:
        for name, aliases in paths.items():
            current = _value_at(event, aliases)
            if name in previous and current != previous[name]:
                changed.append(name)
            if current is not None:
                previous[name] = current
    return sorted(set(changed))


def _detect_transitions(snapshot_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    previous: dict[str, Any] = {}

    def add(event: dict[str, Any], transition: str, before: Any, after: Any) -> None:
        transitions.append(
            {
                "transition": transition,
                "wall_time_utc": event.get("wall_time_utc"),
                "elapsed_seconds": event.get("elapsed_seconds"),
                "before": before,
                "after": after,
            }
        )

    for event in snapshot_events:
        current = {
            "animation": _value_at(event, ("high_value_fields.player.animation", "**.animation")),
            "moving": _value_at(event, ("high_value_fields.player.isMoving", "**.isMoving", "**.moving")),
            "inventory_count": _value_at(event, ("high_value_fields.inventory.itemCount", "high_value_fields.inventory.filledSlots")),
            "inventory_free": _value_at(event, ("high_value_fields.inventory.freeSlots",)),
            "bank_open": _value_at(event, ("high_value_fields.bank.bankOpen", "high_value_fields.bank.open", "**.bankOpen")),
            "menu_open": _value_at(event, ("high_value_fields.menu.menuOpen", "**.menuOpen")),
            "hover_target": _value_at(event, ("high_value_fields.hover.topTarget", "**.topTarget")),
            "widgets": _value_at(event, ("high_value_fields.widgets", "**.widgets")),
            "destination": _value_at(event, ("**.destination", "**.localDestination")),
        }
        if previous:
            if previous.get("animation") in (None, -1, "-1", 0, "0") and current.get("animation") not in (None, -1, "-1", 0, "0"):
                add(event, "idle -> animating", previous.get("animation"), current.get("animation"))
            if previous.get("moving") is True and current.get("moving") is False:
                add(event, "moving -> stopped", True, False)
            if (previous.get("inventory_count"), previous.get("inventory_free")) != (current.get("inventory_count"), current.get("inventory_free")):
                if current.get("inventory_count") is not None or current.get("inventory_free") is not None:
                    add(event, "inventory count changed", previous.get("inventory_count"), current.get("inventory_count"))
            if previous.get("bank_open") is False and current.get("bank_open") is True:
                add(event, "bank closed -> open", False, True)
            if previous.get("menu_open") is False and current.get("menu_open") is True:
                add(event, "menu closed -> open", False, True)
            if previous.get("hover_target") != current.get("hover_target") and current.get("hover_target") is not None:
                add(event, "hover target changed", previous.get("hover_target"), current.get("hover_target"))
            if repr(previous.get("widgets")) != repr(current.get("widgets")) and current.get("widgets") is not None:
                add(event, "widget appeared/disappeared", telemetry_schema.compact_value(previous.get("widgets")), telemetry_schema.compact_value(current.get("widgets")))
            if previous.get("destination") != current.get("destination") and current.get("destination") is not None:
                add(event, "destination changed", previous.get("destination"), current.get("destination"))
        for key, value in current.items():
            if value is not None:
                previous[key] = value
    return transitions


def _markers(events: list[dict[str, Any]], snapshot_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = sorted(snapshot_events, key=lambda event: float(event.get("elapsed_seconds") or 0.0))
    markers: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "manual_marker":
            continue
        elapsed = float(event.get("elapsed_seconds") or 0.0)
        nearby = sorted(
            snapshots,
            key=lambda snapshot: abs(float(snapshot.get("elapsed_seconds") or 0.0) - elapsed),
        )[:3]
        markers.append(
            {
                "label": event.get("label"),
                "wall_time_utc": event.get("wall_time_utc"),
                "elapsed_seconds": elapsed,
                "nearby_snapshots": [
                    {
                        "elapsed_seconds": snapshot.get("elapsed_seconds"),
                        "latest_tick": snapshot.get("latest_tick"),
                        "changed_sources": snapshot.get("changed_sources") or [],
                    }
                    for snapshot in nearby
                ],
            }
        )
    return markers


def _recommendations(categories: dict[str, list[str]]) -> dict[str, list[str]]:
    bridge = categories.get("requires_bridge_export") or []
    sidecar = categories.get("computable_in_sidecar") or []
    present = categories.get("present") or []
    review = categories.get("needs_manual_review") or []
    return {
        "likely_useful_bridge_fields": bridge[:12],
        "likely_useful_sidecar_context_fields": (sidecar + present)[:16],
        "needs_manual_review": review[:12],
    }


def analyze_recording(recording_path: str | Path) -> dict[str, Any]:
    path = Path(recording_path)
    events, warnings = _load_events(path)
    manifest = _load_json(path / "manifest.json")
    snapshot_events = [event for event in events if event.get("event_type") == "source_snapshot"]
    ticks = _ticks(snapshot_events)
    present, missing = _field_sets(snapshot_events)
    current_scan = telemetry_schema.scan_field_presence(snapshot_events)
    present.update(str(item) for item in current_scan.get("available_fields") or [])
    missing.update(str(item) for item in current_scan.get("missing_fields") or [])
    missing -= present
    combined_scan = {
        "fields": {
            spec.name: {"present": spec.name in present}
            for spec in telemetry_schema.FIELD_SPECS
        }
    }
    categories = telemetry_schema.categorize_schema_gaps(combined_scan)
    source_stats = _source_stats(snapshot_events)
    discovered_sources = _dict(manifest.get("source_discovery")).get("sources") or []
    bank_ui_discovery = [source for source in discovered_sources if isinstance(source, dict) and source.get("name") == "bank_ui"]
    bank_ui_observed = next((source for source in source_stats["source_files_observed"] if source.get("name") == "bank_ui"), None)
    combat_state_discovery = [source for source in discovered_sources if isinstance(source, dict) and source.get("name") == "combat_state"]
    combat_state_observed = next((source for source in source_stats["source_files_observed"] if source.get("name") == "combat_state"), None)
    start = next((event for event in events if event.get("event_type") == "recording_start"), None)
    stop = next((event for event in reversed(events) if event.get("event_type") == "recording_stop"), None)
    duration = _first(
        _dict(stop).get("duration_seconds"),
        (float(stop.get("elapsed_seconds")) if stop and stop.get("elapsed_seconds") is not None else None),
        (
            float(events[-1].get("elapsed_seconds") or 0.0) - float(events[0].get("elapsed_seconds") or 0.0)
            if len(events) >= 2
            else 0.0
        ),
    )
    parse_failure_warnings = [
        f"{item.get('source')}: {item.get('status')} {item.get('error') or ''}".strip()
        for item in source_stats["parse_failures"][:20]
    ]
    input_join = input_trace_joiner.analyze_recording(path, write=False, include_mapping=(path / "input_events.jsonl").exists())
    lifecycle = woodcutting_lifecycle.analyze_events(
        events,
        input_action_classifications=input_join.get("input_action_classifications") or [],
        warnings=[],
    )
    summary = {
        "schema_version": ANALYZER_SCHEMA_VERSION,
        "generated_at_utc": telemetry_sources.utc_now(),
        "recording_path": str(path),
        "recording_id": path.name,
        "label": _dict(start).get("label"),
        "description": _dict(start).get("description") or "",
        "session_id": _dict(start).get("session_id"),
        "duration_seconds": duration,
        "event_count": len(events),
        "snapshot_count": len(snapshot_events),
        "tick_range": {
            "first": min(ticks) if ticks else None,
            "last": max(ticks) if ticks else None,
            "count": len(ticks),
        },
        "source_files_observed": source_stats["source_files_observed"],
        "bank_ui_source": {
            "configured": bool(bank_ui_discovery),
            "observed": bool(bank_ui_observed),
            "discovery": bank_ui_discovery[0] if bank_ui_discovery else None,
            "observations": _dict(bank_ui_observed).get("observations") if bank_ui_observed else 0,
            "latest_parse_status": _dict(bank_ui_observed).get("latest_parse_status") if bank_ui_observed else None,
            "latest_age_seconds": _dict(bank_ui_observed).get("latest_age_seconds") if bank_ui_observed else None,
            "stale_observations": _dict(bank_ui_observed).get("stale_observations") if bank_ui_observed else 0,
        },
        "combat_state_source": {
            "configured": bool(combat_state_discovery),
            "observed": bool(combat_state_observed),
            "discovery": combat_state_discovery[0] if combat_state_discovery else None,
            "observations": _dict(combat_state_observed).get("observations") if combat_state_observed else 0,
            "latest_parse_status": _dict(combat_state_observed).get("latest_parse_status") if combat_state_observed else None,
            "latest_age_seconds": _dict(combat_state_observed).get("latest_age_seconds") if combat_state_observed else None,
            "stale_observations": _dict(combat_state_observed).get("stale_observations") if combat_state_observed else 0,
        },
        "source_freshness": {
            "stale_sources": source_stats["stale_sources"],
            "parse_failure_count": len(source_stats["parse_failures"]),
        },
        "parse_failures": source_stats["parse_failures"],
        "fields_present": sorted(present),
        "fields_missing": sorted(missing),
        "fields_changed": _changed_fields(snapshot_events),
        "markers": _markers(events, snapshot_events),
        "state_transitions": _detect_transitions(snapshot_events),
        "schema_gap_categories": categories,
        "recommendations": _recommendations(categories),
        "warnings": sorted(set(warnings + parse_failure_warnings)),
    }
    if lifecycle.get("status") != "FAIL" or lifecycle.get("evidence"):
        summary["woodcutting_lifecycle"] = lifecycle
    interruption = interruption_lifecycle.analyze_data(
        events=events,
        woodcutting_lifecycle=lifecycle,
        summaries={"summary": summary},
        recording_path=path,
    )
    if interruption.get("interruptionDetected") or _dict(interruption.get("combat")).get("combatStateSnapshotCount") or interruption.get("missingCapabilities"):
        summary["interruption_lifecycle"] = interruption
        if isinstance(summary.get("woodcutting_lifecycle"), dict):
            summary["woodcutting_lifecycle"] = woodcutting_lifecycle.attach_interruption(summary["woodcutting_lifecycle"], interruption)
    damage = combat_damage_summary.analyze_data(events=events, interruption_lifecycle_summary=summary.get("interruption_lifecycle") or interruption, recording_path=path)
    if damage.get("combatObserved") or _dict(damage.get("hitsplats")).get("total"):
        summary["combat_damage_summary"] = damage
    preflight = _dict(_dict(manifest.get("input_capture")).get("preflight"))
    if preflight:
        summary["input_preflight"] = preflight
        if not preflight.get("success"):
            summary["warnings"] = sorted(set(list(summary.get("warnings") or []) + ["input preflight failed before this recording"]))
    if (path / "input_events.jsonl").exists() or (path / "arduino_events.jsonl").exists() or (path / "arduino_action_commands.jsonl").exists():
        summary.update(
            {
                "input_trace": input_join.get("input_trace"),
                "click_analysis": input_join.get("click_analysis"),
                "hover_analysis": input_join.get("hover_analysis"),
                "camera_behavior": input_join.get("camera_behavior"),
                "arduino_trace": input_join.get("arduino_trace"),
                "arduino_live_mirror": input_join.get("arduino_live_mirror"),
                "input_action_summary": input_join.get("input_action_summary"),
                "target_match_summary": input_join.get("target_match_summary"),
                "menu_interaction_summary": input_join.get("menu_interaction_summary"),
                "coordinate_alignment_summary": input_join.get("coordinate_alignment_summary"),
                "input_path_integrity_summary": input_join.get("input_path_integrity_summary"),
                "arduino_mirror_verification": input_join.get("arduino_mirror_verification"),
                "mirror_action_timing": input_join.get("mirror_action_timing"),
            }
        )
        if input_join.get("vm_mouse_arduino_mapping"):
            summary["vm_mouse_arduino_mapping"] = input_join.get("vm_mouse_arduino_mapping")
        summary["warnings"] = sorted(set(summary["warnings"] + list(input_join.get("warnings") or [])))
    traversal = traversal_lifecycle.analyze_data(
        events=events,
        joined_input_telemetry=input_join.get("joined_input_telemetry_rows") or [],
        input_action_classifications=input_join.get("input_action_classifications") or [],
        target_match_quality=input_join.get("target_match_quality") or [],
        menu_interactions=input_join.get("menu_interactions") or [],
        summaries={
            "summary": summary,
            "input_action_summary": summary.get("input_action_summary") or {},
            "target_match_summary": summary.get("target_match_summary") or {},
            "menu_interaction_summary": summary.get("menu_interaction_summary") or {},
            "coordinate_alignment_summary": summary.get("coordinate_alignment_summary") or {},
            "camera_behavior_summary": summary.get("camera_behavior") or {},
            "vm_mouse_arduino_mapping": summary.get("vm_mouse_arduino_mapping") or {},
        },
        recording_path=path,
    )
    if traversal.get("status") != "FAIL" or traversal.get("evidence"):
        summary["traversal_lifecycle"] = traversal
    banking = banking_lifecycle.analyze_data(
        events=events,
        input_action_classifications=input_join.get("input_action_classifications") or [],
        target_match_quality=input_join.get("target_match_quality") or [],
        menu_interactions=input_join.get("menu_interactions") or [],
        summaries={"summary": summary},
        recording_path=path,
    )
    if banking.get("status") != "FAIL" or banking.get("evidence"):
        summary["banking_lifecycle"] = banking
    refresh_woodcutting_loop_summary(path, summary, write=False)
    return summary


def refresh_woodcutting_loop_summary(recording_path: Path, summary: dict[str, Any], *, pretty: bool = True, write: bool = True) -> dict[str, Any]:
    lifecycle = woodcutting_loop_lifecycle.analyze_data(summary=summary, recording_path=recording_path)
    if lifecycle.get("status") == "FAIL" and not lifecycle.get("evidence"):
        return lifecycle
    compact = woodcutting_loop_lifecycle.compact_lifecycle(lifecycle)
    summary["woodcutting_loop_lifecycle"] = lifecycle
    summary["woodcuttingLoopStatus"] = compact.get("status")
    summary["woodcuttingLoopState"] = compact.get("loopState")
    summary["woodcuttingLoopCurrentPhase"] = compact.get("currentPhase")
    summary["woodcuttingLoopNextExpectedPhase"] = compact.get("nextExpectedPhase")
    summary["woodcuttingLoopConfidence"] = compact.get("confidence")
    summary["woodcuttingLoopDetectedPhases"] = compact.get("detectedPhases") or []
    if write:
        telemetry_sources.atomic_write_json(recording_path / "woodcutting_loop_lifecycle.json", lifecycle, pretty=pretty)
    return lifecycle


def render_woodcutting_lifecycle_section(summary: dict[str, Any]) -> str:
    lifecycle = _dict(summary.get("woodcutting_lifecycle"))
    if not lifecycle:
        return ""
    inventory = _dict(lifecycle.get("inventory"))
    clicks = _dict(lifecycle.get("clicks"))
    animation = _dict(lifecycle.get("animation"))
    targets = _dict(lifecycle.get("targets"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines = [
        "## Woodcutting Lifecycle",
        f"- Phase: `{lifecycle.get('phase')}`",
        f"- Status: `{lifecycle.get('status')}`",
        f"- Confidence: `{lifecycle.get('confidence')}`",
        f"- Normal logs: `{inventory.get('normalLogsStart')}` -> `{inventory.get('normalLogsEnd')}` (`+{inventory.get('normalLogsGained')}`)",
        f"- Free slots: `{inventory.get('freeSlotsStart')}` -> `{inventory.get('freeSlotsEnd')}`",
        f"- Inventory full: `{inventory.get('inventoryFull')}`",
        f"- Fresh Chop down clicks: `{clicks.get('freshChopClickCount')}`",
        f"- Ignored repeated clicks: `{clicks.get('ignoredRepeatedClickCount')}`",
        f"- Ignored pre-recording clicks: `{clicks.get('ignoredPreRecordingClickCount')}`",
        f"- Woodcutting animation `{animation.get('woodcuttingAnimationId')}` snapshots: `{animation.get('activeSnapshotCount')}`",
        f"- Tree ids seen: `{targets.get('treeIdsSeen')}`",
        f"- Cycle count: `{len(lifecycle.get('cycles') or [])}`",
        "",
        "### Lifecycle Evidence",
        bullets(lifecycle.get("evidence") or []),
        "### Lifecycle Warnings",
        bullets(lifecycle.get("warnings") or []),
    ]
    return "\n".join(lines) + "\n"


def render_traversal_lifecycle_section(summary: dict[str, Any]) -> str:
    lifecycle = _dict(summary.get("traversal_lifecycle"))
    if not lifecycle:
        return ""
    movement = _dict(lifecycle.get("movement"))
    start = _dict(lifecycle.get("start"))
    end = _dict(lifecycle.get("end"))
    steps = _list(lifecycle.get("steps"))
    route_segments = _list(lifecycle.get("routeSegments"))
    review_evidence = _list(lifecycle.get("reviewEvidence"))
    grouping = _dict(lifecycle.get("grouping"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines = [
        "## Traversal Lifecycle",
        f"- Status: `{lifecycle.get('status')}`",
        f"- Route: `{lifecycle.get('routeName')}`",
        f"- Phase: `{lifecycle.get('phase')}`",
        f"- Confidence: `{lifecycle.get('confidence')}`",
        f"- Start: `{start.get('areaLabel')}` `{start.get('world')}`",
        f"- End: `{end.get('areaLabel')}` `{end.get('world')}`",
        f"- Step count: `{lifecycle.get('stepCount')}`",
        f"- Raw / grouped / route segments: `{lifecycle.get('rawStepCount')}` / `{lifecycle.get('groupedStepCount')}` / `{lifecycle.get('routeSegmentCount')}`",
        f"- Supporting / review evidence: `{lifecycle.get('supportingEvidenceCount')}` / `{lifecycle.get('reviewEvidenceCount')}`",
        f"- Successful / partial / failed / unknown: `{lifecycle.get('successfulStepCount')}` / `{lifecycle.get('partialStepCount')}` / `{lifecycle.get('failedStepCount')}` / `{lifecycle.get('unknownStepCount')}`",
        f"- Successful / partial route segments: `{lifecycle.get('successfulSegmentCount')}` / `{lifecycle.get('partialSegmentCount')}`",
        f"- Plane changes: `{len(movement.get('planeChanges') or [])}`",
        f"- Distance approx: `{movement.get('distanceApprox')}`",
        "",
        "### Route Segments",
    ]
    if route_segments:
        for segment in route_segments[:12]:
            action = _dict(segment.get("primaryAction"))
            post = _dict(segment.get("postcondition"))
            lines.append(
                f"- `{segment.get('segmentIndex')}` `{segment.get('segmentType')}` {segment.get('label')} "
                f"result=`{post.get('result')}` postcondition=`{post.get('type')}` "
                f"targetQuality=`{action.get('targetQuality')}` confidence=`{segment.get('confidence')}`"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Grouped Traversal Steps",
        ]
    )
    if steps:
        for step in steps[:12]:
            tq = _dict(step.get("targetQuality"))
            menu = _dict(step.get("menuSelection"))
            post = _dict(step.get("postcondition"))
            lines.append(
                f"- `{step.get('stepIndex')}` `{step.get('type')}` `{step.get('action')}` `{step.get('targetName')}` "
                f"result=`{step.get('result')}` confidence=`{step.get('confidence')}` "
                f"quality=`{tq.get('quality')}` rowGeometry=`{menu.get('rowBoundsPresent')}` "
                f"planeChanged=`{post.get('planeChanged')}` positionChanged=`{post.get('positionChanged')}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "### Raw Steps Folded Into Groups", bullets(grouping.get("warnings") or [])])
    if review_evidence:
        lines.append("### Review-Only Evidence")
        for item in review_evidence[:12]:
            lines.append(
                f"- `{item.get('evidenceId')}` `{item.get('type')}` `{item.get('action')}` `{item.get('targetName')}` "
                f"reason=`{item.get('reviewReason')}` result=`{item.get('result')}`"
            )
    else:
        lines.extend(["### Review-Only Evidence", "- none"])
    lines.extend(["", "### Traversal Evidence", bullets(lifecycle.get("evidence") or []), "### Traversal Warnings", bullets(lifecycle.get("warnings") or [])])
    return "\n".join(lines) + "\n"


def render_banking_lifecycle_section(summary: dict[str, Any]) -> str:
    lifecycle = _dict(summary.get("banking_lifecycle"))
    if not lifecycle:
        return ""
    inventory = _dict(lifecycle.get("inventory"))
    bank = _dict(lifecycle.get("bank"))
    deposit = _dict(lifecycle.get("deposit"))
    withdraw = _dict(lifecycle.get("withdraw"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    def item_lines(items: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"{item.get('name') or item.get('id')} x{item.get('quantity')} "
                f"({item.get('source')})"
            )
        return lines

    lines = [
        "## Banking Lifecycle",
        f"- Phase: `{lifecycle.get('phase')}`",
        f"- Status: `{lifecycle.get('status')}`",
        f"- Confidence: `{lifecycle.get('confidence')}`",
        f"- Bank-like interface: `{lifecycle.get('bankLikeInterface')}`",
        f"- Bank open direct: `{bank.get('openSeen')}`",
        f"- Deposit box open direct: `{bank.get('depositBoxOpenSeen')}`",
        f"- Bank widget/root seen: `{bank.get('widgetRootSeen')}`",
        f"- Bank container available: `{bank.get('containerAvailable')}`",
        f"- Bank container delta available: `{lifecycle.get('bankContainerDeltaAvailable')}`",
        f"- Deposit confirmation level: `{lifecycle.get('depositConfirmationLevel')}`",
        f"- bank_ui present: `{bank.get('bankUiPresent')}`",
        f"- bank_ui snapshots: `{bank.get('bankUiSnapshotCount')}`",
        f"- Deposit detected: `{deposit.get('detected')}`",
        f"- Withdraw detected: `{withdraw.get('detected')}`",
        f"- Free slots: `{inventory.get('freeSlotsBefore')}` -> `{inventory.get('freeSlotsAfter')}` (`{inventory.get('freeSlotDelta')}`)",
        f"- Normal logs itemId 1511: `{inventory.get('normalLogsBefore')}` -> `{inventory.get('normalLogsAfter')}`",
        "",
        "### Deposited Items",
        bullets(item_lines(deposit.get("items") or [])),
        "### Withdrawn Items",
        bullets(item_lines(withdraw.get("items") or [])),
        "### Missing Capabilities",
        bullets(lifecycle.get("missingCapabilities") or []),
        "### Lifecycle Evidence",
        bullets(lifecycle.get("evidence") or []),
        "### Lifecycle Warnings",
        bullets(lifecycle.get("warnings") or []),
    ]
    return "\n".join(lines) + "\n"


def render_interruption_lifecycle_section(summary: dict[str, Any]) -> str:
    lifecycle = _dict(summary.get("interruption_lifecycle"))
    if not lifecycle:
        return ""
    combat = _dict(lifecycle.get("combat"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines = [
        "## Interruption Lifecycle",
        f"- Status: `{lifecycle.get('status')}`",
        f"- Interruption detected: `{lifecycle.get('interruptionDetected')}`",
        f"- Interruption type: `{lifecycle.get('interruptionType')}`",
        f"- Primary cause: `{lifecycle.get('primaryCause')}`",
        f"- Confidence: `{lifecycle.get('confidence')}`",
        f"- Task before: `{lifecycle.get('taskBefore')}`",
        f"- Task resumed: `{lifecycle.get('taskResumed')}`",
        f"- Duration ms: `{lifecycle.get('durationMs')}`",
        f"- Combat observed: `{combat.get('combatObserved')}`",
        f"- combat_state snapshots: `{combat.get('combatStateSnapshotCount')}`",
        f"- NPC targeted player: `{combat.get('npcTargetedPlayer')}`",
        f"- Player targeted NPC: `{combat.get('playerTargetedNpc')}`",
        f"- Hitsplats seen: `{combat.get('hitsplatsSeen')}`",
        f"- Player health changed: `{combat.get('playerHealthChanged')}`",
        f"- Stat changes: `{len(lifecycle.get('statChanges') or [])}`",
        f"- Chat/game messages: `{len(lifecycle.get('messages') or [])}`",
        "",
        "### Missing Capabilities",
        bullets(lifecycle.get("missingCapabilities") or []),
        "### Interruption Evidence",
        bullets(lifecycle.get("evidence") or []),
        "### Interruption Warnings",
        bullets(lifecycle.get("warnings") or []),
    ]
    return "\n".join(lines) + "\n"


def render_combat_damage_summary_section(summary: dict[str, Any]) -> str:
    damage = _dict(summary.get("combat_damage_summary"))
    if not damage:
        return ""
    compact = combat_damage_summary.compact_summary(damage)
    opponent = _dict(compact.get("primaryOpponent"))
    health = _dict(damage.get("health"))
    hitsplats = _dict(damage.get("hitsplats"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines = [
        "## Combat Damage Summary",
        f"- Status: `{damage.get('status')}`",
        f"- Combat observed: `{damage.get('combatObserved')}`",
        f"- Primary opponent: `{opponent.get('name')}` (`{opponent.get('kind')}`, confidence `{opponent.get('confidence')}`)",
        f"- Damage taken / dealt: `{compact.get('damageTakenTotal')}` / `{compact.get('damageDealtTotal')}`",
        f"- Hitsplats total / player / opponent / ambiguous: `{hitsplats.get('total')}` / `{hitsplats.get('localPlayerHitsplats')}` / `{hitsplats.get('opponentHitsplats')}` / `{hitsplats.get('ambiguousHitsplats')}`",
        f"- HP before / after / lowest: `{health.get('hpBefore')}` / `{health.get('hpAfter')}` / `{health.get('lowestObservedHp')}`",
        f"- HP changed: `{compact.get('hpChanged')}`",
        f"- Actor death seen: `{compact.get('actorDeathSeen')}`",
        f"- Task resumed: `{compact.get('taskResumed')}`",
        "### Missing Capabilities",
        bullets(damage.get("missingCapabilities") or []),
        "### Damage Warnings",
        bullets(damage.get("warnings") or []),
    ]
    return "\n".join(lines) + "\n"


def _woodcutting_loop_phase_lines(lifecycle: dict[str, Any]) -> list[str]:
    phases = {_dict(item).get("phase"): _dict(item) for item in _list(lifecycle.get("detectedPhases"))}
    routes = _dict(lifecycle.get("routes"))
    route_legs = {_dict(item).get("phase"): _dict(item) for item in _list(routes.get("routeLegs"))}
    route_legs_by_direction = {_dict(item).get("direction"): _dict(item) for item in _list(routes.get("routeLegs"))}

    def phase_status(*names: str, default: str = "not observed") -> str:
        for name in names:
            phase = phases.get(name)
            if phase:
                return str(phase.get("status") or "PASS")
        return default

    def route_suffix(phase: str, direction: str) -> str:
        leg = route_legs.get(phase) or route_legs_by_direction.get(direction)
        route_name = str(leg.get("routeName") or direction).strip() if leg else ""
        return f", {route_name}" if route_name else ""

    return [
        f"- Woodcutting: {phase_status('cutting', 'at_trees')}",
        f"- Route to Bank: {phase_status('routing_to_bank')}{route_suffix('route_to_bank', 'woodcutting_area_to_bank')}",
        f"- Banking: {phase_status('banking')}",
        f"- Deposit: {phase_status('deposit_complete')}",
        f"- Route to Trees: {phase_status('routing_to_trees')}{route_suffix('route_to_trees', 'bank_to_woodcutting_area')}",
        f"- Resume Cutting: {phase_status('resumed_cutting')}",
    ]


def _woodcutting_loop_route_leg_lines(lifecycle: dict[str, Any]) -> list[str]:
    legs = _list(_dict(lifecycle.get("routes")).get("routeLegs"))
    lines: list[str] = []
    for raw_leg in legs:
        leg = _dict(raw_leg)
        label = leg.get("label") or leg.get("phase") or "Route leg"
        route_name = leg.get("routeName") or leg.get("direction") or "route_unknown"
        status = leg.get("status") or "WARN"
        from_area = leg.get("fromArea") or "unknown"
        to_area = leg.get("toArea") or "unknown"
        lines.append(f"- {label}: `{status}`, `{route_name}` ({from_area} -> {to_area})")
    return lines or ["- none"]


def render_woodcutting_loop_lifecycle_section(summary: dict[str, Any]) -> str:
    lifecycle = _dict(summary.get("woodcutting_loop_lifecycle"))
    if not lifecycle:
        return ""
    compact = woodcutting_loop_lifecycle.compact_lifecycle(lifecycle)

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    current = _dict(lifecycle.get("currentPhase"))
    next_phase = _dict(lifecycle.get("nextExpectedPhase"))
    lines = [
        "## Woodcutting Loop Lifecycle",
        f"- Status: `{compact.get('status')}`",
        f"- Loop state: `{compact.get('loopState')}`",
        f"- Confidence: `{compact.get('confidence')}`",
        f"- Current phase: `{current.get('phase')}` ({current.get('label')})",
        f"- Next expected phase: `{next_phase.get('phase')}` ({next_phase.get('label')})",
        f"- Detected phases: `{', '.join(compact.get('detectedPhases') or [])}`",
        f"- Inventory full: `{compact.get('inventoryFull')}`",
        f"- Deposit complete: `{compact.get('depositComplete')}`",
        f"- Route direction: `{compact.get('routeDirection')}`",
        f"- Route leg count: `{compact.get('routeLegCount')}`",
        f"- Interruption detected: `{compact.get('interruptionDetected')}`",
        f"- Task resumed: `{compact.get('taskResumed')}`",
        "",
        "### Loop Phases",
        "\n".join(_woodcutting_loop_phase_lines(lifecycle)),
        "",
        "### Route Legs",
        "\n".join(_woodcutting_loop_route_leg_lines(lifecycle)),
        "",
        "### Loop Evidence",
        bullets(lifecycle.get("evidence") or []),
        "### Loop Warnings",
        bullets(lifecycle.get("warnings") or []),
        "### Loop Missing Capabilities",
        bullets(lifecycle.get("missingCapabilities") or []),
    ]
    return "\n".join(lines) + "\n"


def render_human_click_profile_section(summary: dict[str, Any]) -> str:
    profile = _dict(summary.get("human_click_profile"))
    if not profile:
        return ""
    clicks = _dict(profile.get("clicks"))
    landing = _dict(profile.get("landing"))
    camera = _dict(profile.get("camera"))
    lines = [
        "## Human Click Profile",
        f"- Status: `{profile.get('status')}`",
        f"- Recordings: `{profile.get('recordingCount')}`",
        f"- Buckets: `{', '.join(profile.get('activityBuckets') or [])}`",
        f"- Target-relative clicks: `{clicks.get('targetRelativeClicks')}`",
        f"- Strong / medium / weak: `{clicks.get('strongTargetClicks')}` / `{clicks.get('mediumTargetClicks')}` / `{clicks.get('weakTargetClicks')}`",
        f"- Aim distance median / p75 / p90 px: `{landing.get('medianAimDistancePx')}` / `{landing.get('p75AimDistancePx')}` / `{landing.get('p90AimDistancePx')}`",
        f"- Clickbox counts: `{landing.get('clickboxCounts')}`",
        f"- Camera segments / middle drags: `{camera.get('cameraSegmentCount')}` / `{camera.get('middleMouseDragCount')}`",
        f"- Imperfect successful clicks: `{profile.get('imperfectSuccessfulClickCount')}`",
    ]
    return "\n".join(lines) + "\n"


def render_route_template_section(summary: dict[str, Any]) -> str:
    template = _dict(summary.get("route_template"))
    comparison = _dict(summary.get("route_template_comparison"))
    variant = _dict(summary.get("route_template_variant"))
    auto_selection = _dict(summary.get("routeTemplateAutoSelection"))
    if not template and not comparison and not variant and not auto_selection:
        return ""

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines: list[str] = []
    if auto_selection:
        lines.extend(
            [
                "## Route Template Auto-Selection",
                f"- Detected route: `{auto_selection.get('routeName')}`",
                f"- Detected start/end: `{auto_selection.get('startArea')}` -> `{auto_selection.get('endArea')}`",
                f"- Status: `{auto_selection.get('status')}`",
                f"- Selection reason: `{auto_selection.get('selectionReason')}`",
                f"- Selected template: `{auto_selection.get('selectedTemplate')}`",
                f"- Suggested template name: `{auto_selection.get('suggestedTemplateName')}`",
                f"- Untemplated route: `{auto_selection.get('untemplatedRoute')}`",
                "",
                "### Auto-Selection Warnings",
                bullets(auto_selection.get("warnings") or []),
                "",
            ]
        )
    if template:
        lines.extend(
            [
                "## Route Template",
                f"- Template path: `{summary.get('routeTemplatePath')}`",
                f"- Route: `{template.get('routeName')}`",
                f"- Template revision: `{template.get('templateRevision')}`",
                f"- Segment count: `{len(template.get('segments') or [])}`",
                f"- Optional/context segments: `{len(template.get('optionalSegments') or [])}`",
                "",
            ]
        )
        notes = _list(template.get("templateNotes"))
        if notes:
            lines.extend(["### Template Notes", bullets(notes), ""])
        lines.append("### Template Segments")
        for segment in _list(template.get("segments"))[:16]:
            action = _dict(segment.get("primaryAction"))
            post = _dict(segment.get("expectedPostcondition"))
            lines.append(
                f"- `{segment.get('segmentIndex')}` `{segment.get('segmentType')}` {segment.get('label')} "
                f"action=`{action.get('option')}/{action.get('target')}` postcondition=`{post.get('type')}` required=`{segment.get('required')}`"
            )
        lines.extend(["", "### Template Warnings", bullets(template.get("warnings") or [])])
    if comparison:
        lines.extend(
            [
                "## Route Template Comparison",
                f"- Status: `{comparison.get('status')}`",
                f"- Status reason: `{comparison.get('statusReason')}`",
                f"- Score: `{comparison.get('score')}`",
                f"- Template direction mismatch: `{comparison.get('routeTemplateDirectionMismatch') or comparison.get('directionMismatch')}`",
                f"- Detected route: `{comparison.get('detectedRouteName')}`",
                f"- Detected start/end: `{comparison.get('detectedStartArea')}` -> `{comparison.get('detectedEndArea')}`",
                f"- Matched variant: `{comparison.get('matchedVariantName')}`",
                f"- Matched / required segments: `{comparison.get('matchedSegmentCount')}` / `{comparison.get('requiredSegmentCount')}`",
                f"- Missing / extra / allowed extra / weak: `{len(comparison.get('missingSegments') or [])}` / `{len(comparison.get('extraSegments') or [])}` / `{len(comparison.get('allowedExtraSegments') or [])}` / `{len(comparison.get('weakSegments') or [])}`",
                f"- Failed postconditions: `{len(comparison.get('failedPostconditions') or [])}`",
                f"- Navigation-support substitutions: `{len(comparison.get('navigationSupportSubstitutions') or [])}`",
                f"- Navigation-support evidence: `{len(comparison.get('navigationSupportEvidence') or [])}`",
                f"- Review evidence segments: `{len(comparison.get('reviewEvidenceSegments') or [])}`",
                "",
            ]
        )
        if comparison.get("templateName") == "Bank_to_Woodcutting_area":
            lines.extend(
                [
                    "### Route Semantics",
                    "- Door/Open is not required for this route.",
                    "- Walk here / Large door is treated as navigation support.",
                    "- Cancel is treated as review evidence.",
                    "",
                ]
            )
        lines.append("### Segment Matches")
        for match in _list(comparison.get("segmentMatches"))[:16]:
            lines.append(
                f"- template `{match.get('templateSegmentIndex')}` -> recording `{match.get('recordingSegmentIndex')}` "
                f"status=`{match.get('matchStatus')}` score=`{match.get('score')}`"
            )
        substitutions = _list(comparison.get("navigationSupportSubstitutions"))
        if substitutions:
            lines.extend(["", "### Navigation-Support Substitutions"])
            for substitution in substitutions[:8]:
                lines.append(
                    f"- template `{substitution.get('baseSegmentIndex')}` {substitution.get('baseSegmentLabel')} "
                    f"-> recording `{substitution.get('recordingSegmentIndex')}` {substitution.get('recordingSegmentLabel')} "
                    f"registered=`{substitution.get('registered')}` variant=`{substitution.get('variantName')}`"
                )
        allowed = _list(comparison.get("allowedExtraSegments"))
        if allowed:
            lines.extend(["", "### Allowed Extra / Review Evidence"])
            for item in allowed[:8]:
                lines.append(f"- recording `{item.get('recordingSegmentIndex')}` `{item.get('segmentType')}` {item.get('label')} reason=`{item.get('reason')}`")
        navigation_support = _list(comparison.get("navigationSupportEvidence"))
        if navigation_support:
            lines.extend(["", "### Navigation-Support Evidence"])
            for item in navigation_support[:8]:
                lines.append(f"- recording `{item.get('recordingSegmentIndex')}` `{item.get('segmentType')}` {item.get('label')}")
        review_segments = _list(comparison.get("reviewEvidenceSegments"))
        if review_segments:
            lines.extend(["", "### Review Evidence Segments"])
            for item in review_segments[:8]:
                lines.append(f"- recording `{item.get('recordingSegmentIndex')}` `{item.get('segmentType')}` {item.get('label')} role=`{item.get('role')}`")
        lines.extend(["", "### Comparison Warnings", bullets(comparison.get("warnings") or [])])
    if variant:
        lines.extend(
            [
                "## Route Variant",
                f"- Variant: `{variant.get('variantName')}`",
                f"- Source recording: `{variant.get('sourceRecording')}`",
                f"- Template updated: `{summary.get('routeVariantTemplateUpdated')}`",
                f"- Segment overrides: `{len(variant.get('segmentOverrides') or [])}`",
                f"- Allowed extra segments: `{len(variant.get('allowedExtraSegments') or [])}`",
                "",
                "### Variant Warnings",
                bullets(variant.get("warnings") or []),
            ]
        )
    return "\n".join(lines) + "\n"


def render_route_monitor_section(summary: dict[str, Any]) -> str:
    monitor = _dict(summary.get("route_monitor"))
    if not monitor:
        return ""

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    next_segment = _dict(monitor.get("nextExpectedSegment"))
    lines = [
        "## Route Monitor",
        f"- Status: `{monitor.get('status')}`",
        f"- Route state: `{monitor.get('routeState')}`",
        f"- Route: `{monitor.get('routeName')}`",
        f"- Template revision: `{monitor.get('templateRevision')}`",
        f"- Mode: `{monitor.get('mode')}`",
        f"- Current area: `{monitor.get('currentArea')}`",
        f"- Start/end matched: `{monitor.get('startAreaMatched')}` / `{monitor.get('endAreaMatched')}`",
        f"- Completed / remaining: `{monitor.get('completedSegmentCount')}` / `{monitor.get('remainingSegmentCount')}`",
        f"- Next expected segment: `{next_segment.get('segmentIndex')}` `{next_segment.get('label')}`" if next_segment else "- Next expected segment: `none`",
        f"- Off route: `{monitor.get('offRoute')}`",
        f"- Comparison reason: `{monitor.get('comparisonStatusReason')}`",
    ]
    if not monitor.get("routeName") or monitor.get("templateRevision") is None:
        lines.extend(
            [
                "- Trust: `config_failure`",
                "- Trust reason: Route monitor did not load a valid template; live segment completion is not trustworthy.",
            ]
        )
    lines.extend(
        [
            "",
            "### Monitor Evidence",
            bullets(monitor.get("evidence") or []),
            "### Monitor Warnings",
            bullets(monitor.get("warnings") or []),
            "### Missing Capabilities",
            bullets(monitor.get("missingCapabilities") or []),
        ]
    )
    return "\n".join(lines) + "\n"


def render_route_history_section(summary: dict[str, Any]) -> str:
    history = _dict(summary.get("route_history"))
    if not history:
        return ""
    next_segment = _dict(history.get("nextExpectedSegment"))
    current_segment = _dict(history.get("currentSegment"))
    lines = [
        "## Route History",
        f"- Status: `{history.get('status')}`",
        f"- Session: `{history.get('sessionId')}`",
        f"- Route: `{history.get('routeName')}` revision `{history.get('templateRevision')}`",
        f"- State: `{history.get('routeState')}`",
        f"- Current area: `{history.get('currentArea')}`",
        f"- Current segment: `{current_segment.get('segmentIndex')}` `{current_segment.get('label')}`",
        f"- Next expected: `{next_segment.get('segmentIndex')}` `{next_segment.get('label')}`" if next_segment else "- Next expected: none",
        f"- Completed / remaining: `{history.get('completedSegmentCount')}` / `{history.get('remainingSegmentCount')}`",
        f"- Plane changes: `{history.get('planeChangeCount')}`",
        f"- Recent path points: `{history.get('recentPathCount')}`",
        f"- Stale periods / longest stale ms: `{history.get('stalePeriodCount')}` / `{history.get('longestStaleMs')}`",
        f"- Off route: `{history.get('offRoute')}`",
        f"- Arrival gate: `{history.get('arrivalGateStatus')}`",
        f"- Arrival requires end cluster: `{history.get('arrivalGateRequiresEndCluster')}`",
        f"- Near end cluster: `{history.get('nearEndCluster')}`",
        f"- End cluster tolerance tiles: `{history.get('endClusterToleranceTiles')}`",
        f"- Distance to end cluster: `{history.get('distanceToEndCluster')}`",
        f"- Distance after last transition: `{history.get('distanceAfterLastTransition')}`",
        f"- Distance-only progress rejected: `{history.get('distanceOnlyProgressRejected')}`",
        f"- Arrival rejected reason: `{history.get('arrivalGateRejectedReason')}`",
        f"- Arrival passed reason: `{history.get('arrivalGatePassedReason')}`",
        f"- Premature arrival prevented: `{history.get('prematureArrivalPrevented')}`",
        f"- Duplicate arrival events suppressed: `{history.get('duplicateArrivalEventsSuppressed')}`",
        f"- State file: `{history.get('statePath')}`",
        f"- Events file: `{history.get('eventsPath')}`",
        f"- Timeline file: `{history.get('timelinePath')}`",
    ]
    warnings = history.get("warnings") or []
    if warnings:
        lines.append("### Route History Warnings")
        lines.extend(f"- {item}" for item in warnings[:8])
    return "\n".join(lines) + "\n"


def render_input_trace_sections(summary: dict[str, Any]) -> str:
    input_trace = _dict(summary.get("input_trace"))
    click = _dict(summary.get("click_analysis"))
    hover = _dict(summary.get("hover_analysis"))
    camera = _dict(summary.get("camera_behavior"))
    arduino = _dict(summary.get("arduino_trace"))
    live_mirror = _dict(summary.get("arduino_live_mirror"))
    mapping = _dict(summary.get("vm_mouse_arduino_mapping"))
    action = _dict(summary.get("input_action_summary"))
    target_quality = _dict(summary.get("target_match_summary"))
    menu_interaction = _dict(summary.get("menu_interaction_summary"))
    coordinate = _dict(summary.get("coordinate_alignment_summary"))
    input_path = _dict(summary.get("input_path_integrity_summary"))
    mirror = _dict(summary.get("arduino_mirror_verification"))
    mirror_timing = _dict(summary.get("mirror_action_timing"))
    click_ownership = _dict(summary.get("click_ownership_summary") or input_path.get("clickOwnershipSummary"))
    if not any((input_trace, click, hover, camera, arduino, live_mirror, mapping, action, target_quality, menu_interaction, coordinate, input_path, mirror, mirror_timing, click_ownership)):
        return ""

    lines = ["## Human Input Trace"]
    if input_trace:
        lines.extend(
            [
                f"- Status: `{input_trace.get('status')}`",
                f"- Capture status: `{input_trace.get('captureStatus')}`",
                f"- Backend requested/used: `{input_trace.get('backendRequested')}` / `{input_trace.get('backendUsed') or input_trace.get('backend')}`",
                f"- Input events: `{input_trace.get('eventCount')}`",
                f"- Real input events: `{input_trace.get('realEventCount')}`",
                f"- OS clicks: `{input_trace.get('clickCount')}`",
                f"- Mouse moves: `{input_trace.get('mouseMoveCount')}`",
                f"- Keyboard events: `{input_trace.get('keyboardEventCount')}`",
            ]
        )
        if input_trace.get("message"):
            lines.append(f"- Message: {input_trace.get('message')}")
        recommendations = input_trace.get("recommendations") or []
        if recommendations:
            lines.append("- Recommendations:")
            lines.extend(f"  - {item}" for item in recommendations)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Input Action Classification")
    if action:
        lines.extend(
            [
                f"- Status: `{action.get('status')}`",
                f"- Raw OS clicks: `{action.get('rawOsClickCount')}`",
                f"- Eligible game-action clicks: `{action.get('eligibleGameActionClickCount')}`",
                f"- Target-relative clicks: `{action.get('targetRelativeClickCount')}`",
                f"- Excluded clicks: `{action.get('excludedClickCount')}`",
                f"- Camera drag releases excluded: `{action.get('cameraDragReleaseCount')}`",
                f"- UI/control clicks excluded: `{action.get('uiControlClickCount')}`",
                f"- Minimap clicks: `{action.get('minimapClickCount')}`",
                f"- Menu setup clicks: `{action.get('rightClickMenuOpenCount')}`",
                f"- Menu selection clicks: `{action.get('menuSelectionClickCount')}`",
                f"- Ambiguous clicks: `{action.get('ambiguousClickCount')}`",
                f"- Classification counts: `{action.get('classificationCounts')}`",
                f"- Exclusion reasons: `{action.get('exclusionReasons')}`",
            ]
        )
        examples = action.get("examples") or []
        if examples:
            lines.append("- Examples:")
            for item in examples[:6]:
                lines.append(
                    f"  - event `{item.get('eventSeq')}` `{item.get('classification')}` button `{item.get('button')}` target `{item.get('target')}` reasons `{item.get('reasons')}`"
                )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Menu Interaction Analysis")
    if menu_interaction:
        lines.extend(
            [
                f"- Status: `{menu_interaction.get('status')}`",
                f"- Right-click menu opens: `{menu_interaction.get('rightClickMenuOpenCount')}`",
                f"- Menu selections: `{menu_interaction.get('menuSelectionCount')}`",
                f"- Rows resolved: `{menu_interaction.get('menuRowsResolvedCount')}`",
                f"- Selections with row geometry: `{menu_interaction.get('menuSelectionsWithRowGeometryCount')}`",
                f"- Selections linked to targets: `{menu_interaction.get('menuSelectionsLinkedToTargetsCount')}`",
                f"- Selections missing row geometry: `{menu_interaction.get('menuSelectionsMissingRowGeometryCount')}`",
            ]
        )
        examples = menu_interaction.get("examples") or []
        if examples:
            lines.append("- Examples:")
            for item in examples[:6]:
                linked = _dict(item.get("linkedTarget"))
                lines.append(
                    f"  - event `{item.get('eventSeq')}` row `{item.get('selectedRowIndex')}` `{item.get('option')}` `{item.get('target')}` "
                    f"rowGeometry=`{item.get('rowBoundsPresent')}` source=`{item.get('rowGeometrySource')}` "
                    f"snapshot=`{item.get('selectedSnapshotId')}` candidates=`{item.get('candidateSnapshotCount')}` "
                    f"linked=`{linked.get('name')}/{linked.get('action')}` quality=`{item.get('targetMatchQuality')}`"
                )
        diagnostics = menu_interaction.get("menuRowDiagnostics") or []
        if diagnostics:
            lines.append("- Row diagnostics:")
            for item in diagnostics[:6]:
                lines.append(
                    f"  - event `{item.get('eventSeq')}` geometry=`{item.get('rowBoundsPresent')}` "
                    f"source=`{item.get('rowGeometrySource')}` selectedSnapshot=`{item.get('selectedSnapshotId')}` "
                    f"reason=`{item.get('selectedSnapshotReason')}` score=`{item.get('selectedSnapshotScore')}` "
                    f"candidates=`{item.get('candidateSnapshotCount')}` missingReason=`{item.get('missingRowGeometryReason')}`"
                )
        if menu_interaction.get("warnings"):
            lines.append("- Warnings:")
            lines.extend(f"  - {item}" for item in menu_interaction.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Coordinate Alignment")
    if coordinate:
        lines.extend(
            [
                f"- Status: `{coordinate.get('status')}`",
                f"- Detected DPI scale: `{coordinate.get('detectedDpiScale')}`",
                f"- Chosen transform: `{coordinate.get('chosenTransform')}`",
                f"- Menu selections considered: `{coordinate.get('menuSelectionCandidateCount')}`",
                f"- Raw menu-row hits: `{coordinate.get('rawMenuRowHitCount')}`",
                f"- Normalized menu-row hits: `{coordinate.get('normalizedMenuRowHitCount')}`",
                f"- Input path: `{coordinate.get('inputPathClassification')}`",
                f"- Mirror status: `{coordinate.get('mirrorVerificationStatus')}`",
            ]
        )
        examples = coordinate.get("examples") or []
        if examples:
            lines.append("- Examples:")
            for item in examples[:4]:
                chosen = _dict(_dict(item.get("chosen")).get("chosen"))
                normalized = _dict(chosen.get("normalizedPoint"))
                selected = _dict(chosen.get("selectedRow"))
                lines.append(
                    f"  - event `{item.get('eventSeq')}` raw `{item.get('rawPoint')}` -> normalized `{normalized.get('x')},{normalized.get('y')}` "
                    f"via `{chosen.get('name')}` row `{selected.get('rowIndex')}` `{selected.get('option')}` `{selected.get('target')}` insideRow=`{chosen.get('insideRowBounds')}`"
                )
        if coordinate.get("warnings"):
            lines.append("- Coordinate warnings:")
            lines.extend(f"  - {item}" for item in coordinate.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Input Path Integrity")
    if input_path or mirror:
        lines.extend(
            [
                f"- Classification: `{input_path.get('inputPathClassification')}`",
                f"- Requested mode: `{input_path.get('requestedMode')}`",
                f"- Actual mode: `{input_path.get('actualDetectedMode')}`",
                f"- Mirror verified: `{input_path.get('mirrorVerified')}`",
                f"- Live mirror active: `{input_path.get('liveMirrorActive')}`",
                f"- Live mirror verified: `{input_path.get('liveMirrorVerified')}`",
                f"- Mirror quality: `{input_path.get('mirrorQuality')}`",
                f"- Max Arduino/click commands per second: `{input_path.get('maxArduinoCommandsPerSecond')}` / `{input_path.get('maxClickCommandsPerSecond')}`",
                f"- Dropped/throttled/panic counts: `{input_path.get('droppedCommandCount')}` / `{input_path.get('throttledCommandCount')}` / `{input_path.get('panicStopCount')}`",
                f"- Live mirror safety: `{', '.join(input_path.get('liveMirrorSafetyClassifications') or []) or 'none'}`",
                f"- Probe verified: `{input_path.get('probeVerified')}`",
                f"- Probe classification: `{input_path.get('probeClassification')}`",
                f"- Arduino port/protocol: `{input_path.get('arduinoPort')}` / `{input_path.get('arduinoProtocol')}`",
                f"- Arduino commands: `{input_path.get('commandCount')}`",
                f"- Non-probe action commands: `{input_path.get('nonProbeActionCommandCount')}`",
                f"- Movement/click commands: `{input_path.get('movementCommandCount')}` / `{input_path.get('clickCommandCount')}`",
                f"- Post-action Arduino commands: `{input_path.get('postActionArduinoCommandCount')}`",
                f"- Post-action movement/click commands: `{input_path.get('postActionMovementCommandCount')}` / `{input_path.get('postActionClickCommandCount')}`",
                f"- Post-action weird movement suspected: `{input_path.get('postActionWeirdMovementSuspected')}`",
                f"- Feedback loop suspected: `{input_path.get('feedbackLoopSuspected')}`",
                f"- Acks: `{input_path.get('ackCount')}`",
                f"- Correlated movement/click commands: `{input_path.get('correlatedCommandToObservedMovementCount')}` / `{input_path.get('correlatedCommandToObservedClickCount')}`",
                f"- Possible double input: `{input_path.get('possibleDoubleInput')}`",
                f"- Uncorrelated OS move/clicks: `{input_path.get('uncorrelatedOsMoveCount')}` / `{input_path.get('uncorrelatedOsClickCount')}`",
                f"- Raw Input attribution available: `{input_path.get('rawInputAttributionAvailable')}`",
            ]
        )
        if click_ownership:
            lines.extend(
                [
                    "",
                    "### Click Ownership",
                    f"- Click policy used: `{click_ownership.get('clickPolicyUsed')}`",
                    f"- OS clicks: `{click_ownership.get('totalOsClicks')}`",
                    f"- Live Arduino click commands: `{click_ownership.get('totalArduinoLiveClickCommands')}`",
                    f"- Map-only clicks: `{click_ownership.get('mapOnlyClickCount')}`",
                    f"- Arduino physical clicks: `{click_ownership.get('arduinoPhysicalClickCount')}`",
                    f"- Duplicate click candidates/likely: `{click_ownership.get('duplicateClickCandidateCount')}` / `{click_ownership.get('duplicateClickLikelyCount')}`",
                    f"- Live clicks without source suppression: `{click_ownership.get('liveClickWithoutSuppressionCount')}`",
                    f"- Source suppression verified: `{click_ownership.get('sourceSuppressionVerified')}`",
                    f"- Click owners: `{click_ownership.get('clickOwners')}`",
                ]
            )
            if click_ownership.get("warnings"):
                lines.append("- Click ownership warnings:")
                lines.extend(f"  - {item}" for item in click_ownership.get("warnings") or [])
        if input_path.get("warnings"):
            lines.append("- Input path warnings:")
            lines.extend(f"  - {item}" for item in input_path.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Persistent Arm Diagnosis")
    if mirror_timing:
        lines.extend(
            [
                f"- Final mirror recording verdict: `{mirror_timing.get('finalMirrorRecordingVerdict')}`",
                f"- Arm mode: `{mirror_timing.get('armMode')}`",
                f"- Armed start elapsed seconds: `{mirror_timing.get('mirrorArmedStartElapsedSeconds')}`",
                f"- Disarm elapsed seconds: `{mirror_timing.get('mirrorDisarmElapsedSeconds')}`",
                f"- Disarm reason: `{mirror_timing.get('disarmReason')}`",
                f"- First game action time: `{mirror_timing.get('firstGameActionTime')}`",
                f"- First menu selection time: `{mirror_timing.get('firstMenuSelectionTime')}`",
                f"- Menu selections after disarm: `{mirror_timing.get('menuSelectionsAfterDisarm')}`",
                f"- Action clicks after disarm: `{mirror_timing.get('actionClicksAfterDisarm')}`",
                f"- Target actions after disarm: `{mirror_timing.get('targetActionsAfterDisarm')}`",
                f"- Post-action Arduino commands: `{mirror_timing.get('postActionArduinoCommandCount')}`",
                f"- Post-action movement/click commands: `{mirror_timing.get('postActionMovementCommandCount')}` / `{mirror_timing.get('postActionClickCommandCount')}`",
                f"- Post-action weird movement suspected: `{mirror_timing.get('postActionWeirdMovementSuspected')}`",
                f"- Feedback loop suspected: `{mirror_timing.get('feedbackLoopSuspected')}`",
            ]
        )
        if mirror_timing.get("warnings"):
            lines.append("- Persistent arm warnings:")
            lines.extend(f"  - {item}" for item in mirror_timing.get("warnings") or [])
        examples = mirror_timing.get("actions") if isinstance(mirror_timing.get("actions"), list) else []
        if examples:
            lines.append("- Action timing examples:")
            for item in examples[:5]:
                lines.append(
                    f"  - event `{item.get('eventSeq')}` `{item.get('classification')}` "
                    f"`{item.get('action')}` `{item.get('target')}` "
                    f"armed=`{item.get('mirrorArmedAtAction')}` "
                    f"after_disarm_ms=`{item.get('timeSinceMirrorDisarmMs')}` "
                    f"mirrored_likely=`{item.get('mirroredActionLikely')}`"
                )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Target Match Quality")
    if target_quality:
        counts = target_quality.get("qualityCounts") or {}
        lines.extend(
            [
                f"- Status: `{target_quality.get('status')}`",
                f"- Target-relative clicks: `{target_quality.get('targetRelativeClickCount')}`",
                f"- Strong matches: `{target_quality.get('strongMatchCount')}`",
                f"- Medium matches: `{target_quality.get('mediumMatchCount')}`",
                f"- Weak matches: `{target_quality.get('weakMatchCount')}`",
                f"- Unmatched: `{target_quality.get('unmatchedCount')}`",
                f"- Quality counts: `{counts}`",
            ]
        )
        landing = _dict(target_quality.get("clickLandingSummary"))
        if landing:
            aim = _dict(landing.get("aimDistancePx"))
            lines.extend(
                [
                    f"- Clickbox inside / outside / unknown / unavailable: `{_dict(landing.get('clickboxCounts')).get('inside')}` / `{_dict(landing.get('clickboxCounts')).get('outside')}` / `{_dict(landing.get('clickboxCounts')).get('unknown')}` / `{_dict(landing.get('clickboxCounts')).get('unavailable')}`",
                    f"- Aim distance px median / max: `{aim.get('median')}` / `{aim.get('max')}`",
                    f"- Aim distance buckets <=12 / <=30 / <=80 / >80 / unknown: `{_dict(landing.get('aimDistanceBuckets')).get('le12')}` / `{_dict(landing.get('aimDistanceBuckets')).get('le30')}` / `{_dict(landing.get('aimDistanceBuckets')).get('le80')}` / `{_dict(landing.get('aimDistanceBuckets')).get('gt80')}` / `{_dict(landing.get('aimDistanceBuckets')).get('unknown')}`",
                    f"- Menu row inside / missing bounds: `{_dict(landing.get('menuRowCounts')).get('inside')}` / `{_dict(landing.get('menuRowCounts')).get('missingBounds')}`",
                    f"- Imperfect but useful click examples: `{landing.get('imperfectButUsefulClickCount')}`",
                ]
            )
        association = _dict(target_quality.get("targetAssociation"))
        if association:
            lines.extend(
                [
                    f"- Target association conflicts: `{association.get('conflictCount')}`",
                    f"- Rejected geometry candidates: `{association.get('rejectedCandidateCount')}`",
                ]
            )
            assoc_examples = association.get("examples") or []
            if assoc_examples:
                lines.append("- Target Association Diagnostics:")
                for item in assoc_examples[:5]:
                    selected = _dict(item.get("selectedCandidate"))
                    rejected = _dict((_list(item.get("rejectedCandidates")) or [{}])[0])
                    lines.append(
                        f"  - event `{item.get('eventSeq')}` intended `{item.get('intendedAction')}` `{item.get('intendedTargetName')}` via `{item.get('associationMethod')}`; "
                        f"selected `{selected.get('name')}` / `{selected.get('action')}`; rejected `{rejected.get('name')}` / `{rejected.get('action')}` because `{item.get('conflictReasons')}`"
                    )
        examples = target_quality.get("examples") or []
        if examples:
            lines.append("- Examples:")
            for item in examples[:6]:
                lines.append(
                    f"  - event `{item.get('eventSeq')}` `{item.get('classification')}` target `{item.get('targetName')}` action `{item.get('targetAction')}` quality `{item.get('quality')}` score `{item.get('score')}`"
                )
        if target_quality.get("warnings"):
            lines.append("- Warnings:")
            lines.extend(f"  - {item}" for item in target_quality.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Click/Hover Analysis")
    if click or hover:
        lines.extend(
            [
                f"- Clicks joined: `{click.get('clickCount')}`",
                f"- Eligible game-action clicks: `{click.get('eligibleGameActionClickCount')}`",
                f"- Target-relative clicks: `{click.get('targetRelativeClickCount')}`",
                f"- Hover-to-click median ms: `{hover.get('hoverToClickMedianMs')}`",
                f"- Hover-to-click average ms: `{hover.get('hoverToClickAverageMs')}`",
            ]
        )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Camera Behavior")
    if camera:
        lines.extend(
            [
                f"- Status: `{camera.get('status')}`",
                f"- Camera segments: `{camera.get('totalCameraSegments')}`",
                f"- Middle mouse drag segments: `{camera.get('middleMouseDragSegments')}`",
                f"- Arrow key camera segments: `{camera.get('arrowKeyCameraSegments')}`",
                f"- Camera-before-click count: `{camera.get('cameraBeforeClickCount')}`",
            ]
        )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Arduino Input Path")
    if arduino:
        lines.extend(
            [
                f"- Status: `{arduino.get('status')}`",
                f"- Classification: `{arduino.get('classification')}`",
                f"- Port/protocol: `{arduino.get('port')}` / `{arduino.get('protocol')}`",
                f"- Arduino events: `{arduino.get('eventCount')}`",
                f"- Commands: `{arduino.get('commandCount')}`",
                f"- Health/status commands: `{arduino.get('statusHealthCommandCount')}`",
                f"- Action commands: `{arduino.get('actionCommandCount')}`",
                f"- Movement commands: `{arduino.get('movementCommandCount')}`",
                f"- Click commands: `{arduino.get('clickCommandCount')}`",
                f"- Acks: `{arduino.get('ackCount')}`",
                f"- Errors: `{arduino.get('errorCount')}`",
                f"- Per-action HID evidence: `{arduino.get('perActionHidEvidence')}`",
                f"- Action command file: `arduino_action_commands.jsonl`",
            ]
        )
        if arduino.get("warnings"):
            lines.append("- Arduino warnings:")
            lines.extend(f"  - {item}" for item in arduino.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Arduino Live Mirror")
    if live_mirror:
        lines.extend(
            [
                f"- Status: `{live_mirror.get('status')}`",
                f"- Active: `{live_mirror.get('liveMirrorActive')}`",
                f"- Verified: `{live_mirror.get('liveMirrorVerified')}`",
                f"- Classification: `{live_mirror.get('inputPathClassification')}`",
                f"- Non-probe action commands: `{live_mirror.get('nonProbeActionCommandCount')}`",
                f"- Movement/click/key commands: `{live_mirror.get('movementCommandCount')}` / `{live_mirror.get('clickCommandCount')}` / `{live_mirror.get('keyboardCommandCount')}`",
                f"- Acks/errors: `{live_mirror.get('ackCount')}` / `{live_mirror.get('errorCount')}`",
                f"- Correlated movement/click commands: `{live_mirror.get('correlatedCommandToObservedMovementCount')}` / `{live_mirror.get('correlatedCommandToObservedClickCount')}`",
                f"- Mirror state/paused: `{live_mirror.get('mirrorState')}` / `{live_mirror.get('mirrorPaused')}`",
                f"- Dropped/throttled/duplicate/panic counts: `{live_mirror.get('droppedCommandCount')}` / `{live_mirror.get('throttledCommandCount')}` / `{live_mirror.get('duplicateCommandCount')}` / `{live_mirror.get('panicStopCount')}`",
                f"- Max command/click rate per second: `{live_mirror.get('maxArduinoCommandsPerSecond') or live_mirror.get('maxCommandsPerSecondObserved')}` / `{live_mirror.get('maxClickCommandsPerSecond') or live_mirror.get('maxClickCommandsPerSecondObserved')}`",
                f"- Safety classification: `{', '.join(live_mirror.get('liveMirrorSafetyClassifications') or []) or 'none'}`",
            ]
        )
        if live_mirror.get("warnings"):
            lines.append("- Live mirror warnings:")
            lines.extend(f"  - {item}" for item in live_mirror.get("warnings") or [])
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## VM Mouse To Arduino Mapping")
    if mapping:
        mouse_path = _dict(mapping.get("mousePath"))
        lines.extend(
            [
                f"- Status: `{mapping.get('status')}`",
                f"- Reason: `{mapping.get('reason')}`",
                f"- Input events mapped: `{mapping.get('inputEventCount')}`",
                f"- Segment count: `{mouse_path.get('segment_count')}`",
                f"- Total dx/dy: `{mouse_path.get('total_dx')}`, `{mouse_path.get('total_dy')}`",
            ]
        )
        if mapping.get("warnings"):
            lines.append("- Mapping warnings:")
            lines.extend(f"  - {item}" for item in mapping.get("warnings") or [])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def render_schema_gap_report(summary: dict[str, Any]) -> str:
    categories = _dict(summary.get("schema_gap_categories"))

    def bullets(items: list[Any]) -> str:
        if not items:
            return "- none\n"
        return "".join(f"- {item}\n" for item in items)

    lines = [
        "# Manual Telemetry Schema Gap Report",
        "",
        f"Schema: `{GAP_REPORT_SCHEMA_VERSION}`",
        f"Recording: `{summary.get('recording_id')}`",
        f"Duration: `{summary.get('duration_seconds')}` seconds",
        f"Snapshots: `{summary.get('snapshot_count')}`",
        "",
        "## Present",
        bullets(categories.get("present") or []),
        "## Missing",
        bullets(categories.get("missing") or []),
        "## Computable In Sidecar",
        bullets(categories.get("computable_in_sidecar") or []),
        "## Requires Bridge Export",
        bullets(categories.get("requires_bridge_export") or []),
        "## Needs Manual Review",
        bullets(categories.get("needs_manual_review") or []),
        "## Changed During Recording",
        bullets(summary.get("fields_changed") or []),
        "## State Transitions",
        bullets([item.get("transition") for item in summary.get("state_transitions") or []]),
        render_woodcutting_lifecycle_section(summary),
        render_interruption_lifecycle_section(summary),
        render_combat_damage_summary_section(summary),
        render_banking_lifecycle_section(summary),
        render_traversal_lifecycle_section(summary),
        render_route_template_section(summary),
        render_route_monitor_section(summary),
        render_route_history_section(summary),
        render_woodcutting_loop_lifecycle_section(summary),
        render_human_click_profile_section(summary),
        render_input_trace_sections(summary),
        "## Warnings",
        bullets(summary.get("warnings") or []),
        "## How To Use This",
        "- Treat `present` fields as safe to shape into compact context now.",
        "- Treat `computable_in_sidecar` fields as Python tasks over existing telemetry.",
        "- Treat `requires_bridge_export` fields as targeted RuneLite/plugin export tasks.",
        "- Treat `needs_manual_review` fields as recorder/analyzer follow-up candidates.",
    ]
    return "\n".join(lines).replace("\n\n##", "\n##") + "\n"


def update_outputs(recording_path: Path, *, pretty: bool = True) -> dict[str, Any]:
    summary = analyze_recording(recording_path)
    telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
    if isinstance(summary.get("woodcutting_lifecycle"), dict):
        telemetry_sources.atomic_write_json(recording_path / "woodcutting_lifecycle.json", summary["woodcutting_lifecycle"], pretty=pretty)
    if isinstance(summary.get("interruption_lifecycle"), dict):
        telemetry_sources.atomic_write_json(recording_path / "interruption_lifecycle.json", summary["interruption_lifecycle"], pretty=pretty)
    if isinstance(summary.get("combat_damage_summary"), dict):
        telemetry_sources.atomic_write_json(recording_path / "combat_damage_summary.json", summary["combat_damage_summary"], pretty=pretty)
    if isinstance(summary.get("banking_lifecycle"), dict):
        telemetry_sources.atomic_write_json(recording_path / "banking_lifecycle.json", summary["banking_lifecycle"], pretty=pretty)
    if (recording_path / "input_events.jsonl").exists() or (recording_path / "arduino_events.jsonl").exists() or (recording_path / "arduino_action_commands.jsonl").exists():
        input_join = input_trace_joiner.analyze_recording(
            recording_path,
            write=True,
            include_mapping=bool((recording_path / "input_events.jsonl").exists()),
        )
        events, _ = _load_events(recording_path / "events.jsonl")
        woodcutting = woodcutting_lifecycle.analyze_events(
            events,
            input_action_classifications=input_join.get("input_action_classifications") or [],
            warnings=[],
        )
        if woodcutting.get("status") != "FAIL" or woodcutting.get("evidence"):
            summary["woodcutting_lifecycle"] = woodcutting
            telemetry_sources.atomic_write_json(recording_path / "woodcutting_lifecycle.json", woodcutting, pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
        interruption = interruption_lifecycle.analyze_data(
            events=events,
            woodcutting_lifecycle=woodcutting,
            summaries={"summary": summary},
            recording_path=recording_path,
        )
        if interruption.get("interruptionDetected") or _dict(interruption.get("combat")).get("combatStateSnapshotCount") or interruption.get("missingCapabilities"):
            summary["interruption_lifecycle"] = interruption
            if isinstance(summary.get("woodcutting_lifecycle"), dict):
                summary["woodcutting_lifecycle"] = woodcutting_lifecycle.attach_interruption(summary["woodcutting_lifecycle"], interruption)
                telemetry_sources.atomic_write_json(recording_path / "woodcutting_lifecycle.json", summary["woodcutting_lifecycle"], pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "interruption_lifecycle.json", interruption, pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
        damage = combat_damage_summary.analyze_data(events=events, interruption_lifecycle_summary=summary.get("interruption_lifecycle") or interruption, recording_path=recording_path)
        if damage.get("combatObserved") or _dict(damage.get("hitsplats")).get("total"):
            summary["combat_damage_summary"] = damage
            telemetry_sources.atomic_write_json(recording_path / "combat_damage_summary.json", damage, pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
        traversal = traversal_lifecycle.analyze_data(
            events=events,
            joined_input_telemetry=input_join.get("joined_input_telemetry_rows") or [],
            input_action_classifications=input_join.get("input_action_classifications") or [],
            target_match_quality=input_join.get("target_match_quality") or [],
            menu_interactions=input_join.get("menu_interactions") or [],
            summaries={
                "summary": summary,
                "input_action_summary": input_join.get("input_action_summary") or summary.get("input_action_summary") or {},
                "target_match_summary": input_join.get("target_match_summary") or summary.get("target_match_summary") or {},
                "menu_interaction_summary": input_join.get("menu_interaction_summary") or summary.get("menu_interaction_summary") or {},
                "coordinate_alignment_summary": input_join.get("coordinate_alignment_summary") or summary.get("coordinate_alignment_summary") or {},
                "camera_behavior_summary": input_join.get("camera_behavior") or summary.get("camera_behavior") or {},
                "vm_mouse_arduino_mapping": input_join.get("vm_mouse_arduino_mapping") or summary.get("vm_mouse_arduino_mapping") or {},
            },
            recording_path=recording_path,
        )
        if traversal.get("status") != "FAIL" or traversal.get("evidence"):
            summary["traversal_lifecycle"] = traversal
            telemetry_sources.atomic_write_json(recording_path / "traversal_lifecycle.json", traversal, pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
        banking = banking_lifecycle.analyze_data(
            events=events,
            input_action_classifications=input_join.get("input_action_classifications") or [],
            target_match_quality=input_join.get("target_match_quality") or [],
            menu_interactions=input_join.get("menu_interactions") or [],
            summaries={"summary": summary},
            recording_path=recording_path,
        )
        if banking.get("status") != "FAIL" or banking.get("evidence"):
            summary["banking_lifecycle"] = banking
            telemetry_sources.atomic_write_json(recording_path / "banking_lifecycle.json", banking, pretty=pretty)
            telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
    elif isinstance(summary.get("traversal_lifecycle"), dict):
        telemetry_sources.atomic_write_json(recording_path / "traversal_lifecycle.json", summary["traversal_lifecycle"], pretty=pretty)
    refresh_woodcutting_loop_summary(recording_path, summary, pretty=pretty, write=True)
    telemetry_sources.atomic_write_json(recording_path / "summary.json", summary, pretty=pretty)
    report = render_schema_gap_report(summary)
    (recording_path / "schema_gap_report.md").write_text(report, encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a manual telemetry recording folder.")
    parser.add_argument("recording", help="Recording folder or events.jsonl path.")
    parser.add_argument("--summary", action="store_true", help="Print summary.")
    parser.add_argument("--schema-gap", action="store_true", help="Print schema gap report.")
    parser.add_argument("--print-events", action="store_true", help="Print events from events.jsonl.")
    parser.add_argument("--max-events", type=int, default=20, help="Maximum events to print with --print-events.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown output.")
    parser.add_argument("--woodcutting-lifecycle", action="store_true", help="Print or include woodcutting lifecycle analysis.")
    parser.add_argument("--woodcutting-loop-lifecycle", action="store_true", help="Generate full woodcutting task-loop lifecycle analysis.")
    parser.add_argument("--print-woodcutting-loop-lifecycle", action="store_true", help="Print woodcutting_loop_lifecycle JSON.")
    parser.add_argument("--interruption-lifecycle", action="store_true", help="Generate task interruption/combat lifecycle analysis.")
    parser.add_argument("--print-interruption-lifecycle", action="store_true", help="Print interruption_lifecycle JSON.")
    parser.add_argument("--combat-damage-summary", action="store_true", help="Generate compact combat damage summary analysis.")
    parser.add_argument("--print-combat-damage-summary", action="store_true", help="Print combat_damage_summary JSON.")
    parser.add_argument("--banking-lifecycle", action="store_true", help="Generate banking/deposit/withdraw lifecycle analysis.")
    parser.add_argument("--print-banking-lifecycle", action="store_true", help="Print banking_lifecycle JSON.")
    parser.add_argument("--traversal-lifecycle", action="store_true", help="Generate route/traversal lifecycle analysis.")
    parser.add_argument("--group-traversal-steps", action="store_true", help="Group raw traversal evidence into route-progress segments. Enabled by default with traversal lifecycle.")
    parser.add_argument("--print-traversal-lifecycle", action="store_true", help="Print traversal_lifecycle JSON.")
    parser.add_argument("--print-route-segments", action="store_true", help="Print compact traversal route segments JSON.")
    parser.add_argument("--extract-route-template", action="store_true", help="Extract a reusable route template from traversal route segments.")
    parser.add_argument("--route-template-out", default="route_templates", help="Directory for extracted route templates.")
    parser.add_argument("--compare-route-template", help="Compare traversal route segments against a route template JSON file.")
    parser.add_argument("--auto-route-template", action="store_true", help="Auto-select a matching route template by traversal route name or start/end areas.")
    parser.add_argument("--no-auto-route-template", action="store_true", help="Disable route template auto-selection.")
    parser.add_argument("--extract-template-if-missing", action="store_true", help="Extract a route template when auto-selection finds no matching template.")
    parser.add_argument("--print-route-template-comparison", action="store_true", help="Print route_template_comparison JSON.")
    parser.add_argument("--extract-route-variant", action="store_true", help="Extract a route variant from navigation-support substitutions.")
    parser.add_argument("--route-variant-name", help="Name for an extracted route variant.")
    parser.add_argument("--add-route-variant-to-template", help="Route template JSON file to update with the extracted variant.")
    parser.add_argument("--variant-description", help="Description for an extracted route variant.")
    parser.add_argument("--print-route-variant", action="store_true", help="Print route_template_variant JSON.")
    parser.add_argument("--route-monitor", action="store_true", help="Generate route_monitor_status.json.")
    parser.add_argument("--route-monitor-template", help="Route template JSON path for route monitor.")
    parser.add_argument("--print-route-monitor", action="store_true", help="Print route_monitor_status JSON.")
    parser.add_argument("--route-history", action="store_true", help="Generate route history/session artifacts.")
    parser.add_argument("--route-history-out", help="Optional output directory for route history artifacts.")
    parser.add_argument("--print-route-history", action="store_true", help="Print route_history_summary JSON.")
    parser.add_argument("--input-trace", action="store_true", help="Print or include OS input trace summary.")
    parser.add_argument("--join-input", action="store_true", help="Generate joined_input_telemetry.jsonl and related summaries.")
    parser.add_argument("--camera-behavior", action="store_true", help="Generate camera_behavior_summary.json.")
    parser.add_argument("--human-input-summary", action="store_true", help="Generate input_trace_summary.json.")
    parser.add_argument("--arduino-trace", action="store_true", help="Generate arduino_trace_summary.json when arduino_events.jsonl exists.")
    parser.add_argument("--vm-mouse-mapping", action="store_true", help="Generate vm_mouse_arduino_mapping.json.")
    parser.add_argument("--classify-input-actions", action="store_true", help="Generate input action click classifications.")
    parser.add_argument("--print-input-action-summary", action="store_true", help="Print input_action_summary JSON.")
    parser.add_argument("--target-match-quality", action="store_true", help="Generate target match quality tiers for target-relative clicks.")
    parser.add_argument("--print-target-match-quality", action="store_true", help="Print target_match_summary JSON.")
    parser.add_argument("--menu-interactions", action="store_true", help="Generate normalized menu interaction summaries.")
    parser.add_argument("--print-menu-interactions", action="store_true", help="Print menu_interaction_summary JSON.")
    parser.add_argument("--menu-row-diagnostics", action="store_true", help="Generate detailed menu row pairing diagnostics.")
    parser.add_argument("--print-menu-row-diagnostics", action="store_true", help="Print menu row diagnostics JSON.")
    parser.add_argument("--coordinate-alignment", action="store_true", help="Generate coordinate alignment summary.")
    parser.add_argument("--print-coordinate-alignment", action="store_true", help="Print coordinate_alignment_summary JSON.")
    parser.add_argument("--input-path-integrity", action="store_true", help="Generate input path integrity summary.")
    parser.add_argument("--arduino-mirror-verification", action="store_true", help="Generate Arduino mirror verification summary.")
    parser.add_argument("--print-input-path-integrity", action="store_true", help="Print input_path_integrity_summary JSON.")
    parser.add_argument("--human-click-profile", action="store_true", help="Generate human click/camera profile summary.")
    parser.add_argument("--human-click-profile-out", help="Output JSON file or directory for human click profile.")
    parser.add_argument("--profile-recordings", nargs="*", help="Additional recording folders to aggregate into the human click profile.")
    parser.add_argument("--print-human-click-profile", action="store_true", help="Print human click/camera profile JSON.")
    parser.add_argument("--update-knowledge", action="store_true", help="Update telemetry-viewer/knowledge_base after analysis.")
    parser.add_argument("--no-update-knowledge", action="store_true", help="Skip project knowledge update even when a caller normally enables it.")
    parser.add_argument("--knowledge-out", help="Optional output directory for project knowledge JSON indexes.")
    parser.add_argument("--out", help="Optional output path for printed result.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.recording)
    recording_dir = path if path.is_dir() else path.parent
    summary = update_outputs(recording_dir)
    if (
        args.join_input
        or args.input_trace
        or args.camera_behavior
        or args.human_input_summary
        or args.arduino_trace
        or args.vm_mouse_mapping
        or args.classify_input_actions
        or args.print_input_action_summary
        or args.target_match_quality
        or args.print_target_match_quality
        or args.menu_interactions
        or args.print_menu_interactions
        or args.menu_row_diagnostics
        or args.print_menu_row_diagnostics
        or args.coordinate_alignment
        or args.print_coordinate_alignment
        or args.input_path_integrity
        or args.arduino_mirror_verification
        or args.print_input_path_integrity
        or args.banking_lifecycle
        or args.print_banking_lifecycle
        or args.interruption_lifecycle
        or args.print_interruption_lifecycle
        or args.combat_damage_summary
        or args.print_combat_damage_summary
        or args.traversal_lifecycle
        or args.group_traversal_steps
        or args.print_traversal_lifecycle
        or args.print_route_segments
        or args.extract_route_template
        or args.compare_route_template
        or args.auto_route_template
        or args.extract_template_if_missing
        or args.print_route_template_comparison
        or args.extract_route_variant
        or args.print_route_variant
        or args.route_monitor
        or args.print_route_monitor
        or args.route_history
        or args.print_route_history
        or args.human_click_profile
        or args.print_human_click_profile
        or args.woodcutting_loop_lifecycle
        or args.print_woodcutting_loop_lifecycle
    ):
        input_join = input_trace_joiner.analyze_recording(recording_dir, write=True, include_mapping=bool(args.vm_mouse_mapping))
        summary.update({key: value for key, value in input_join.items() if key in {"input_trace", "click_analysis", "hover_analysis", "camera_behavior", "arduino_trace", "arduino_live_mirror", "vm_mouse_arduino_mapping", "input_action_summary", "target_match_summary", "menu_interaction_summary", "coordinate_alignment_summary", "input_path_integrity_summary", "arduino_mirror_verification", "mirror_action_timing", "click_ownership_summary"}})
        summary["warnings"] = sorted(set(list(summary.get("warnings") or []) + list(input_join.get("warnings") or [])))
        if args.banking_lifecycle or args.print_banking_lifecycle:
            events, _ = _load_events(recording_dir / "events.jsonl")
            banking = banking_lifecycle.analyze_data(
                events=events,
                input_action_classifications=input_join.get("input_action_classifications") or [],
                target_match_quality=input_join.get("target_match_quality") or [],
                menu_interactions=input_join.get("menu_interactions") or [],
                summaries={"summary": summary},
                recording_path=recording_dir,
            )
            summary["banking_lifecycle"] = banking
            telemetry_sources.atomic_write_json(recording_dir / "banking_lifecycle.json", banking, pretty=True)
        if args.interruption_lifecycle or args.print_interruption_lifecycle or args.combat_damage_summary or args.print_combat_damage_summary:
            events, _ = _load_events(recording_dir / "events.jsonl")
            woodcutting_for_interruption = _dict(summary.get("woodcutting_lifecycle"))
            if not woodcutting_for_interruption:
                woodcutting_for_interruption = woodcutting_lifecycle.analyze_events(
                    events,
                    input_action_classifications=input_join.get("input_action_classifications") or [],
                    warnings=[],
                )
                if woodcutting_for_interruption.get("status") != "FAIL" or woodcutting_for_interruption.get("evidence"):
                    summary["woodcutting_lifecycle"] = woodcutting_for_interruption
                    telemetry_sources.atomic_write_json(recording_dir / "woodcutting_lifecycle.json", woodcutting_for_interruption, pretty=True)
            interruption = interruption_lifecycle.analyze_data(
                events=events,
                woodcutting_lifecycle=woodcutting_for_interruption,
                summaries={"summary": summary},
                recording_path=recording_dir,
            )
            summary["interruption_lifecycle"] = interruption
            if isinstance(summary.get("woodcutting_lifecycle"), dict):
                summary["woodcutting_lifecycle"] = woodcutting_lifecycle.attach_interruption(summary["woodcutting_lifecycle"], interruption)
                telemetry_sources.atomic_write_json(recording_dir / "woodcutting_lifecycle.json", summary["woodcutting_lifecycle"], pretty=True)
            telemetry_sources.atomic_write_json(recording_dir / "interruption_lifecycle.json", interruption, pretty=True)
            damage = combat_damage_summary.analyze_data(
                events=events,
                interruption_lifecycle_summary=interruption,
                recording_path=recording_dir,
            )
            if damage.get("combatObserved") or _dict(damage.get("hitsplats")).get("total") or damage.get("missingCapabilities"):
                summary["combat_damage_summary"] = damage
                telemetry_sources.atomic_write_json(recording_dir / "combat_damage_summary.json", damage, pretty=True)
        if args.traversal_lifecycle or args.group_traversal_steps or args.print_traversal_lifecycle or args.print_route_segments or args.extract_route_template or args.compare_route_template or args.auto_route_template or args.extract_template_if_missing or args.print_route_template_comparison or args.extract_route_variant or args.print_route_variant or args.route_monitor or args.print_route_monitor or args.route_history or args.print_route_history:
            events, _ = _load_events(recording_dir / "events.jsonl")
            traversal = traversal_lifecycle.analyze_data(
                events=events,
                joined_input_telemetry=input_join.get("joined_input_telemetry_rows") or [],
                input_action_classifications=input_join.get("input_action_classifications") or [],
                target_match_quality=input_join.get("target_match_quality") or [],
                menu_interactions=input_join.get("menu_interactions") or [],
                summaries={
                    "summary": summary,
                    "input_action_summary": summary.get("input_action_summary") or {},
                    "target_match_summary": summary.get("target_match_summary") or {},
                    "menu_interaction_summary": summary.get("menu_interaction_summary") or {},
                    "coordinate_alignment_summary": summary.get("coordinate_alignment_summary") or {},
                    "camera_behavior_summary": summary.get("camera_behavior") or {},
                    "vm_mouse_arduino_mapping": summary.get("vm_mouse_arduino_mapping") or {},
                },
                recording_path=recording_dir,
            )
            summary["traversal_lifecycle"] = traversal
            telemetry_sources.atomic_write_json(recording_dir / "traversal_lifecycle.json", traversal, pretty=True)
        traversal_for_template = _dict(summary.get("traversal_lifecycle"))
        if traversal_for_template:
            summary["detectedRouteName"] = traversal_for_template.get("routeName")
            summary["detectedStartArea"] = _dict(traversal_for_template.get("start")).get("areaLabel")
            summary["detectedEndArea"] = _dict(traversal_for_template.get("end")).get("areaLabel")
        if args.extract_route_template and traversal_for_template:
            template = route_template.extract_template(traversal_for_template, created_from_recording=recording_dir)
            template_path = route_template.write_template(template, args.route_template_out, pretty=True)
            summary["route_template"] = template
            summary["routeTemplatePath"] = str(template_path)
        if args.compare_route_template and traversal_for_template:
            resolution = route_template.resolve_route_template(args.compare_route_template)
            template = _dict(resolution.get("template"))
            comparison = route_template.compare_template(template, traversal_for_template, recording=recording_dir)
            comparison_path = route_template.write_comparison(comparison, recording_dir, pretty=True)
            summary["route_template_comparison"] = comparison
            summary["routeTemplateComparisonPath"] = str(comparison_path)
            summary["routeTemplatePath"] = str(resolution.get("resolvedPath") or Path(args.compare_route_template))
            summary["routeTemplateResolution"] = {key: value for key, value in resolution.items() if key != "template"}
            summary["routeTemplateCompared"] = True
            summary["routeTemplateDirectionMismatch"] = bool(comparison.get("routeTemplateDirectionMismatch") or comparison.get("directionMismatch"))
        auto_route_template = bool(args.auto_route_template) and not bool(args.no_auto_route_template)
        if auto_route_template and not args.compare_route_template and traversal_for_template:
            selection = route_template.resolve_template_auto(traversal_for_template)
            summary["routeTemplateAutoSelection"] = {key: value for key, value in selection.items() if key != "template"}
            summary["untemplatedRoute"] = bool(selection.get("untemplatedRoute"))
            summary["suggestedTemplateName"] = selection.get("suggestedTemplateName")
            if selection.get("status") == "PASS" and selection.get("selectedTemplate"):
                template = _dict(selection.get("template"))
                comparison = route_template.compare_template(template, traversal_for_template, recording=recording_dir)
                comparison_path = route_template.write_comparison(comparison, recording_dir, pretty=True)
                summary["route_template_comparison"] = comparison
                summary["routeTemplateComparisonPath"] = str(comparison_path)
                summary["routeTemplatePath"] = str(selection.get("selectedTemplate"))
                summary["routeTemplateResolution"] = selection.get("resolution") or {}
                summary["routeTemplateCompared"] = True
                summary["routeTemplateStatus"] = comparison.get("status")
                summary["routeTemplateDirectionMismatch"] = bool(comparison.get("routeTemplateDirectionMismatch") or comparison.get("directionMismatch"))
            elif args.extract_template_if_missing:
                template = route_template.extract_template(traversal_for_template, created_from_recording=recording_dir)
                template_path = route_template.write_template(template, args.route_template_out, pretty=True)
                summary["route_template"] = template
                summary["routeTemplatePath"] = str(template_path)
                summary["routeTemplateExtractedBecauseMissing"] = True
            else:
                summary["routeTemplateCompared"] = False
                summary["routeTemplateStatus"] = "UNAVAILABLE"
        if args.extract_route_variant and traversal_for_template:
            template_path_for_variant = args.add_route_variant_to_template or args.compare_route_template
            template_resolution = route_template.resolve_route_template(template_path_for_variant) if template_path_for_variant else {}
            template = _dict(template_resolution.get("template")) if template_path_for_variant else _dict(summary.get("route_template"))
            comparison = _dict(summary.get("route_template_comparison"))
            if template and not comparison:
                comparison = route_template.compare_template(template, traversal_for_template, recording=recording_dir)
                comparison_path = route_template.write_comparison(comparison, recording_dir, pretty=True)
                summary["route_template_comparison"] = comparison
                summary["routeTemplateComparisonPath"] = str(comparison_path)
            variant = route_template.extract_variant(
                template,
                traversal_for_template,
                comparison,
                variant_name=args.route_variant_name,
                description=args.variant_description,
                source_recording=recording_dir,
            )
            variant_path = route_template.write_variant(variant, recording_dir, pretty=True)
            summary["route_template_variant"] = variant
            summary["routeTemplateVariantPath"] = str(variant_path)
            if args.add_route_variant_to_template:
                variant_template_path = template_resolution.get("resolvedPath") or args.add_route_variant_to_template
                _, updated_path = route_template.add_variant_to_template(variant_template_path, variant, pretty=True)
                summary["routeVariantTemplateUpdated"] = str(updated_path)
                summary["routeTemplatePath"] = str(updated_path)
        if (args.route_monitor or args.print_route_monitor) and traversal_for_template:
            monitor_template_path = args.route_monitor_template or args.compare_route_template or summary.get("routeTemplatePath")
            if monitor_template_path:
                resolution = route_template.resolve_route_template(monitor_template_path)
                template = _dict(resolution.get("template"))
                comparison = _dict(summary.get("route_template_comparison"))
                if not comparison and template:
                    comparison = route_template.compare_template(template, traversal_for_template, recording=recording_dir)
                    comparison_path = route_template.write_comparison(comparison, recording_dir, pretty=True)
                    summary["route_template_comparison"] = comparison
                    summary["routeTemplateComparisonPath"] = str(comparison_path)
                    summary["routeTemplatePath"] = str(resolution.get("resolvedPath") or Path(monitor_template_path))
                monitor = route_monitor.monitor_recording(
                    monitor_template_path,
                    recording_dir,
                    lifecycle=traversal_for_template,
                    comparison=comparison,
                )
                if resolution.get("status") != "PASS":
                    monitor.setdefault("warnings", []).append("Route monitor did not load a valid template; live segment completion is not trustworthy.")
                monitor_path = route_monitor.write_status(monitor, recording_dir, pretty=True)
                summary["route_monitor"] = monitor
                summary["routeMonitorPath"] = str(monitor_path)
            else:
                summary["route_monitor"] = {
                    "schema": route_monitor.SCHEMA_VERSION,
                    "status": "WARN",
                    "routeState": "unknown",
                    "warnings": ["route monitor requested without a route template path"],
                    "missingCapabilities": ["route_template"],
                }
        if (args.route_history or args.print_route_history) and traversal_for_template:
            history_template_path = args.route_monitor_template or args.compare_route_template or summary.get("routeTemplatePath")
            if history_template_path:
                resolution = route_template.resolve_route_template(history_template_path)
                template = _dict(resolution.get("template"))
                comparison = _dict(summary.get("route_template_comparison"))
                if not comparison and template:
                    comparison = route_template.compare_template(template, traversal_for_template, recording=recording_dir)
                    comparison_path = route_template.write_comparison(comparison, recording_dir, pretty=True)
                    summary["route_template_comparison"] = comparison
                    summary["routeTemplateComparisonPath"] = str(comparison_path)
                    summary["routeTemplatePath"] = str(resolution.get("resolvedPath") or Path(history_template_path))
                _state, paths, history_summary = route_monitor.write_recording_history(
                    history_template_path,
                    recording_dir,
                    out_dir=args.route_history_out,
                    lifecycle=traversal_for_template,
                    comparison=comparison,
                )
                summary["route_history"] = history_summary
                summary["routeHistorySummaryPath"] = str(paths["summary"])
                summary["routeSessionStatePath"] = str(paths["state"])
                summary["routeSessionEventsPath"] = str(paths["events"])
                summary["routeProgressTimelinePath"] = str(paths["timeline"])
            else:
                summary["route_history"] = {
                    "schema": route_monitor.HISTORY_SUMMARY_SCHEMA,
                    "status": "WARN",
                    "routeState": "unknown",
                    "warnings": ["route history requested without a route template path"],
                }
        if args.human_click_profile or args.print_human_click_profile:
            profile_recordings = [recording_dir]
            if args.profile_recordings:
                profile_recordings = [Path(item) for item in args.profile_recordings]
                if recording_dir not in profile_recordings:
                    profile_recordings.insert(0, recording_dir)
            profile = human_click_profile.analyze_recordings(profile_recordings)
            if args.human_click_profile_out:
                profile_path = human_click_profile.write_profile(profile, args.human_click_profile_out, pretty=True)
            else:
                profile_path = human_click_profile.write_profile(profile, recording_dir / "human_click_profile.json", pretty=True)
            compact_profile = human_click_profile.compact_profile(profile)
            summary["human_click_profile"] = compact_profile
            summary["humanClickProfileStatus"] = profile.get("status")
            summary["humanClickProfilePath"] = str(profile_path)
            summary["clickLandingSummary"] = profile.get("landing") or {}
            summary["cameraProfileSummary"] = profile.get("camera") or {}
            summary["imperfectSuccessfulClickCount"] = profile.get("imperfectSuccessfulClickCount")
        if args.woodcutting_loop_lifecycle or args.print_woodcutting_loop_lifecycle:
            refresh_woodcutting_loop_summary(recording_dir, summary, pretty=True, write=True)
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=True)
    report = render_schema_gap_report(summary)
    if args.update_knowledge and not args.no_update_knowledge:
        try:
            import update_project_knowledge

            knowledge_result = update_project_knowledge.update_knowledge(
                recording=recording_dir,
                scan_recordings=True,
                write_docs_flag=False,
                knowledge_out=args.knowledge_out,
            )
            summary["knowledgeUpdated"] = knowledge_result.get("status") == "PASS"
            summary["knowledgeIndexPath"] = knowledge_result.get("knowledgeIndexPath")
            summary["recordingIndexed"] = bool(knowledge_result.get("recordingIndexed"))
            summary["knowledgeUpdate"] = knowledge_result
        except Exception as error:
            summary["knowledgeUpdated"] = False
            summary["recordingIndexed"] = False
            summary["knowledgeUpdate"] = {
                "schema": "project_knowledge_update_result.v1",
                "status": "WARN",
                "warnings": [f"{type(error).__name__}: {error}"],
            }
            summary["warnings"] = sorted(set(list(summary.get("warnings") or []) + [f"knowledge update failed: {type(error).__name__}: {error}"]))
        telemetry_sources.atomic_write_json(recording_dir / "summary.json", summary, pretty=True)
        report = render_schema_gap_report(summary)
        (recording_dir / "schema_gap_report.md").write_text(report, encoding="utf-8")
    output = ""
    if args.print_events:
        events, warnings = _load_events(path)
        output = json.dumps({"events": events[: max(0, args.max_events)], "warnings": warnings}, indent=2, default=str)
    elif args.print_input_action_summary:
        output = json.dumps(summary.get("input_action_summary") or {}, indent=2, default=str)
    elif args.print_menu_row_diagnostics:
        output = json.dumps(
            {
                "menu_row_diagnostics": _dict(summary.get("menu_interaction_summary")).get("menuRowDiagnostics") or [],
                "menu_interaction_summary": summary.get("menu_interaction_summary") or {},
            },
            indent=2,
            default=str,
        )
    elif args.print_menu_interactions and args.print_target_match_quality:
        output = json.dumps(
            {
                "menu_interaction_summary": summary.get("menu_interaction_summary") or {},
                "target_match_summary": summary.get("target_match_summary") or {},
                "coordinate_alignment_summary": summary.get("coordinate_alignment_summary") or {},
                "input_path_integrity_summary": summary.get("input_path_integrity_summary") or {},
                "arduino_mirror_verification": summary.get("arduino_mirror_verification") or {},
                "arduino_live_mirror": summary.get("arduino_live_mirror") or {},
                "mirror_action_timing": summary.get("mirror_action_timing") or {},
            },
            indent=2,
            default=str,
        )
    elif args.print_coordinate_alignment and args.print_input_path_integrity:
        output = json.dumps(
            {
                "coordinate_alignment_summary": summary.get("coordinate_alignment_summary") or {},
                "input_path_integrity_summary": summary.get("input_path_integrity_summary") or {},
                "arduino_mirror_verification": summary.get("arduino_mirror_verification") or {},
                "arduino_live_mirror": summary.get("arduino_live_mirror") or {},
                "mirror_action_timing": summary.get("mirror_action_timing") or {},
            },
            indent=2,
            default=str,
        )
    elif args.print_coordinate_alignment:
        output = json.dumps(summary.get("coordinate_alignment_summary") or {}, indent=2, default=str)
    elif args.print_input_path_integrity:
        output = json.dumps(
            {
                "input_path_integrity_summary": summary.get("input_path_integrity_summary") or {},
                "arduino_mirror_verification": summary.get("arduino_mirror_verification") or {},
                "mirror_action_timing": summary.get("mirror_action_timing") or {},
            },
            indent=2,
            default=str,
        )
    elif args.print_menu_interactions:
        output = json.dumps(summary.get("menu_interaction_summary") or {}, indent=2, default=str)
    elif args.print_target_match_quality:
        output = json.dumps(summary.get("target_match_summary") or {}, indent=2, default=str)
    elif args.print_route_template_comparison:
        output = json.dumps(summary.get("route_template_comparison") or {}, indent=2, default=str)
    elif args.print_route_variant:
        output = json.dumps(summary.get("route_template_variant") or {}, indent=2, default=str)
    elif args.print_route_monitor:
        output = json.dumps(summary.get("route_monitor") or {}, indent=2, default=str)
    elif args.print_route_history:
        output = json.dumps(summary.get("route_history") or {}, indent=2, default=str)
    elif args.print_human_click_profile:
        profile_payload = {}
        if args.human_click_profile_out:
            profile_payload = human_click_profile.load_profile(args.human_click_profile_out)
        if not profile_payload:
            profile_payload = human_click_profile.load_profile(recording_dir / "human_click_profile.json")
        output = json.dumps(profile_payload or summary.get("human_click_profile") or {}, indent=2, default=str)
    elif args.print_woodcutting_loop_lifecycle:
        output = json.dumps(summary.get("woodcutting_loop_lifecycle") or {}, indent=2, default=str)
    elif args.print_route_segments:
        lifecycle = _dict(summary.get("traversal_lifecycle"))
        output = json.dumps(
            {
                "schema": "traversal_route_segments.v1",
                "status": lifecycle.get("status"),
                "routeName": lifecycle.get("routeName"),
                "rawStepCount": lifecycle.get("rawStepCount"),
                "groupedStepCount": lifecycle.get("groupedStepCount"),
                "routeSegmentCount": lifecycle.get("routeSegmentCount"),
                "successfulSegmentCount": lifecycle.get("successfulSegmentCount"),
                "partialSegmentCount": lifecycle.get("partialSegmentCount"),
                "reviewEvidenceCount": lifecycle.get("reviewEvidenceCount"),
                "grouping": lifecycle.get("grouping") or {},
                "routeSegments": lifecycle.get("routeSegments") or [],
                "reviewEvidence": lifecycle.get("reviewEvidence") or [],
            },
            indent=2,
            default=str,
        )
    elif args.print_traversal_lifecycle:
        output = json.dumps(summary.get("traversal_lifecycle") or {}, indent=2, default=str)
    elif args.print_banking_lifecycle:
        output = json.dumps(summary.get("banking_lifecycle") or {}, indent=2, default=str)
    elif args.print_interruption_lifecycle and args.print_combat_damage_summary:
        output = json.dumps(
            {
                "interruption_lifecycle": summary.get("interruption_lifecycle") or {},
                "combat_damage_summary": summary.get("combat_damage_summary") or {},
            },
            indent=2,
            default=str,
        )
    elif args.print_interruption_lifecycle:
        output = json.dumps(summary.get("interruption_lifecycle") or {}, indent=2, default=str)
    elif args.print_combat_damage_summary:
        output = json.dumps(summary.get("combat_damage_summary") or {}, indent=2, default=str)
    elif args.banking_lifecycle and not (args.summary or args.schema_gap or args.markdown):
        output = json.dumps(summary.get("banking_lifecycle") or {}, indent=2, default=str)
    elif args.interruption_lifecycle and not (args.summary or args.schema_gap or args.markdown):
        output = json.dumps(summary.get("interruption_lifecycle") or {}, indent=2, default=str)
    elif args.combat_damage_summary and not (args.summary or args.schema_gap or args.markdown):
        output = json.dumps(summary.get("combat_damage_summary") or {}, indent=2, default=str)
    elif args.woodcutting_lifecycle and not (args.summary or args.schema_gap or args.markdown):
        output = json.dumps(summary.get("woodcutting_lifecycle") or {}, indent=2, default=str)
    elif args.woodcutting_loop_lifecycle and not (args.summary or args.schema_gap or args.markdown):
        output = json.dumps(summary.get("woodcutting_loop_lifecycle") or {}, indent=2, default=str)
    elif args.schema_gap or args.markdown:
        output = report
    else:
        output = json.dumps(summary, indent=2 if args.json or args.summary else None, separators=None if args.json or args.summary else (",", ":"), default=str)
    if args.out:
        out_path = Path(os.path.expandvars(args.out)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    if args.summary or args.schema_gap or args.print_events or not args.out:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

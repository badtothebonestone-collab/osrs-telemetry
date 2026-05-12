from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import brain_core
import context_service
import live_target_processor as live
from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "live_core_daemon.v1"
OVERLAY_INTENT_SCHEMA = "overlay_intent_state.v1"
HEALTH_SCHEMA = context_service.HEALTH_SCHEMA
STATUS_SCHEMA = context_service.STATUS_SCHEMA
READ_ONLY_NOTES = [
    "This daemon is read-only telemetry/context infrastructure.",
    "It does not click, type, move, invoke menus, execute actions, or mutate game/client state.",
    "Daily mode keeps context in memory and avoids rolling live JSON files unless debug writes are enabled.",
]
DEFAULT_CONTEXT_NEEDS = [
    "baseline",
    "best:tree",
    "nearest:tree",
    "reachability:tree",
    "inventory",
    "activity",
    "liveness",
    "navigation_readiness",
    "events",
    "diagnostics",
    "task_summary",
]
OVERLAY_MODES = {"intent", "candidates", "debug"}


def utc_now() -> str:
    return live.utc_now()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def resolve_session(args: argparse.Namespace) -> Path:
    if args.session:
        session = Path(args.session).expanduser()
        if not session.exists():
            raise RuntimeError(f"Session does not exist: {session}")
        return session.resolve()
    if not args.latest_session:
        raise RuntimeError("Pass --session or --latest-session.")
    session = find_newest_session(get_sessions_dir(getattr(args, "sessions_dir", None)))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(getattr(args, 'sessions_dir', None))}")
    return session.resolve()


def compact_packet_available(session: Path) -> bool:
    state = live.compact_packet_state(session)
    return bool(state.get("available") and state.get("recent"))


def brain_state_scope(session: Path, task: str, goal_count: int | None) -> dict:
    resource_group = brain_core.task_resource_group(task)
    return {
        "sessionPath": str(session.resolve()),
        "task": task,
        "goalCount": goal_count,
        "resourceGroup": resource_group.get("id") or ("woodcutting_logs" if task == "woodcutting" else None),
    }


def load_daemon_brain_state(session: Path, args: argparse.Namespace) -> tuple[dict, str | None]:
    state = brain_core.load_state(args.brain_state_file, args.brain_task, args.goal_count, reset=bool(args.reset_brain_state))
    expected = brain_state_scope(session, args.brain_task, args.goal_count)
    warning = None
    path_warning = brain_core.state_file_path_warning(args.brain_state_file)
    if path_warning:
        warning = path_warning
    if args.brain_state_file and not args.reset_brain_state:
        actual = state.get("brainStateScope") if isinstance(state.get("brainStateScope"), dict) else {}
        expanded_state_path = brain_core.expand_state_path(args.brain_state_file)
        state_path_exists = bool(expanded_state_path and expanded_state_path.exists())
        mismatch = [
            key
            for key in ("sessionPath", "task", "goalCount", "resourceGroup")
            if actual.get(key) is not None and actual.get(key) != expected.get(key)
        ]
        if mismatch or (state_path_exists and not actual and (state.get("latestTick") is not None or state.get("resourceProgress"))):
            warning = "; ".join(item for item in (warning, "brain state scope changed; progress baseline was reset") if item)
            state = brain_core.default_state(args.brain_task, args.goal_count)
    elif args.reset_brain_state:
        warning = "; ".join(item for item in (warning, "brain state reset requested; progress baseline will start from the next inventory snapshot") if item)
    state["sessionPath"] = expected["sessionPath"]
    state["brainStateScope"] = expected
    state["goalResourceGroup"] = expected["resourceGroup"]
    return state, warning


def persist_daemon_brain_state(path: str | None, state: dict, session: Path, args: argparse.Namespace) -> None:
    if not path:
        return
    expected = brain_state_scope(session, args.brain_task, args.goal_count)
    state["sessionPath"] = expected["sessionPath"]
    state["brainStateScope"] = expected
    state["goalResourceGroup"] = expected["resourceGroup"]
    brain_core.write_state(path, state)


def brain_status_fields(state: dict, reset_applied: bool) -> dict:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    return {
        "brainResetApplied": bool(reset_applied),
        "brainBaselineEstablished": bool(progress.get("baselineEstablished") or state.get("baselineEstablished")),
        "brainBaselineTick": progress.get("baselineTick") or state.get("brainBaselineTick"),
        "brainLastProcessedInventorySignature": progress.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature"),
        "brainLastProcessedInventoryTick": progress.get("lastProcessedInventoryTick") or state.get("lastProcessedInventoryTick"),
        "brainObservedGained": None,
        "brainObservedRemoved": None,
        "brainCurrentHeldCount": progress.get("currentHeldCount"),
        "brainBaselineHeldCount": progress.get("baselineHeldCount") if progress.get("baselineHeldCount") is not None else state.get("baselineHeldCount"),
        "brainHasValidPostBaselineProgressHistory": False,
        "brainProgressStateRepaired": bool(progress.get("progressStateRepaired")),
        "brainProgressRepairReason": progress.get("repairReason"),
        "brainCurrentInventorySignature": progress.get("currentInventorySignature"),
        "brainCurrentSnapshotValid": progress.get("currentSnapshotValid"),
        "brainPreviousInventorySnapshotAvailable": progress.get("previousInventorySnapshotAvailable"),
        "progressInvalidSnapshotCount": progress.get("progressInvalidSnapshotCount", 0),
        "progressRetainedPreviousCount": progress.get("progressRetainedPreviousCount", 0),
        "progressFlickerPreventedCount": progress.get("progressFlickerPreventedCount", 0),
        "lastProgressInvalidReason": progress.get("lastProgressInvalidReason"),
        "lastProgressRetainedTick": progress.get("lastProgressRetainedTick"),
        "lastValidProgressTick": progress.get("lastValidProgressTick"),
        "lastValidInventorySignature": progress.get("lastValidInventorySignature"),
        "progressRetainedPreviousThisPoll": bool(progress.get("progressRetainedFromPrevious")),
        "progressInvalidSnapshotThisPoll": progress.get("currentSnapshotValid") is False,
        "progressRetainedFromPrevious": bool(progress.get("progressRetainedFromPrevious")),
        "progressRetainedReason": progress.get("retainedReason"),
    }


def one_shot_brain_warning(warning: str | None) -> bool:
    if not warning:
        return False
    text = str(warning)
    return (
        "brain state reset requested" in text
        or "brain state scope changed; progress baseline was reset" in text
    )


def context_best_label(response: dict, class_id: str = "tree") -> str:
    best = brain_core.best_target(response, class_id)
    if not best:
        return "none"
    name = best.get("targetName") or best.get("name") or "target"
    target_id = best.get("id")
    distance = best.get("distanceTiles")
    reachability = brain_core.candidate_reachability(best)
    parts = [str(name)]
    if target_id is not None:
        parts.append(str(target_id))
    if distance is not None:
        try:
            number = float(distance)
            parts.append(f"d={int(number) if number.is_integer() else round(number, 1)}")
        except (TypeError, ValueError):
            parts.append(f"d={distance}")
    if reachability and reachability != "unknown":
        parts.append("R" if reachability == "reachable" else str(reachability))
    return " ".join(parts)


def brain_progress_label(decision: dict) -> str:
    progress = decision.get("goalProgress") if isinstance(decision.get("goalProgress"), dict) else {}
    if not progress:
        return "off"
    value = progress.get("displayedGoalProgress")
    goal = progress.get("goalCount")
    if value is None or goal is None:
        return "observe"
    return f"{value}/{goal}"


def candidate_identity(candidate: dict | None) -> tuple:
    if not isinstance(candidate, dict):
        return ()
    return (
        candidate.get("objectKey"),
        candidate.get("candidateKey"),
        candidate.get("hash"),
        candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        candidate.get("worldX"),
        candidate.get("worldY"),
        candidate.get("plane"),
        candidate.get("classId"),
    )


def target_type_for_candidate(candidate: dict) -> str:
    target_type = candidate.get("targetType")
    if target_type:
        return str(target_type)
    class_id = str(candidate.get("classId") or "").lower()
    if "npc" in class_id or candidate.get("npcId") is not None:
        return "npc"
    if candidate.get("uiTargetId") is not None:
        return "ui"
    if candidate.get("slot") is not None and candidate.get("itemId") is not None:
        return "inventorySlot"
    if candidate.get("worldX") is not None and candidate.get("worldY") is not None:
        return "sceneObject"
    return "tile"


def intent_marker_from_candidate(
    candidate: dict,
    marker_type: str,
    label: str,
    reason: str,
    *,
    confidence: float | None = None,
    source: str = "brain",
) -> dict:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    aim = candidate.get("aimPoint") if isinstance(candidate.get("aimPoint"), dict) else None
    marker = {
        "markerType": marker_type,
        "label": label,
        "reason": reason,
        "confidence": confidence,
        "source": source,
        "targetType": target_type_for_candidate(candidate),
        "classId": candidate.get("classId"),
        "id": candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        "hash": candidate.get("hash"),
        "objectKey": candidate.get("objectKey"),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "sceneX": candidate.get("sceneX"),
        "sceneY": candidate.get("sceneY"),
        "aimPoint": aim,
        "reachability": navigation.get("directReachability") or candidate.get("directReachability"),
        "liveness": candidate.get("targetLiveState") or candidate.get("liveness"),
        "qualityTier": candidate.get("qualityTier"),
        "name": candidate.get("targetName") or candidate.get("name"),
        "distanceTiles": candidate.get("distanceTiles"),
        "onScreen": candidate.get("onScreen"),
        "geometryAvailable": candidate.get("geometryAvailable"),
    }
    bounds = candidate.get("bounds") if isinstance(candidate.get("bounds"), dict) else None
    if bounds:
        marker["bounds"] = bounds
    return {key: value for key, value in marker.items() if value is not None}


def warning_intent_marker(label: str, reason: str, *, source: str = "brain") -> dict:
    return {
        "markerType": "warning",
        "label": label,
        "reason": reason,
        "confidence": 0.5,
        "source": source,
        "targetType": "tile",
    }


def overlay_target_from_intent_marker(marker: dict) -> dict:
    target = {
        "markerType": marker.get("markerType"),
        "source": marker.get("source"),
        "reason": marker.get("reason"),
        "targetType": marker.get("targetType"),
        "classId": marker.get("classId"),
        "name": marker.get("name") or marker.get("label"),
        "id": marker.get("id"),
        "hash": marker.get("hash"),
        "objectKey": marker.get("objectKey"),
        "worldX": marker.get("worldX"),
        "worldY": marker.get("worldY"),
        "plane": marker.get("plane"),
        "sceneX": marker.get("sceneX"),
        "sceneY": marker.get("sceneY"),
        "distanceTiles": marker.get("distanceTiles"),
        "onScreen": marker.get("onScreen", True),
        "geometryAvailable": marker.get("geometryAvailable"),
        "qualityTier": marker.get("qualityTier"),
        "targetLiveState": marker.get("liveness"),
        "directReachability": marker.get("reachability"),
        "isBest": marker.get("markerType") == "selected_target",
        "overlayLabel": marker.get("label"),
        "aimPoint": marker.get("aimPoint"),
        "bounds": marker.get("bounds"),
    }
    return {key: value for key, value in target.items() if value is not None}


def candidate_is_tree(candidate: dict) -> bool:
    class_id = str(candidate.get("classId") or "").lower()
    name = str(candidate.get("targetName") or candidate.get("name") or "").lower()
    return "tree" in class_id or "tree" in name


def build_intent_overlay_state(context: dict, brain_decision: dict, args: argparse.Namespace, generated_at: str) -> dict:
    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    active_task = str(getattr(args, "brain_task", None) or brain_decision.get("task") or "")
    active_intent = str(brain_decision.get("phase") or "observe")
    markers: list[dict] = []

    if active_task == "woodcutting":
        selected = brain_core.safe_get(brain_decision, "currentContextSummary.bestTarget", {})
        if isinstance(selected, dict) and selected and candidate_is_tree(selected):
            name = selected.get("targetName") or selected.get("name") or "target"
            markers.append(
                intent_marker_from_candidate(
                    selected,
                    "selected_target",
                    f"Target: {name}",
                    "brain selected best reachable tree",
                    confidence=brain_decision.get("confidence"),
                    source="brain",
                )
            )
            selected_key = candidate_identity(selected)
            backup_limit = max(0, int(getattr(args, "overlay_backup_candidates", 2) or 0))
            backups = []
            for candidate in candidates:
                if not isinstance(candidate, dict) or not candidate_is_tree(candidate):
                    continue
                if candidate_identity(candidate) == selected_key:
                    continue
                backups.append(candidate)
                if len(backups) >= backup_limit:
                    break
            for candidate in backups:
                markers.append(
                    intent_marker_from_candidate(
                        candidate,
                        "backup_candidate",
                        "Backup",
                        "nearby backup candidate retained for context",
                        confidence=None,
                        source="context",
                    )
                )
        else:
            markers.append(warning_intent_marker("No reachable tree", "brain did not select a reachable woodcutting target"))

    return {
        "schema": OVERLAY_INTENT_SCHEMA,
        "generatedAtUtc": generated_at,
        "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
        "activeTask": active_task or None,
        "activeIntent": active_intent,
        "status": "WARN" if any(marker.get("markerType") == "warning" for marker in markers) else "PASS",
        "markers": markers,
    }


def build_overlay_state_for_mode(
    session: Path,
    args: argparse.Namespace,
    result: dict,
    context: dict,
    brain_decision: dict,
    generated_at: str,
) -> dict:
    overlay = dict(result.get("overlayDebug") or {})
    summary = dict(overlay.get("summary") or {})
    mode = str(getattr(args, "overlay_mode", "intent") or "intent")
    if mode not in OVERLAY_MODES:
        mode = "intent"
    if mode != "intent":
        summary["overlayMode"] = mode
        summary["intentMarkerCount"] = 0
        summary["candidateMarkersSuppressed"] = 0
        overlay["summary"] = summary
        return overlay

    status = context.get("status") if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
    intent = build_intent_overlay_state(context, brain_decision if isinstance(brain_decision, dict) else {}, args, generated_at)
    markers = list(intent.get("markers") or [])
    targets = [overlay_target_from_intent_marker(marker) for marker in markers if marker.get("markerType") != "warning"]
    candidate_marker_count = sum(1 for marker in markers if marker.get("markerType") in {"selected_target", "backup_candidate"})
    summary.update(
        {
            "overlayMode": "intent",
            "candidateCount": len(candidates),
            "targetsWritten": len(targets),
            "targetLimit": 1 + max(0, int(getattr(args, "overlay_backup_candidates", 2) or 0)),
            "intentMarkerCount": len(markers),
            "candidateMarkersSuppressed": max(0, len(candidates) - candidate_marker_count),
        }
    )
    if not overlay:
        overlay = {
            "schema": live.LIVE_OVERLAY_DEBUG_SCHEMA,
            "generatedAtUtc": generated_at,
            "sessionPath": str(session),
            "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
            "profile": getattr(args, "profile", None),
            "status": intent.get("status"),
            "safety": {"readOnly": True, "drawOnly": True},
        }
    overlay["safety"] = {"readOnly": True, "drawOnly": True}
    overlay["summary"] = summary
    overlay["targets"] = targets
    overlay["markers"] = markers
    overlay["intentState"] = intent
    overlay["status"] = "WARN" if intent.get("status") == "WARN" or status.get("warnings") else "PASS"
    return overlay


def timing_bottleneck(status: dict) -> str | None:
    timing = status.get("timingBreakdownMillis") if isinstance(status.get("timingBreakdownMillis"), dict) else {}
    if not timing:
        return None
    best_key = None
    best_value = None
    for key, value in timing.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if best_value is None or number > best_value:
            best_key = str(key)
            best_value = number
    return best_key


def plugin_snapshot_health_available(args: argparse.Namespace) -> dict:
    return live.plugin_snapshot_state(
        args.plugin_snapshot_host,
        args.plugin_snapshot_port,
        getattr(args, "plugin_snapshot_token", ""),
        getattr(args, "plugin_snapshot_timeout", 0.5),
        probe=True,
    )


def processor_args(args: argparse.Namespace, input_source: str, *, suppress_output_writes: bool) -> SimpleNamespace:
    overlay_target_limit, overlay_hull_limit = live.effective_overlay_draw_limits(args)
    return SimpleNamespace(
        session=str(getattr(args, "session", "") or ""),
        latest_session=bool(getattr(args, "latest_session", False)),
        sessions_dir=getattr(args, "sessions_dir", None),
        input_source=input_source,
        compact_stream_host="127.0.0.1",
        compact_stream_port=8891,
        compact_stream_timeout=0.1,
        stream_fallback_to_compact_packets=False,
        stream_required_types_timeout=2.0,
        plugin_snapshot_host=args.plugin_snapshot_host,
        plugin_snapshot_port=args.plugin_snapshot_port,
        plugin_snapshot_token=getattr(args, "plugin_snapshot_token", ""),
        plugin_snapshot_timeout=args.plugin_snapshot_timeout,
        plugin_snapshot_tier=args.plugin_snapshot_tier,
        plugin_snapshot_max_projection_refs=args.plugin_snapshot_max_projection_refs,
        plugin_snapshot_max_age_ticks=5,
        plugin_snapshot_include_geometry=False,
        plugin_snapshot_response_mode="compact",
        plugin_snapshot_projection_field_mode="compact",
        plugin_snapshot_fallback=getattr(args, "plugin_snapshot_fallback", "none"),
        plugin_snapshot_auto_escalate=False,
        plugin_snapshot_min_candidates=1,
        auto_prefer_plugin_snapshot=False,
        compare_input_sources=False,
        require_compact_packets=input_source == live.COMPACT_PACKET_SOURCE,
        profile=args.profile,
        target_type="all",
        limit=100,
        window_ticks=10,
        poll_interval=args.poll_interval,
        once=False,
        follow=True,
        latency_mode="realtime",
        max_new_ticks_per_update=1,
        candidate_output_window="latest",
        drop_backlog_to_meet_budget=True,
        drain_backlog_on_overrun=True,
        include_ui_targets=False,
        latest=None,
        latest_with_frames=None,
        exclude_ui_blocked=False,
        emit_world_targets="candidates",
        world_target_output_limit=2000,
        depleted_suppress_ticks=20,
        liveness_mode="delta",
        liveness_budget_ms=20.0,
        max_recently_unavailable=1000,
        liveness_visible_ref_scan_limit=500,
        target_update_ms=100.0,
        warn_update_ms=250.0,
        benchmark=bool(args.benchmark),
        verbose=False,
        quiet=True,
        log_every=1,
        event_limit=args.max_events,
        event_timeline_limit=args.max_events,
        disable_event_timeline=False,
        overlay_debug_target_limit=overlay_target_limit,
        overlay_debug_hull_limit=overlay_hull_limit,
        force_window_rebuild=False,
        startup_backfill_ticks=1,
        no_startup_backfill=False,
        process_existing=False,
        no_ui_targets=True,
        summary=bool(args.summary),
        clear_live_output=False,
        max_runtime_seconds=None,
        write_retry_attempts=10,
        write_retry_delay_ms=10,
        strict_writes=False,
        target_library=str(live.DEFAULT_TARGET_LIBRARY_PATH),
        target_profiles=str(live.DEFAULT_TARGET_PROFILES_PATH),
        suppress_output_writes=suppress_output_writes,
    )


@dataclass
class LiveCoreState:
    session: Path
    profile: str
    write_debug_live_files: bool = False
    write_overlay_state: bool = False
    latest_result: dict | None = None
    latest_context: dict = field(default_factory=dict)
    source_status: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    generated_at_utc: str | None = None
    overlay_state_written: bool = False
    overlay_write_error: str | None = None
    update_count: int = 0
    brain_state: dict = field(default_factory=dict)
    brain_decision: dict = field(default_factory=dict)
    context_retained_previous_count: int = 0
    candidate_retained_previous_count: int = 0
    context_retention_streak: int = 0
    last_good_context: dict = field(default_factory=dict)
    last_good_tick: int | None = None

    def result_is_good_context(self, result: dict, status: dict) -> bool:
        baseline = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
        player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        return bool(baseline and player.get("worldX") is not None and candidates)

    def retention_age_ticks(self, status: dict) -> int | None:
        current_tick = status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick")
        try:
            if current_tick is not None and self.last_good_tick is not None:
                return max(0, int(current_tick) - int(self.last_good_tick))
        except (TypeError, ValueError):
            return None
        return None

    def update_from_result(
        self,
        result: dict,
        *,
        input_source_requested: str,
        fallback_reason: str | None,
        active_millis: float,
        overlay_state_written: bool,
        overlay_write_error: str | None,
        brain_decision: dict | None = None,
    ) -> None:
        status = dict(result.get("status") or {})
        status["liveCoreDaemonActive"] = True
        status["liveCoreSchema"] = SCHEMA
        status["writeDebugLiveFiles"] = bool(self.write_debug_live_files)
        status["overlayStateWritten"] = bool(overlay_state_written)
        status["inputSourceRequestedByDaemon"] = input_source_requested
        status["liveCoreFallbackReason"] = fallback_reason
        status["liveCoreActiveMillis"] = round(active_millis, 3)
        status.setdefault("inputSourceActive", status.get("inputSourceActive") or input_source_requested)
        good_context = self.result_is_good_context(result, status)
        retain_context = bool(self.last_good_context and not good_context and self.context_retention_streak < 2)
        retained_reason = None
        retained_age_ticks = self.retention_age_ticks(status)
        if retain_context:
            retained_reason = "current poll had incomplete context; retaining previous good context"
            self.context_retention_streak += 1
            self.context_retained_previous_count += 1
            if not result.get("candidates"):
                self.candidate_retained_previous_count += 1
            retained = dict(self.last_good_context)
            retained_status = dict(retained.get("status") or {})
            retained_status.update(status)
            retained_status.update(
                {
                    "contextRetainedPrevious": True,
                    "contextRetainedReason": retained_reason,
                    "candidateRetainedPrevious": not bool(result.get("candidates")),
                    "candidateRetainedAgeTicks": retained_age_ticks,
                    "contextRetainedPreviousCount": self.context_retained_previous_count,
                    "candidateRetainedPreviousCount": self.candidate_retained_previous_count,
                }
            )
            retained["status"] = retained_status
            retained["warnings"] = list(dict.fromkeys((retained.get("warnings") or []) + [retained_reason]))
            retained["cacheStats"] = dict(retained.get("cacheStats") or {})
            retained["cacheStats"]["retainedPreviousContext"] = True
            retained["cacheStats"]["retainedAgeTicks"] = retained_age_ticks
            self.latest_context = retained
        else:
            if good_context:
                self.context_retention_streak = 0

        self.latest_result = result
        self.generated_at_utc = utc_now()
        self.overlay_state_written = bool(overlay_state_written)
        self.overlay_write_error = overlay_write_error
        self.update_count += 1
        status["contextRetainedPrevious"] = retain_context
        status["contextRetainedReason"] = retained_reason
        status["candidateRetainedPrevious"] = bool(retain_context and not result.get("candidates"))
        status["candidateRetainedAgeTicks"] = retained_age_ticks if retain_context else None
        status["contextRetainedPreviousCount"] = self.context_retained_previous_count
        status["candidateRetainedPreviousCount"] = self.candidate_retained_previous_count
        self.source_status = status
        self.warnings = list(status.get("warnings") or [])
        if overlay_write_error:
            self.warnings.append(f"overlay state write failed: {overlay_write_error}")

        if not retain_context:
            self.latest_context = {
                "session": self.session,
                "paths": live.live_output_paths(self.session),
                "baseline": result.get("baseline") or {},
                "context": result.get("contextIndex") or {},
                "status": status,
                "activity": result.get("activity") or {},
                "events": result.get("events") or [],
                "navigation": result.get("navigation") or {},
                "watchValues": result.get("watchValues") or {},
                "performance": result.get("performance") or {},
                "candidates": result.get("candidates") or [],
                "warnings": list(self.warnings),
                "missingFields": [],
                "sourceFiles": [],
                "cacheStats": {
                    "source": "memory",
                    "reloadCount": self.update_count,
                    "lastReloadUtc": self.generated_at_utc,
                },
            }
        if good_context:
            self.last_good_context = dict(self.latest_context)
            self.last_good_tick = status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick")
        if brain_decision:
            self.brain_decision = brain_decision

    def context(self) -> dict:
        if self.latest_context:
            return dict(self.latest_context)
        return {
            "session": self.session,
            "paths": live.live_output_paths(self.session),
            "baseline": {},
            "context": {},
            "status": {
                "schema": "live_status.v1",
                "liveCoreDaemonActive": True,
                "inputSourceActive": None,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
            },
            "activity": {},
            "events": [],
            "navigation": {},
            "watchValues": {},
            "performance": {},
            "candidates": [],
            "warnings": ["live core daemon has not processed a telemetry update yet"],
            "missingFields": ["baseline", "candidates", "status"],
            "sourceFiles": [],
            "cacheStats": {"source": "memory", "reloadCount": self.update_count},
        }

    def health(self) -> dict:
        context = self.context()
        payload = context_service.health_payload(context)
        payload.update(
            {
                "schema": HEALTH_SCHEMA,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "inputSourceActive": (context.get("status") or {}).get("inputSourceActive"),
                "candidateCount": len(context.get("candidates") or []),
                "updateCount": self.update_count,
                "readOnlyTelemetry": True,
            }
        )
        return payload

    def status(self) -> dict:
        context = self.context()
        payload = context_service.status_payload(context)
        brain = self.brain_decision if isinstance(self.brain_decision, dict) else {}
        progress = brain.get("goalProgress") if isinstance(brain.get("goalProgress"), dict) else {}
        target = brain_core.safe_get(brain, "currentContextSummary.bestTarget", {}) if brain else {}
        passthrough_status_keys = (
            "activeMs",
            "budgetExceeded",
            "activeBottleneck",
            "contextRetainedPrevious",
            "contextRetainedReason",
            "candidateRetainedPrevious",
            "candidateRetainedAgeTicks",
            "contextRetainedPreviousCount",
            "candidateRetainedPreviousCount",
            "progressInvalidSnapshotThisPoll",
            "progressRetainedPreviousThisPoll",
            "progressInvalidSnapshotCount",
            "progressRetainedPreviousCount",
            "progressFlickerPreventedCount",
            "lastProgressInvalidReason",
            "lastProgressRetainedTick",
            "lastValidProgressTick",
            "lastValidInventorySignature",
            "progressRetainedFromPrevious",
            "progressRetainedReason",
            "overlayMode",
            "intentMarkerCount",
            "candidateMarkersSuppressed",
            "overlayStateBytes",
            "overlayWriteError",
        )
        for key in passthrough_status_keys:
            if key in self.source_status:
                payload[key] = self.source_status.get(key)
        payload.update(
            {
                "schema": STATUS_SCHEMA,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "latestUpdateUtc": self.generated_at_utc,
                "brain": brain or None,
                "brainPhase": brain.get("phase"),
                "brainProgress": progress or None,
                "brainBestTree": target if isinstance(target, dict) and target else None,
            }
        )
        return payload


class LiveCoreDaemon:
    def __init__(self, session: Path, args: argparse.Namespace):
        self.session = session
        self.args = args
        brain_state, brain_state_warning = load_daemon_brain_state(session, args)
        self.brain_state_warning = brain_state_warning
        self.state = LiveCoreState(
            session=session,
            profile=args.profile,
            write_debug_live_files=bool(args.write_debug_live_files),
            write_overlay_state=bool(args.write_overlay_state),
            brain_state=brain_state,
        )
        if brain_state_warning:
            self.state.warnings.append(brain_state_warning)
        self.brain_reset_applied = bool(args.reset_brain_state)
        self.args.reset_brain_state = False
        self.processors: dict[str, live.LiveTargetProcessor] = {}
        self.active_source: str | None = None
        self.fallback_reason: str | None = None
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

    def make_processor(self, input_source: str) -> live.LiveTargetProcessor:
        existing = self.processors.get(input_source)
        if existing is not None:
            return existing
        args = processor_args(self.args, input_source, suppress_output_writes=not self.args.write_debug_live_files)
        processor = live.LiveTargetProcessor(self.session, args)
        processor.initialize_from_existing()
        if processor.tick_window and processor.last_result is None:
            processor.process_window(rebuild_reason="startup")
        self.processors[input_source] = processor
        return processor

    def initial_source(self) -> str:
        requested = self.args.input_source
        if requested == live.PLUGIN_SNAPSHOT_SOURCE:
            return live.PLUGIN_SNAPSHOT_SOURCE
        if requested == live.COMPACT_PACKET_SOURCE:
            return live.COMPACT_PACKET_SOURCE
        if requested != "auto":
            return live.COMPACT_PACKET_SOURCE

        health = plugin_snapshot_health_available(self.args)
        if health.get("available"):
            return live.PLUGIN_SNAPSHOT_SOURCE
        self.fallback_reason = health.get("error") or "plugin snapshot endpoint is not healthy; using compact packet files"
        return live.COMPACT_PACKET_SOURCE

    def should_accept_plugin_snapshot(self, result: dict, active_millis: float) -> bool:
        status = result.get("status") if isinstance(result, dict) else {}
        if not isinstance(status, dict):
            return False
        if status.get("pluginSnapshotAvailable") is not True:
            self.fallback_reason = status.get("pluginSnapshotLastError") or "plugin snapshot endpoint unavailable"
            return False
        if status.get("candidateCount", 0) <= 0:
            self.fallback_reason = "plugin snapshot produced no candidates; using compact packet files"
            return False
        if active_millis > float(self.args.target_update_ms):
            self.fallback_reason = f"plugin snapshot active time {active_millis:.1f} ms exceeded daily target"
            return False
        return True

    def poll_once(self) -> dict:
        source = self.active_source or self.initial_source()
        start = time.perf_counter()
        processor = self.make_processor(source)
        _added, result = processor.poll_once()
        active_millis = (time.perf_counter() - start) * 1000.0

        if self.args.input_source == "auto" and source == live.PLUGIN_SNAPSHOT_SOURCE and not self.should_accept_plugin_snapshot(result, active_millis):
            if compact_packet_available(self.session):
                source = live.COMPACT_PACKET_SOURCE
                processor = self.make_processor(source)
                start = time.perf_counter()
                _added, result = processor.poll_once()
                active_millis = (time.perf_counter() - start) * 1000.0
            else:
                self.fallback_reason = (self.fallback_reason or "plugin snapshot was not accepted") + "; compact packet fallback unavailable"

        self.active_source = source
        if isinstance(result.get("status"), dict):
            result["status"] = dict(result["status"])
            result["status"]["liveCoreDaemonActive"] = True
            result["status"]["writeDebugLiveFiles"] = bool(self.args.write_debug_live_files)
            result["status"]["overlayStateWritten"] = False
            result["status"]["inputSourceRequestedByDaemon"] = self.args.input_source
            result["status"]["liveCoreFallbackReason"] = self.fallback_reason
            result["status"]["liveCoreActiveMillis"] = round(active_millis, 3)
            result["status"]["activeMs"] = round(active_millis, 3)
            result["status"]["budgetExceeded"] = bool(active_millis > float(self.args.target_update_ms))
            result["status"]["activeBottleneck"] = timing_bottleneck(result["status"])
            if self.brain_state_warning is None:
                result["status"]["warnings"] = [
                    warning
                    for warning in (result["status"].get("warnings") or [])
                    if not one_shot_brain_warning(warning)
                ]
            if self.brain_state_warning:
                warnings = list(result["status"].get("warnings") or [])
                if self.brain_state_warning not in warnings:
                    warnings.append(self.brain_state_warning)
                result["status"]["warnings"] = warnings
        self.state.update_from_result(
            result,
            input_source_requested=self.args.input_source,
            fallback_reason=self.fallback_reason,
            active_millis=active_millis,
            overlay_state_written=False,
            overlay_write_error=None,
        )
        brain_decision = self.evaluate_brain_if_enabled(result)
        if brain_decision:
            self.state.brain_decision = brain_decision
        overlay_written, overlay_error, overlay_bytes = self.write_overlay_state_if_enabled(result)
        overlay_fields = {
            "overlayStateWritten": bool(overlay_written),
            "overlayWriteError": overlay_error,
            "overlayMode": self.args.overlay_mode,
            "intentMarkerCount": 0,
            "candidateMarkersSuppressed": 0,
            "overlayStateBytes": overlay_bytes,
        }
        if isinstance(result.get("overlayDebug"), dict):
            summary = result["overlayDebug"].get("summary") if isinstance(result["overlayDebug"].get("summary"), dict) else {}
            overlay_fields["overlayMode"] = summary.get("overlayMode", self.args.overlay_mode)
            overlay_fields["intentMarkerCount"] = summary.get("intentMarkerCount", 0)
            overlay_fields["candidateMarkersSuppressed"] = summary.get("candidateMarkersSuppressed", 0)
        self.state.overlay_state_written = bool(overlay_written)
        self.state.overlay_write_error = overlay_error
        self.state.source_status.update(overlay_fields)
        if isinstance(result.get("status"), dict):
            result["status"].update(overlay_fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(overlay_fields)
        if overlay_error:
            warning = f"overlay state write failed: {overlay_error}"
            if warning not in self.state.warnings:
                self.state.warnings.append(warning)
        self.brain_state_warning = None
        return result

    def write_overlay_state_if_enabled(self, result: dict) -> tuple[bool, str | None, int]:
        if not self.args.write_overlay_state:
            return False, None, 0
        generated_at = utc_now()
        overlay = build_overlay_state_for_mode(
            self.session,
            self.args,
            result,
            self.state.context(),
            self.state.brain_decision if isinstance(self.state.brain_decision, dict) else {},
            generated_at,
        )
        if not overlay:
            return False, "overlay debug state was not available", 0
        path = live.live_output_paths(self.session)["overlayDebug"]
        try:
            overlay_summary = overlay.get("summary") if isinstance(overlay.get("summary"), dict) else {}
            overlay["summary"] = overlay_summary
            result["overlayDebug"] = overlay
            text = ""
            size = 0
            for _index in range(3):
                text = json.dumps(overlay, indent=2, sort_keys=False) + "\n"
                size = len(text.encode("utf-8"))
                if overlay["summary"].get("overlayStateBytes") == size:
                    break
                overlay["summary"]["overlayStateBytes"] = size
            live.atomic_write_text(path, text)
            return True, None, size
        except OSError as error:
            return False, f"{type(error).__name__}: {error}", 0

    def default_context_request(self) -> dict:
        return {
            "schema": context_service.REQUEST_SCHEMA,
            "task": self.args.brain_task,
            "needs": list(DEFAULT_CONTEXT_NEEDS),
            "maxCandidates": max(1, int(self.args.max_candidates)),
            "maxEvents": max(0, int(self.args.max_events)),
            "responseMode": "compact",
        }

    def build_context_response(self, request: dict | None = None) -> dict:
        payload = request if isinstance(request, dict) else self.default_context_request()
        if payload.get("schema") != context_service.REQUEST_SCHEMA:
            return context_service.error_payload(f"unsupported schema: {payload.get('schema')}", request_id=payload.get("requestId"))
        return context_service.build_context_response(
            self.state.context(),
            payload,
            default_max_candidates=max(1, int(self.args.max_candidates)),
            max_response_bytes=int(self.args.max_response_bytes),
            compact_include_source_files=False,
            compact_liveness_examples=0,
        )

    def evaluate_brain_if_enabled(self, result: dict) -> dict | None:
        if not self.args.human_dashboard and not self.args.goal_count:
            return None
        response = self.build_context_response(self.default_context_request())
        decision, updated = brain_core.evaluate_brain(
            response,
            self.state.brain_state,
            task=self.args.brain_task,
            goal_count=self.args.goal_count,
            max_events=self.args.max_events,
        )
        self.state.brain_state = updated
        fields = brain_status_fields(self.state.brain_state, self.brain_reset_applied)
        self.state.source_status.update(fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.args.brain_state_file, self.state.brain_state, self.session, self.args)
        return decision

    def start_context_server(self) -> None:
        if self.server is not None:
            return
        if self.args.context_host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("live_core_daemon only binds localhost context endpoints")
        daemon = self

        class Handler(LiveCoreRequestHandler):
            pass

        self.server = ThreadingHTTPServer((self.args.context_host, int(self.args.context_port)), Handler)
        self.server.live_core_daemon = daemon  # type: ignore[attr-defined]
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="live-core-context", daemon=True)
        self.server_thread.start()
        self.args.context_port = int(self.server.server_address[1])

    def stop_context_server(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=1.0)
        self.server = None
        self.server_thread = None

    def run(self) -> int:
        self.start_context_server()
        if self.args.summary:
            print(f"Live core daemon: session={self.session}")
            print(f"context API: http://{self.args.context_host}:{self.args.context_port}")
            print("Daily mode:")
            print(f"  source={self.args.input_source}")
            print(f"  writes={'on' if self.args.write_debug_live_files else 'off'}")
            print(f"  overlay={'on' if self.args.write_overlay_state else 'off'} ({self.args.overlay_mode})")
            print(f"  brain={'on' if (self.args.human_dashboard or self.args.goal_count) else 'off'}")
            print(f"  debug files={'on' if self.args.write_debug_live_files else 'off'}")
            print(
                "  experimental snapshot/stream="
                f"{'on' if self.args.input_source == live.PLUGIN_SNAPSHOT_SOURCE else 'off'}/off"
            )
        while not self.stop_event.is_set():
            started = time.perf_counter()
            try:
                result = self.poll_once()
                if self.args.benchmark:
                    self.print_benchmark(result)
                if self.args.human_dashboard:
                    self.print_human_summary()
            except KeyboardInterrupt:
                break
            except Exception as error:  # noqa: BLE001
                self.state.warnings.append(f"poll failed: {type(error).__name__}: {error}")
                print(f"Warning: live core poll failed: {type(error).__name__}: {error}", file=sys.stderr)
            elapsed = time.perf_counter() - started
            self.stop_event.wait(max(0.0, float(self.args.poll_interval) - elapsed))
        return 0

    def print_benchmark(self, result: dict) -> None:
        status = result.get("status") or {}
        timing = status.get("timingBreakdownMillis") if isinstance(status.get("timingBreakdownMillis"), dict) else {}
        print(
            "tick={tick} input={source} candidates={candidates} best={best} "
            "activeMs={active} contextMs={context_ms} brain={brain} progress={progress} overlay={overlay} writes={writes} "
            "budget={budget} bottleneck={bottleneck} progressRetained={progress_retained} "
            "candidateRetained={candidate_retained}".format(
                tick=status.get("lastProcessedTick") or status.get("latestTickProcessed"),
                source=status.get("inputSourceActive"),
                candidates=status.get("candidateCount"),
                best=context_best_label(self.build_context_response(self.default_context_request())),
                active=status.get("liveCoreActiveMillis") or status.get("processingDurationMillis"),
                context_ms=round(float(timing.get("contextIndexMillis") or 0.0), 3),
                brain=(self.state.brain_decision or {}).get("phase") or "off",
                progress=brain_progress_label(self.state.brain_decision or {}),
                overlay="on" if self.args.write_overlay_state else "off",
                writes="on" if self.args.write_debug_live_files else "off",
                budget="exceeded" if status.get("budgetExceeded") else "ok",
                bottleneck=status.get("activeBottleneck") or "unknown",
                progress_retained="yes" if status.get("progressRetainedPreviousThisPoll") else "no",
                candidate_retained="yes" if status.get("candidateRetainedPrevious") else "no",
            )
        )
        if status.get("progressRetainedPreviousThisPoll"):
            print(
                "progress retained: {reason}; activeMs={active}; bottleneck={bottleneck}".format(
                    reason=status.get("progressRetainedReason") or "invalid inventory snapshot",
                    active=status.get("liveCoreActiveMillis") or status.get("processingDurationMillis"),
                    bottleneck=status.get("activeBottleneck") or "unknown",
                )
            )

    def print_human_summary(self) -> None:
        response = self.build_context_response(self.default_context_request())
        print(format_context_human(response, compact=True, top=max(1, int(self.args.max_candidates))))
        if self.state.brain_decision:
            print("")
            print(brain_core.format_human(self.state.brain_decision))


class LiveCoreRequestHandler(BaseHTTPRequestHandler):
    server_version = "OSRSTelemetryLiveCoreDaemon/0.1"

    @property
    def daemon(self) -> LiveCoreDaemon:
        return self.server.live_core_daemon  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/health":
            self.send_json(self.daemon.state.health())
        elif path == "/schema":
            payload = context_service.schema_payload()
            payload["service"] = "live_core_daemon"
            payload["notes"] = list(dict.fromkeys((payload.get("notes") or []) + READ_ONLY_NOTES))
            payload["endpoints"] = {
                "GET": ["/health", "/schema", "/status", "/summary", "/brain", "/capabilities", "/watches"],
                "POST": ["/context", "/context/batch", "/brain", "/watch-request"],
            }
            self.send_json(payload)
        elif path == "/status":
            self.send_json(self.daemon.state.status())
        elif path == "/summary":
            self.handle_summary(params)
        elif path == "/brain":
            self.handle_brain(params, {})
        elif path == "/capabilities":
            self.send_json(context_service.capabilities_payload(self.daemon.state.context()))
        elif path == "/watches":
            self.send_json(context_service.watches_payload(self.daemon.state.context()))
        else:
            self.send_json(context_service.error_payload(f"unknown endpoint: {self.path}"), status_code=404)

    def do_POST(self):  # noqa: N802
        if self.path not in {"/context", "/context/batch", "/brain", "/watch-request"}:
            self.send_json(context_service.error_payload(f"unknown endpoint: {self.path}"), status_code=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            body = self.rfile.read(min(length, 1_000_000)).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_json(context_service.error_payload(f"invalid JSON request: {error}"), status_code=400)
            return
        if self.path == "/context/batch":
            if not isinstance(payload, list):
                self.send_json(context_service.error_payload("/context/batch expects a JSON list"), status_code=400)
                return
            self.send_json([self.daemon.build_context_response(item) for item in payload])
        elif self.path == "/watch-request":
            self.send_json(context_service.handle_watch_request_payload(self.daemon.state.context(), payload))
        elif self.path == "/brain":
            self.handle_brain({}, payload if isinstance(payload, dict) else {})
        else:
            self.send_json(self.daemon.build_context_response(payload if isinstance(payload, dict) else {}))

    def handle_summary(self, params: dict[str, list[str]]) -> None:
        task = (params.get("task") or [self.daemon.args.brain_task])[0] or self.daemon.args.brain_task
        try:
            top = max(1, int((params.get("top") or [str(self.daemon.args.max_candidates)])[0]))
        except ValueError:
            top = max(1, int(self.daemon.args.max_candidates))
        request = dict(self.daemon.default_context_request())
        request["task"] = task
        request["maxCandidates"] = top
        request["maxEvents"] = top
        response = self.daemon.build_context_response(request)
        if ((params.get("format") or ["text"])[0] or "text").lower() == "json":
            self.send_json(response)
        else:
            self.send_text(format_context_human(response, compact=False, top=top))

    def handle_brain(self, params: dict[str, list[str]], payload: dict) -> None:
        task = payload.get("task") if isinstance(payload, dict) else None
        task = task or ((params.get("task") or [self.daemon.args.brain_task])[0] if params else self.daemon.args.brain_task)
        goal_count = payload.get("goalCount") if isinstance(payload, dict) else None
        if goal_count is None:
            goal_count = self.daemon.args.goal_count
        request = dict(self.daemon.default_context_request())
        request["task"] = task
        response = self.daemon.build_context_response(request)
        decision, updated = brain_core.evaluate_brain(
            response,
            self.daemon.state.brain_state,
            task=str(task or "woodcutting"),
            goal_count=goal_count,
            max_events=self.daemon.args.max_events,
        )
        self.daemon.state.brain_state = updated
        fields = brain_status_fields(self.daemon.state.brain_state, self.daemon.brain_reset_applied)
        self.daemon.state.source_status.update(fields)
        if isinstance(self.daemon.state.latest_context.get("status"), dict):
            self.daemon.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.daemon.args.brain_state_file, self.daemon.state.brain_state, self.daemon.session, self.daemon.args)
        self.daemon.state.brain_decision = decision
        self.send_json(decision)

    def send_json(self, payload: Any, status_code: int = 200) -> None:
        data = json_bytes(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_text(self, text: str, status_code: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only in-memory daily live daemon. It serves context from telemetry observations only and emits no actions."
    )
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--session", help="Telemetry session directory.")
    session.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--sessions-dir", help="Override sessions root.")
    parser.add_argument("--profile", default="woodcutting", choices=["woodcutting", "broad_qa", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa"])
    parser.add_argument("--input-source", choices=["plugin-snapshot", "compact-packets", "auto"], default="compact-packets")
    parser.add_argument("--plugin-snapshot-host", default="127.0.0.1")
    parser.add_argument("--plugin-snapshot-port", type=int, default=8893)
    parser.add_argument("--plugin-snapshot-token", default="")
    parser.add_argument("--plugin-snapshot-timeout", type=float, default=0.5)
    parser.add_argument("--plugin-snapshot-tier", choices=sorted(live.PLUGIN_SNAPSHOT_TIERS), default="hot")
    parser.add_argument("--plugin-snapshot-max-projection-refs", type=int)
    parser.add_argument("--plugin-snapshot-fallback", choices=["none", "compact-packets"], default="none")
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--context-host", default="127.0.0.1")
    parser.add_argument("--context-port", type=int, default=8890)
    parser.add_argument("--write-overlay-state", action="store_true")
    parser.add_argument("--overlay-mode", choices=sorted(OVERLAY_MODES), default="intent")
    parser.add_argument("--overlay-backup-candidates", type=int, default=2)
    parser.add_argument("--overlay-debug-target-limit", type=int, default=10)
    parser.add_argument("--write-debug-live-files", dest="write_debug_live_files", action="store_true")
    parser.add_argument("--no-debug-live-files", dest="write_debug_live_files", action="store_false")
    parser.set_defaults(write_debug_live_files=False)
    parser.add_argument("--human-dashboard", action="store_true")
    parser.add_argument("--brain-task", default="woodcutting")
    parser.add_argument("--goal-count", type=int)
    parser.add_argument("--brain-state-file", help="Optional brain_state.v1 file. If omitted, daily daemon progress stays in process memory only.")
    parser.add_argument("--reset-brain-state", action="store_true", help="Reset in-memory or file-backed brain progress before observing.")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000)
    parser.add_argument("--target-update-ms", type=float, default=100.0)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--summary", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        session = resolve_session(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    daemon = LiveCoreDaemon(session, args)

    def stop(_signum=None, _frame=None):
        daemon.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    try:
        return daemon.run()
    finally:
        daemon.stop_context_server()


if __name__ == "__main__":
    raise SystemExit(main())

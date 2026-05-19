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
import cycle_history as cycle_history_module
import intent_stabilizer
import live_target_processor as live
import mission_presets
import runtime_control
import task_policy as task_policy_module
from analyzers import activity_analyzer
from analyzers import bank_operation_analyzer
from analyzers import bank_ui_analyzer
from analyzers import brain_context_analyzer
from analyzers import close_bank_analyzer
from analyzers import intent_overlay_analyzer
from analyzers import navigation_analyzer
from analyzers import navigation_intent_analyzer
from analyzers import pathing_analyzer
from analyzers import post_bank_reacquisition_analyzer
from analyzers import process_inventory_analyzer
from analyzers import resource_return_analyzer
from analyzers import return_to_resource_analyzer
from analyzers import service_analyzer
from analyzers import target_analyzer
from analyzers.live_state import BankOperationContext, InventoryContext, LiveAnalysisResult, LiveInputSnapshot, LiveSourceStatus, PlayerContext
from input_control.input_geometry import input_geometry_from_status
from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "live_core_daemon.v1"
OVERLAY_INTENT_SCHEMA = intent_overlay_analyzer.OVERLAY_INTENT_SCHEMA
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
OVERLAY_MODES = intent_overlay_analyzer.OVERLAY_MODES
DAILY_MODE_COMPACT_PACKETS = "compact-packets"
DAILY_MODE_SNAPSHOT_NO_FILES = "snapshot-no-files"
DAILY_MODES = {DAILY_MODE_COMPACT_PACKETS, DAILY_MODE_SNAPSHOT_NO_FILES}


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


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def safe_json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def compact_packet_file_write_state(session: Path) -> dict:
    manifest = safe_json_dict(session / "manifest.json")
    packet_dir = session / "live_packets"
    packet_state = live.compact_packet_state(session)
    manifest_enabled = boolish(manifest.get("compactLivePacketFilesEnabled"))
    if manifest_enabled is not None:
        writing = manifest_enabled
    else:
        writing = False
    return {
        "compactPacketFilesWriting": writing,
        "compactPacketFilesEnabledInManifest": manifest_enabled,
        "compactPacketFilesAvailable": bool(packet_state.get("available")),
        "compactPacketFilesRecent": bool(packet_state.get("recent")),
        "compactPacketFileCount": len(list(packet_dir.glob("live-*.ndjson"))) if packet_dir.exists() else 0,
    }


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
    return brain_context_analyzer.brain_status_fields(state, reset_applied)


def runtime_control_from_args(args: argparse.Namespace) -> runtime_control.RuntimeControlState:
    brain_enabled = getattr(args, "brain_enabled", None)
    if brain_enabled is None:
        brain_enabled = bool(args.human_dashboard or args.goal_count is not None)
    return runtime_control.RuntimeControlState(
        activeTask=str(args.brain_task or args.profile or "woodcutting"),
        activeMissionPreset=getattr(args, "preset", None),
        taskPolicy=str(args.task_policy or task_policy_module.default_policy_name(args.brain_task, args.profile)),
        goalCount=args.goal_count,
        observeOnly=bool(getattr(args, "observe_only", False)),
        brainEnabled=bool(brain_enabled),
        overlayEnabled=bool(args.write_overlay_state),
        overlayMode=str(args.overlay_mode or "intent"),
        overlayBackupCandidates=int(args.overlay_backup_candidates),
    )


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


# Compatibility names for tests and small tools. Implementation lives in analyzers.intent_overlay_analyzer.
candidate_identity = intent_overlay_analyzer.candidate_identity
candidate_id_value = intent_overlay_analyzer.candidate_id_value
target_identity_keys = intent_overlay_analyzer.target_identity_keys
same_target_identity = intent_overlay_analyzer.same_target_identity
polygon_points = intent_overlay_analyzer.polygon_points
marker_geometry_value = intent_overlay_analyzer.marker_geometry_value
marker_bounds_value = intent_overlay_analyzer.marker_bounds_value
best_marker_geometry_source = intent_overlay_analyzer.best_marker_geometry_source
finalize_intent_marker = intent_overlay_analyzer.finalize_intent_marker
merge_marker_from_source = intent_overlay_analyzer.merge_marker_from_source
target_type_for_candidate = intent_overlay_analyzer.target_type_for_candidate
intent_marker_from_candidate = intent_overlay_analyzer.intent_marker_from_candidate
warning_intent_marker = intent_overlay_analyzer.warning_intent_marker
overlay_target_from_intent_marker = intent_overlay_analyzer.overlay_target_from_intent_marker
marker_label_for_candidate = intent_overlay_analyzer.marker_label_for_candidate
target_required_for_intent = intent_overlay_analyzer.target_required_for_intent
candidate_key = intent_overlay_analyzer.candidate_key
candidate_matches_key = intent_overlay_analyzer.candidate_matches_key
stable_backup_candidates = intent_overlay_analyzer.stable_backup_candidates
build_intent_overlay_state = intent_overlay_analyzer.build_intent_overlay_state
build_overlay_state_for_mode = intent_overlay_analyzer.build_overlay_state_for_mode

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
        task_policy=getattr(args, "task_policy", None),
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
    analysis_result: LiveAnalysisResult = field(default_factory=LiveAnalysisResult)
    path_intent_state: pathing_analyzer.PathIntentState = field(default_factory=pathing_analyzer.PathIntentState)
    service_target_state: service_analyzer.ServiceTargetState = field(default_factory=service_analyzer.ServiceTargetState)
    resource_area_memory: resource_return_analyzer.ResourceAreaMemoryState = field(default_factory=resource_return_analyzer.ResourceAreaMemoryState)
    cycle_history: cycle_history_module.CycleHistoryTracker = field(default_factory=cycle_history_module.CycleHistoryTracker)
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
                "bankUi": result.get("bankUi") or {},
                "watchValues": result.get("watchValues") or {},
                "performance": result.get("performance") or {},
                "candidates": result.get("candidates") or [],
                "loadedServiceScene": result.get("loadedServiceScene") or [],
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

    def record_cycle_history(self, timestamp: str | None = None) -> bool:
        if not isinstance(self.brain_decision, dict) or not self.brain_decision:
            return False
        status = dict(self.source_status)
        status["brain"] = self.brain_decision
        entry = cycle_history_module.entry_from_status(status, timestamp=timestamp or self.generated_at_utc)
        return self.cycle_history.update(entry)

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
                "dailyMode": None,
            },
            "activity": {},
            "events": [],
            "navigation": {},
            "bankUi": {},
            "watchValues": {},
            "performance": {},
            "candidates": [],
            "loadedServiceScene": [],
            "warnings": ["live core daemon has not processed a telemetry update yet"],
            "missingFields": ["baseline", "candidates", "status"],
            "sourceFiles": [],
            "cacheStats": {"source": "memory", "reloadCount": self.update_count},
        }

    def health(self) -> dict:
        context = self.context()
        payload = context_service.health_payload(context)
        status = context.get("status") if isinstance(context.get("status"), dict) else {}
        payload.update(
            {
                "schema": HEALTH_SCHEMA,
                "service": "live_core_daemon",
                "liveCoreDaemonActive": True,
                "profile": self.profile,
                "activeProfile": self.profile,
                "writeDebugLiveFiles": bool(self.write_debug_live_files),
                "overlayStateWritten": bool(self.overlay_state_written),
                "inputSourceActive": status.get("inputSourceActive"),
                "dailyMode": status.get("dailyMode"),
                "noFileDaily": status.get("noFileDaily"),
                "compactPacketFilesRequired": status.get("compactPacketFilesRequired"),
                "compactPacketFilesWriting": status.get("compactPacketFilesWriting"),
                "debugMirrorEnabled": status.get("debugMirrorEnabled"),
                "candidateCount": len(context.get("candidates") or []),
                "updateCount": self.update_count,
                "readOnlyTelemetry": True,
            }
        )
        return payload

    def status(self) -> dict:
        context = self.context()
        payload = context_service.status_payload(context)
        baseline = context.get("baseline") if isinstance(context.get("baseline"), dict) else {}
        input_geometry = input_geometry_from_status({"baseline": baseline})
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
            "rawBestTarget",
            "stabilizedIntentTarget",
            "intentRawBestTarget",
            "intentStableTarget",
            "stabilizedIntentTargetLabel",
            "intentStableForTicks",
            "intentSwitchReason",
            "intentHardSwitch",
            "intentSoftSwitch",
            "intentSelectedTargetKey",
            "intentRawBestTargetKey",
            "intentPreviousTargetKey",
            "intentCandidateWasRetained",
            "intentCandidateWasSwitched",
            "intentSwitchedThisTick",
            "intentInterruptReason",
            "intentRetainedDueToGrace",
            "intentCurrentMissingTicks",
            "intentCurrentMissingThisTick",
            "intentCurrentInvalidReason",
            "intentSwitchAuditTail",
            "intentStabilizerMillis",
            "intentCandidatesConsidered",
            "dailyMode",
            "noFileDaily",
            "compactPacketFilesRequired",
            "compactPacketFilesWriting",
            "compactPacketFilesEnabledInManifest",
            "compactPacketFilesAvailable",
            "compactPacketFilesRecent",
            "compactPacketFileCount",
            "debugMirrorEnabled",
            "serviceNeeded",
            "serviceTypeNeeded",
            "serviceCandidateCount",
            "serviceTargetRetained",
            "retainedServiceTargetName",
            "retainedServiceMissingTicks",
            "retainedServiceCandidateCount",
            "retainedBestServiceCandidate",
            "retainedServiceAgeTicks",
            "preferredServiceTypesSeen",
            "preferredServiceTypesRecentlySeen",
            "missingPreferredReason",
            "selectedServiceTargetSource",
            "primaryServiceVisible",
            "primaryServiceRetained",
            "depositFallbackAllowed",
            "selectedServiceGroup",
            "logicError",
            "visiblePrimaryServiceTargetCount",
            "visibleDepositServiceTargetCount",
            "sourceStageCounts",
            "memoryLifecycle",
            "serviceSwitchReason",
            "serviceCandidateDroppedReason",
            "serviceReady",
            "serviceReadyReason",
            "serviceReadyStableForTicks",
            "bankOpen",
            "bankReadable",
            "bankPinOpen",
            "bankUiStatus",
            "bankUiReason",
            "bankUiMissingCapabilities",
            "bankUiWarnings",
            "bankTopLevelInterfaceId",
            "bankRootVisible",
            "bankContainerVisible",
            "bankInventoryVisible",
            "depositInventoryButtonVisible",
            "closeButtonVisible",
            "bankCloseButtonVisible",
            "bankOccupiedSlots",
            "bankUniqueItemCount",
            "inventoryOccupiedSlots",
            "inventoryFreeSlots",
            "inventoryMatchingResourceCount",
            "bankOperationNeeded",
            "bankOperationType",
            "bankResourceItemsHeld",
            "bankResourceItemSlots",
            "bankResourceItemQuantity",
            "bankNonResourceItemsHeld",
            "bankOperationInventoryFreeSlots",
            "bankOperationInventoryFull",
            "bankDepositInventoryAvailable",
            "bankingComplete",
            "bankOperationCompletionReason",
            "bankOperationStatus",
            "bankOperationReason",
            "bankOperationMissingCapabilities",
            "bankOperationWarnings",
            "returnToResourceNeeded",
            "returnToResourceReady",
            "returnToResourceStatus",
            "returnToResourceReason",
            "returnResourceTargetAvailable",
            "returnBestResourceTarget",
            "returnResourcePathingNeeded",
            "returnServiceComplete",
            "returnInventoryFreeSlots",
            "returnInventoryFull",
            "returnToResourceMissingCapabilities",
            "returnToResourceWarnings",
            "resourceMemoryValid",
            "resourceMemoryAgeTicks",
            "resourceMemoryInvalidReason",
            "resourceReturnDestinationNeeded",
            "resourceReturnDestinationAvailable",
            "resourceReturnDestinationTile",
            "resourceReturnDestinationSource",
            "resourceReturnReason",
            "resourceReturnStatus",
            "resourceReturnTargetCurrentlyVisible",
            "resourceReturnMissingCapabilities",
            "resourceReturnWarnings",
            "postBankReacquisitionNeeded",
            "postBankUiStillOpen",
            "postBankWorldViewReady",
            "postBankResourceTargetReacquisitionAllowed",
            "postBankResourceTargetAvailable",
            "postBankReacquisitionStatus",
            "postBankReacquisitionReason",
            "postBankReacquisitionMissingCapabilities",
            "postBankReacquisitionWarnings",
            "closeBankNeeded",
            "closeBankReady",
            "closeBankStatus",
            "closeBankReason",
            "closeBankOpen",
            "closeBankingComplete",
            "closeBankCloseButtonVisible",
            "closeBankCloseButtonAvailable",
            "closeBankKeyboardClosePossible",
            "closeBankMissingCapabilities",
            "closeBankWarnings",
            "selectedServiceTargetName",
            "selectedServiceTargetTile",
            "distanceToServiceTarget",
            "serviceArrivedAtFinalApproach",
            "serviceArrivedNearDestination",
            "serviceDistanceToFinalApproach",
            "profileCandidateCount",
            "broadCandidateCount",
            "serviceCandidateInputCount",
            "serviceCandidateVisibility",
            "serviceCandidateSourceLanes",
            "pluginSnapshotServiceHintsUsed",
            "serviceCandidateInputsPreview",
            "collisionWindowAvailable",
            "collisionWindowFresh",
            "collisionWindowRadius",
            "collisionWindowCenterWorld",
            "collisionWindowPlane",
            "collisionWindowAgeTicks",
            "collisionWindowMissingReason",
            "processInventoryNeeded",
            "processTypeNeeded",
            "navigationIntentNeeded",
            "navigationIntentReason",
            "navigationIntentTargetKind",
            "navigationIntentReachability",
            "navigationIntentDistanceTiles",
            "navigationIntentCollisionWindowAvailable",
            "pathingNeeded",
            "pathingReason",
            "pathingLocalReachability",
            "pathingPathLengthTiles",
            "pathingDestinationTile",
            "pathingFinalApproachTile",
            "pathingFinalApproachTileSource",
            "pathingFinalApproachCandidateCount",
            "pathingRejectedApproachTileReasons",
            "pathingFinalApproachTileUsed",
            "pathingPathTargetTile",
            "pathingPathTargetTileSource",
            "pathingNextWaypointTile",
            "pathingCollisionWindowAvailable",
            "pathingCollisionWindowFresh",
            "pathingCollisionWindowRadius",
            "pathingCollisionWindowCenterWorld",
            "pathingCollisionWindowPlane",
            "pathingCollisionWindowAgeTicks",
            "pathingDestinationInsideCollisionWindow",
            "pathingDestinationPlaneMatches",
            "pathingCollisionWindowMissingReason",
            "pathingMovementModel",
            "pathingPathCapTiles",
            "pathingExactDestinationReached",
            "pathingFinalApproachSubstituted",
            "pathingPredictedPathCount",
            "pathingPredictedPathDisplayedCount",
            "pathingPredictedPathAvailableCount",
            "pathingPathWasCapped",
            "pathingPathDisplayWasCapped",
            "overlayPredictedPathLimit",
            "pathingPathSegmentsValid",
            "pathingInvalidPathSegmentCount",
            "pathingFirstInvalidPathSegment",
            "pathingSelectedApproachReason",
            "pathingApproachQuality",
            "pathingApproachCandidatesTested",
            "pathingApproachCandidatesRejectedByBlockedSide",
            "pathingApproachCandidatesRejectedByNoLineOfSight",
            "pathingSideAccessValid",
            "pathingLineOfSightToTarget",
            "pathingDiagonalStepCount",
            "pathingCardinalStepCount",
            "pathingMillis",
            "pathNodesExpanded",
            "pathingBudgetExceeded",
            "pathIntentKey",
            "pathDestinationTargetKey",
            "pathIntentRetained",
            "pathStableForTicks",
            "pathMovementState",
            "pathRetentionReason",
            "pathSwitchReason",
            "arrivedAtFinalApproach",
            "arrivedNearDestination",
            "distanceToFinalApproach",
            "distanceToDestination",
            "distanceToPathTarget",
            "arrivedStableForTicks",
            "arrivalReason",
            "pathCompleted",
            "pathCompletionReason",
            "retainedPathAfterArrival",
            "requiredContextDomains",
            "missingRequiredContextDomains",
            "optionalMissingContextDomains",
            "targetCandidatesRequired",
            "runtimeControl",
            "runtimeControlLastUpdatedUtc",
            "brainTaskPolicy",
            "brainGoalCount",
            "observeOnly",
            "brainEnabled",
            "inputGeometry",
            "inputGeometryAvailable",
            "canvasScreenOrigin",
            "canvasSize",
            "sourceCanvasSize",
            "clientWindowBounds",
            "displayScale",
            "inputGeometryReason",
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
                "runtimeControl": self.source_status.get("runtimeControl"),
                "inputGeometry": input_geometry,
                "inputGeometryAvailable": input_geometry.get("inputGeometryAvailable"),
                "canvasScreenOrigin": input_geometry.get("canvasScreenOrigin"),
                "canvasSize": input_geometry.get("canvasSize"),
                "sourceCanvasSize": input_geometry.get("sourceCanvasSize"),
                "clientWindowBounds": input_geometry.get("clientWindowBounds"),
                "displayScale": input_geometry.get("displayScale"),
                "inputGeometryReason": input_geometry.get("reason"),
            }
        )
        cycle_summary = self.cycle_history.summary(tail=10)
        payload.update(
            {
                "currentCycleStage": cycle_summary.get("currentCycleStage"),
                "currentCycleStageStableForTicks": cycle_summary.get("currentCycleStageStableForTicks"),
                "lastCycleStage": cycle_summary.get("lastCycleStage"),
                "lastCycleTransitionReason": cycle_summary.get("lastCycleTransitionReason"),
                "lastCycleStageChangeTick": cycle_summary.get("lastStageChangeTick"),
                "cycleHistoryCount": cycle_summary.get("cycleHistoryCount"),
                "cycleTransitionCount": cycle_summary.get("transitionCount"),
                "cycleHistoryTail": cycle_summary.get("cycleHistory"),
                "cycleLastWarningSummary": cycle_summary.get("lastWarningSummary"),
                "cycleHistory": cycle_summary,
            }
        )
        return payload


class LiveCoreDaemon:
    def __init__(self, session: Path, args: argparse.Namespace):
        self.session = session
        self.args = args
        self.runtime_control = runtime_control_from_args(args)
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
        startup_warnings = list(getattr(args, "startup_warnings", []) or [])
        if startup_warnings:
            self.state.warnings.extend(warning for warning in startup_warnings if warning not in self.state.warnings)
            self.runtime_control.warnings = list(dict.fromkeys([*self.runtime_control.warnings, *startup_warnings]))
        self.brain_reset_applied = bool(args.reset_brain_state)
        self.args.reset_brain_state = False
        self.processors: dict[str, live.LiveTargetProcessor] = {}
        self.intent_stabilizer = intent_stabilizer.IntentStabilizer()
        self.latest_intent_result: intent_stabilizer.IntentResult | None = None
        self.active_source: str | None = None
        self.fallback_reason: str | None = None
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.publish_runtime_control_status()

    def effective_brain_task(self) -> str:
        return str(self.runtime_control.activeTask or self.args.brain_task or self.args.profile or "woodcutting")

    def effective_task_policy(self) -> str:
        if self.runtime_control.observeOnly:
            return "observe_only"
        return str(self.runtime_control.taskPolicy or self.args.task_policy or "woodcutting_bank")

    def effective_goal_count(self) -> int | None:
        if self.runtime_control.observeOnly:
            return None
        return self.runtime_control.goalCount

    def effective_overlay_args(self) -> argparse.Namespace:
        values = vars(self.args).copy()
        values["overlay_mode"] = self.runtime_control.overlayMode
        values["overlay_backup_candidates"] = self.runtime_control.overlayBackupCandidates
        return argparse.Namespace(**values)

    def publish_runtime_control_status(self) -> None:
        control_state = self.runtime_control.to_dict()
        fields = {
            "runtimeControl": control_state,
            "runtimeControlLastUpdatedUtc": control_state.get("lastUpdatedUtc"),
            "brainTaskPolicy": self.effective_task_policy(),
            "brainGoalCount": self.effective_goal_count(),
            "observeOnly": bool(self.runtime_control.observeOnly),
            "brainEnabled": bool(self.runtime_control.brainEnabled),
        }
        self.state.source_status.update(fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(fields)

    def runtime_control_payload(self) -> dict:
        self.publish_runtime_control_status()
        return {
            "schema": runtime_control.CONTROL_RESULT_SCHEMA,
            "status": "PASS",
            "state": self.runtime_control.to_dict(),
            "acceptedFields": [],
            "rejectedFields": [],
            "warnings": list(self.runtime_control.warnings),
            "resetBrainState": False,
            "noActionEmitted": True,
        }

    def apply_runtime_control_payload(self, payload: dict) -> tuple[dict, int]:
        previous_task = self.runtime_control.activeTask
        previous_preset = self.runtime_control.activeMissionPreset
        previous_policy = self.runtime_control.taskPolicy
        previous_observe_only = self.runtime_control.observeOnly
        result = runtime_control.apply_control_command(self.runtime_control, payload)
        if result.status != "PASS":
            return result.to_dict(), 400
        if "taskPolicy" in result.acceptedFields and self.runtime_control.taskPolicy != previous_policy:
            task_policy_module.clear_task_policy_cache()
        self.args.task_policy = self.runtime_control.taskPolicy
        self.args.goal_count = self.effective_goal_count()
        self.args.brain_task = self.effective_brain_task()
        if (
            result.resetBrainState
            or self.runtime_control.activeTask != previous_task
            or self.runtime_control.activeMissionPreset != previous_preset
            or self.runtime_control.taskPolicy != previous_policy
            or self.runtime_control.observeOnly != previous_observe_only
        ):
            self.intent_stabilizer = intent_stabilizer.IntentStabilizer()
            self.latest_intent_result = None
            for processor in self.processors.values():
                processor.args.task_policy = self.effective_task_policy()
                processor.processed_ticks.clear()
                processor.classification_cache.clear()
                processor.last_result = None
        if result.resetBrainState:
            task = self.effective_brain_task()
            goal_count = self.effective_goal_count()
            self.state.brain_state = brain_core.default_state(task, goal_count)
            self.state.brain_state["sessionPath"] = str(self.session.resolve())
            self.state.brain_state["brainStateScope"] = brain_state_scope(self.session, task, goal_count)
            self.state.brain_state["goalResourceGroup"] = self.state.brain_state["brainStateScope"].get("resourceGroup")
            self.state.brain_decision = {}
            self.state.path_intent_state.clear(reason="runtime_control_changed")
            self.state.service_target_state.clear(reason="runtime_control_changed")
            self.brain_reset_applied = True
            self.runtime_control.resetBaselineRequested = False
        self.publish_runtime_control_status()
        return result.to_dict(), 200

    def make_processor(self, input_source: str) -> live.LiveTargetProcessor:
        existing = self.processors.get(input_source)
        if existing is not None:
            existing.args.task_policy = self.effective_task_policy()
            return existing
        args = processor_args(self.args, input_source, suppress_output_writes=not self.args.write_debug_live_files)
        args.task_policy = self.effective_task_policy()
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

    def analyze_current_context(self, result: dict) -> LiveAnalysisResult:
        context = self.state.context()
        status = context.get("status") if isinstance(context.get("status"), dict) else {}
        baseline = context.get("baseline") if isinstance(context.get("baseline"), dict) else {}
        player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
        candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
        loaded_service_scene = context.get("loadedServiceScene") if isinstance(context.get("loadedServiceScene"), list) else []
        navigation = context.get("navigation") if isinstance(context.get("navigation"), dict) else {}
        bank_ui = context.get("bankUi") if isinstance(context.get("bankUi"), dict) else {}
        activity = context.get("activity") if isinstance(context.get("activity"), dict) else {}
        events = context.get("events") if isinstance(context.get("events"), list) else []
        latest_tick = status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick")
        analysis = LiveAnalysisResult(
            input_snapshot=LiveInputSnapshot(
                source=status.get("inputSourceActive"),
                session_path=str(self.session),
                latest_tick=latest_tick,
                payload=result,
            ),
            source_status=LiveSourceStatus(
                input_source_active=status.get("inputSourceActive"),
                fallback_reason=status.get("liveCoreFallbackReason"),
                fresh=status.get("fresh"),
                diagnostics=dict(status),
            ),
            player=PlayerContext(
                world_x=player.get("worldX"),
                world_y=player.get("worldY"),
                plane=player.get("plane"),
                scene_x=player.get("sceneX"),
                scene_y=player.get("sceneY"),
                raw=dict(player),
            ),
            targets=target_analyzer.analyze_targets(
                candidates,
                class_id="tree" if self.args.profile == "woodcutting" else None,
                max_candidates=max(1, int(self.args.max_candidates)),
                loaded_service_scene=loaded_service_scene,
            ),
            navigation=navigation_analyzer.analyze_navigation(navigation, candidates),
            activity=activity_analyzer.analyze_activity(activity, events),
            bank_ui=bank_ui_analyzer.analyze_bank_ui_context(
                self.effective_task_policy(),
                bank_ui_payload=bank_ui,
                source_tick=latest_tick if isinstance(latest_tick, int) else None,
            ),
            diagnostics={"contextSource": "memory", "analyzerFileWrites": False},
        )
        self.state.analysis_result = analysis
        target_fields = {
            "profileCandidateCount": analysis.targets.profile_candidate_count,
            "broadCandidateCount": analysis.targets.broad_candidate_count,
            "loadedServiceSceneCount": analysis.targets.loaded_service_scene_count,
            "serviceCandidateInputCount": analysis.targets.service_candidate_input_count,
            "serviceCandidateVisibility": analysis.targets.service_candidate_visibility,
            "serviceCandidateSourceLanes": {
                "profileCandidates": analysis.targets.profile_candidate_count,
                "broadCandidates": analysis.targets.broad_candidate_count,
                "loadedServiceScene": analysis.targets.loaded_service_scene_count,
                "serviceCandidateInputs": analysis.targets.service_candidate_input_count,
                "retainedServiceCandidates": len(self.state.service_target_state.recent_service_candidates),
            },
            "pluginSnapshotServiceHintsUsed": (
                list(getattr(live, "PLUGIN_SNAPSHOT_SERVICE_CLASS_HINTS", ()))
                if live.task_policy_requires_service(self.args)
                else []
            ),
            "serviceCandidateInputsPreview": [
                {
                    key: candidate.get(key)
                    for key in (
                        "objectKey",
                        "targetKey",
                        "targetType",
                        "classId",
                        "targetName",
                        "name",
                        "id",
                        "worldX",
                        "worldY",
                        "plane",
                        "sceneX",
                        "sceneY",
                        "distanceTiles",
                    )
                    if candidate.get(key) is not None
                }
                for candidate in analysis.targets.service_candidate_inputs[:10]
            ],
        }
        navigation_fields = {
            "collisionWindowAvailable": analysis.navigation.collision_window_available,
            "collisionWindowFresh": analysis.navigation.collision_window_fresh,
            "collisionWindowRadius": analysis.navigation.collision_window_radius,
            "collisionWindowCenterWorld": analysis.navigation.collision_window_center_world,
            "collisionWindowPlane": analysis.navigation.collision_window_plane,
            "collisionWindowAgeTicks": analysis.navigation.collision_window_age_ticks,
            "collisionWindowMissingReason": analysis.navigation.collision_window_missing_reason,
        }
        if (
            analysis.targets.service_candidate_input_count == 0
            and status.get("inputSourceActive") == live.PLUGIN_SNAPSHOT_SOURCE
            and (status.get("pluginSnapshotProjectionCapped") or self.args.plugin_snapshot_tier == "hot")
        ):
            target_fields["serviceCandidateVisibility"] = "possibly_capped_or_filtered"
            analysis.targets.service_candidate_visibility = "possibly_capped_or_filtered"
        self.state.source_status.update(target_fields)
        self.state.source_status.update(navigation_fields)
        if isinstance(result.get("status"), dict):
            result["status"].update(target_fields)
            result["status"].update(navigation_fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(target_fields)
            self.state.latest_context["status"].update(navigation_fields)
        self.state.latest_context["profileCandidates"] = list(analysis.targets.profile_candidates)
        self.state.latest_context["broadCandidates"] = list(analysis.targets.broad_candidates)
        self.state.latest_context["loadedServiceScene"] = list(analysis.targets.loaded_service_scene)
        self.state.latest_context["serviceCandidateInputs"] = list(analysis.targets.service_candidate_inputs)
        return analysis

    def stabilize_intent(self, brain_decision: dict | None) -> tuple[intent_stabilizer.IntentResult, dict]:
        started = time.perf_counter()
        context = self.state.context()
        status = context.get("status") if isinstance(context.get("status"), dict) else {}
        candidates = context.get("candidates") if isinstance(context.get("candidates"), list) else []
        decision = brain_decision if isinstance(brain_decision, dict) else {}
        raw_best = brain_core.safe_get(decision, "currentContextSummary.bestTarget", {})
        generic_state = decision.get("genericTaskState") if isinstance(decision.get("genericTaskState"), dict) else {}
        phase = str(generic_state.get("activeIntent") or generic_state.get("phase") or decision.get("phase") or "observe")
        if phase in {"return_to_resource_area", "navigate_to_resource_area"} and isinstance(generic_state.get("activeIntentTarget"), dict):
            raw_best = generic_state["activeIntentTarget"]
            candidates = [raw_best]
        if phase in {"select_target", "target_selected", "continue_current_target", "continue_task", "wait_for_result"}:
            profile_candidates = context.get("profileCandidates") if isinstance(context.get("profileCandidates"), list) else []
            if not profile_candidates and self.state.analysis_result and self.state.analysis_result.targets:
                profile_candidates = list(self.state.analysis_result.targets.profile_candidates)
            candidates = [candidate for candidate in profile_candidates if isinstance(candidate, dict)]
            if raw_best and raw_best not in candidates:
                raw_best = {}
        if phase in {"goal_complete", "inventory_full", "stale_context", "no_context", "observe", "none", "needs_service", "process_inventory", "needs_more_context", "navigate_to_service", "service_available", "bank_operation_pending", "resume_resource_collection", "wait_for_world_view", "close_service_context", "resume_resource_collection_pending", "service_interaction_pending"}:
            raw_best = {}
            candidates = []
        if not isinstance(raw_best, dict) or not raw_best:
            raw_best = candidates[0] if decision and candidates else {}
        task = str(decision.get("task") or self.effective_brain_task() or self.args.profile or "")
        priority = intent_stabilizer.PRIORITY_SELECTED_TARGET
        if phase in {"goal_complete", "inventory_full", "none", "needs_service", "process_inventory", "banking_needed", "navigate_to_service", "service_available", "bank_operation_pending", "resume_resource_collection", "wait_for_world_view", "close_service_context", "resume_resource_collection_pending", "service_interaction_pending"}:
            priority = intent_stabilizer.PRIORITY_TASK_TRANSITION
        if decision.get("interrupt") or str(decision.get("interruptReason") or "").lower() in {"threat", "emergency", "escape"}:
            priority = intent_stabilizer.PRIORITY_EMERGENCY
        intent_context = {
            "activeTask": task,
            "activeIntent": phase,
            "profile": self.args.profile,
            "latestTick": status.get("lastProcessedTick") or status.get("latestTickProcessed") or status.get("latestTick"),
            "rawBestTarget": raw_best,
            "intentPriority": decision.get("intentPriority") or priority,
            "forceSwitch": bool(decision.get("forceSwitch")),
            "interrupt": bool(decision.get("interrupt")),
            "interruptReason": decision.get("interruptReason"),
            "interruptPriority": decision.get("interruptPriority"),
            "interruptTarget": decision.get("interruptTarget"),
            "requireReachability": True,
            "requireAimPoint": False,
        }
        result = self.intent_stabilizer.choose(candidates, intent_context)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        selected_raw = result.selectedTarget.raw if result.selectedTarget else {}
        selected_label = None
        if selected_raw:
            selected_label = selected_raw.get("targetName") or selected_raw.get("name") or selected_raw.get("classId") or result.selectedTargetKey
        fields = {
            "rawBestTarget": result.rawBestTargetKey,
            "stabilizedIntentTarget": result.selectedTargetKey,
            "intentRawBestTarget": result.rawBestTargetKey,
            "intentStableTarget": result.selectedTargetKey,
            "stabilizedIntentTargetLabel": selected_label,
            "intentSelectedTargetKey": result.selectedTargetKey,
            "intentRawBestTargetKey": result.rawBestTargetKey,
            "intentPreviousTargetKey": result.previousTargetKey,
            "intentStableForTicks": result.stableForTicks,
            "intentSwitchReason": result.switchReason,
            "intentHardSwitch": result.hardSwitch,
            "intentSoftSwitch": result.softSwitch,
            "intentCandidateWasRetained": result.candidateWasRetained,
            "intentCandidateWasSwitched": result.candidateWasSwitched,
            "intentSwitchedThisTick": result.candidateWasSwitched,
            "intentInterruptReason": result.interruptReason,
            "intentRetainedDueToGrace": result.retainedDueToGrace,
            "intentCurrentMissingTicks": result.currentMissingTicks,
            "intentCurrentMissingThisTick": result.currentTargetMissingThisTick,
            "intentCurrentInvalidReason": result.currentInvalidReason,
            "intentSwitchAuditTail": result.switchAuditTail[-5:],
            "intentStabilizerMillis": elapsed_ms,
            "intentCandidatesConsidered": result.candidatesConsidered,
        }
        return result, fields

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
        no_file_daily = self.args.daily_mode == DAILY_MODE_SNAPSHOT_NO_FILES
        packet_write_state = compact_packet_file_write_state(self.session)
        daily_fields = {
            "dailyMode": self.args.daily_mode,
            "noFileDaily": bool(no_file_daily),
            "compactPacketFilesRequired": source == live.COMPACT_PACKET_SOURCE,
            "compactPacketFilesWriting": packet_write_state.get("compactPacketFilesWriting"),
            "compactPacketFilesEnabledInManifest": packet_write_state.get("compactPacketFilesEnabledInManifest"),
            "compactPacketFilesAvailable": packet_write_state.get("compactPacketFilesAvailable"),
            "compactPacketFilesRecent": packet_write_state.get("compactPacketFilesRecent"),
            "compactPacketFileCount": packet_write_state.get("compactPacketFileCount"),
            "debugMirrorEnabled": bool(no_file_daily and packet_write_state.get("compactPacketFilesWriting") is True),
        }
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
            result["status"].update(daily_fields)
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
        self.analyze_current_context(result)
        brain_decision = self.evaluate_brain_if_enabled(result)
        if brain_decision:
            self.state.brain_decision = brain_decision
        stable_intent, intent_fields = self.stabilize_intent(brain_decision)
        self.latest_intent_result = stable_intent
        self.state.source_status.update(intent_fields)
        if isinstance(result.get("status"), dict):
            result["status"].update(intent_fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(intent_fields)
        overlay_written, overlay_error, overlay_bytes = self.write_overlay_state_if_enabled(result)
        overlay_fields = {
            "overlayStateWritten": bool(overlay_written),
            "overlayWriteError": overlay_error,
            "overlayMode": self.runtime_control.overlayMode,
            "intentMarkerCount": 0,
            "candidateMarkersSuppressed": 0,
            "overlayStateBytes": overlay_bytes,
        }
        if isinstance(result.get("overlayDebug"), dict):
            summary = result["overlayDebug"].get("summary") if isinstance(result["overlayDebug"].get("summary"), dict) else {}
            overlay_fields["overlayMode"] = summary.get("overlayMode", self.runtime_control.overlayMode)
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
        self.state.record_cycle_history(timestamp=self.state.generated_at_utc)
        self.publish_runtime_control_status()
        self.brain_state_warning = None
        return result

    def write_overlay_state_if_enabled(self, result: dict) -> tuple[bool, str | None, int]:
        if not self.args.write_overlay_state or not self.runtime_control.overlayEnabled:
            return False, None, 0
        generated_at = utc_now()
        overlay_args = self.effective_overlay_args()
        overlay_context = intent_overlay_analyzer.analyze_intent_overlay(
            session=self.session,
            args=overlay_args,
            result=result,
            context=self.state.context(),
            brain_decision=self.state.brain_decision if isinstance(self.state.brain_decision, dict) else {},
            generated_at=generated_at,
            stable_intent=self.latest_intent_result,
        )
        overlay = overlay_context.overlay
        if self.state.analysis_result:
            self.state.analysis_result.intent_overlay = overlay_context
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
            "task": self.effective_brain_task(),
            "needs": list(DEFAULT_CONTEXT_NEEDS),
            "maxCandidates": max(1, int(self.args.max_candidates)),
            "maxEvents": max(0, int(self.args.max_events)),
            "responseMode": "compact",
        }

    def build_context_response(self, request: dict | None = None, *, annotate: bool = True) -> dict:
        payload = request if isinstance(request, dict) else self.default_context_request()
        if payload.get("schema") != context_service.REQUEST_SCHEMA:
            return context_service.error_payload(f"unsupported schema: {payload.get('schema')}", request_id=payload.get("requestId"))
        response = context_service.build_context_response(
            self.state.context(),
            payload,
            default_max_candidates=max(1, int(self.args.max_candidates)),
            max_response_bytes=int(self.args.max_response_bytes),
            compact_include_source_files=False,
            compact_liveness_examples=0,
        )
        return self.annotate_context_response_for_current_phase(response) if annotate else response

    def annotate_context_response_for_current_phase(self, response: dict) -> dict:
        if not isinstance(response, dict):
            return response
        decision = self.state.brain_decision if isinstance(self.state.brain_decision, dict) else {}
        if not decision:
            return response
        annotated = dict(response)
        domains = brain_core.context_domain_summary(
            decision,
            response=response,
            policy=self.effective_task_policy(),
        )
        annotated.update(domains)
        if annotated.get("status") == "FAIL" and not domains.get("missingRequiredContextDomains"):
            annotated["status"] = "WARN"
            warnings = list(annotated.get("warnings") or [])
            note = "context endpoint reported FAIL, but missing context is optional for the current policy phase"
            if note not in warnings:
                warnings.append(note)
            annotated["warnings"] = warnings
        return annotated

    def evaluate_brain_if_enabled(self, result: dict) -> dict | None:
        if not self.runtime_control.brainEnabled:
            return None
        task = self.effective_brain_task()
        goal_count = self.effective_goal_count()
        policy_name = self.effective_task_policy()
        previous_decision = self.state.brain_decision if isinstance(self.state.brain_decision, dict) else {}
        response = self.build_context_response(self.default_context_request(), annotate=False)
        brain_context = brain_context_analyzer.evaluate_brain_context(
            response,
            self.state.brain_state,
            task=task,
            goal_count=goal_count,
            max_events=self.args.max_events,
            reset_applied=self.brain_reset_applied,
            task_policy=policy_name,
        )
        self.state.brain_state = brain_context.updated_state
        if self.state.analysis_result:
            self.state.analysis_result.brain = brain_context
            policy = task_policy_module.resolve_task_policy(policy_name, task=task, profile=self.args.profile)
            context = self.state.context()
            target_context = self.state.analysis_result.targets
            service_candidate_inputs = target_context.service_candidate_inputs if target_context else []
            service_memory_candidates = []
            if target_context:
                for lane_name, lane_candidates in (
                    ("profileCandidates", target_context.profile_candidates),
                    ("broadCandidates", target_context.broad_candidates),
                    ("loadedServiceScene", target_context.loaded_service_scene),
                    ("serviceCandidateInputs", target_context.service_candidate_inputs),
                ):
                    for candidate in lane_candidates:
                        payload = dict(candidate)
                        payload["_serviceSourceLane"] = lane_name
                        service_memory_candidates.append(payload)
            progress = brain_context.decision.get("progress") if isinstance(brain_context.decision.get("progress"), dict) else {}
            source_tick = context.get("status", {}).get("lastProcessedTick") if isinstance(context.get("status"), dict) else None
            inventory_context = InventoryContext(
                inventory=context.get("inventory") if isinstance(context.get("inventory"), dict) else {},
                progress=progress,
                source_tick=source_tick,
            )
            self.state.analysis_result.service = service_analyzer.analyze_service_context(
                policy,
                candidates=service_candidate_inputs,
                memory_candidates=service_memory_candidates,
                profile_candidates=target_context.profile_candidates if target_context else [],
                broad_candidates=target_context.broad_candidates if target_context else [],
                loaded_service_scene=target_context.loaded_service_scene if target_context else [],
                source_tick=source_tick,
                service_target_state=self.state.service_target_state,
                current_plane=self.state.analysis_result.player.plane if self.state.analysis_result.player else None,
            )
            self.state.analysis_result.process_inventory = process_inventory_analyzer.analyze_process_inventory_context(
                policy,
                inventory_context,
                source_tick=source_tick,
            )
            service_context = self.state.analysis_result.service
            process_context = self.state.analysis_result.process_inventory
            if service_context:
                brain_context.decision["serviceContext"] = service_context.to_dict()
            if process_context:
                brain_context.decision["processInventoryContext"] = process_context.to_dict()
            generic_state = brain_context.decision.get("genericTaskState") if isinstance(brain_context.decision.get("genericTaskState"), dict) else {}
            if generic_state.get("activeIntent") == "needs_service" and service_context and service_context.best_service_candidate:
                active_target = dict(service_context.best_service_candidate)
                target_type = active_target.get("targetType") or "sceneObject"
                generic_state["activeIntentTarget"] = active_target
                generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(active_target, str(target_type))
                brain_context.decision["genericTaskState"] = generic_state
            self.state.analysis_result.navigation_intent = navigation_intent_analyzer.analyze_navigation_intent(
                policy,
                player_context=self.state.analysis_result.player,
                target_context=self.state.analysis_result.targets,
                service_context=service_context,
                process_inventory_context=process_context,
                navigation_context=self.state.analysis_result.navigation,
                generic_task_state=generic_state,
                source_tick=source_tick,
            )
            brain_context.decision["navigationIntentContext"] = self.state.analysis_result.navigation_intent.to_dict()
            self.state.analysis_result.pathing = pathing_analyzer.analyze_pathing_context(
                player_context=self.state.analysis_result.player,
                navigation_context=self.state.analysis_result.navigation,
                navigation_intent_context=self.state.analysis_result.navigation_intent,
                service_context=service_context,
                process_inventory_context=process_context,
                target_context=self.state.analysis_result.targets,
                activity_context=self.state.analysis_result.activity,
                generic_task_state=generic_state,
                path_intent_state=self.state.path_intent_state,
                source_tick=source_tick,
                movement_model="osrs_like_predicted",
            )
            if service_context and self.state.analysis_result.pathing:
                pathing = self.state.analysis_result.pathing
                selected_service = service_context.best_service_candidate if isinstance(service_context.best_service_candidate, dict) else {}
                selected_name = selected_service.get("targetName") or selected_service.get("name") or selected_service.get("classId")
                service_context.service_ready = bool(pathing.service_ready)
                service_context.service_ready_reason = pathing.service_ready_reason
                service_context.service_ready_stable_for_ticks = pathing.service_ready_stable_for_ticks
                service_context.selected_service_target_name = str(selected_name) if selected_name else None
                service_context.selected_service_target_tile = pathing.destination_tile
                service_context.distance_to_service_target = pathing.distance_to_destination
                service_context.arrived_at_final_approach = pathing.arrived_at_final_approach
                service_context.arrived_near_destination = pathing.arrived_near_destination
                service_context.distance_to_final_approach = pathing.distance_to_final_approach
                brain_context.decision["serviceContext"] = service_context.to_dict()
                if pathing.service_ready and service_context.best_service_candidate:
                    active_target = dict(service_context.best_service_candidate)
                    target_type = active_target.get("targetType") or "sceneObject"
                    generic_state["phase"] = "service_available"
                    generic_state["activeIntent"] = "service_available"
                    generic_state["activeIntentTarget"] = active_target
                    generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(active_target, str(target_type))
                    generic_state["serviceReady"] = True
                    generic_state["serviceReadyReason"] = pathing.service_ready_reason
                    brain_context.decision["phase"] = "service_available"
                    brain_context.decision["genericTaskState"] = generic_state
            context_bank_ui = context.get("bankUi") if isinstance(context.get("bankUi"), dict) else {}
            self.state.analysis_result.bank_ui = bank_ui_analyzer.analyze_bank_ui_context(
                policy,
                bank_ui_payload=context_bank_ui,
                inventory_context=inventory_context,
                service_context=service_context,
                pathing_context=self.state.analysis_result.pathing,
                source_tick=source_tick,
            )
            bank_ui_context = self.state.analysis_result.bank_ui
            brain_context.decision["bankUiContext"] = bank_ui_context.to_dict()
            self.state.resource_area_memory = resource_return_analyzer.update_resource_area_memory(
                policy,
                self.state.resource_area_memory,
                inventory_context=inventory_context,
                target_context=self.state.analysis_result.targets,
                bank_ui_context=bank_ui_context,
                current_task_state=generic_state,
                player_context=self.state.analysis_result.player,
                source_tick=source_tick,
            )
            self.state.analysis_result.bank_operation = bank_operation_analyzer.analyze_bank_operation_context(
                policy,
                bank_ui_context=bank_ui_context,
                inventory_context=inventory_context,
                resource_definition=brain_core.task_resource_group(task),
                current_task_state=generic_state,
                source_tick=source_tick,
            )
            bank_operation_context = self.state.analysis_result.bank_operation
            brain_context.decision["bankOperationContext"] = bank_operation_context.to_dict()
            previous_post_bank = (
                previous_decision.get("postBankReacquisitionContext")
                if isinstance(previous_decision.get("postBankReacquisitionContext"), dict)
                else {}
            )
            previous_bank_operation = (
                previous_decision.get("bankOperationContext")
                if isinstance(previous_decision.get("bankOperationContext"), dict)
                else {}
            )
            previous_banking_complete = (
                previous_bank_operation.get("bankingComplete") is True
                or (
                    previous_post_bank.get("postBankReacquisitionNeeded") is True
                    and previous_post_bank.get("reason") == "bank_ui_still_open"
                )
            )
            if (
                not bank_operation_context.banking_complete
                and previous_banking_complete
                and bank_ui_context.bank_open is False
            ):
                bank_operation_context = BankOperationContext(
                    status="PASS",
                    source_tick=source_tick,
                    operation_needed=False,
                    operation_type="none",
                    resource_items_held=0,
                    resource_item_slots=[],
                    resource_item_quantity=0,
                    non_resource_items_held=bank_operation_context.non_resource_items_held,
                    inventory_free_slots=bank_operation_context.inventory_free_slots,
                    inventory_full=bank_operation_context.inventory_full,
                    deposit_inventory_available=bank_operation_context.deposit_inventory_available,
                    bank_readable=False,
                    banking_complete=True,
                    completion_reason=str(previous_bank_operation.get("completionReason") or "no_resource_items_held"),
                    reason="banking completion retained while bank UI closed and world view resumes",
                )
                self.state.analysis_result.bank_operation = bank_operation_context
                brain_context.decision["bankOperationContext"] = bank_operation_context.to_dict()
            self.state.analysis_result.return_to_resource = return_to_resource_analyzer.analyze_return_to_resource_context(
                policy,
                bank_operation_context=bank_operation_context,
                inventory_context=inventory_context,
                target_context=self.state.analysis_result.targets,
                current_task_state=generic_state,
                source_tick=source_tick,
            )
            return_context = self.state.analysis_result.return_to_resource
            brain_context.decision["returnToResourceContext"] = return_context.to_dict()
            self.state.analysis_result.post_bank_reacquisition = post_bank_reacquisition_analyzer.analyze_post_bank_reacquisition_context(
                policy,
                bank_operation_context=bank_operation_context,
                bank_ui_context=bank_ui_context,
                target_context=self.state.analysis_result.targets,
                current_task_state=generic_state,
                source_tick=source_tick,
            )
            post_bank_context = self.state.analysis_result.post_bank_reacquisition
            brain_context.decision["postBankReacquisitionContext"] = post_bank_context.to_dict()
            self.state.analysis_result.close_bank = close_bank_analyzer.analyze_close_bank_context(
                policy,
                bank_ui_context=bank_ui_context,
                bank_operation_context=bank_operation_context,
                post_bank_reacquisition_context=post_bank_context,
                current_task_state=generic_state,
                source_tick=source_tick,
            )
            close_bank_context = self.state.analysis_result.close_bank
            brain_context.decision["closeBankContext"] = close_bank_context.to_dict()
            self.state.analysis_result.resource_return = resource_return_analyzer.analyze_resource_return_context(
                policy,
                bank_operation_context=bank_operation_context,
                bank_ui_context=bank_ui_context,
                target_context=self.state.analysis_result.targets,
                resource_memory_state=self.state.resource_area_memory,
                player_context=self.state.analysis_result.player,
                current_task_state=generic_state,
                source_tick=source_tick,
            )
            resource_return_context = self.state.analysis_result.resource_return
            brain_context.decision["resourceAreaMemory"] = self.state.resource_area_memory.to_dict(
                source_tick=source_tick,
                current_plane=self.state.analysis_result.player.plane if self.state.analysis_result.player else None,
            )
            brain_context.decision["resourceReturnContext"] = resource_return_context.to_dict()

            def recompute_navigation_for_generic_state() -> None:
                self.state.analysis_result.navigation_intent = navigation_intent_analyzer.analyze_navigation_intent(
                    policy,
                    player_context=self.state.analysis_result.player,
                    target_context=self.state.analysis_result.targets,
                    service_context=service_context,
                    process_inventory_context=process_context,
                    navigation_context=self.state.analysis_result.navigation,
                    generic_task_state=generic_state,
                    source_tick=source_tick,
                )
                brain_context.decision["navigationIntentContext"] = self.state.analysis_result.navigation_intent.to_dict()
                self.state.analysis_result.pathing = pathing_analyzer.analyze_pathing_context(
                    player_context=self.state.analysis_result.player,
                    navigation_context=self.state.analysis_result.navigation,
                    navigation_intent_context=self.state.analysis_result.navigation_intent,
                    service_context=service_context,
                    process_inventory_context=process_context,
                    target_context=self.state.analysis_result.targets,
                    activity_context=self.state.analysis_result.activity,
                    generic_task_state=generic_state,
                    path_intent_state=self.state.path_intent_state,
                    source_tick=source_tick,
                    movement_model="osrs_like_predicted",
                )

            if service_context and service_context.service_ready:
                service_target = service_context.best_service_candidate if isinstance(service_context.best_service_candidate, dict) else None
                if bank_ui_context.bank_pin_open is True:
                    generic_state["phase"] = "blocked"
                    generic_state["activeIntent"] = "needs_user_resolution"
                    generic_state["activeIntentTarget"] = service_target
                    if service_target:
                        target_type = service_target.get("targetType") or "sceneObject"
                        generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(service_target, str(target_type))
                    blocking = [str(item) for item in generic_state.get("blockingConditions") or [] if item]
                    if "bank_pin_required" not in blocking:
                        blocking.append("bank_pin_required")
                    generic_state["blockingConditions"] = blocking
                    generic_state["bankPinOpen"] = True
                    generic_state["bankUiReason"] = bank_ui_context.reason
                    brain_context.decision["phase"] = "blocked"
                    brain_context.decision["genericTaskState"] = generic_state
                elif bank_ui_context.bank_open is True and bank_ui_context.bank_readable:
                    if bank_operation_context.banking_complete:
                        generic_state["phase"] = "service_complete"
                        generic_state["activeIntent"] = "resume_resource_collection"
                        active_intent_target = None
                    else:
                        generic_state["phase"] = "service_open"
                        generic_state["activeIntent"] = "bank_operation_pending"
                        active_intent_target = service_target
                    generic_state["activeIntentTarget"] = active_intent_target
                    if active_intent_target:
                        target_type = active_intent_target.get("targetType") or "sceneObject"
                        generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(active_intent_target, str(target_type))
                    else:
                        generic_state["selectedTargetKey"] = None
                    generic_state["bankOpen"] = True
                    generic_state["bankReadable"] = True
                    generic_state["bankUiReason"] = bank_ui_context.reason
                    generic_state["bankOperationNeeded"] = bank_operation_context.operation_needed
                    generic_state["bankOperationType"] = bank_operation_context.operation_type
                    generic_state["bankingComplete"] = bank_operation_context.banking_complete
                    generic_state["bankOperationCompletionReason"] = bank_operation_context.completion_reason
                    brain_context.decision["phase"] = generic_state["phase"]
                    brain_context.decision["genericTaskState"] = generic_state
            if post_bank_context.post_bank_reacquisition_needed and post_bank_context.bank_ui_still_open:
                generic_state["phase"] = "waiting_for_world_view"
                generic_state["activeIntent"] = "close_service_context" if close_bank_context.close_bank_needed else "wait_for_world_view"
                generic_state["activeIntentTarget"] = None
                generic_state["selectedTargetKey"] = None
                generic_state["availableTarget"] = None
                generic_state["returnNeeded"] = return_context.return_needed
                generic_state["returnReady"] = False
                generic_state["returnToResourceReason"] = return_context.reason
                generic_state["bankingComplete"] = bank_operation_context.banking_complete
                generic_state["postBankReacquisitionNeeded"] = post_bank_context.post_bank_reacquisition_needed
                generic_state["postBankReacquisitionReason"] = post_bank_context.reason
                generic_state["resourceTargetReacquisitionAllowed"] = post_bank_context.resource_target_reacquisition_allowed
                generic_state["closeBankNeeded"] = close_bank_context.close_bank_needed
                generic_state["closeBankReady"] = close_bank_context.close_bank_ready
                generic_state["closeBankReason"] = close_bank_context.reason
                generic_state["closeButtonAvailable"] = close_bank_context.close_button_available
                blocking = [str(item) for item in generic_state.get("blockingConditions") or [] if item and item != "no_target_observed"]
                generic_state["blockingConditions"] = blocking
                brain_context.decision["phase"] = "waiting_for_world_view"
                brain_context.decision["genericTaskState"] = generic_state
                recompute_navigation_for_generic_state()
            elif return_context.return_needed:
                resource_target = return_context.best_resource_target if isinstance(return_context.best_resource_target, dict) else None
                resource_return_target = resource_return_context.destination_target if isinstance(resource_return_context.destination_target, dict) else None
                generic_state["returnNeeded"] = return_context.return_needed
                generic_state["returnReady"] = return_context.return_ready
                generic_state["returnToResourceReason"] = return_context.reason
                generic_state["resourceTargetAvailable"] = return_context.resource_target_available
                generic_state["bankingComplete"] = return_context.banking_complete
                generic_state["resourceReturnDestinationNeeded"] = resource_return_context.return_destination_needed
                generic_state["resourceReturnDestinationAvailable"] = resource_return_context.return_destination_available
                generic_state["resourceReturnReason"] = resource_return_context.reason
                if return_context.return_ready and resource_target:
                    target_type = resource_target.get("targetType") or "sceneObject"
                    generic_state["phase"] = "target_selected"
                    generic_state["activeIntent"] = "select_target"
                    generic_state["activeIntentTarget"] = resource_target
                    generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(resource_target, str(target_type))
                    generic_state["availableTarget"] = resource_target
                    generic_state.pop("blockingConditions", None)
                    brain_context.decision["phase"] = "target_selected"
                elif resource_return_context.return_destination_available and resource_return_target:
                    generic_state["phase"] = "return_to_resource"
                    generic_state["activeIntent"] = "return_to_resource_area"
                    generic_state["activeIntentTarget"] = resource_return_target
                    generic_state["selectedTargetKey"] = intent_stabilizer.build_target_key(resource_return_target, str(resource_return_target.get("targetType") or "tile"))
                    generic_state["availableTarget"] = None
                    generic_state["pathingNeeded"] = True
                    blocking = [str(item) for item in generic_state.get("blockingConditions") or [] if item and item != "no_target_observed"]
                    generic_state["blockingConditions"] = blocking
                    brain_context.decision["phase"] = "return_to_resource"
                else:
                    generic_state["phase"] = "needs_more_context"
                    generic_state["activeIntent"] = "select_target"
                    generic_state["activeIntentTarget"] = None
                    generic_state["selectedTargetKey"] = None
                    generic_state["availableTarget"] = None
                    blocking = [str(item) for item in generic_state.get("blockingConditions") or [] if item]
                    if "no_target_observed" not in blocking:
                        blocking.append("no_target_observed")
                    generic_state["blockingConditions"] = blocking
                    brain_context.decision["phase"] = "no_target_observed"
                brain_context.decision["genericTaskState"] = generic_state
                recompute_navigation_for_generic_state()
            pathing_payload = self.state.analysis_result.pathing.to_dict()
            pathing_payload["overlayPredictedPathLimit"] = intent_overlay_analyzer.predicted_path_limit(self.args, self.args.overlay_mode)
            brain_context.decision["pathingContext"] = pathing_payload
            brain_context.decision.update(
                brain_core.context_domain_summary(
                    brain_context.decision,
                    response=response,
                    policy=policy,
                )
            )
        fields = brain_context.status_fields
        for key in (
            "requiredContextDomains",
            "missingRequiredContextDomains",
            "optionalMissingContextDomains",
            "targetCandidatesRequired",
        ):
            if key in brain_context.decision:
                fields[key] = brain_context.decision.get(key)
        fields["brainTaskPolicy"] = policy_name
        fields["brainGoalCount"] = goal_count
        fields["observeOnly"] = bool(self.runtime_control.observeOnly)
        fields["brainEnabled"] = bool(self.runtime_control.brainEnabled)
        if self.state.analysis_result and self.state.analysis_result.service:
            fields["serviceNeeded"] = self.state.analysis_result.service.service_required
            fields["serviceTypeNeeded"] = self.state.analysis_result.service.service_type_needed
            fields["serviceCandidateCount"] = self.state.analysis_result.service.candidate_count
            fields["serviceTargetRetained"] = self.state.analysis_result.service.service_target_retained
            fields["retainedServiceTargetName"] = self.state.analysis_result.service.retained_service_target_name
            fields["retainedServiceMissingTicks"] = self.state.analysis_result.service.retained_service_missing_ticks
            fields["retainedServiceCandidateCount"] = self.state.analysis_result.service.retained_service_candidate_count
            fields["retainedBestServiceCandidate"] = self.state.analysis_result.service.retained_best_service_candidate
            fields["retainedServiceAgeTicks"] = self.state.analysis_result.service.retained_service_age_ticks
            fields["preferredServiceTypesSeen"] = self.state.analysis_result.service.preferred_service_types_seen
            fields["preferredServiceTypesRecentlySeen"] = self.state.analysis_result.service.preferred_service_types_recently_seen
            fields["missingPreferredReason"] = self.state.analysis_result.service.missing_preferred_reason
            fields["selectedServiceTargetSource"] = self.state.analysis_result.service.selected_service_target_source
            fields["primaryServiceVisible"] = self.state.analysis_result.service.primary_service_visible
            fields["primaryServiceRetained"] = self.state.analysis_result.service.primary_service_retained
            fields["depositFallbackAllowed"] = self.state.analysis_result.service.deposit_fallback_allowed
            fields["selectedServiceGroup"] = self.state.analysis_result.service.selected_service_group
            fields["logicError"] = self.state.analysis_result.service.logic_error
            fields["visiblePrimaryServiceTargetCount"] = self.state.analysis_result.service.visible_primary_service_target_count
            fields["visibleDepositServiceTargetCount"] = self.state.analysis_result.service.visible_deposit_service_target_count
            fields["sourceStageCounts"] = self.state.analysis_result.service.source_stage_counts
            fields["memoryLifecycle"] = self.state.analysis_result.service.memory_lifecycle
            fields["serviceSwitchReason"] = self.state.analysis_result.service.service_switch_reason
            fields["serviceCandidateDroppedReason"] = self.state.analysis_result.service.service_candidate_dropped_reason
            fields["serviceReady"] = self.state.analysis_result.service.service_ready
            fields["serviceReadyReason"] = self.state.analysis_result.service.service_ready_reason
            fields["serviceReadyStableForTicks"] = self.state.analysis_result.service.service_ready_stable_for_ticks
            fields["selectedServiceTargetName"] = self.state.analysis_result.service.selected_service_target_name
            fields["selectedServiceTargetTile"] = self.state.analysis_result.service.selected_service_target_tile
            fields["distanceToServiceTarget"] = self.state.analysis_result.service.distance_to_service_target
            fields["serviceArrivedAtFinalApproach"] = self.state.analysis_result.service.arrived_at_final_approach
            fields["serviceArrivedNearDestination"] = self.state.analysis_result.service.arrived_near_destination
            fields["serviceDistanceToFinalApproach"] = self.state.analysis_result.service.distance_to_final_approach
            fields["serviceCandidateSourceLanes"] = {
                "profileCandidates": self.state.source_status.get("profileCandidateCount"),
                "broadCandidates": self.state.source_status.get("broadCandidateCount"),
                "loadedServiceScene": self.state.source_status.get("loadedServiceSceneCount"),
                "serviceCandidateInputs": self.state.source_status.get("serviceCandidateInputCount"),
                "retainedServiceCandidates": self.state.analysis_result.service.retained_service_candidate_count,
            }
            fields["pluginSnapshotServiceHintsUsed"] = (
                list(getattr(live, "PLUGIN_SNAPSHOT_SERVICE_CLASS_HINTS", ()))
                if live.task_policy_requires_service(self.args)
                else []
            )
        if self.state.analysis_result and self.state.analysis_result.process_inventory:
            fields["processInventoryNeeded"] = self.state.analysis_result.process_inventory.process_required
            fields["processTypeNeeded"] = self.state.analysis_result.process_inventory.process_type_needed
        if self.state.analysis_result and self.state.analysis_result.navigation_intent:
            navigation_intent = self.state.analysis_result.navigation_intent
            fields["navigationIntentNeeded"] = navigation_intent.navigation_needed
            fields["navigationIntentReason"] = navigation_intent.navigation_reason
            fields["navigationIntentTargetKind"] = navigation_intent.target_kind
            fields["navigationIntentReachability"] = navigation_intent.direct_reachability
            fields["navigationIntentDistanceTiles"] = navigation_intent.distance_tiles
            fields["navigationIntentCollisionWindowAvailable"] = navigation_intent.collision_window_available
        if self.state.analysis_result and self.state.analysis_result.pathing:
            pathing = self.state.analysis_result.pathing
            fields["pathingNeeded"] = pathing.pathing_needed
            fields["pathingReason"] = pathing.reason
            fields["pathingLocalReachability"] = pathing.local_reachability
            fields["pathingPathLengthTiles"] = pathing.path_length_tiles
            fields["pathingDestinationTile"] = pathing.destination_tile
            fields["pathingFinalApproachTile"] = pathing.final_approach_tile
            fields["pathingFinalApproachTileSource"] = pathing.final_approach_tile_source
            fields["pathingFinalApproachCandidateCount"] = pathing.final_approach_candidate_count
            fields["pathingRejectedApproachTileReasons"] = pathing.rejected_approach_tile_reasons
            fields["pathingFinalApproachTileUsed"] = pathing.final_approach_tile_used
            fields["pathingPathTargetTile"] = pathing.path_target_tile
            fields["pathingPathTargetTileSource"] = pathing.path_target_tile_source
            fields["pathingNextWaypointTile"] = pathing.next_waypoint_tile
            fields["pathingCollisionWindowAvailable"] = pathing.collision_window_available
            fields["pathingCollisionWindowFresh"] = pathing.collision_window_fresh
            fields["pathingCollisionWindowRadius"] = pathing.collision_window_radius
            fields["pathingCollisionWindowCenterWorld"] = pathing.collision_window_center_world
            fields["pathingCollisionWindowPlane"] = pathing.collision_window_plane
            fields["pathingCollisionWindowAgeTicks"] = pathing.collision_window_age_ticks
            fields["pathingDestinationInsideCollisionWindow"] = pathing.destination_inside_collision_window
            fields["pathingDestinationPlaneMatches"] = pathing.destination_plane_matches
            fields["pathingCollisionWindowMissingReason"] = pathing.collision_window_missing_reason
            fields["pathingMovementModel"] = pathing.predicted_movement_model
            fields["pathingPathCapTiles"] = pathing.path_cap_tiles
            fields["pathingExactDestinationReached"] = pathing.exact_destination_reached
            fields["pathingFinalApproachSubstituted"] = pathing.final_approach_substituted
            fields["pathingPredictedPathCount"] = pathing.predicted_path_count
            fields["pathingPredictedPathDisplayedCount"] = pathing.predicted_path_displayed_count
            fields["pathingPredictedPathAvailableCount"] = pathing.predicted_path_available_count
            fields["pathingPathWasCapped"] = pathing.path_was_capped
            fields["pathingPathDisplayWasCapped"] = pathing.path_display_was_capped
            fields["overlayPredictedPathLimit"] = intent_overlay_analyzer.predicted_path_limit(self.args, self.args.overlay_mode)
            fields["pathingPathSegmentsValid"] = pathing.path_segments_valid
            fields["pathingInvalidPathSegmentCount"] = pathing.invalid_path_segment_count
            fields["pathingFirstInvalidPathSegment"] = pathing.first_invalid_path_segment
            fields["pathingSelectedApproachReason"] = pathing.selected_approach_reason
            fields["pathingApproachQuality"] = pathing.approach_quality
            fields["pathingApproachCandidatesTested"] = pathing.approach_candidates_tested
            fields["pathingApproachCandidatesRejectedByBlockedSide"] = pathing.approach_candidates_rejected_by_blocked_side
            fields["pathingApproachCandidatesRejectedByNoLineOfSight"] = pathing.approach_candidates_rejected_by_no_line_of_sight
            fields["pathingSideAccessValid"] = pathing.side_access_valid
            fields["pathingLineOfSightToTarget"] = pathing.line_of_sight_to_target
            fields["pathingDiagonalStepCount"] = pathing.diagonal_step_count
            fields["pathingCardinalStepCount"] = pathing.cardinal_step_count
            fields["pathingMillis"] = pathing.pathing_millis
            fields["pathNodesExpanded"] = pathing.path_nodes_expanded
            fields["pathingBudgetExceeded"] = pathing.pathing_budget_exceeded
            fields["pathIntentKey"] = pathing.path_intent_key
            fields["pathDestinationTargetKey"] = pathing.destination_target_key
            fields["pathIntentRetained"] = pathing.path_intent_retained
            fields["pathStableForTicks"] = pathing.path_stable_for_ticks
            fields["pathMovementState"] = pathing.movement_state
            fields["pathRetentionReason"] = pathing.retention_reason
            fields["pathSwitchReason"] = pathing.switch_reason
            fields["arrivedAtFinalApproach"] = pathing.arrived_at_final_approach
            fields["arrivedNearDestination"] = pathing.arrived_near_destination
            fields["distanceToFinalApproach"] = pathing.distance_to_final_approach
            fields["distanceToDestination"] = pathing.distance_to_destination
            fields["distanceToPathTarget"] = pathing.distance_to_path_target
            fields["arrivedStableForTicks"] = pathing.arrived_stable_for_ticks
            fields["arrivalReason"] = pathing.arrival_reason
            fields["pathCompleted"] = pathing.path_completed
            fields["pathCompletionReason"] = pathing.path_completion_reason
            fields["retainedPathAfterArrival"] = pathing.retained_path_after_arrival
        if self.state.analysis_result and self.state.analysis_result.bank_ui:
            bank_ui = self.state.analysis_result.bank_ui
            bank_summary = bank_ui.bank_summary if isinstance(bank_ui.bank_summary, dict) else {}
            inventory_summary = bank_ui.inventory_summary if isinstance(bank_ui.inventory_summary, dict) else {}
            fields["bankOpen"] = bank_ui.bank_open
            fields["bankReadable"] = bank_ui.bank_readable
            fields["bankPinOpen"] = bank_ui.bank_pin_open
            fields["bankUiStatus"] = bank_ui.status
            fields["bankUiReason"] = bank_ui.reason
            fields["bankUiMissingCapabilities"] = list(bank_ui.missing_capabilities)
            fields["bankUiWarnings"] = list(bank_ui.warnings)
            fields["bankTopLevelInterfaceId"] = bank_ui.top_level_interface_id
            fields["bankRootVisible"] = bank_ui.bank_root_visible
            fields["bankContainerVisible"] = bank_ui.bank_container_visible
            fields["bankInventoryVisible"] = bank_ui.bank_inventory_visible
            fields["depositInventoryButtonVisible"] = bank_ui.deposit_inventory_button_visible
            fields["closeButtonVisible"] = bank_ui.bank_close_button_visible
            fields["bankCloseButtonVisible"] = bank_ui.bank_close_button_visible
            fields["bankOccupiedSlots"] = bank_summary.get("occupiedSlots")
            fields["bankUniqueItemCount"] = bank_summary.get("uniqueItemCount")
            fields["inventoryFreeSlots"] = inventory_summary.get("freeSlots")
            fields["inventoryOccupiedSlots"] = inventory_summary.get("occupiedSlots")
            fields["inventoryMatchingResourceCount"] = inventory_summary.get("matchingResourceCount")
        if self.state.analysis_result and self.state.analysis_result.bank_operation:
            operation = self.state.analysis_result.bank_operation
            fields["bankOperationNeeded"] = operation.operation_needed
            fields["bankOperationType"] = operation.operation_type
            fields["bankResourceItemsHeld"] = operation.resource_items_held
            fields["bankResourceItemSlots"] = list(operation.resource_item_slots)
            fields["bankResourceItemQuantity"] = operation.resource_item_quantity
            fields["bankNonResourceItemsHeld"] = operation.non_resource_items_held
            fields["bankOperationInventoryFreeSlots"] = operation.inventory_free_slots
            fields["bankOperationInventoryFull"] = operation.inventory_full
            fields["bankDepositInventoryAvailable"] = operation.deposit_inventory_available
            fields["bankingComplete"] = operation.banking_complete
            fields["bankOperationCompletionReason"] = operation.completion_reason
            fields["bankOperationStatus"] = operation.status
            fields["bankOperationReason"] = operation.reason
            fields["bankOperationMissingCapabilities"] = list(operation.missing_capabilities)
            fields["bankOperationWarnings"] = list(operation.warnings)
        if self.state.analysis_result and self.state.analysis_result.return_to_resource:
            return_context = self.state.analysis_result.return_to_resource
            best_target = return_context.best_resource_target if isinstance(return_context.best_resource_target, dict) else None
            fields["returnToResourceNeeded"] = return_context.return_needed
            fields["returnToResourceReady"] = return_context.return_ready
            fields["returnToResourceStatus"] = return_context.status
            fields["returnToResourceReason"] = return_context.reason
            fields["returnResourceTargetAvailable"] = return_context.resource_target_available
            fields["returnBestResourceTarget"] = best_target
            fields["returnResourcePathingNeeded"] = return_context.resource_pathing_needed
            fields["returnServiceComplete"] = return_context.service_complete
            fields["returnInventoryFreeSlots"] = return_context.inventory_free_slots
            fields["returnInventoryFull"] = return_context.inventory_full
            fields["returnToResourceMissingCapabilities"] = list(return_context.missing_capabilities)
            fields["returnToResourceWarnings"] = list(return_context.warnings)
        if self.state.analysis_result and self.state.analysis_result.resource_return:
            resource_return = self.state.analysis_result.resource_return
            fields["resourceMemoryValid"] = resource_return.resource_memory_valid
            fields["resourceMemoryAgeTicks"] = resource_return.resource_memory_age_ticks
            fields["resourceMemoryInvalidReason"] = resource_return.resource_memory_invalid_reason
            fields["resourceReturnDestinationNeeded"] = resource_return.return_destination_needed
            fields["resourceReturnDestinationAvailable"] = resource_return.return_destination_available
            fields["resourceReturnDestinationTile"] = resource_return.return_destination_tile
            fields["resourceReturnDestinationSource"] = resource_return.return_destination_source
            fields["resourceReturnReason"] = resource_return.reason
            fields["resourceReturnStatus"] = resource_return.status
            fields["resourceReturnTargetCurrentlyVisible"] = resource_return.resource_target_currently_visible
            fields["resourceReturnMissingCapabilities"] = list(resource_return.missing_capabilities)
            fields["resourceReturnWarnings"] = list(resource_return.warnings)
        if self.state.analysis_result and self.state.analysis_result.post_bank_reacquisition:
            post_bank = self.state.analysis_result.post_bank_reacquisition
            fields["postBankReacquisitionNeeded"] = post_bank.post_bank_reacquisition_needed
            fields["postBankUiStillOpen"] = post_bank.bank_ui_still_open
            fields["postBankWorldViewReady"] = post_bank.world_view_ready
            fields["postBankResourceTargetReacquisitionAllowed"] = post_bank.resource_target_reacquisition_allowed
            fields["postBankResourceTargetAvailable"] = post_bank.resource_target_available
            fields["postBankReacquisitionStatus"] = post_bank.status
            fields["postBankReacquisitionReason"] = post_bank.reason
            fields["postBankReacquisitionMissingCapabilities"] = list(post_bank.missing_capabilities)
            fields["postBankReacquisitionWarnings"] = list(post_bank.warnings)
        if self.state.analysis_result and self.state.analysis_result.close_bank:
            close_bank = self.state.analysis_result.close_bank
            fields["closeBankNeeded"] = close_bank.close_bank_needed
            fields["closeBankReady"] = close_bank.close_bank_ready
            fields["closeBankStatus"] = close_bank.status
            fields["closeBankReason"] = close_bank.reason
            fields["closeBankOpen"] = close_bank.bank_open
            fields["closeBankingComplete"] = close_bank.banking_complete
            fields["closeBankCloseButtonVisible"] = close_bank.close_button_visible
            fields["closeBankCloseButtonAvailable"] = close_bank.close_button_available
            fields["closeBankKeyboardClosePossible"] = close_bank.keyboard_close_possible
            fields["closeBankMissingCapabilities"] = list(close_bank.missing_capabilities)
            fields["closeBankWarnings"] = list(close_bank.warnings)
        self.state.source_status.update(fields)
        if isinstance(self.state.latest_context.get("status"), dict):
            self.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.args.brain_state_file, self.state.brain_state, self.session, self.args)
        self.publish_runtime_control_status()
        return brain_context.decision

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
            print(f"  mode={self.args.daily_mode}")
            print(f"  source={self.args.input_source}")
            print(f"  writes={'on' if self.args.write_debug_live_files else 'off'}")
            print(
                "  overlay="
                f"{'on' if (self.args.write_overlay_state and self.runtime_control.overlayEnabled) else 'off'} "
                f"({self.runtime_control.overlayMode})"
            )
            print(f"  brain={'on' if self.runtime_control.brainEnabled else 'off'}")
            print(f"  task policy={self.effective_task_policy()}")
            print(f"  debug files={'on' if self.args.write_debug_live_files else 'off'}")
            print(
                "  compact packet files required="
                f"{'no' if self.args.input_source == live.PLUGIN_SNAPSHOT_SOURCE else 'yes'}"
            )
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
            "candidateRetained={candidate_retained} intent={intent} stableFor={stable_for} switch={switch} intentMs={intent_ms}".format(
                tick=status.get("lastProcessedTick") or status.get("latestTickProcessed"),
                source=status.get("inputSourceActive"),
                candidates=status.get("candidateCount"),
                best=context_best_label(self.build_context_response(self.default_context_request())),
                active=status.get("liveCoreActiveMillis") or status.get("processingDurationMillis"),
                context_ms=round(float(timing.get("contextIndexMillis") or 0.0), 3),
                brain=(self.state.brain_decision or {}).get("phase") or "off",
                progress=brain_progress_label(self.state.brain_decision or {}),
                overlay="on" if (self.args.write_overlay_state and self.runtime_control.overlayEnabled) else "off",
                writes="on" if self.args.write_debug_live_files else "off",
                budget="exceeded" if status.get("budgetExceeded") else "ok",
                bottleneck=status.get("activeBottleneck") or "unknown",
                progress_retained="yes" if status.get("progressRetainedPreviousThisPoll") else "no",
                candidate_retained="yes" if status.get("candidateRetainedPrevious") else "no",
                intent=status.get("stabilizedIntentTargetLabel") or status.get("stabilizedIntentTarget") or "none",
                stable_for=status.get("intentStableForTicks"),
                switch=status.get("intentSwitchReason") or "unknown",
                intent_ms=status.get("intentStabilizerMillis"),
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
                "GET": ["/health", "/schema", "/status", "/summary", "/brain", "/capabilities", "/watches", "/control"],
                "POST": ["/context", "/context/batch", "/brain", "/watch-request", "/control"],
            }
            self.send_json(payload)
        elif path == "/status":
            self.send_json(self.daemon.state.status())
        elif path == "/control":
            self.send_json(self.daemon.runtime_control_payload())
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
        if self.path not in {"/context", "/context/batch", "/brain", "/watch-request", "/control"}:
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
        if self.path == "/control":
            result, status_code = self.daemon.apply_runtime_control_payload(payload if isinstance(payload, dict) else {})
            self.send_json(result, status_code=status_code)
        elif self.path == "/context/batch":
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
        default_task = self.daemon.effective_brain_task()
        task = (params.get("task") or [default_task])[0] or default_task
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
        default_task = self.daemon.effective_brain_task()
        task = task or ((params.get("task") or [default_task])[0] if params else default_task)
        goal_count = payload.get("goalCount") if isinstance(payload, dict) else None
        if goal_count is None:
            goal_count = self.daemon.effective_goal_count()
        if str(task or default_task) == str(default_task) and goal_count == self.daemon.effective_goal_count():
            enriched = self.daemon.evaluate_brain_if_enabled(self.daemon.state.latest_result if isinstance(self.daemon.state.latest_result, dict) else {})
            if enriched:
                self.daemon.state.brain_decision = enriched
                self.send_json(enriched)
                return
        request = dict(self.daemon.default_context_request())
        request["task"] = task
        response = self.daemon.build_context_response(request)
        brain_context = brain_context_analyzer.evaluate_brain_context(
            response,
            self.daemon.state.brain_state,
            task=str(task or "woodcutting"),
            goal_count=goal_count,
            max_events=self.daemon.args.max_events,
            reset_applied=self.daemon.brain_reset_applied,
            task_policy=self.daemon.effective_task_policy(),
        )
        self.daemon.state.brain_state = brain_context.updated_state
        if self.daemon.state.analysis_result:
            self.daemon.state.analysis_result.brain = brain_context
        fields = brain_context.status_fields
        self.daemon.state.source_status.update(fields)
        if isinstance(self.daemon.state.latest_context.get("status"), dict):
            self.daemon.state.latest_context["status"].update(fields)
        persist_daemon_brain_state(self.daemon.args.brain_state_file, self.daemon.state.brain_state, self.daemon.session, self.daemon.args)
        self.daemon.state.brain_decision = brain_context.decision
        self.send_json(brain_context.decision)

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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    daily_mode_explicit = any(item == "--daily-mode" or item.startswith("--daily-mode=") for item in raw_argv)
    task_policy_explicit = any(item == "--task-policy" or item.startswith("--task-policy=") for item in raw_argv)
    goal_count_explicit = any(item == "--goal-count" or item.startswith("--goal-count=") for item in raw_argv)
    brain_task_explicit = any(item == "--brain-task" or item.startswith("--brain-task=") for item in raw_argv)
    overlay_mode_explicit = any(item == "--overlay-mode" or item.startswith("--overlay-mode=") for item in raw_argv)
    overlay_backup_explicit = any(item == "--overlay-backup-candidates" or item.startswith("--overlay-backup-candidates=") for item in raw_argv)
    parser = argparse.ArgumentParser(
        description="Read-only in-memory daily live daemon. It serves context from telemetry observations only and emits no actions."
    )
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--session", help="Telemetry session directory.")
    session.add_argument("--latest-session", action="store_true", help="Use newest telemetry session.")
    parser.add_argument("--sessions-dir", help="Override sessions root.")
    parser.add_argument("--profile", default="woodcutting", choices=["woodcutting", "broad_qa", "navigation_qa", "npc_qa", "ground_item_qa", "ui_qa"])
    parser.add_argument("--daily-mode", choices=sorted(DAILY_MODES), default=DAILY_MODE_COMPACT_PACKETS)
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
    parser.add_argument("--overlay-predicted-path-limit", type=int, default=None)
    parser.add_argument("--write-debug-live-files", dest="write_debug_live_files", action="store_true")
    parser.add_argument("--no-debug-live-files", dest="write_debug_live_files", action="store_false")
    parser.set_defaults(write_debug_live_files=False)
    parser.add_argument("--human-dashboard", action="store_true")
    parser.add_argument("--brain-task", default="woodcutting")
    parser.add_argument("--preset", choices=mission_presets.preset_names(), help="Apply a named read-only startup mission preset.")
    parser.add_argument("--task-policy", choices=task_policy_module.policy_names(), default="woodcutting_bank")
    parser.add_argument("--goal-count", type=int)
    parser.add_argument("--brain-state-file", help="Optional brain_state.v1 file. If omitted, daily daemon progress stays in process memory only.")
    parser.add_argument("--reset-brain-state", action="store_true", help="Reset in-memory or file-backed brain progress before observing.")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000)
    parser.add_argument("--target-update-ms", type=float, default=100.0)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    args.daily_mode_explicit = daily_mode_explicit
    args.startup_warnings = []
    args.observe_only = False
    args.brain_enabled = None
    if args.preset:
        preset_fields = mission_presets.runtime_control_fields_for_preset(args.preset)
        if not brain_task_explicit:
            args.brain_task = str(preset_fields.get("activeTask") or args.brain_task)
        if task_policy_explicit:
            args.startup_warnings.append("task policy overridden by explicit --task-policy")
        else:
            args.task_policy = str(preset_fields.get("taskPolicy") or args.task_policy)
        if not goal_count_explicit:
            args.goal_count = preset_fields.get("goalCount")
        args.observe_only = bool(preset_fields.get("observeOnly"))
        args.brain_enabled = bool(preset_fields.get("brainEnabled"))
        if not overlay_mode_explicit:
            args.overlay_mode = str(preset_fields.get("overlayMode") or args.overlay_mode)
        if not overlay_backup_explicit:
            args.overlay_backup_candidates = int(preset_fields.get("overlayBackupCandidates") or args.overlay_backup_candidates)
    if daily_mode_explicit:
        if args.daily_mode == DAILY_MODE_SNAPSHOT_NO_FILES:
            args.input_source = live.PLUGIN_SNAPSHOT_SOURCE
            args.plugin_snapshot_tier = "hot"
        elif args.daily_mode == DAILY_MODE_COMPACT_PACKETS:
            args.input_source = live.COMPACT_PACKET_SOURCE
    return args


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

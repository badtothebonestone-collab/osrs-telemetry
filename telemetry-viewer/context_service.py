import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import live_context_query as query
import external_knowledge
import knowledge_fabric
import telemetry_capabilities
import telemetry_schema
import route_monitor
import route_template
import banking_lifecycle
import task_script_api
import traversal_lifecycle
import woodcutting_lifecycle
import human_click_profile
import interruption_lifecycle
import combat_damage_summary
import woodcutting_loop_lifecycle
from live_context_format import format_context_human
from telemetry_paths import find_newest_session, get_sessions_dir


REQUEST_SCHEMA = "context_request.v1"
RESPONSE_SCHEMA = "context_response.v1"
HEALTH_SCHEMA = "context_health.v1"
STATUS_SCHEMA = "context_status.v1"
SCHEMA_SCHEMA = "context_schema.v1"
CAPABILITY_REGISTRY_SCHEMA = "capability_registry.v1"
WATCH_LIBRARY_SCHEMA = "watch_library.v1"
WATCH_REQUEST_SCHEMA = "context_watch_request.v1"
WATCH_RESPONSE_SCHEMA = "context_watch_response.v1"
CAPABILITY_REGISTRY_PATH = Path(__file__).resolve().with_name("capability_registry.json")
WATCH_LIBRARY_PATH = Path(__file__).resolve().with_name("watch_library.json")
PIPELINE_MANIFEST_PATH = Path(__file__).resolve().with_name("pipeline_manifest.json")
CONFIG_KEYS_PATH = Path(__file__).resolve().with_name("config_keys.json")
PIPELINE_HEALTH_SCHEMA = "pipeline_health.v1"
DEFAULT_DAEMON_URL = "http://127.0.0.1:8890"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8893/snapshot"
WATCH_REQUEST_DIR = "live_requests"
WATCH_REQUEST_FILE = "watch_requests.json"
WATCH_REQUEST_LIMIT = 32
WATCH_MAX_TTL_TICKS = 5000
WATCH_DEFAULT_TTL_TICKS = 500
WATCH_MAX_VALUES_PER_TICK = 32
WATCH_SUPPORTED_TYPES = {"varbit", "varp", "varclient_int", "varclient_str", "item_container", "widget_summary", "builtin"}
WATCH_SAMPLE_MODES = {"on_change", "every_tick", "interval"}
WATCH_TYPE_LIMITS = {
    "varbit": 16,
    "varp": 16,
    "varclient_int": 8,
    "varclient_str": 8,
    "item_container": 4,
    "widget_summary": 4,
    "builtin": 16,
}
SUPPORTED_TASKS = ["woodcutting"]
SUPPORTED_RESPONSE_MODES = ["compact", "normal", "full"]
SUPPORTED_NEEDS = [
    "baseline",
    "inventory",
    "equipment",
    "bank",
    "bank_ui",
    "banking",
    "banking_lifecycle",
    "bank_state",
    "inventory_delta",
    "deposit_result",
    "combat_state",
    "combat",
    "recent_hitsplats",
    "recent_stat_changes",
    "recent_chat_messages",
    "interruption_lifecycle",
    "current_interruption",
    "task_interruption_status",
    "combat_damage_summary",
    "damage_taken",
    "damage_dealt",
    "primary_opponent",
    "recent_combat_summary",
    "widgets",
    "hover",
    "menu",
    "nearby_objects",
    "route_objects",
    "nearby_npcs",
    "activity",
    "woodcutting_lifecycle",
    "woodcutting_loop",
    "woodcutting_loop_lifecycle",
    "task_loop",
    "next_expected_phase",
    "lifecycle:woodcutting",
    "task_summary:woodcutting",
    "traversal_lifecycle",
    "route_summary",
    "latest_recording_traversal",
    "route_template",
    "route_template_comparison",
    "latest_route_comparison",
    "route_monitor",
    "route_readiness",
    "route_progress",
    "route_next_segment",
    "route_history",
    "route_session_state",
    "route_progress_timeline",
    "route_completed_segments",
    "route_remaining_segments",
    "human_click_profile",
    "task_click_profile",
    "click_landing_profile",
    "camera_action_profile",
    "click_plan",
    "human_click_plan",
    "click_planning_context",
    "liveness",
    "diagnostics",
    "navigation_readiness",
    "frame",
    "candidates",
    "events",
    "watches",
    "capabilities",
    "watch:<alias>",
    "capability:<id>",
    "aim_point",
    "task_summary",
    "world_model",
    "world_model_summary",
    "resource_object_census",
    "service_object_census",
    "route_object_census",
    "projection_audit",
    "pathing_frontier",
    "knowledge_fabric",
    "knowledge_fabric_status",
    "knowledge_current_debug_context",
    "knowledge_current_blocker",
    "knowledge_resource_candidates",
    "knowledge_service_candidates",
    "knowledge_route_objects",
    "knowledge_path_frontier",
    "knowledge_navigation_decision_trace",
    "knowledge_view_quality",
    "knowledge_session_memory",
    "knowledge_debug_evidence",
    "knowledge_data_quality_report",
    "knowledge_data_source_inventory",
    "knowledge_query_coverage_matrix",
    "knowledge_coverage_report",
    "knowledge_task_probe",
    "knowledge_task_script_runtime_evidence",
    "knowledge_task_failure_classification",
    "knowledge_task_step_readiness",
    "knowledge_task_run_readiness",
    "external_knowledge_status",
    "knowledge_handoff_summary",
    "best:<classId>",
    "nearest:<classId>",
    "best:object:<name_or_class>",
    "best:npc:<name_or_class>",
    "best:route:<name_or_class>",
    "nearest:object:<name_or_class>",
    "nearest:npc:<name_or_class>",
    "nearest:route:<name_or_class>",
    "reachability:<classId>",
]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
NAMED_QUERY_NEEDS = {
    "current-debug-context": ["knowledge_current_debug_context"],
    "current-blocker": ["knowledge_current_blocker"],
    "explain-current-blocker": ["knowledge_current_blocker"],
    "knowledge-fabric-status": ["knowledge_fabric_status"],
    "data-quality-report": ["knowledge_data_quality_report"],
    "data-source-inventory": ["knowledge_data_source_inventory"],
    "query-coverage-matrix": ["knowledge_query_coverage_matrix"],
    "navigation-decision-trace": ["knowledge_navigation_decision_trace"],
    "coverage-report": ["knowledge_coverage_report"],
    "task-script-runtime-evidence": ["knowledge_task_script_runtime_evidence"],
    "task-failure-classification": ["knowledge_task_failure_classification"],
    "task-script-step-readiness": ["knowledge_task_step_readiness"],
    "step-readiness": ["knowledge_task_step_readiness"],
    "task-script-run-readiness": ["knowledge_task_run_readiness"],
    "run-readiness": ["knowledge_task_run_readiness"],
    "external-knowledge-status": ["external_knowledge_status"],
    "handoff-summary": ["knowledge_handoff_summary"],
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")


def is_local_host(host: str) -> bool:
    return str(host or "").lower() in LOCAL_HOSTS


def resolve_session(session: str | None, latest_session: bool, sessions_dir: str | None) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if session:
        return Path(session).expanduser(), warnings
    if latest_session:
        return find_newest_session(get_sessions_dir(sessions_dir)), warnings
    warnings.append("No --session or --latest-session supplied.")
    return None, warnings


def file_signature(path: Path) -> tuple[bool, int | None, int | None]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False, None, None
    except OSError:
        return False, None, None
    return True, int(stat.st_mtime_ns), int(stat.st_size)


def read_json_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise json.JSONDecodeError("empty file", text, 0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def read_jsonl_file(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                records.append(value)
    return records


def safe_load_json(path: Path, fallback: dict | None = None) -> dict:
    try:
        return read_json_file(path)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError, ValueError):
        return dict(fallback or {})


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
    temp.replace(path)


def watch_request_path(session: Path | None) -> Path | None:
    if session is None:
        return None
    return session / WATCH_REQUEST_DIR / WATCH_REQUEST_FILE


def load_capability_registry() -> dict:
    registry = safe_load_json(CAPABILITY_REGISTRY_PATH, {"schema": CAPABILITY_REGISTRY_SCHEMA, "capabilities": []})
    if registry.get("schema") != CAPABILITY_REGISTRY_SCHEMA:
        return {"schema": CAPABILITY_REGISTRY_SCHEMA, "capabilities": [], "warnings": ["capability registry schema mismatch"]}
    return registry


def load_watch_library() -> dict:
    library = safe_load_json(WATCH_LIBRARY_PATH, {"schema": WATCH_LIBRARY_SCHEMA, "watches": [], "limits": {}})
    if library.get("schema") != WATCH_LIBRARY_SCHEMA:
        return {"schema": WATCH_LIBRARY_SCHEMA, "watches": [], "limits": {}, "warnings": ["watch library schema mismatch"]}
    return library


def active_watch_file_payload(session: Path | None) -> dict:
    path = watch_request_path(session)
    if path is None:
        return {"schema": WATCH_RESPONSE_SCHEMA, "activeWatches": []}
    payload = safe_load_json(path, {"schema": WATCH_RESPONSE_SCHEMA, "activeWatches": []})
    if not isinstance(payload.get("activeWatches"), list):
        payload["activeWatches"] = []
    return payload


def watch_library_by_alias(library: dict) -> dict[str, dict]:
    return {
        str(item.get("alias")): item
        for item in library.get("watches") or []
        if isinstance(item, dict) and item.get("alias")
    }


def watch_library_by_id(library: dict) -> dict[str, dict]:
    return {
        str(item.get("id")): item
        for item in library.get("watches") or []
        if isinstance(item, dict) and item.get("id") is not None
    }


def capability_by_id(registry: dict) -> dict[str, dict]:
    return {
        str(item.get("id")): item
        for item in registry.get("capabilities") or []
        if isinstance(item, dict) and item.get("id")
    }


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_watch_request_item(item: dict, library: dict) -> tuple[dict | None, str | None]:
    if not isinstance(item, dict):
        return None, "watch entry must be an object"
    alias = str(item.get("alias") or "").strip()
    watch_type = str(item.get("type") or "").strip()
    if not alias:
        return None, "watch alias is required"
    if any(char in alias for char in "*?[]{}"):
        return None, f"wildcard/unbounded alias rejected: {alias}"
    if watch_type not in WATCH_SUPPORTED_TYPES:
        return None, f"unsupported watch type for {alias}: {watch_type}"
    library_entry = watch_library_by_alias(library).get(alias)
    watch_id = item.get("id", library_entry.get("id") if library_entry else None)
    if watch_type == "item_container" and watch_id is None:
        watch_id = item.get("containerId")
    if isinstance(watch_id, str) and watch_id.strip() in {"*", "all", "ALL"}:
        return None, f"wildcard/unbounded id rejected for {alias}"
    if watch_type == "builtin" and not isinstance(watch_id, str):
        return None, f"builtin watch {alias} requires a string id"
    if watch_type in {"varbit", "varp", "varclient_int", "item_container"} and not isinstance(watch_id, int):
        return None, f"{watch_type} watch {alias} requires an integer id"
    if watch_type == "varclient_str" and not isinstance(watch_id, int):
        return None, f"varclient_str watch {alias} requires an integer id"
    if watch_type == "widget_summary":
        group = item.get("group", item.get("groupId"))
        child = item.get("child", item.get("childId"))
        if not isinstance(group, int) or not isinstance(child, int):
            return None, f"widget_summary watch {alias} requires integer group/child"
    sample_mode = str(item.get("sampleMode") or (library_entry or {}).get("sampleMode") or "on_change")
    if sample_mode not in WATCH_SAMPLE_MODES:
        return None, f"unsupported sampleMode for {alias}: {sample_mode}"
    interval_ticks = bounded_int(item.get("intervalTicks", (library_entry or {}).get("intervalTicks", 0)), 0, 0, WATCH_MAX_TTL_TICKS)
    if sample_mode == "interval" and interval_ticks <= 0:
        return None, f"interval watch {alias} requires intervalTicks > 0"
    ttl_source = item.get("ttlTicks")
    if ttl_source is None or ttl_source == 0:
        ttl_source = (library_entry or {}).get("ttlTicks")
    if ttl_source in (None, 0):
        ttl_source = WATCH_DEFAULT_TTL_TICKS
    ttl_ticks = bounded_int(ttl_source, WATCH_DEFAULT_TTL_TICKS, 1, WATCH_MAX_TTL_TICKS)
    max_emit = bounded_int(item.get("maxEmitPerTick", (library_entry or {}).get("maxEmitPerTick", 1)), 1, 1, WATCH_MAX_VALUES_PER_TICK)
    normal_live_allowed = bool(item.get("normalLiveAllowed", (library_entry or {}).get("normalLiveAllowed", True)))
    if not normal_live_allowed:
        return None, f"watch {alias} is not allowed in normal live mode"
    normalized = {
        "alias": alias,
        "type": watch_type,
        "id": watch_id,
        "group": item.get("group", item.get("groupId")),
        "child": item.get("child", item.get("childId")),
        "containerId": item.get("containerId"),
        "sampleMode": sample_mode,
        "intervalTicks": interval_ticks,
        "ttlTicks": ttl_ticks,
        "maxEmitPerTick": max_emit,
        "taskProfiles": item.get("taskProfiles") or (library_entry or {}).get("taskProfiles") or [],
        "normalLiveAllowed": normal_live_allowed,
        "debugAuditOnly": bool(item.get("debugAuditOnly", (library_entry or {}).get("debugAuditOnly", False))),
        "requestedAtUtc": utc_now(),
        "source": "context_service",
    }
    return {key: value for key, value in normalized.items() if value is not None}, None


def runtime_capability_status(capability: dict, context: dict) -> str:
    cap_id = capability.get("id")
    status_doc = context.get("status") or {}
    baseline = context.get("baseline") or {}
    activity = context.get("activity") or {}
    navigation = context.get("navigation") or {}
    watch_values = context.get("watchValues") or {}
    if cap_id == "compact_packets.input":
        return "retired"
    if cap_id == "baseline.player_location":
        player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
        return "available" if player.get("worldX") is not None or player.get("sceneX") is not None else "missing"
    if cap_id == "baseline.camera_viewport":
        viewport = baseline.get("cameraViewport") if isinstance(baseline.get("cameraViewport"), dict) else {}
        return "available" if viewport.get("canvasWidth") is not None or viewport.get("canvasHeight") is not None else "missing"
    if cap_id in {"inventory.summary", "inventory.items", "inventory.deltas"}:
        if not isinstance(activity, dict) or not activity:
            return "missing"
        inventory = activity.get("inventoryState") or activity.get("inventory") or {}
        if cap_id == "inventory.deltas":
            return "available" if activity.get("recentInventoryDeltas") is not None or inventory.get("inventoryDeltaTrackingKnown") is True else "missing"
        return "available" if isinstance(inventory, dict) and inventory.get("known") is not False and inventory else "missing"
    if cap_id == "activity.summary":
        return "available" if activity.get("activityState") or activity.get("activity") else "missing"
    if cap_id == "liveness.summary":
        return "available" if activity.get("targetLiveness") or status_doc.get("livenessMode") else "missing"
    if cap_id == "navigation.summary":
        return "available" if navigation.get("collisionKnown") is not None or navigation.get("playerTileKnown") is not None else "missing"
    if cap_id == "navigation.local_reachability":
        return "available" if navigation.get("reachabilityComputed") else "missing"
    if cap_id in {"candidates.best", "candidates.nearest"}:
        return "available" if context.get("candidates") else "missing"
    if cap_id == "overlay.debug_state":
        path = (context.get("paths") or {}).get("overlayDebug")
        return "available" if isinstance(path, Path) and path.exists() else "missing"
    if cap_id == "diagnostics.writer_health":
        return "available" if status_doc else "missing"
    if cap_id == "watch_values.java_runtime":
        return "unsupported"
    if cap_id == "watch_values.builtin":
        return "available" if watch_values.get("valuesByAlias") else "missing"
    if cap_id == "events.timeline":
        return "available" if context.get("events") else "stale"
    if capability.get("status") in {"watchable", "future", "debug_only", "unavailable"}:
        return capability.get("status")
    return "available"


def capabilities_payload(context: dict) -> dict:
    registry = load_capability_registry()
    capabilities = []
    for capability in registry.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        item = dict(capability)
        item["runtimeStatus"] = runtime_capability_status(item, context)
        item["availableNow"] = item["runtimeStatus"] == "available"
        item["watchable"] = item.get("status") == "watchable" or str(item.get("id", "")).startswith("watch:")
        capabilities.append(item)
    telemetry_discovery = telemetry_capabilities.capability_summary_from_context(context)
    return {
        "schema": CAPABILITY_REGISTRY_SCHEMA,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "capabilities": capabilities,
        "telemetryDiscovery": telemetry_discovery,
        "runtimeSummary": {
            "available": sum(1 for item in capabilities if item.get("runtimeStatus") == "available"),
            "missing": sum(1 for item in capabilities if item.get("runtimeStatus") in {"missing", "stale"}),
            "watchable": sum(1 for item in capabilities if item.get("watchable")),
            "unsupported": sum(1 for item in capabilities if item.get("runtimeStatus") == "unsupported"),
            "telemetryFieldsAvailable": len(telemetry_discovery.get("available_fields") or []),
            "telemetryFieldsMissing": len(telemetry_discovery.get("missing_fields") or []),
        },
    }


def watches_payload(context: dict) -> dict:
    library = load_watch_library()
    active = active_watch_file_payload(context.get("session")).get("activeWatches") or []
    watch_values = context.get("watchValues") if isinstance(context.get("watchValues"), dict) else {}
    return {
        "schema": WATCH_LIBRARY_SCHEMA,
        "generatedAtUtc": utc_now(),
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "limits": library.get("limits") or {},
        "watches": library.get("watches") or [],
        "activeWatches": active,
        "activeWatchCount": len(active),
        "currentValues": compact_watch_values(watch_values, response_mode="compact"),
        "javaWatchRuntime": "future",
        "warnings": ["Java dynamic watch polling is not implemented yet; builtin watch values are produced by the Python live processor."],
    }


def compact_watch_record(record: dict, response_mode: str) -> dict:
    if response_mode == "full":
        return dict(record)
    return {
        key: record.get(key)
        for key in (
            "alias",
            "type",
            "value",
            "changed",
            "latestTick",
            "source",
            "unavailableReason",
        )
        if key in record
    }


def compact_watch_values(watch_values: dict, *, response_mode: str, aliases: list[str] | None = None) -> dict:
    values = watch_values.get("valuesByAlias") if isinstance(watch_values.get("valuesByAlias"), dict) else {}
    selected_aliases = aliases if aliases is not None else sorted(values)
    selected = {
        alias: compact_watch_record(values[alias], response_mode)
        for alias in selected_aliases
        if isinstance(values.get(alias), dict)
    }
    return {
        "schema": watch_values.get("schema", "live_watch_values.v1"),
        "latestTick": watch_values.get("latestTick"),
        "activeWatchCount": watch_values.get("activeWatchCount"),
        "watchBudgetExceeded": watch_values.get("watchBudgetExceeded"),
        "changedAliases": watch_values.get("changedAliases") or [],
        "unavailableWatches": watch_values.get("unavailableWatches") or [],
        "valuesByAlias": selected,
        "warnings": watch_values.get("warnings") or [],
    }


def suggested_watch_for(alias_or_capability: str, library: dict | None = None) -> dict | None:
    library = library or load_watch_library()
    by_alias = watch_library_by_alias(library)
    by_id = watch_library_by_id(library)
    item = by_alias.get(alias_or_capability) or by_id.get(alias_or_capability)
    if not isinstance(item, dict):
        return None
    if item.get("normalLiveAllowed") is False:
        return None
    return {
        "alias": item.get("alias"),
        "type": item.get("type"),
        "id": item.get("id"),
        "sampleMode": item.get("sampleMode") or "on_change",
        "intervalTicks": item.get("intervalTicks", 0),
        "ttlTicks": item.get("ttlTicks") or WATCH_DEFAULT_TTL_TICKS,
    }


def handle_watch_request_payload(context: dict, payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {"schema": WATCH_RESPONSE_SCHEMA, "accepted": [], "rejected": [{"reason": "watch request must be an object"}], "activeWatches": [], "warnings": []}
    if payload.get("schema") != WATCH_REQUEST_SCHEMA:
        return {"schema": WATCH_RESPONSE_SCHEMA, "requestId": payload.get("requestId"), "accepted": [], "rejected": [{"reason": f"unsupported schema: {payload.get('schema')}"}], "activeWatches": [], "warnings": []}
    library = load_watch_library()
    requested = payload.get("watches") if isinstance(payload.get("watches"), list) else []
    accepted: list[dict] = []
    rejected: list[dict] = []
    type_counts: dict[str, int] = {}
    for item in requested[: WATCH_REQUEST_LIMIT + 1]:
        normalized, reason = normalize_watch_request_item(item, library)
        alias = item.get("alias") if isinstance(item, dict) else None
        if reason:
            rejected.append({"alias": alias, "reason": reason})
            continue
        assert normalized is not None
        watch_type = normalized["type"]
        if len(accepted) >= WATCH_REQUEST_LIMIT:
            rejected.append({"alias": normalized["alias"], "reason": f"watch request limit exceeded ({WATCH_REQUEST_LIMIT})"})
            continue
        type_counts[watch_type] = type_counts.get(watch_type, 0) + 1
        if type_counts[watch_type] > WATCH_TYPE_LIMITS.get(watch_type, WATCH_REQUEST_LIMIT):
            rejected.append({"alias": normalized["alias"], "reason": f"{watch_type} watch limit exceeded"})
            continue
        accepted.append(normalized)
    request_path = watch_request_path(context.get("session"))
    request_written = False
    warnings = ["Java dynamic watch polling is not implemented yet; request file is written for future plugin support."]
    if request_path is not None:
        active_payload = {
            "schema": WATCH_RESPONSE_SCHEMA,
            "requestSchema": WATCH_REQUEST_SCHEMA,
            "requestId": payload.get("requestId"),
            "task": payload.get("task"),
            "generatedAtUtc": utc_now(),
            "activeWatches": accepted,
            "rejected": rejected,
            "limits": {
                "maxTotalWatches": WATCH_REQUEST_LIMIT,
                "maxTtlTicks": WATCH_MAX_TTL_TICKS,
                "maxValuesPerTick": WATCH_MAX_VALUES_PER_TICK,
                "typeLimits": WATCH_TYPE_LIMITS,
            },
            "source": "context_service",
            "readOnly": True,
        }
        atomic_write_json(request_path, active_payload)
        request_written = True
    return {
        "schema": WATCH_RESPONSE_SCHEMA,
        "requestId": payload.get("requestId"),
        "generatedAtUtc": utc_now(),
        "accepted": accepted,
        "rejected": rejected,
        "activeWatches": accepted,
        "warnings": warnings,
        "limits": {
            "maxTotalWatches": WATCH_REQUEST_LIMIT,
            "maxTtlTicks": WATCH_MAX_TTL_TICKS,
            "maxValuesPerTick": WATCH_MAX_VALUES_PER_TICK,
            "typeLimits": WATCH_TYPE_LIMITS,
        },
        "requestWritten": request_written,
        "requestPath": str(request_path) if request_path else None,
        "noActionEmitted": True,
    }


class LiveContextCache:
    def __init__(self, session: Path | None, reload_interval: float = 0.5):
        self.session = session
        self.reload_interval = max(0.0, float(reload_interval))
        self.lock = threading.RLock()
        self.last_check = 0.0
        self.context: dict | None = None
        self.signatures: dict[str, tuple[bool, int | None, int | None]] = {}
        self.reload_count = 0
        self.cache_hit_count = 0
        self.read_error_count = 0
        self.last_reload_time: str | None = None
        self.last_error: str | None = None
        self.last_error_file: str | None = None
        self.last_error_time: str | None = None

    def paths(self) -> dict[str, Path]:
        if self.session is None:
            return {}
        paths = query.live_paths(self.session)
        paths["performance"] = self.session / "interaction_geometry" / "live" / "live_performance_summary.json"
        return paths

    def stats(self) -> dict:
        return {
            "reloadCount": self.reload_count,
            "cacheHitCount": self.cache_hit_count,
            "readErrorCount": self.read_error_count,
            "lastReloadUtc": self.last_reload_time,
            "lastError": self.last_error,
            "lastErrorPath": self.last_error_file,
            "lastErrorUtc": self.last_error_time,
        }

    def load(self, force: bool = False) -> dict:
        with self.lock:
            now = time.monotonic()
            if not force and self.context is not None and now - self.last_check < self.reload_interval:
                self.cache_hit_count += 1
                return self._with_cache_stats(self.context)
            self.last_check = now

            if self.session is None:
                context = {
                    "session": None,
                    "paths": {},
                    "baseline": {},
                    "context": {},
                    "status": {},
                    "activity": {},
                    "events": [],
                    "navigation": {},
                    "watchValues": {},
                    "performance": {},
                    "candidates": [],
                    "warnings": ["No telemetry session selected."],
                    "missingFields": ["session"],
                    "sourceFiles": [],
                }
                self.context = context
                self.cache_hit_count += 1
                return self._with_cache_stats(context)

            paths = self.paths()
            signatures = {name: file_signature(path) for name, path in paths.items()}
            if not force and self.context is not None and signatures == self.signatures:
                self.cache_hit_count += 1
                return self._with_cache_stats(self.context)

            warnings: list[str] = []
            missing: list[str] = []
            previous = self.context or {}

            def load_json(name: str, required: bool) -> dict:
                path = paths[name]
                exists, _mtime, _size = signatures[name]
                if not exists:
                    if required:
                        warnings.append(f"{name} missing: {path}")
                        missing.append(name)
                    return {}
                try:
                    return read_json_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError, ValueError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get(self._context_key(name))
                    if isinstance(cached, dict) and cached:
                        warnings.append(f"{name} transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"{name} unreadable: {exc}")
                    missing.append(name)
                    return {}

            def load_candidates() -> list[dict]:
                path = paths["candidates"]
                exists, _mtime, _size = signatures["candidates"]
                if not exists:
                    warnings.append(f"candidates missing: {path}")
                    missing.append("candidates")
                    return []
                try:
                    return read_jsonl_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get("candidates")
                    if isinstance(cached, list) and cached:
                        warnings.append(f"candidates transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"candidates unreadable: {exc}")
                    missing.append("candidates")
                    return []

            def load_events() -> list[dict]:
                path = paths["events"]
                exists, _mtime, _size = signatures["events"]
                if not exists:
                    return []
                try:
                    return read_jsonl_file(path)
                except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError) as exc:
                    self._record_error(path, exc)
                    cached = previous.get("events")
                    if isinstance(cached, list):
                        warnings.append(f"events transient read failure; kept previous cached data: {exc}")
                        return cached
                    warnings.append(f"events unreadable: {exc}")
                    return []

            context = {
                "session": self.session,
                "paths": paths,
                "baseline": load_json("baseline", True),
                "context": load_json("context", True),
                "status": load_json("status", True),
                "activity": load_json("activity", False),
                "events": load_events(),
                "navigation": load_json("navigation", False),
                "watchValues": load_json("watchValues", False),
                "performance": load_json("performance", False),
                "candidates": load_candidates(),
                "warnings": warnings,
                "missingFields": missing,
                "sourceFiles": query.source_files_payload(paths),
            }
            if paths["candidates"].exists() and not context["candidates"]:
                context["warnings"].append("live_candidates is present but empty.")
            self.context = context
            self.signatures = signatures
            self.reload_count += 1
            self.last_reload_time = utc_now()
            return self._with_cache_stats(context)

    def _context_key(self, name: str) -> str:
        return "context" if name == "context" else name

    def _record_error(self, path: Path, exc: BaseException) -> None:
        self.read_error_count += 1
        self.last_error = str(exc)
        self.last_error_file = str(path)
        self.last_error_time = utc_now()

    def _with_cache_stats(self, context: dict) -> dict:
        context = dict(context)
        context["cacheStats"] = self.stats()
        return context


def request_args(request: dict, default_max_candidates: int) -> SimpleNamespace:
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    response_mode = request.get("responseMode") or "compact"
    if response_mode not in SUPPORTED_RESPONSE_MODES:
        response_mode = "compact"
    return SimpleNamespace(
        profile=request.get("profile"),
        max_distance=query.as_number(constraints.get("maxDistanceTiles")),
        freshness_ticks=int(request.get("maxAgeTicks") or query.DEFAULT_FRESHNESS_TICKS),
        freshness_ms=int(request.get("maxAgeMillis") or query.DEFAULT_FRESHNESS_MS),
        verbose=response_mode == "full",
        fields=response_mode,
        top=int(request.get("maxCandidates") or default_max_candidates or 3),
        benchmark=False,
    )


def candidate_satisfies(candidate: dict, constraints: dict) -> bool:
    if constraints.get("onScreen") is True and candidate.get("onScreen") is not True:
        return False
    if constraints.get("notUiBlocked") is True and candidate.get("uiBlocked") is not False:
        return False
    if constraints.get("geometryAvailable") is True and candidate.get("geometryAvailable") is not True:
        return False
    max_distance = query.as_number(constraints.get("maxDistanceTiles"))
    if max_distance is not None:
        distance = query.candidate_distance(candidate)
        if distance is None or distance > max_distance:
            return False
    return True


def constrained_context(context: dict, request: dict) -> dict:
    constraints = request.get("constraints") if isinstance(request.get("constraints"), dict) else {}
    profile = request.get("profile")
    candidates = query.filter_candidates(context.get("candidates", []), profile=profile)
    candidates = [candidate for candidate in candidates if candidate_satisfies(candidate, constraints)]
    copy = dict(context)
    copy["candidates"] = candidates
    return copy


def compact_baseline(baseline: dict, mode: str) -> dict:
    if mode == "full":
        return baseline
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    viewport = baseline.get("cameraViewport") if isinstance(baseline.get("cameraViewport"), dict) else {}
    compact = {
        "schema": baseline.get("schema"),
        "generatedAtUtc": baseline.get("generatedAtUtc"),
        "latestTick": baseline.get("latestTick"),
        "latestFrameTick": baseline.get("latestFrameTick"),
        "latestFramePath": baseline.get("latestFramePath"),
        "gameState": baseline.get("gameState"),
        "player": {
            key: player.get(key)
            for key in (
                "worldX",
                "worldY",
                "plane",
                "sceneX",
                "sceneY",
                "localX",
                "localY",
                "animation",
                "poseAnimation",
                "animationFrame",
                "interacting",
                "isMoving",
                "runEnergy",
            )
        },
        "cameraViewport": {
            key: viewport.get(key)
            for key in (
                "cameraX",
                "cameraY",
                "cameraZ",
                "cameraPitch",
                "cameraYaw",
                "canvasWidth",
                "canvasHeight",
                "projectionStateHash",
            )
        },
    }
    if isinstance(baseline.get("inputGeometry"), dict):
        compact["inputGeometry"] = baseline.get("inputGeometry")
    return compact


def compact_candidate_answer(answer: dict | None, mode: str) -> dict | None:
    if not answer:
        return None
    if mode == "full":
        return answer
    keys = [
        "classId",
        "targetName",
        "id",
        "rawId",
        "hash",
        "worldX",
        "worldY",
        "plane",
        "sceneX",
        "sceneY",
        "distanceTiles",
        "onScreen",
        "geometryAvailable",
        "uiBlocked",
        "blockingUiRegions",
        "qualityScore",
        "qualityTier",
        "aimPoint",
        "preferredGeometryType",
        "tick",
        "freshness",
        "targetLiveState",
        "livenessInterpretation",
        "navigation",
    ]
    if mode == "normal":
        keys.extend(["positiveSignals", "negativeSignals", "targetLiveEvidence", "lastSeenTick", "lastChangedTick", "lastDespawnedTick"])
    return {key: answer.get(key) for key in keys if key in answer}


def select_class_candidate(context: dict, class_id: str, mode: str, args: SimpleNamespace) -> tuple[dict | None, str, float, list[str], list[str], list[str]]:
    candidate = (
        query.nearest_candidate(context["candidates"], class_id, args.max_distance, args.profile)
        if mode == "nearest"
        else query.best_candidate(context["candidates"], class_id, args.max_distance, args.profile)
    )
    answer, status, confidence, reasons, warnings, missing = query.candidate_answer(candidate, context, args.freshness_ticks, args.freshness_ms)
    return answer, status, confidence, reasons, warnings, missing


def candidate_items(context: dict, request: dict, args: SimpleNamespace, limit: int, mode: str) -> list[dict]:
    candidates = list(context.get("candidates") or [])
    candidates.sort(key=query.best_sort_key)
    items = []
    for candidate in candidates[:limit]:
        answer, _status, _confidence, _reasons, _warnings, _missing = query.candidate_answer(candidate, context, args.freshness_ticks, args.freshness_ms)
        items.append(compact_candidate_answer(answer, mode) or {})
    return items


def combine_status(current: str, incoming: str) -> str:
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    return incoming if order.get(incoming, 1) > order.get(current, 1) else current


def diagnostics_payload(context: dict, mode: str) -> dict:
    status = context.get("status") or {}
    performance = context.get("performance") or {}
    input_source = {
        "inputSourceRequested": status.get("inputSourceRequested"),
        "inputSourceActive": status.get("inputSourceActive"),
        "defaultLiveInputPreference": status.get("defaultLiveInputPreference"),
        "compactPacketsAvailable": status.get("compactPacketsAvailable"),
        "compactPacketsRecent": status.get("compactPacketsRecent"),
        "inputFallbackReason": status.get("inputFallbackReason"),
        "latestCompactPacketSequence": status.get("compactPacketLastSequence") or status.get("compactPacketLatestSequence"),
        "latestCompactPacketSegment": status.get("compactPacketLatestSegment"),
        "recordingMode": status.get("recordingMode"),
        "rawTickRecordingEnabled": status.get("rawTickRecordingEnabled"),
        "rawEventRecordingEnabled": status.get("rawEventRecordingEnabled"),
        "frameRecordingEnabled": status.get("frameRecordingEnabled"),
        "compactPacketRecordingEnabled": status.get("compactPacketRecordingEnabled"),
    }
    payload = {
        "activeProfile": status.get("profile"),
        "candidateCount": status.get("candidateCount"),
        "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status.get("sourceCapHit"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "latestTickProcessed": status.get("latestTickProcessed"),
        "performance": performance if mode == "full" else {
            key: performance.get(key)
            for key in ("avgTotalMs", "p95TotalMs", "maxTotalMs", "budgetExceededCount", "recommendations")
            if key in performance
        },
        "inputSource": input_source,
        "cacheStats": context.get("cacheStats") or {},
    }
    return payload


def source_files_summary(source_files: list[dict]) -> dict:
    missing = [item.get("name") for item in source_files if not item.get("exists")]
    return {
        "allRequiredPresent": not any(name in {"baseline", "context", "candidates", "status"} for name in missing),
        "missingFiles": missing,
        "staleFiles": [],
        "liveFilesPresent": not missing,
        "fileCount": len(source_files),
    }


def compact_liveness_state(state: dict, *, examples: int = 0) -> dict:
    if not isinstance(state, dict):
        return {}
    keys = [
        "activeCandidateLiveState",
        "bestCandidateLiveState",
        "livenessInterpretation",
        "recentlyUnavailableCount",
        "recentlyDepletedCount",
        "suppressedCandidateCount",
        "candidatesSuppressedByLiveness",
        "candidatesSuppressedAsDepleted",
        "candidatesRevivedAfterRespawn",
        "livenessMode",
        "livenessDegraded",
        "livenessBudgetExceeded",
        "livenessCandidatesChecked",
        "livenessCandidatesSkippedByBudget",
    ]
    compact = {key: state.get(key) for key in keys if key in state}
    if "livenessInterpretation" not in compact:
        live_state = state.get("bestCandidateLiveState") or state.get("activeCandidateLiveState")
        if state.get("livenessDegraded") or state.get("livenessBudgetExceeded"):
            compact["livenessInterpretation"] = "degraded"
        elif live_state in ("recently_despawned", "depleted_or_stump", "stale", "changed"):
            compact["livenessInterpretation"] = "degraded"
        elif live_state == "live":
            compact["livenessInterpretation"] = "direct"
        elif live_state == "live_assumed":
            compact["livenessInterpretation"] = "assumed"
        else:
            compact["livenessInterpretation"] = "unknown"
    if state.get("bestCandidateChanged"):
        for key in ("previousBestCandidate", "currentBestCandidate", "bestCandidateChanged", "bestCandidateChangeReason", "previousBestSuppressedReason"):
            if key in state:
                compact[key] = state.get(key)
    if examples > 0 and isinstance(state.get("recentlyUnavailableTargets"), list):
        compact["recentlyUnavailableTargets"] = state["recentlyUnavailableTargets"][:examples]
    return compact


def compact_reachability_report(report: dict, mode: str) -> tuple[dict, list[dict]]:
    if mode == "full":
        return report, report.get("candidates") or []
    summary = report.get("reachabilitySummary") or {}
    compact_summary = {
        "status": report.get("status"),
        "latestTick": report.get("latestTick"),
        "classId": report.get("classId"),
        "player": report.get("player"),
        "collisionWindow": report.get("collisionWindow"),
        "navigationStatus": (report.get("navigationReadiness") or {}).get("status"),
        "candidateCount": summary.get("candidateCount"),
        "candidatesInsideCollisionWindow": summary.get("candidatesInsideCollisionWindow"),
        "candidatesOutsideCollisionWindow": summary.get("candidatesOutsideCollisionWindow"),
        "reachableCount": summary.get("reachableCount"),
        "blockedCount": summary.get("blockedCount"),
        "unknownCount": summary.get("unknownCount"),
    }
    candidates = []
    for candidate in report.get("candidates") or []:
        item = {
            key: candidate.get(key)
            for key in (
                "classId",
                "targetName",
                "id",
                "hash",
                "worldX",
                "worldY",
                "plane",
                "sceneX",
                "sceneY",
                "distanceTiles",
                "onScreen",
                "geometryAvailable",
                "targetLiveState",
                "aimPoint",
                "directReachability",
                "targetInCollisionWindow",
                "pathLengthTiles",
                "reachabilityConfidence",
                "reachabilityEvidence",
                "missingNavigationFields",
            )
            if key in candidate
        }
        candidates.append(item)
    return compact_summary, candidates


def frame_payload(context: dict) -> dict:
    baseline = context.get("baseline") or {}
    status = context.get("status") or {}
    return {
        "latestFrameTick": query.first_value(status.get("latestFrameTick"), baseline.get("latestFrameTick")),
        "latestFramePath": query.first_value(status.get("latestFramePath"), baseline.get("latestFramePath")),
        "selectedTickHasFrame": status.get("selectedTickHasFrame"),
        "frameIndexExists": status.get("frameIndexExists"),
    }


def requested_class_needs(needs: list[str], prefix: str) -> list[str]:
    values = []
    marker = prefix + ":"
    for need in needs:
        if isinstance(need, str) and need.startswith(marker) and need[len(marker) :]:
            values.append(need[len(marker) :])
    return values


def requested_typed_target_needs(needs: list[str], prefix: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    marker = prefix + ":"
    for need in needs:
        if not isinstance(need, str) or not need.startswith(marker):
            continue
        rest = need[len(marker) :]
        parts = rest.split(":", 1)
        if len(parts) != 2:
            continue
        kind, query_text = parts[0].strip().lower(), parts[1].strip()
        if kind in {"object", "npc", "route"} and query_text:
            values.append((kind, query_text))
    return values


def normalized_context_model(context: dict, *, max_items: int) -> dict:
    return telemetry_schema.normalized_telemetry(context, max_items=max_items)


def compact_section_missing(section: Any) -> bool:
    return isinstance(section, dict) and section.get("missing") is True


def compact_target_helper(model: dict, *, kind: str, query_text: str, mode: str, limit: int) -> dict:
    result = telemetry_schema.select_target(model, kind=kind, query=query_text, mode=mode, limit=limit)
    candidate = result.get("candidate")
    if isinstance(candidate, dict):
        result["candidate"] = {
            key: candidate.get(key)
            for key in (
                "ref",
                "kind",
                "rawId",
                "effectiveId",
                "effectiveName",
                "effectiveActions",
                "worldPoint",
                "localPoint",
                "distance",
                "onScreen",
                "geometry",
                "menuActionAvailable",
                "routeObjectCandidate",
                "routeObjectKind",
                "freshness",
                "confidence",
                "reasons",
                "missingFields",
                "source",
            )
            if key in candidate
        }
    return result


def recordings_root() -> Path:
    return _repo_root() / "recordings"


def resolve_recording_path(identifier: str | None, *, root: Path | None = None) -> Path | None:
    if not identifier:
        return None
    text = str(identifier)
    candidate = Path(text).expanduser()
    if candidate.exists():
        return candidate
    root = root or recordings_root()
    safe_name = Path(text).name
    if not safe_name:
        return None
    return root / safe_name


def list_recordings_payload(root: Path | None = None) -> dict[str, Any]:
    root = root or recordings_root()
    items = []
    if root.exists():
        for child in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name, reverse=True):
            summary_path = child / "summary.json"
            manifest_path = child / "manifest.json"
            manifest = safe_load_json(manifest_path, {})
            summary = safe_load_json(summary_path, {})
            target_quality = compact_target_quality_counts(summary)
            menu_interactions = compact_menu_interaction_counts(summary)
            banking = compact_banking_lifecycle_counts(summary)
            woodcutting_loop = compact_woodcutting_loop_counts(summary)
            traversal = compact_traversal_lifecycle_counts(summary)
            route_comparison = compact_route_template_counts(summary)
            items.append(
                {
                    "id": child.name,
                    "path": str(child),
                    "label": manifest.get("label") or summary.get("label"),
                    "description": manifest.get("description") or summary.get("description") or "",
                    "createdAtUtc": manifest.get("created_at_utc") or manifest.get("createdAtUtc"),
                    "durationSeconds": summary.get("duration_seconds") or manifest.get("duration_seconds"),
                    "snapshotCount": summary.get("snapshot_count") or manifest.get("snapshot_count"),
                    "eventCount": summary.get("event_count") or manifest.get("event_count"),
                    "targetMatchQualityCounts": target_quality.get("targetMatchQualityCounts"),
                    "menuInteractionCounts": menu_interactions.get("menuInteractionCounts"),
                    "bankingLifecycleStatus": banking.get("bankingLifecycleStatus"),
                    "bankOpenSeen": banking.get("bankOpenSeen"),
                    "bankContainerAvailable": banking.get("bankContainerAvailable"),
                    "depositedItemCount": banking.get("depositedItemCount"),
                    "depositedItems": banking.get("depositedItems"),
                    "woodcuttingLoopStatus": woodcutting_loop.get("woodcuttingLoopStatus"),
                    "woodcuttingLoopState": woodcutting_loop.get("woodcuttingLoopState"),
                    "woodcuttingLoopNextExpectedPhase": woodcutting_loop.get("woodcuttingLoopNextExpectedPhase"),
                    "traversalStatus": traversal.get("traversalStatus"),
                    "routeName": traversal.get("routeName"),
                    "traversalStepCount": traversal.get("traversalStepCount"),
                    "routeSegmentCount": traversal.get("routeSegmentCount"),
                    "reviewEvidenceCount": traversal.get("reviewEvidenceCount"),
                    "routeTemplateStatus": route_comparison.get("routeTemplateStatus"),
                    "routeTemplateStatusReason": route_comparison.get("routeTemplateStatusReason"),
                    "routeTemplateScore": route_comparison.get("routeTemplateScore"),
                    "matchedVariantName": route_comparison.get("matchedVariantName"),
                    "routeTemplateMissingSegmentCount": route_comparison.get("missingSegmentCount"),
                    "summaryAvailable": summary_path.exists(),
                    "schemaGapReportAvailable": (child / "schema_gap_report.md").exists(),
                }
            )
    return {
        "schema": "manual_recordings_list.v1",
        "generatedAtUtc": utc_now(),
        "recordingsRoot": str(root),
        "recordings": items,
        "count": len(items),
    }


def compact_target_quality_counts(summary: dict[str, Any]) -> dict[str, Any]:
    target_summary = summary.get("target_match_summary") if isinstance(summary.get("target_match_summary"), dict) else {}
    counts = target_summary.get("qualityCounts") if isinstance(target_summary.get("qualityCounts"), dict) else {}
    examples = target_summary.get("examples") if isinstance(target_summary.get("examples"), list) else []
    return {
        "targetMatchQualityCounts": {
            "strong": int(counts.get("strong") or target_summary.get("strongMatchCount") or 0),
            "medium": int(counts.get("medium") or target_summary.get("mediumMatchCount") or 0),
            "weak": int(counts.get("weak") or target_summary.get("weakMatchCount") or 0),
            "unmatched": int(counts.get("unmatched") or target_summary.get("unmatchedCount") or 0),
        },
        "strongTargetClicks": [item for item in examples if isinstance(item, dict) and item.get("quality") == "strong"][:5],
        "mediumTargetClicks": [item for item in examples if isinstance(item, dict) and item.get("quality") == "medium"][:5],
        "weakTargetClicks": [item for item in examples if isinstance(item, dict) and item.get("quality") == "weak"][:5],
        "unmatchedTargetClicks": [item for item in examples if isinstance(item, dict) and item.get("quality") == "unmatched"][:5],
    }


def compact_menu_interaction_counts(summary: dict[str, Any]) -> dict[str, Any]:
    menu_summary = summary.get("menu_interaction_summary") if isinstance(summary.get("menu_interaction_summary"), dict) else {}
    examples = menu_summary.get("examples") if isinstance(menu_summary.get("examples"), list) else []
    return {
        "menuInteractionCounts": {
            "rightClickMenuOpens": int(menu_summary.get("rightClickMenuOpenCount") or 0),
            "menuSelections": int(menu_summary.get("menuSelectionCount") or 0),
            "menuRowsResolved": int(menu_summary.get("menuRowsResolvedCount") or 0),
            "menuSelectionsWithRowGeometry": int(menu_summary.get("menuSelectionsWithRowGeometryCount") or 0),
            "menuSelectionsLinkedToTargets": int(menu_summary.get("menuSelectionsLinkedToTargetsCount") or 0),
            "menuSelectionsMissingRowGeometry": int(menu_summary.get("menuSelectionsMissingRowGeometryCount") or 0),
        },
        "menuSelections": examples[:5],
        "menuSelectionsWithRowGeometry": [item for item in examples if isinstance(item, dict) and item.get("rowBoundsPresent")][:5],
        "menuSelectionsLinkedToTargets": [item for item in examples if isinstance(item, dict) and item.get("linkedTarget")][:5],
    }


def compact_traversal_lifecycle_counts(summary: dict[str, Any]) -> dict[str, Any]:
    lifecycle = summary.get("traversal_lifecycle") if isinstance(summary.get("traversal_lifecycle"), dict) else {}
    if not lifecycle:
        return {
            "traversalStatus": None,
            "routeName": None,
            "traversalStepCount": 0,
            "routeSegmentCount": 0,
            "successfulSegmentCount": 0,
            "partialSegmentCount": 0,
            "reviewEvidenceCount": 0,
            "traversalSummary": None,
        }
    compact = traversal_lifecycle.compact_lifecycle(lifecycle)
    return {
        "traversalStatus": compact.get("status"),
        "routeName": compact.get("routeName"),
        "traversalStepCount": compact.get("stepCount"),
        "routeSegmentCount": compact.get("routeSegmentCount"),
        "successfulSegmentCount": compact.get("successfulSegmentCount"),
        "partialSegmentCount": compact.get("partialSegmentCount"),
        "reviewEvidenceCount": compact.get("reviewEvidenceCount"),
        "startArea": (compact.get("start") or {}).get("areaLabel") if isinstance(compact.get("start"), dict) else None,
        "endArea": (compact.get("end") or {}).get("areaLabel") if isinstance(compact.get("end"), dict) else None,
        "traversalSummary": compact,
    }


def compact_banking_lifecycle_counts(summary: dict[str, Any]) -> dict[str, Any]:
    lifecycle = summary.get("banking_lifecycle") if isinstance(summary.get("banking_lifecycle"), dict) else {}
    if not lifecycle:
        return {
            "bankingLifecycleStatus": None,
            "bankOpenSeen": False,
            "depositBoxOpenSeen": False,
        "bankContainerAvailable": False,
            "bankContainerDeltaAvailable": False,
            "bankUiPresent": False,
            "depositedItemCount": 0,
            "depositedItems": [],
            "bankingSummary": None,
        }
    compact = banking_lifecycle.compact_lifecycle(lifecycle)
    return {
        "bankingLifecycleStatus": compact.get("status"),
        "bankingPhase": compact.get("phase"),
        "bankLikeInterface": compact.get("bankLikeInterface"),
        "bankOpenSeen": compact.get("bankOpenSeen"),
        "depositBoxOpenSeen": compact.get("depositBoxOpenSeen"),
        "bankWidgetRootSeen": compact.get("bankWidgetRootSeen"),
        "bankContainerAvailable": compact.get("bankContainerAvailable"),
        "bankContainerDeltaAvailable": compact.get("bankContainerDeltaAvailable"),
        "depositConfirmationLevel": compact.get("depositConfirmationLevel"),
        "bankUiPresent": compact.get("bankUiPresent"),
        "bankUiSnapshotCount": compact.get("bankUiSnapshotCount"),
        "bankUiFreshness": compact.get("bankUiFreshness"),
        "depositDetected": compact.get("depositDetected"),
        "withdrawDetected": compact.get("withdrawDetected"),
        "depositedItemCount": compact.get("depositedItemCount") or 0,
        "depositedItems": compact.get("depositedItems") or [],
        "withdrawnItemCount": compact.get("withdrawnItemCount") or 0,
        "withdrawnItems": compact.get("withdrawnItems") or [],
        "bankingMissingCapabilities": compact.get("missingCapabilities") or [],
        "bankingWarnings": compact.get("warnings") or [],
        "bankingSummary": compact,
    }


def compact_interruption_lifecycle_counts(summary: dict[str, Any]) -> dict[str, Any]:
    lifecycle = summary.get("interruption_lifecycle") if isinstance(summary.get("interruption_lifecycle"), dict) else {}
    damage_summary = summary.get("combat_damage_summary") if isinstance(summary.get("combat_damage_summary"), dict) else {}
    if not lifecycle:
        damage_compact = combat_damage_summary.compact_summary(damage_summary) if damage_summary else {}
        return {
            "interruptionLifecycleStatus": None,
            "interruptionDetected": False,
            "interruptionType": None,
            "interruptionPrimaryCause": None,
            "interruptionTaskResumed": False,
            "combatObserved": bool(damage_compact.get("combatObserved")),
            "hitsplatsSeen": damage_compact.get("hitsplatCount") or 0,
            "damageTakenTotal": damage_compact.get("damageTakenTotal"),
            "damageDealtTotal": damage_compact.get("damageDealtTotal"),
            "primaryOpponent": damage_compact.get("primaryOpponent"),
            "interruptionSummary": None,
        }
    compact = interruption_lifecycle.compact_lifecycle(lifecycle)
    if damage_summary:
        damage_compact = combat_damage_summary.compact_summary(damage_summary)
        compact.update({key: value for key, value in damage_compact.items() if value not in (None, "", [], {})})
    return {
        "interruptionLifecycleStatus": compact.get("status"),
        "interruptionDetected": compact.get("interruptionDetected"),
        "interruptionType": compact.get("interruptionType"),
        "interruptionPrimaryCause": compact.get("primaryCause"),
        "interruptionTaskResumed": compact.get("taskResumed"),
        "combatObserved": compact.get("combatObserved"),
        "hitsplatsSeen": compact.get("hitsplatsSeen") or 0,
        "damageTakenTotal": compact.get("damageTakenTotal"),
        "damageDealtTotal": compact.get("damageDealtTotal"),
        "primaryOpponent": compact.get("primaryOpponent"),
        "npcTargetedPlayer": compact.get("npcTargetedPlayer"),
        "playerTargetedNpc": compact.get("playerTargetedNpc"),
        "interruptionMissingCapabilities": compact.get("missingCapabilities") or [],
        "interruptionWarnings": compact.get("warnings") or [],
        "interruptionSummary": compact,
    }


def compact_woodcutting_loop_counts(summary: dict[str, Any]) -> dict[str, Any]:
    lifecycle = summary.get("woodcutting_loop_lifecycle") if isinstance(summary.get("woodcutting_loop_lifecycle"), dict) else {}
    if not lifecycle:
        return {
            "woodcuttingLoopStatus": None,
            "woodcuttingLoopState": None,
            "woodcuttingLoopCurrentPhase": None,
            "woodcuttingLoopNextExpectedPhase": None,
            "woodcuttingLoopSummary": None,
        }
    compact = woodcutting_loop_lifecycle.compact_lifecycle(lifecycle)
    return {
        "woodcuttingLoopStatus": compact.get("status"),
        "woodcuttingLoopState": compact.get("loopState"),
        "woodcuttingLoopCurrentPhase": compact.get("currentPhase"),
        "woodcuttingLoopNextExpectedPhase": compact.get("nextExpectedPhase"),
        "woodcuttingLoopConfidence": compact.get("confidence"),
        "woodcuttingLoopDetectedPhases": compact.get("detectedPhases") or [],
        "woodcuttingLoopSummary": compact,
    }


def compact_route_template_counts(summary: dict[str, Any]) -> dict[str, Any]:
    comparison = summary.get("route_template_comparison") if isinstance(summary.get("route_template_comparison"), dict) else {}
    if not comparison:
        return {
            "routeTemplatePath": summary.get("routeTemplatePath"),
            "detectedRouteName": summary.get("detectedRouteName"),
            "detectedStartArea": summary.get("detectedStartArea"),
            "detectedEndArea": summary.get("detectedEndArea"),
            "routeTemplateAutoSelection": summary.get("routeTemplateAutoSelection"),
            "routeTemplateDirectionMismatch": bool(summary.get("routeTemplateDirectionMismatch")),
            "untemplatedRoute": bool(summary.get("untemplatedRoute")),
            "suggestedTemplateName": summary.get("suggestedTemplateName"),
            "routeTemplateStatus": None,
            "routeTemplateStatusReason": None,
            "routeTemplateScore": None,
            "routeTemplateRevision": None,
            "matchedVariantName": None,
            "validUnregisteredVariant": False,
            "matchedSegmentCount": 0,
            "requiredSegmentCount": 0,
            "optionalSegmentCount": 0,
            "missingSegmentCount": 0,
            "reviewEvidenceCount": 0,
            "navigationSupportSubstitutions": 0,
            "navigationSupportEvidenceCount": 0,
            "allowedExtraSegmentCount": 0,
            "routeTemplateComparison": None,
        }
    compact = route_template.compact_comparison(comparison)
    return {
        "routeTemplatePath": summary.get("routeTemplatePath"),
        "routeTemplateComparisonPath": summary.get("routeTemplateComparisonPath"),
        "detectedRouteName": summary.get("detectedRouteName") or compact.get("detectedRouteName"),
        "detectedStartArea": summary.get("detectedStartArea") or compact.get("detectedStartArea"),
        "detectedEndArea": summary.get("detectedEndArea") or compact.get("detectedEndArea"),
        "routeTemplateAutoSelection": summary.get("routeTemplateAutoSelection"),
        "routeTemplateDirectionMismatch": bool(summary.get("routeTemplateDirectionMismatch") or compact.get("routeTemplateDirectionMismatch")),
        "untemplatedRoute": bool(summary.get("untemplatedRoute")),
        "suggestedTemplateName": summary.get("suggestedTemplateName"),
        "routeTemplateStatus": compact.get("status"),
        "routeTemplateStatusReason": compact.get("statusReason"),
        "routeTemplateScore": compact.get("score"),
        "routeTemplateRevision": compact.get("templateRevision"),
        "matchedVariantName": compact.get("matchedVariantName"),
        "validUnregisteredVariant": compact.get("validUnregisteredVariant"),
        "matchedSegmentCount": compact.get("matchedSegmentCount"),
        "requiredSegmentCount": compact.get("requiredSegmentCount"),
        "optionalSegmentCount": comparison.get("optionalSegmentCount"),
        "missingSegmentCount": compact.get("missingSegmentCount"),
        "extraSegmentCount": compact.get("extraSegmentCount"),
        "allowedExtraSegmentCount": compact.get("allowedExtraSegmentCount"),
        "navigationSupportSubstitutions": compact.get("navigationSupportSubstitutionCount"),
        "navigationSupportEvidenceCount": compact.get("navigationSupportEvidenceCount"),
        "reviewEvidenceCount": compact.get("reviewEvidenceCount"),
        "reviewEvidenceSegmentCount": compact.get("reviewEvidenceSegmentCount"),
        "weakSegmentCount": compact.get("weakSegmentCount"),
        "routeTemplateWarningCount": compact.get("warningCount"),
        "routeTemplateComparison": compact,
    }


def compact_route_monitor_counts(summary: dict[str, Any]) -> dict[str, Any]:
    monitor = summary.get("route_monitor") if isinstance(summary.get("route_monitor"), dict) else {}
    if not monitor:
        return {
            "routeMonitorStatus": None,
            "routeState": None,
            "routeMonitorRouteName": None,
            "routeMonitorTemplateRevision": None,
            "routeMonitorCurrentArea": None,
            "nextExpectedSegment": None,
            "routeMonitorCompletedSegmentCount": 0,
            "routeMonitorRemainingSegmentCount": 0,
            "offRoute": False,
            "routeMonitor": None,
        }
    compact = route_monitor.compact_status(monitor)
    return {
        "routeMonitorStatus": compact.get("status"),
        "routeState": compact.get("routeState"),
        "routeMonitorRouteName": compact.get("routeName"),
        "routeMonitorTemplateRevision": compact.get("templateRevision"),
        "routeMonitorCurrentArea": compact.get("currentArea"),
        "nextExpectedSegment": compact.get("nextExpectedSegment"),
        "routeMonitorCompletedSegmentCount": compact.get("completedSegmentCount"),
        "routeMonitorRemainingSegmentCount": compact.get("remainingSegmentCount"),
        "offRoute": compact.get("offRoute"),
        "routeMonitor": compact,
    }


def compact_route_history_counts(summary: dict[str, Any]) -> dict[str, Any]:
    history = summary.get("route_history") if isinstance(summary.get("route_history"), dict) else {}
    if not history:
        return {
            "routeHistoryStatus": None,
            "routeHistoryState": None,
            "routeHistorySessionId": None,
            "routeHistoryCompletedSegmentCount": 0,
            "routeHistoryRemainingSegmentCount": 0,
            "routeHistory": None,
        }
    return {
        "routeHistoryStatus": history.get("status"),
        "routeHistoryState": history.get("routeState"),
        "routeHistorySessionId": history.get("sessionId"),
        "routeHistoryRouteName": history.get("routeName"),
        "routeHistoryCurrentArea": history.get("currentArea"),
        "routeHistoryCompletedSegmentCount": history.get("completedSegmentCount"),
        "routeHistoryRemainingSegmentCount": history.get("remainingSegmentCount"),
        "routeHistoryOffRoute": history.get("offRoute"),
        "routeHistory": {
            "status": history.get("status"),
            "sessionId": history.get("sessionId"),
            "routeName": history.get("routeName"),
            "routeState": history.get("routeState"),
            "currentArea": history.get("currentArea"),
            "completedSegmentCount": history.get("completedSegmentCount"),
            "remainingSegmentCount": history.get("remainingSegmentCount"),
            "nextExpectedSegment": history.get("nextExpectedSegment"),
            "offRoute": history.get("offRoute"),
            "warnings": (history.get("warnings") or [])[:5],
        },
    }


def resolve_route_template_request_path(value: Any) -> Path | str | None:
    if not str(value or "").strip():
        return None
    if str(value).strip().lower() == "auto":
        return "auto"
    resolution = route_template.resolve_route_template(value)
    if resolution.get("status") != "PASS" or not resolution.get("resolvedPath"):
        return None
    return Path(str(resolution.get("resolvedPath")))


def latest_route_session_state(root: Path | None = None) -> dict[str, Any]:
    root = root or route_monitor.DEFAULT_ROUTE_HISTORY_ROOT
    try:
        candidates = list(root.glob("*/*/route_session_state.json"))
    except OSError:
        candidates = []
    if not candidates:
        return {}
    newest = max(candidates, key=lambda path: path.stat().st_mtime)
    state = safe_load_json(newest, {})
    if state:
        state.setdefault("statePath", str(newest))
    return state


def compact_route_session_state(state: dict[str, Any]) -> dict[str, Any]:
    if not state:
        return {}
    return {
        "schema": state.get("schema"),
        "sessionId": state.get("sessionId"),
        "routeName": state.get("routeName"),
        "templateRevision": state.get("templateRevision"),
        "routeState": state.get("routeState"),
        "currentArea": state.get("currentArea"),
        "currentWorld": state.get("currentWorld"),
        "currentSegment": {
            "segmentIndex": state.get("currentSegmentIndex"),
            "label": state.get("currentSegmentLabel"),
        },
        "nextExpectedSegment": state.get("nextExpectedSegment"),
        "completedSegmentCount": len(state.get("completedSegments") or []),
        "remainingSegmentCount": len(state.get("remainingSegments") or []),
        "completedSegments": state.get("completedSegments") or [],
        "remainingSegments": state.get("remainingSegments") or [],
        "offRoute": state.get("offRoute"),
        "arrivalGateStatus": state.get("arrivalGateStatus"),
        "distanceToEndCluster": state.get("distanceToEndCluster"),
        "endClusterToleranceTiles": state.get("endClusterToleranceTiles"),
        "nearEndCluster": state.get("nearEndCluster"),
        "nearEndClusterSampleCount": state.get("nearEndClusterSampleCount"),
        "distanceAfterLastTransition": state.get("distanceAfterLastTransition"),
        "arrivalGateRequiresEndCluster": state.get("arrivalGateRequiresEndCluster"),
        "distanceOnlyProgressRejected": state.get("distanceOnlyProgressRejected"),
        "arrivalGateRejectedReason": state.get("arrivalGateRejectedReason"),
        "arrivalGatePassedReason": state.get("arrivalGatePassedReason"),
        "arrivalGateWarnings": (state.get("arrivalGateWarnings") or [])[:5],
        "freshness": state.get("freshness"),
        "warnings": (state.get("warnings") or [])[:5],
        "statePath": state.get("statePath"),
        "actualRouteMonitorFolder": str(Path(state.get("statePath")).parent) if state.get("statePath") else None,
    }


def recording_summary_payload(identifier: str | None, *, root: Path | None = None) -> dict[str, Any]:
    recording = resolve_recording_path(identifier, root=root)
    if recording is None or not recording.exists():
        return {"schema": "manual_recording_summary.v1", "status": "FAIL", "error": "recording not found", "recording": identifier}
    summary_path = recording / "summary.json"
    if summary_path.exists():
        summary = safe_load_json(summary_path, {})
    else:
        import analyze_manual_recording

        summary = analyze_manual_recording.update_outputs(recording)
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(summary.get("traversal_lifecycle"), dict):
        lifecycle = safe_load_json(recording / "traversal_lifecycle.json", {})
        if lifecycle:
            summary["traversal_lifecycle"] = lifecycle
    if not isinstance(summary.get("banking_lifecycle"), dict):
        lifecycle = safe_load_json(recording / "banking_lifecycle.json", {})
        if lifecycle:
            summary["banking_lifecycle"] = lifecycle
    if not isinstance(summary.get("interruption_lifecycle"), dict):
        lifecycle = safe_load_json(recording / "interruption_lifecycle.json", {})
        if lifecycle:
            summary["interruption_lifecycle"] = lifecycle
    if not isinstance(summary.get("combat_damage_summary"), dict):
        damage = safe_load_json(recording / "combat_damage_summary.json", {})
        if damage:
            summary["combat_damage_summary"] = damage
    if not isinstance(summary.get("woodcutting_loop_lifecycle"), dict):
        lifecycle = safe_load_json(recording / "woodcutting_loop_lifecycle.json", {})
        if lifecycle:
            summary["woodcutting_loop_lifecycle"] = lifecycle
    if not isinstance(summary.get("route_template_comparison"), dict):
        comparison = safe_load_json(recording / "route_template_comparison.json", {})
        if comparison:
            summary["route_template_comparison"] = comparison
    if not isinstance(summary.get("route_monitor"), dict):
        monitor = safe_load_json(recording / "route_monitor_status.json", {})
        if monitor:
            summary["route_monitor"] = monitor
    summary.setdefault("schema", "manual_recording_summary.v1")
    summary.setdefault("status", "PASS")
    summary.update({key: value for key, value in compact_target_quality_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_menu_interaction_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_banking_lifecycle_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_interruption_lifecycle_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_woodcutting_loop_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_traversal_lifecycle_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_route_template_counts(summary).items() if key not in summary})
    summary.update({key: value for key, value in compact_route_monitor_counts(summary).items() if key not in summary})
    if not isinstance(summary.get("route_history"), dict):
        history = safe_load_json(recording / "route_history_summary.json", {})
        if history:
            summary["route_history"] = history
    summary.update({key: value for key, value in compact_route_history_counts(summary).items() if key not in summary})
    return summary


def recording_schema_gap_payload(identifier: str | None, *, root: Path | None = None) -> dict[str, Any]:
    recording = resolve_recording_path(identifier, root=root)
    if recording is None or not recording.exists():
        return {"schema": "manual_recording_schema_gap.v1", "status": "FAIL", "error": "recording not found", "recording": identifier}
    report_path = recording / "schema_gap_report.md"
    if not report_path.exists():
        import analyze_manual_recording

        analyze_manual_recording.update_outputs(recording)
    try:
        report = report_path.read_text(encoding="utf-8")
    except OSError as error:
        return {"schema": "manual_recording_schema_gap.v1", "status": "FAIL", "error": f"{type(error).__name__}: {error}", "recording": str(recording)}
    return {
        "schema": "manual_recording_schema_gap.v1",
        "status": "PASS",
        "recording": str(recording),
        "reportMarkdown": report,
    }


def build_context_response(
    context: dict,
    request: dict,
    *,
    default_max_candidates: int = 3,
    max_response_bytes: int = 1_000_000,
    compact_include_source_files: bool = False,
    compact_liveness_examples: int = 0,
) -> dict:
    started = time.perf_counter()
    request_id = request.get("requestId")
    response_mode = request.get("responseMode") if request.get("responseMode") in SUPPORTED_RESPONSE_MODES else "compact"
    needs = request.get("needs") if isinstance(request.get("needs"), list) else []
    needs = [str(need) for need in needs]
    if not needs:
        needs = ["task_summary"] if request.get("task") else ["baseline", "diagnostics"]
    max_candidates = int(request.get("maxCandidates") or default_max_candidates or 3)
    max_candidates = max(1, max_candidates)
    try:
        max_events = max(0, int(request.get("maxEvents") if request.get("maxEvents") is not None else 5))
    except (TypeError, ValueError):
        max_events = 5
    args = request_args(request, max_candidates)
    scoped_context = constrained_context(context, request)
    telemetry_model = normalized_context_model(scoped_context, max_items=max_candidates)
    warnings = list(scoped_context.get("warnings") or [])
    missing = list(scoped_context.get("missingFields") or [])
    status = "PASS"
    confidence_values: list[float] = []

    selected_candidate_for_freshness = scoped_context["candidates"][0] if scoped_context.get("candidates") else None
    freshness, freshness_warnings = query.freshness_info(scoped_context, selected_candidate_for_freshness, args.freshness_ticks, args.freshness_ms)
    warnings.extend(freshness_warnings)
    if not freshness.get("freshByTicks") or not freshness.get("freshByMillis"):
        status = combine_status(status, "WARN")

    response: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "requestId": request_id,
        "generatedAtUtc": query.utc_now(),
        "latestTick": query.latest_tick(scoped_context),
        "latestExportSequence": telemetry_model.get("latest_export_sequence"),
        "status": "PASS",
        "freshness": freshness,
        "warnings": [],
        "missingCapabilities": [],
    }
    if response_mode == "compact" and not compact_include_source_files:
        response["sourceFilesSummary"] = source_files_summary(scoped_context.get("sourceFiles") or [])
    else:
        response["sourceFiles"] = scoped_context.get("sourceFiles") or []

    if "baseline" in needs:
        response["baseline"] = compact_baseline(scoped_context.get("baseline") or {}, response_mode)
    if "inventory" in needs:
        inventory = query.inventory_payload(scoped_context)
        response["inventory"] = inventory.get("inventoryState") if response_mode != "full" else inventory
        if response_mode == "compact":
            response["recentInventoryDeltas"] = (inventory.get("recentInventoryDeltas") or [])[:max_candidates]
        status = combine_status(status, inventory.get("status", "WARN"))
        warnings.extend(inventory.get("warnings") or [])
        missing.extend(inventory.get("missingFields") or [])
    for section in ("equipment", "bank", "widgets", "hover", "menu"):
        if section in needs:
            value = telemetry_model.get(section)
            response[section] = value
            if compact_section_missing(value):
                status = combine_status(status, "WARN")
                missing.append(section)
                warnings.append(f"{section} telemetry is not available in the current source set.")
    if "bank_ui" in needs:
        value = telemetry_model.get("bank")
        response["bankUi"] = value
        if compact_section_missing(value):
            status = combine_status(status, "WARN")
            missing.append("bank_ui")
            warnings.append("bank_ui telemetry is not available in the current source set.")
    if "nearby_objects" in needs:
        response["nearbyObjects"] = {
            "count": len(telemetry_model.get("nearby_objects") or []),
            "items": (telemetry_model.get("nearby_objects") or [])[:max_candidates],
        }
        if not response["nearbyObjects"]["items"]:
            status = combine_status(status, "WARN")
            missing.append("nearby_objects")
    if "route_objects" in needs:
        response["routeObjects"] = {
            "count": len(telemetry_model.get("route_objects") or []),
            "items": (telemetry_model.get("route_objects") or [])[:max_candidates],
        }
        if not response["routeObjects"]["items"]:
            status = combine_status(status, "WARN")
            missing.append("route_objects")
    if "nearby_npcs" in needs:
        response["nearbyNpcs"] = {
            "count": len(telemetry_model.get("nearby_npcs") or []),
            "items": (telemetry_model.get("nearby_npcs") or [])[:max_candidates],
        }
        if not response["nearbyNpcs"]["items"]:
            status = combine_status(status, "WARN")
            missing.append("nearby_npcs")
    if any(need in needs for need in ("combat_state", "combat", "recent_hitsplats", "recent_stat_changes", "recent_chat_messages", "combat_damage_summary", "damage_taken", "damage_dealt", "primary_opponent", "recent_combat_summary")):
        combat_value = _dict(
            query.first_value(
                context.get("combat_state"),
                context.get("combatState"),
                _dict(context.get("normalized")).get("combat"),
                telemetry_model.get("combat"),
            )
        )
        response["combatState"] = combat_value
        if compact_section_missing(combat_value):
            status = combine_status(status, "WARN")
            missing.append("combat_state")
            warnings.append("combat_state telemetry is not available in the current source set.")
        else:
            if "combat" in needs:
                response["combat"] = {
                    "inCombat": combat_value.get("inCombat"),
                    "playerInteracting": combat_value.get("playerInteracting"),
                    "actorsInteractingWithPlayer": (combat_value.get("actorsInteractingWithPlayer") or [])[:max_candidates],
                    "nearbyHostileNpcs": (combat_value.get("nearbyHostileNpcs") or [])[:max_candidates],
                    "playerHealth": combat_value.get("playerHealth"),
                }
            if "recent_hitsplats" in needs:
                response["recentHitsplats"] = (combat_value.get("recentHitsplats") or [])[:max_candidates]
            if "recent_stat_changes" in needs:
                response["recentStatChanges"] = (combat_value.get("recentStatChanges") or [])[:max_candidates]
            if "recent_chat_messages" in needs:
                response["recentChatMessages"] = (combat_value.get("recentChatMessages") or [])[:max_candidates]
            if any(need in needs for need in ("combat_damage_summary", "damage_taken", "damage_dealt", "primary_opponent", "recent_combat_summary")):
                lifecycle = interruption_lifecycle.analyze_context(
                    {
                        "combat_state": combat_value,
                        "woodcutting_lifecycle": _dict(response.get("woodcuttingLifecycle")),
                    }
                )
                damage = combat_damage_summary.analyze_context(
                    {
                        "combat_state": combat_value,
                        "interruption_lifecycle": lifecycle,
                    }
                )
                compact_damage = combat_damage_summary.compact_summary(damage)
                response["combatDamageSummary"] = compact_damage if response_mode == "compact" else damage
                if "damage_taken" in needs:
                    response["damageTaken"] = {
                        "total": compact_damage.get("damageTakenTotal"),
                        "hitsplats": compact_damage.get("damageTakenHitsplats"),
                        "hpChanged": compact_damage.get("hpChanged"),
                    }
                if "damage_dealt" in needs:
                    response["damageDealt"] = {
                        "total": compact_damage.get("damageDealtTotal"),
                        "hitsplats": compact_damage.get("damageDealtHitsplats"),
                        "targets": _dict(damage.get("damageDealt")).get("targets") or [],
                    }
                if "primary_opponent" in needs:
                    response["primaryOpponent"] = compact_damage.get("primaryOpponent") or {}
                if "recent_combat_summary" in needs:
                    response["recentCombatSummary"] = {
                        "combatObserved": compact_damage.get("combatObserved"),
                        "primaryOpponent": compact_damage.get("primaryOpponent"),
                        "damageTakenTotal": compact_damage.get("damageTakenTotal"),
                        "damageDealtTotal": compact_damage.get("damageDealtTotal"),
                        "hitsplatCount": compact_damage.get("hitsplatCount"),
                        "hpChanged": compact_damage.get("hpChanged"),
                        "actorDeathSeen": compact_damage.get("actorDeathSeen"),
                        "taskResumed": compact_damage.get("taskResumed"),
                        "warnings": compact_damage.get("warnings") or [],
                    }
    if "activity" in needs:
        activity = query.activity_payload(scoped_context)
        response["activity"] = activity.get("activityState") if response_mode != "full" else activity
        if response_mode == "compact":
            response["woodcuttingState"] = activity.get("woodcuttingState")
            response["recentActivityEvents"] = (activity.get("recentActivityEvents") or [])[:max_candidates]
        else:
            response["woodcuttingState"] = activity.get("woodcuttingState")
        status = combine_status(status, activity.get("status", "WARN"))
        warnings.extend(activity.get("warnings") or [])
        missing.extend(activity.get("missingFields") or [])
    if "woodcutting_lifecycle" in needs or "lifecycle:woodcutting" in needs:
        lifecycle = woodcutting_lifecycle.analyze_context(scoped_context)
        response["woodcuttingLifecycle"] = (
            woodcutting_lifecycle.compact_lifecycle(lifecycle)
            if response_mode == "compact"
            else lifecycle
        )
        status = combine_status(status, lifecycle.get("status", "WARN"))
        warnings.extend(lifecycle.get("warnings") or [])
    if any(need in needs for need in ("interruption_lifecycle", "current_interruption", "task_interruption_status")):
        lifecycle = interruption_lifecycle.analyze_context({"combat_state": telemetry_model.get("combat"), "woodcutting_lifecycle": _dict(response.get("woodcuttingLifecycle"))})
        compact = interruption_lifecycle.compact_lifecycle(lifecycle)
        response["interruptionLifecycle"] = compact if response_mode == "compact" else lifecycle
        if "current_interruption" in needs:
            response["currentInterruption"] = {
                "interruptionDetected": compact.get("interruptionDetected"),
                "interruptionType": compact.get("interruptionType"),
                "primaryCause": compact.get("primaryCause"),
                "combatObserved": compact.get("combatObserved"),
                "taskResumed": compact.get("taskResumed"),
                "confidence": compact.get("confidence"),
                "warnings": compact.get("warnings") or [],
            }
        if "task_interruption_status" in needs:
            response["taskInterruptionStatus"] = {
                "status": compact.get("status"),
                "interruptionDetected": compact.get("interruptionDetected"),
                "cause": compact.get("primaryCause"),
                "missingCapabilities": compact.get("missingCapabilities") or [],
            }
        status = combine_status(status, lifecycle.get("status", "WARN"))
        warnings.extend(lifecycle.get("warnings") or [])
        missing.extend(lifecycle.get("missingCapabilities") or [])
    if any(need in needs for need in ("banking", "banking_lifecycle", "bank_state", "inventory_delta", "deposit_result")):
        lifecycle = banking_lifecycle.analyze_context(scoped_context)
        compact = banking_lifecycle.compact_lifecycle(lifecycle)
        response["bankingLifecycle"] = compact if response_mode == "compact" else lifecycle
        if "banking" in needs:
            response["banking"] = compact if response_mode == "compact" else lifecycle
        if "bank_state" in needs:
            response["bankState"] = {
                "bankOpenSeen": compact.get("bankOpenSeen"),
                "depositBoxOpenSeen": compact.get("depositBoxOpenSeen"),
                "bankWidgetRootSeen": compact.get("bankWidgetRootSeen"),
                "bankContainerAvailable": compact.get("bankContainerAvailable"),
                "bankContainerDeltaAvailable": compact.get("bankContainerDeltaAvailable"),
                "bankLikeInterface": compact.get("bankLikeInterface"),
                "bankUiPresent": compact.get("bankUiPresent"),
                "bankUiFreshness": compact.get("bankUiFreshness"),
                "inventoryFreeSlots": compact.get("freeSlotsAfter"),
                "depositedItems": compact.get("depositedItems") or [],
                "depositConfirmationLevel": compact.get("depositConfirmationLevel"),
                "missingCapabilities": compact.get("missingCapabilities") or [],
                "warnings": compact.get("warnings") or [],
            }
        if "inventory_delta" in needs:
            response["inventoryDelta"] = {
                "freeSlotsBefore": compact.get("freeSlotsBefore"),
                "freeSlotsAfter": compact.get("freeSlotsAfter"),
                "freeSlotDelta": compact.get("freeSlotDelta"),
                "depositedItems": compact.get("depositedItems") or [],
                "withdrawnItems": compact.get("withdrawnItems") or [],
            }
        if "deposit_result" in needs:
            response["depositResult"] = {
                "depositComplete": bool(compact.get("depositDetected")),
                "depositedItems": compact.get("depositedItems") or [],
                "totalDepositedCount": compact.get("depositedItemCount") or 0,
                "depositConfirmationLevel": compact.get("depositConfirmationLevel"),
                "bankContainerDeltaAvailable": compact.get("bankContainerDeltaAvailable"),
                "bankOpen": compact.get("bankOpenSeen"),
                "depositBoxOpen": compact.get("depositBoxOpenSeen"),
                "activeBankLikeInterface": compact.get("bankLikeInterface"),
                "confidence": compact.get("confidence"),
                "missingCapabilities": compact.get("missingCapabilities") or [],
                "warnings": compact.get("warnings") or [],
            }
        status = combine_status(status, lifecycle.get("status", "WARN"))
        warnings.extend(lifecycle.get("warnings") or [])
        missing.extend(lifecycle.get("missingCapabilities") or [])
    if any(need in needs for need in ("woodcutting_loop", "woodcutting_loop_lifecycle", "task_loop", "next_expected_phase")):
        wood_lifecycle = woodcutting_lifecycle.analyze_context(scoped_context)
        bank_lifecycle = banking_lifecycle.analyze_context(scoped_context)
        interruption = interruption_lifecycle.analyze_context(
            {"combat_state": telemetry_model.get("combat"), "woodcutting_lifecycle": wood_lifecycle}
        )
        damage = _dict(response.get("combatDamageSummary"))
        loop = woodcutting_loop_lifecycle.analyze_context(
            {
                "woodcutting_lifecycle": wood_lifecycle,
                "banking_lifecycle": bank_lifecycle,
                "interruption_lifecycle": interruption,
                "combat_damage_summary": damage,
            }
        )
        compact = woodcutting_loop_lifecycle.compact_lifecycle(loop)
        response["woodcuttingLoop"] = compact if response_mode == "compact" else loop
        if "woodcutting_loop_lifecycle" in needs:
            response["woodcuttingLoopLifecycle"] = compact if response_mode == "compact" else loop
        if "task_loop" in needs:
            response["taskLoop"] = {
                "status": compact.get("status"),
                "loopState": compact.get("loopState"),
                "currentPhase": compact.get("currentPhase"),
                "nextExpectedPhase": compact.get("nextExpectedPhase"),
                "confidence": compact.get("confidence"),
                "warnings": compact.get("warnings") or [],
            }
        if "next_expected_phase" in needs:
            response["nextExpectedPhase"] = {
                "phase": compact.get("nextExpectedPhase"),
                "label": compact.get("nextExpectedPhaseLabel"),
                "currentPhase": compact.get("currentPhase"),
                "loopState": compact.get("loopState"),
            }
        status = combine_status(status, loop.get("status", "WARN"))
        warnings.extend(loop.get("warnings") or [])
        missing.extend(loop.get("missingCapabilities") or [])
    if "traversal_lifecycle" in needs or "route_summary" in needs or "latest_recording_traversal" in needs:
        latest = latest_recording_path = None
        recordings = []
        root = recordings_root()
        if root.exists():
            recordings = sorted([path for path in root.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime)
        latest_recording_path = recordings[-1] if recordings else None
        lifecycle = {}
        if latest_recording_path:
            summary = safe_load_json(latest_recording_path / "summary.json", {})
            lifecycle = summary.get("traversal_lifecycle") if isinstance(summary.get("traversal_lifecycle"), dict) else {}
            if not lifecycle:
                lifecycle = safe_load_json(latest_recording_path / "traversal_lifecycle.json", {})
        if lifecycle:
            latest = traversal_lifecycle.compact_lifecycle(lifecycle) if response_mode == "compact" else lifecycle
            response["latestRecordingTraversal"] = latest
            response["routeSummary"] = latest
            status = combine_status(status, lifecycle.get("status", "WARN"))
            warnings.extend(lifecycle.get("warnings") or [])
        else:
            response["latestRecordingTraversal"] = None
            response["routeSummary"] = None
            status = combine_status(status, "WARN")
            warnings.append("latest recording traversal lifecycle is not available")
    if "route_template" in needs or "route_template_comparison" in needs or "latest_route_comparison" in needs:
        latest_recording_path = None
        recordings = []
        root = recordings_root()
        if root.exists():
            recordings = sorted([path for path in root.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime)
        latest_recording_path = recordings[-1] if recordings else None
        comparison = {}
        route_template_payload = {}
        if latest_recording_path:
            summary = safe_load_json(latest_recording_path / "summary.json", {})
            comparison = summary.get("route_template_comparison") if isinstance(summary.get("route_template_comparison"), dict) else {}
            if not comparison:
                comparison = safe_load_json(latest_recording_path / "route_template_comparison.json", {})
            template_path = summary.get("routeTemplatePath")
            if template_path:
                route_template_payload = safe_load_json(Path(template_path), {})
        if route_template_payload and "route_template" in needs:
            response["routeTemplate"] = route_template_payload if response_mode != "compact" else {
                "schema": route_template_payload.get("schema"),
                "routeName": route_template_payload.get("routeName"),
                "templateRevision": route_template_payload.get("templateRevision"),
                "segmentCount": len(route_template_payload.get("segments") or []),
                "optionalSegmentCount": len(route_template_payload.get("optionalSegments") or []),
                "templateNotes": (route_template_payload.get("templateNotes") or [])[:5],
                "warnings": (route_template_payload.get("warnings") or [])[:5],
            }
        if comparison:
            compact = route_template.compact_comparison(comparison)
            response["latestRouteComparison"] = compact if response_mode == "compact" else comparison
            response["routeTemplateComparison"] = compact if response_mode == "compact" else comparison
            status = combine_status(status, comparison.get("status", "WARN"))
            warnings.extend(comparison.get("warnings") or [])
        else:
            response["latestRouteComparison"] = None
            response["routeTemplateComparison"] = None
            status = combine_status(status, "WARN")
            warnings.append("latest route template comparison is not available")
    if any(need in needs for need in ("route_monitor", "route_readiness", "route_progress", "route_next_segment")):
        template_path = resolve_route_template_request_path(request.get("routeTemplate") or request.get("routeTemplatePath"))
        if template_path is None:
            response["routeMonitor"] = None
            status = combine_status(status, "WARN")
            missing.append("route_template")
            warnings.append("route monitor requested without routeTemplate path")
        else:
            monitor = route_monitor.monitor_live_context(str(template_path), scoped_context)
            compact = route_monitor.compact_status(monitor)
            response["routeMonitor"] = compact if response_mode == "compact" else monitor
            response["routeReadiness"] = {
                "status": compact.get("status"),
                "routeState": compact.get("routeState"),
                "currentArea": compact.get("currentArea"),
                "offRoute": compact.get("offRoute"),
                "warnings": compact.get("warnings") or [],
            }
            response["routeProgress"] = {
                "completedSegmentCount": compact.get("completedSegmentCount"),
                "remainingSegmentCount": compact.get("remainingSegmentCount"),
                "nextExpectedSegment": compact.get("nextExpectedSegment"),
            }
            response["routeNextSegment"] = compact.get("nextExpectedSegment")
            status = combine_status(status, monitor.get("status", "WARN"))
            warnings.extend(monitor.get("warnings") or [])
            missing.extend(monitor.get("missingCapabilities") or [])
    if any(need in needs for need in ("route_history", "route_session_state", "route_progress_timeline", "route_completed_segments", "route_remaining_segments")):
        state_path = request.get("routeSessionState") or request.get("routeSessionStatePath")
        state_payload = safe_load_json(Path(state_path), {}) if state_path else latest_route_session_state()
        if state_payload:
            compact_state = compact_route_session_state(state_payload)
            response["routeHistory"] = compact_state if response_mode == "compact" else state_payload
            response["routeSessionState"] = compact_state if response_mode == "compact" else state_payload
            response["routeCompletedSegments"] = compact_state.get("completedSegments") or []
            response["routeRemainingSegments"] = compact_state.get("remainingSegments") or []
            response["routeProgressTimeline"] = {
                "available": bool(compact_state.get("statePath")),
                "statePath": compact_state.get("statePath"),
                "recentPath": (state_payload.get("recentPath") or [])[-10:],
                "planeChanges": (state_payload.get("planeChanges") or [])[-10:],
            }
            state_freshness = state_payload.get("freshness") if isinstance(state_payload.get("freshness"), dict) else {}
            status = combine_status(status, "WARN" if state_freshness.get("status") == "stale" else "PASS")
            warnings.extend(state_payload.get("warnings") or [])
        else:
            template_path = resolve_route_template_request_path(request.get("routeTemplate") or request.get("routeTemplatePath"))
            if template_path is not None:
                monitor = route_monitor.monitor_live_context(str(template_path), scoped_context)
                compact = route_monitor.compact_status(monitor)
                response["routeHistory"] = {
                    "fallback": "snapshot_route_monitor",
                    "routeName": compact.get("routeName"),
                    "routeState": compact.get("routeState"),
                    "currentArea": compact.get("currentArea"),
                    "nextExpectedSegment": compact.get("nextExpectedSegment"),
                    "completedSegmentCount": compact.get("completedSegmentCount"),
                    "remainingSegmentCount": compact.get("remainingSegmentCount"),
                    "offRoute": compact.get("offRoute"),
                    "freshness": compact.get("freshness"),
                    "warnings": compact.get("warnings") or [],
                }
                status = combine_status(status, monitor.get("status", "WARN"))
                warnings.extend(monitor.get("warnings") or [])
            else:
                response["routeHistory"] = None
                status = combine_status(status, "WARN")
                missing.append("route_session_state")
                warnings.append("route history requested but no route session state or routeTemplate path is available")
    if any(need in needs for need in ("human_click_profile", "task_click_profile", "click_landing_profile", "camera_action_profile")):
        profile_path = request.get("humanClickProfile") or request.get("humanClickProfilePath")
        profile = human_click_profile.load_profile(profile_path)
        activity = request.get("activity") or request.get("taskActivity")
        compact = human_click_profile.compact_profile(profile, activity=str(activity) if activity else None) if profile else {}
        if profile:
            response["humanClickProfile"] = compact if response_mode == "compact" else profile
            if "task_click_profile" in needs:
                response["taskClickProfile"] = compact.get("taskProfile")
            if "click_landing_profile" in needs:
                response["clickLandingProfile"] = compact.get("landing")
            if "camera_action_profile" in needs:
                response["cameraActionProfile"] = compact.get("camera")
            status = combine_status(status, profile.get("status", "WARN"))
            warnings.extend(profile.get("warnings") or [])
            missing.extend(profile.get("missingCapabilities") or [])
        else:
            response["humanClickProfile"] = None
            status = combine_status(status, "WARN")
            missing.append("human_click_profile")
            warnings.append("human click profile is not available; run human_click_profile.py or analyzer --human-click-profile")
    if any(need in needs for need in ("click_plan", "human_click_plan", "click_planning_context")):
        profile_path = request.get("humanClickProfile") or request.get("humanClickProfilePath")
        profile = human_click_profile.load_profile(profile_path)
        activity = request.get("activity") or request.get("taskActivity") or request.get("task")
        target = _dict(request.get("target") or request.get("plannedTarget"))
        if not target and scoped_context.get("candidates"):
            target = _dict(scoped_context["candidates"][0])
        action = request.get("action") or request.get("plannedAction")
        if not action:
            actions = target.get("actions") if isinstance(target.get("actions"), list) else target.get("effectiveActions")
            action = actions[0] if isinstance(actions, list) and actions else target.get("action")
        compact_profile = human_click_profile.compact_profile(profile, activity=str(activity) if activity else None) if profile else {}
        plan_source = {
            "target": target,
            "action": action,
            "humanClickProfile": compact_profile,
            "actionInputVisibility": {
                "plannedAction": action,
                "plannedTarget": target,
            },
        }
        if request.get("woodcuttingLoopLifecycle"):
            plan_source["woodcuttingLoopLifecycle"] = request.get("woodcuttingLoopLifecycle")
        if request.get("routeMonitor"):
            plan_source["routeMonitor"] = request.get("routeMonitor")
        if request.get("depositResult"):
            plan_source["depositResult"] = request.get("depositResult")
        plan = task_script_api.get_human_click_plan(
            target=target,
            action=str(action or "unknown"),
            activity=str(activity) if activity else None,
            source=plan_source,
        )
        if "click_planning_context" in needs:
            response["clickPlanningContext"] = task_script_api.get_click_planning_context(activity=str(activity) if activity else None, source=plan_source)
        response["clickPlan"] = click_plan = (
            {
                "status": plan.get("status"),
                "task": plan.get("task"),
                "action": plan.get("action"),
                "target": _dict(plan.get("target")).get("name"),
                "plannedPoint": _dict(plan.get("aim")).get("plannedPoint"),
                "centerPoint": _dict(plan.get("aim")).get("basePoint"),
                "offset": _dict(plan.get("aim")).get("offset"),
                "confidence": plan.get("confidence"),
                "reasons": plan.get("reasons") or [],
                "warnings": plan.get("warnings") or [],
                "blockedReasons": _dict(plan.get("readiness")).get("blockedReasons") or [],
            }
            if response_mode == "compact"
            else plan
        )
        if "human_click_plan" in needs:
            response["humanClickPlan"] = click_plan
        status = combine_status(status, plan.get("status", "WARN"))
        warnings.extend(plan.get("warnings") or [])
        missing.extend(plan.get("missingCapabilities") or [])
    if "liveness" in needs:
        liveness = query.liveness_payload(scoped_context)
        if response_mode == "compact":
            response["liveness"] = compact_liveness_state(liveness.get("targetLivenessState") or {}, examples=compact_liveness_examples)
        elif response_mode == "normal":
            response["liveness"] = compact_liveness_state(liveness.get("targetLivenessState") or {}, examples=max(1, compact_liveness_examples or 3))
        else:
            response["liveness"] = liveness
        status = combine_status(status, liveness.get("status", "WARN"))
        warnings.extend(liveness.get("warnings") or [])
        missing.extend(liveness.get("missingFields") or [])
    if "navigation_readiness" in needs:
        response["navigationReadiness"] = query.navigation_readiness(scoped_context.get("navigation") or {}, scoped_context.get("baseline") or {})
        missing.extend(response["navigationReadiness"].get("missingCapabilities") or [])
        if response["navigationReadiness"].get("status") in {"unknown", "summary"}:
            status = combine_status(status, "WARN")
            warnings.extend(response["navigationReadiness"].get("warnings") or [])
            warning = response["navigationReadiness"].get("warning")
            if warning:
                warnings.append(warning)
    if "diagnostics" in needs:
        response["diagnostics"] = diagnostics_payload(scoped_context, response_mode)
    world_model_payloads = scoped_context.get("worldModelPayloads") if isinstance(scoped_context.get("worldModelPayloads"), dict) else {}
    if "world_model" in needs:
        response["worldModel"] = {
            "summary": scoped_context.get("worldModelSummary") or world_model_payloads.get("world_model_summary") or {},
            "resourceObjectCensus": scoped_context.get("worldModelResourceObjectCensus") or world_model_payloads.get("resource_object_census") or {},
            "serviceObjectCensus": scoped_context.get("worldModelServiceObjectCensus") or world_model_payloads.get("service_object_census") or {},
            "routeObjectCensus": scoped_context.get("worldModelRouteObjectCensus") or world_model_payloads.get("route_object_census") or {},
            "projectionAudit": scoped_context.get("worldModelProjectionAudit") or world_model_payloads.get("projection_audit") or {},
            "pathingFrontier": scoped_context.get("worldModelPathingFrontier") or world_model_payloads.get("pathing_frontier") or {},
        }
    if "world_model_summary" in needs:
        response["worldModelSummary"] = scoped_context.get("worldModelSummary") or world_model_payloads.get("world_model_summary") or {}
    if "resource_object_census" in needs:
        response["resourceObjectCensus"] = scoped_context.get("worldModelResourceObjectCensus") or world_model_payloads.get("resource_object_census") or {}
    if "service_object_census" in needs:
        response["serviceObjectCensus"] = scoped_context.get("worldModelServiceObjectCensus") or world_model_payloads.get("service_object_census") or {}
    if "route_object_census" in needs:
        response["routeObjectCensus"] = scoped_context.get("worldModelRouteObjectCensus") or world_model_payloads.get("route_object_census") or {}
    if "projection_audit" in needs:
        response["projectionAudit"] = scoped_context.get("worldModelProjectionAudit") or world_model_payloads.get("projection_audit") or {}
    if "pathing_frontier" in needs:
        response["pathingFrontier"] = scoped_context.get("worldModelPathingFrontier") or world_model_payloads.get("pathing_frontier") or {}
    knowledge_needs = {
        "knowledge_fabric",
        "knowledge_fabric_status",
        "knowledge_current_debug_context",
        "knowledge_current_blocker",
        "knowledge_resource_candidates",
        "knowledge_service_candidates",
        "knowledge_route_objects",
        "knowledge_path_frontier",
        "knowledge_navigation_decision_trace",
        "knowledge_view_quality",
        "knowledge_session_memory",
        "knowledge_debug_evidence",
        "knowledge_data_quality_report",
        "knowledge_data_source_inventory",
        "knowledge_query_coverage_matrix",
        "knowledge_coverage_report",
        "knowledge_task_probe",
        "knowledge_task_script_runtime_evidence",
        "knowledge_task_failure_classification",
        "knowledge_task_step_readiness",
        "knowledge_task_run_readiness",
        "external_knowledge_status",
        "knowledge_handoff_summary",
    }
    if any(need in needs for need in knowledge_needs):
        fabric = knowledge_fabric.KnowledgeFabric.from_status(scoped_context)
        if "knowledge_fabric" in needs:
            response["knowledgeFabric"] = {
                "status": fabric.status(),
                "worldSummary": fabric.query_world_summary(),
                "sessionMemory": fabric.session_memory,
                "staticLibrary": fabric.static_library.get("summary", {}),
            }
        if "knowledge_fabric_status" in needs:
            response["knowledgeFabricStatus"] = fabric.status()
        if "knowledge_current_debug_context" in needs:
            response["knowledgeCurrentDebugContext"] = fabric.query_current_debug_context(limit=max_candidates)
        if "knowledge_current_blocker" in needs:
            response["knowledgeCurrentBlocker"] = fabric.explain_current_blocker()
        if "knowledge_resource_candidates" in needs:
            response["knowledgeResourceCandidates"] = fabric.query_resource_candidates(limit=max_candidates)
        if "knowledge_service_candidates" in needs:
            response["knowledgeServiceCandidates"] = fabric.query_service_candidates(limit=max_candidates)
        if "knowledge_route_objects" in needs:
            response["knowledgeRouteObjects"] = fabric.query_route_objects(limit=max_candidates)
        if "knowledge_path_frontier" in needs:
            response["knowledgePathFrontier"] = fabric.query_path_frontier(limit=max_candidates)
        if "knowledge_navigation_decision_trace" in needs:
            response["knowledgeNavigationDecisionTrace"] = fabric.query_navigation_decision_trace(limit=max_candidates)
        if "knowledge_view_quality" in needs:
            response["knowledgeViewQuality"] = fabric.query_view_quality(intent=str(scoped_context.get("currentIntent") or "unknown"))
        if "knowledge_session_memory" in needs:
            response["knowledgeSessionMemory"] = fabric.query_session_memory(limit=max_candidates)
        if "knowledge_debug_evidence" in needs:
            response["knowledgeDebugEvidence"] = fabric.query_debug_evidence(limit=max_candidates)
        if "knowledge_data_quality_report" in needs:
            response["knowledgeDataQualityReport"] = fabric.data_quality_report(limit=max_candidates)
        if "knowledge_data_source_inventory" in needs:
            response["knowledgeDataSourceInventory"] = fabric.data_source_inventory()
        if "knowledge_query_coverage_matrix" in needs:
            response["knowledgeQueryCoverageMatrix"] = fabric.query_coverage_matrix()
        if "knowledge_coverage_report" in needs:
            response["knowledgeCoverageReport"] = fabric.coverage_report(intent=str(scoped_context.get("currentIntent") or ""), limit=max_candidates)
        if "knowledge_task_probe" in needs:
            response["knowledgeTaskProbe"] = fabric.probe_task(str(request.get("taskDescription") or request.get("task") or "woodcutting"), limit=max_candidates)
        if "knowledge_task_script_runtime_evidence" in needs:
            response["knowledgeTaskScriptRuntimeEvidence"] = fabric.query_task_script_runtime_evidence()
        if "knowledge_task_failure_classification" in needs:
            response["knowledgeTaskFailureClassification"] = fabric.classify_task_failure()
        if "knowledge_task_step_readiness" in needs:
            response["knowledgeTaskStepReadiness"] = fabric.assess_task_script_step()
        if "knowledge_task_run_readiness" in needs:
            response["knowledgeTaskRunReadiness"] = fabric.assess_task_script_run()
        if "external_knowledge_status" in needs:
            response["externalKnowledgeStatus"] = external_knowledge.knowledge_status()
        if "knowledge_handoff_summary" in needs:
            response["knowledgeHandoffSummary"] = fabric.handoff_summary()
    if "frame" in needs:
        response["frame"] = frame_payload(scoped_context)
    if "candidates" in needs:
        response["candidates"] = {
            "count": len(scoped_context.get("candidates") or []),
            "items": candidate_items(scoped_context, request, args, max_candidates, response_mode),
        }
    if "events" in needs:
        events = query.events_payload(scoped_context, max_events)
        if response_mode == "full":
            all_events = scoped_context.get("events") if isinstance(scoped_context.get("events"), list) else []
            event_items = [event for event in all_events[-max_events:] if isinstance(event, dict)] if max_events else []
        else:
            event_items = events.get("events") or []
        response["events"] = event_items
        response["recentEvents"] = event_items
        response["eventCount"] = events.get("eventCount")
        if not events.get("events"):
            status = combine_status(status, "WARN")
            warnings.extend(events.get("warnings") or [])

    requested_watch_aliases = requested_class_needs(needs, "watch")
    requested_capability_ids = requested_class_needs(needs, "capability")
    suggested_watch_requests: list[dict] = []
    if "watches" in needs or requested_watch_aliases:
        watch_values = scoped_context.get("watchValues") if isinstance(scoped_context.get("watchValues"), dict) else {}
        if "watches" in needs:
            response["watches"] = compact_watch_values(watch_values, response_mode=response_mode)
        if requested_watch_aliases:
            requested_values = compact_watch_values(watch_values, response_mode=response_mode, aliases=requested_watch_aliases)
            response["watchValues"] = requested_values
            present_aliases = set(requested_values.get("valuesByAlias") or {})
            library = load_watch_library()
            for alias in requested_watch_aliases:
                if alias in present_aliases:
                    continue
                status = combine_status(status, "WARN")
                missing.append(f"watch:{alias}")
                suggestion = suggested_watch_for(alias, library)
                if suggestion:
                    suggested_watch_requests.append(suggestion)
                else:
                    warnings.append(f"Watch value is unavailable and no safe bounded watch is registered for alias: {alias}")
    if "capabilities" in needs:
        full_capabilities = capabilities_payload(scoped_context)
        if response_mode == "compact":
            response["capabilities"] = {
                "schema": full_capabilities.get("schema"),
                "runtimeSummary": full_capabilities.get("runtimeSummary"),
                "capabilities": [
                    {
                        key: item.get(key)
                        for key in ("id", "status", "runtimeStatus", "availableNow", "watchable")
                        if key in item
                    }
                    for item in full_capabilities.get("capabilities") or []
                    if isinstance(item, dict)
                ],
            }
        else:
            response["capabilities"] = full_capabilities
    if requested_capability_ids:
        registry_payload = capabilities_payload(scoped_context)
        by_id = capability_by_id(registry_payload)
        if not isinstance(response.get("capabilityStatus"), dict):
            response["capabilityStatus"] = {}
        library = load_watch_library()
        for capability_id in requested_capability_ids:
            capability = by_id.get(capability_id)
            if not capability:
                status = combine_status(status, "WARN")
                missing.append(f"capability:{capability_id}")
                warnings.append(f"Capability is not registered: {capability_id}")
                continue
            compact_capability = dict(capability)
            if response_mode == "compact":
                compact_capability = {
                    key: capability.get(key)
                    for key in (
                        "id",
                        "status",
                        "runtimeStatus",
                        "availableNow",
                        "watchable",
                        "normalLiveAllowed",
                        "debugAuditOnly",
                        "missingReason",
                    )
                    if key in capability
                }
            response["capabilityStatus"][capability_id] = compact_capability
            if not capability.get("availableNow"):
                status = combine_status(status, "WARN")
                missing.append(f"capability:{capability_id}")
                suggestion = suggested_watch_for(capability_id, library)
                if suggestion:
                    suggested_watch_requests.append(suggestion)
                elif capability.get("missingReason"):
                    warnings.append(str(capability.get("missingReason")))
    if suggested_watch_requests:
        deduped_suggestions: list[dict] = []
        seen_suggestions: set[tuple[str, str]] = set()
        for suggestion in suggested_watch_requests:
            key = (str(suggestion.get("alias")), str(suggestion.get("id")))
            if key in seen_suggestions:
                continue
            seen_suggestions.add(key)
            deduped_suggestions.append(suggestion)
        response["suggestedWatchRequests"] = deduped_suggestions

    reachability_summary: dict[str, Any] = {}
    reachability_candidates: dict[str, list[dict]] = {}
    reachability_reports: dict[str, dict] = {}
    for class_id in requested_class_needs(needs, "reachability"):
        report = query.reachability_payload(scoped_context, class_id, args)
        compact_summary, items = compact_reachability_report(report, response_mode)
        if response_mode == "full":
            reachability_reports[class_id] = compact_summary
        else:
            reachability_summary[class_id] = compact_summary
            reachability_candidates[class_id] = items[:max_candidates]
        status = combine_status(status, report.get("status", "WARN"))
        warnings.extend(report.get("warnings") or [])
        missing.extend(report.get("missingCapabilities") or [])
        missing.extend(report.get("missingFields") or [])
    if reachability_summary:
        response["reachabilitySummary"] = reachability_summary
    if reachability_candidates:
        response["reachabilityCandidates"] = reachability_candidates
    if reachability_reports:
        response["reachabilityReports"] = reachability_reports

    best_candidates: dict[str, dict | None] = {}
    nearest_candidates: dict[str, dict | None] = {}
    for class_id in requested_class_needs(needs, "best"):
        if ":" in class_id:
            continue
        answer, answer_status, confidence, reasons, answer_warnings, answer_missing = select_class_candidate(scoped_context, class_id, "best", args)
        best_candidates[class_id] = compact_candidate_answer(answer, response_mode)
        status = combine_status(status, answer_status)
        confidence_values.append(confidence)
        warnings.extend(answer_warnings)
        missing.extend(answer_missing)
        if response_mode != "compact" and isinstance(best_candidates[class_id], dict):
            best_candidates[class_id]["reasons"] = reasons if best_candidates[class_id] else reasons
    for class_id in requested_class_needs(needs, "nearest"):
        if ":" in class_id:
            continue
        answer, answer_status, confidence, reasons, answer_warnings, answer_missing = select_class_candidate(scoped_context, class_id, "nearest", args)
        nearest_candidates[class_id] = compact_candidate_answer(answer, response_mode)
        status = combine_status(status, answer_status)
        confidence_values.append(confidence)
        warnings.extend(answer_warnings)
        missing.extend(answer_missing)
        if response_mode != "compact" and isinstance(nearest_candidates[class_id], dict):
            nearest_candidates[class_id]["reasons"] = reasons if nearest_candidates[class_id] else reasons
    if best_candidates:
        response["bestCandidates"] = best_candidates
    if nearest_candidates:
        response["nearestCandidates"] = nearest_candidates
    typed_best: dict[str, dict[str, Any]] = {}
    typed_nearest: dict[str, dict[str, Any]] = {}
    for kind, query_text in requested_typed_target_needs(needs, "best"):
        result = compact_target_helper(telemetry_model, kind=kind, query_text=query_text, mode="best", limit=max_candidates * 4)
        typed_best.setdefault(kind, {})[query_text] = result
        status = combine_status(status, result.get("status", "WARN"))
        warnings.extend(result.get("warnings") or [])
        missing.extend(result.get("missingFields") or [])
        candidate = result.get("candidate")
        if isinstance(candidate, dict) and isinstance(candidate.get("confidence"), (int, float)):
            confidence_values.append(float(candidate.get("confidence")))
    for kind, query_text in requested_typed_target_needs(needs, "nearest"):
        result = compact_target_helper(telemetry_model, kind=kind, query_text=query_text, mode="nearest", limit=max_candidates * 4)
        typed_nearest.setdefault(kind, {})[query_text] = result
        status = combine_status(status, result.get("status", "WARN"))
        warnings.extend(result.get("warnings") or [])
        missing.extend(result.get("missingFields") or [])
        candidate = result.get("candidate")
        if isinstance(candidate, dict) and isinstance(candidate.get("confidence"), (int, float)):
            confidence_values.append(float(candidate.get("confidence")))
    if typed_best:
        response["bestTargets"] = typed_best
    if typed_nearest:
        response["nearestTargets"] = typed_nearest
    if "aim_point" in needs:
        source = None
        if best_candidates:
            source = next((candidate for candidate in best_candidates.values() if candidate), None)
        if source is None and nearest_candidates:
            source = next((candidate for candidate in nearest_candidates.values() if candidate), None)
        if source is None and scoped_context.get("candidates"):
            answer, _answer_status, _confidence, _reasons, _answer_warnings, _answer_missing = query.candidate_answer(
                scoped_context["candidates"][0],
                scoped_context,
                args.freshness_ticks,
                args.freshness_ms,
            )
            source = answer
        response["aimPoint"] = source.get("aimPoint") if isinstance(source, dict) else None
        if response["aimPoint"] is None:
            status = combine_status(status, "WARN")
            missing.append("aimPoint")
            warnings.append("No aim point available in requested context.")
    if "task_summary" in needs or "task_summary:woodcutting" in needs or request.get("task"):
        task = str(request.get("task") or ("woodcutting" if "task_summary:woodcutting" in needs else "")).lower()
        if task == "woodcutting":
            task_payload = query.woodcutting_task_payload(scoped_context, args)
            response["taskSummary"] = query.compact_json_payload(task_payload, args) if response_mode == "compact" else task_payload
            status = combine_status(status, task_payload.get("status", "WARN"))
            warnings.extend(task_payload.get("warnings") or [])
            missing.extend(task_payload.get("missingFields") or [])
            missing.extend(task_payload.get("missingCapabilities") or [])
        elif task:
            status = combine_status(status, "FAIL")
            warnings.append(f"Unsupported task context: {task}")
            missing.append("task")

    if not scoped_context.get("baseline") and not scoped_context.get("status") and not scoped_context.get("candidates"):
        status = combine_status(status, "FAIL")

    response["status"] = status
    response["confidence"] = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else (0.7 if status == "PASS" else 0.4 if status == "WARN" else 0.0)
    response["warnings"] = sorted(set(str(warning) for warning in warnings if warning))
    response["missingCapabilities"] = sorted(set(str(field) for field in missing if field))
    status_doc = scoped_context.get("status") or {}
    if status_doc.get("livenessDegraded") or status_doc.get("livenessBudgetExceeded"):
        response["warnings"] = sorted(set(response["warnings"] + ["liveness degraded due to realtime budget; targetLiveState may be assumed."]))
        if response["status"] == "PASS":
            response["status"] = "WARN"
    if status_doc.get("livenessMode") == "off":
        response["missingCapabilities"] = sorted(set(response["missingCapabilities"] + ["realtimeLiveness"]))
    if status_doc.get("inputSourceActive") == "raw-ticks" and status_doc.get("inputSourceRequested") == "auto":
        response["warnings"] = sorted(
            set(response["warnings"] + ["live processor is using retired raw tick fallback; plugin-snapshot should be the live source."])
        )
    response["serviceTimingMillis"] = round((time.perf_counter() - started) * 1000.0, 3)
    return enforce_response_size(response, max_response_bytes, response_mode)


def enforce_response_size(payload: dict, max_bytes: int, response_mode: str) -> dict:
    if max_bytes <= 0 or len(json_bytes(payload)) <= max_bytes or response_mode == "full":
        return payload
    warnings = payload.setdefault("warnings", [])
    warnings.append(f"response exceeded {max_bytes} bytes; candidate arrays were truncated.")
    candidates = payload.get("candidates")
    if isinstance(candidates, dict) and isinstance(candidates.get("items"), list):
        candidates["items"] = candidates["items"][:1]
        candidates["truncated"] = True
    if len(json_bytes(payload)) > max_bytes:
        payload.pop("sourceFiles", None)
        warnings.append("sourceFiles omitted to keep compact response below size guard.")
    return payload


def health_payload(context: dict) -> dict:
    status_doc = context.get("status") or {}
    baseline = context.get("baseline") or {}
    latest = query.latest_tick(context)
    candidate = context.get("candidates", [None])[0] if context.get("candidates") else None
    freshness, freshness_warnings = query.freshness_info(context, candidate, query.DEFAULT_FRESHNESS_TICKS, query.DEFAULT_FRESHNESS_MS)
    warnings = list(context.get("warnings") or []) + freshness_warnings
    missing = context.get("missingFields") or []
    status = "ok"
    if missing:
        status = "warn"
    if not baseline and not status_doc:
        status = "fail"
    return {
        "schema": HEALTH_SCHEMA,
        "status": status,
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "latestTick": latest,
        "liveFilesPresent": not missing,
        "liveFreshness": freshness,
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
    }


def status_payload(context: dict) -> dict:
    status_doc = context.get("status") or {}
    context_index = context.get("context") or {}
    telemetry_discovery = telemetry_capabilities.capability_summary_from_context(context)
    return {
        "schema": STATUS_SCHEMA,
        "status": "ok" if status_doc else "warn",
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "latestTick": query.latest_tick(context),
        "latestExportSequence": telemetry_discovery.get("latest_export_sequence"),
        "sourceFreshness": telemetry_discovery.get("source_freshness"),
        "liveProcessorFreshness": health_payload(context).get("liveFreshness"),
        "activeProfile": status_doc.get("profile") or context_index.get("activeProfile"),
        "candidateCount": len(context.get("candidates") or []),
        "inputSourceActive": status_doc.get("inputSourceActive"),
        "compactPacketsAvailable": status_doc.get("compactPacketsAvailable"),
        "inputFallbackReason": status_doc.get("inputFallbackReason"),
        "recordingMode": status_doc.get("recordingMode"),
        "rawTickRecordingEnabled": status_doc.get("rawTickRecordingEnabled"),
        "rawEventRecordingEnabled": status_doc.get("rawEventRecordingEnabled"),
        "frameRecordingEnabled": status_doc.get("frameRecordingEnabled"),
        "sourceSceneKnowledgeComplete": status_doc.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status_doc.get("sourceCapHit"),
        "budgetExceeded": status_doc.get("budgetExceeded"),
        "writeFailures": status_doc.get("writeFailureCount"),
        "warnings": sorted(set(str(warning) for warning in context.get("warnings", []) if warning)),
        "cacheStats": context.get("cacheStats") or {},
    }


def schema_payload() -> dict:
    return {
        "schema": SCHEMA_SCHEMA,
        "supportedRequestSchemas": [REQUEST_SCHEMA, WATCH_REQUEST_SCHEMA],
        "supportedResponseSchemas": [RESPONSE_SCHEMA, HEALTH_SCHEMA, STATUS_SCHEMA, CAPABILITY_REGISTRY_SCHEMA, WATCH_LIBRARY_SCHEMA, WATCH_RESPONSE_SCHEMA, "manual_recordings_list.v1", "manual_recording_summary.v1", "manual_recording_schema_gap.v1"],
        "supportedNeeds": SUPPORTED_NEEDS,
        "supportedTasks": SUPPORTED_TASKS,
        "supportedResponseModes": SUPPORTED_RESPONSE_MODES,
        "supportedRequestOptions": ["maxCandidates", "maxEvents", "responseMode", "constraints", "maxAgeTicks", "maxAgeMillis"],
        "endpoints": {
            "GET": ["/health", "/schema", "/status", "/summary", "/capabilities", "/watches", "/recordings", "/recordings/<id>/summary", "/recordings/<id>/schema-gap"],
            "POST": ["/context", "/context/batch", "/watch-request"],
        },
        "notes": [
            "This service is read-only.",
            "Responses contain observations and readiness hints only.",
            "Watch requests are bounded, typed, TTL-limited, and observation-only.",
            "No action, click, menu, mouse, or keyboard endpoints are implemented.",
        ],
    }


def error_payload(message: str, status: str = "FAIL", request_id: Any = None) -> dict:
    return {
        "schema": RESPONSE_SCHEMA,
        "requestId": request_id,
        "generatedAtUtc": utc_now(),
        "latestTick": None,
        "status": status,
        "freshness": {},
        "warnings": [message],
        "missingCapabilities": [],
        "sourceFiles": [],
        "serviceTimingMillis": 0.0,
    }


class ContextState:
    def __init__(self, args):
        self.args = args
        session, session_warnings = resolve_session(args.session, args.latest_session, args.sessions_dir)
        self.session_warnings = session_warnings
        self.cache = LiveContextCache(session, args.reload_interval)
        self.max_candidates = args.max_candidates
        self.max_response_bytes = args.max_response_bytes
        self.auth_token = args.auth_token if not args.no_auth_token else None
        self.debug = args.debug
        self.compact_include_source_files = bool(args.compact_include_source_files)
        self.compact_include_liveness_examples = max(0, int(args.compact_include_liveness_examples))

    def load_context(self, force: bool = False) -> dict:
        context = self.cache.load(force=force)
        if self.session_warnings:
            context = dict(context)
            context["warnings"] = self.session_warnings + list(context.get("warnings") or [])
        return context


class ContextRequestHandler(BaseHTTPRequestHandler):
    server_version = "OSRSTelemetryContextService/0.1"

    def log_message(self, format, *args):  # noqa: A002
        if getattr(self.server.context_state, "debug", False):
            super().log_message(format, *args)

    def do_GET(self):  # noqa: N802
        if not self.authorized():
            self.send_json(error_payload("missing or invalid X-Context-Token"), status_code=401)
            return
        context = self.server.context_state.load_context()
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/health":
            self.send_json(health_payload(context))
        elif path == "/schema":
            self.send_json(schema_payload())
        elif path == "/status":
            self.send_json(status_payload(context))
        elif path == "/summary":
            self.handle_summary(context, params)
        elif path == "/capabilities":
            self.send_json(capabilities_payload(context))
        elif path == "/watches":
            self.send_json(watches_payload(context))
        elif path == "/recordings":
            self.send_json(list_recordings_payload())
        elif path.startswith("/recordings/") and path.endswith("/summary"):
            recording_id = path[len("/recordings/") : -len("/summary")].strip("/")
            self.send_json(recording_summary_payload(recording_id))
        elif path.startswith("/recordings/") and path.endswith("/schema-gap"):
            recording_id = path[len("/recordings/") : -len("/schema-gap")].strip("/")
            self.send_json(recording_schema_gap_payload(recording_id))
        else:
            self.send_json(error_payload(f"unknown endpoint: {self.path}"), status_code=404)

    def do_POST(self):  # noqa: N802
        if not self.authorized():
            self.send_json(error_payload("missing or invalid X-Context-Token"), status_code=401)
            return
        if self.path not in {"/context", "/context/batch", "/watch-request"}:
            self.send_json(error_payload(f"unknown endpoint: {self.path}"), status_code=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json(error_payload(f"invalid JSON request: {exc}"), status_code=400)
            return
        state = self.server.context_state
        context = state.load_context()
        if self.path == "/watch-request":
            self.send_json(handle_watch_request_payload(context, payload))
        elif self.path == "/context/batch":
            if not isinstance(payload, list):
                self.send_json(error_payload("/context/batch expects a JSON list"), status_code=400)
                return
            responses = [handle_context_request(context, item, state) for item in payload]
            self.send_json(responses)
        else:
            response = handle_context_request(context, payload, state)
            self.send_json(response)

    def authorized(self) -> bool:
        token = self.server.context_state.auth_token
        if not token:
            return True
        return self.headers.get("X-Context-Token") == token

    def send_json(self, payload: Any, status_code: int = 200) -> None:
        data = json_bytes(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str, status_code: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_summary(self, context: dict, params: dict[str, list[str]]) -> None:
        task = (params.get("task") or ["woodcutting"])[0] or "woodcutting"
        response_format = ((params.get("format") or ["text"])[0] or "text").lower()
        try:
            top = max(1, int((params.get("top") or ["3"])[0]))
        except ValueError:
            top = 3
        request = {
            "schema": REQUEST_SCHEMA,
            "task": task,
            "needs": [
                "baseline",
                "best:tree",
                "nearest:tree",
                "reachability:tree",
                "inventory",
                "activity",
                "liveness",
                "events",
                "navigation_readiness",
                "diagnostics",
                "task_summary",
            ],
            "maxCandidates": top,
            "maxEvents": top,
            "responseMode": "compact",
        }
        response = build_context_response(
            context,
            request,
            default_max_candidates=self.server.context_state.max_candidates,
            max_response_bytes=self.server.context_state.max_response_bytes,
            compact_include_source_files=self.server.context_state.compact_include_source_files,
            compact_liveness_examples=self.server.context_state.compact_include_liveness_examples,
        )
        if response_format == "json":
            self.send_json(response)
            return
        self.send_text(format_context_human(response, compact=False, top=top))


def handle_context_request(context: dict, payload: Any, state: ContextState) -> dict:
    if not isinstance(payload, dict):
        return error_payload("context request must be a JSON object")
    if payload.get("schema") != REQUEST_SCHEMA:
        return error_payload(f"unsupported schema: {payload.get('schema')}", request_id=payload.get("requestId"))
    return build_context_response(
        context,
        payload,
        default_max_candidates=state.max_candidates,
        max_response_bytes=state.max_response_bytes,
        compact_include_source_files=state.compact_include_source_files,
        compact_liveness_examples=state.compact_include_liveness_examples,
    )


def serve(args) -> int:
    if not is_local_host(args.host):
        if not args.allow_nonlocal_host:
            print("Refusing to bind to a non-local host without --allow-nonlocal-host.")
            return 2
        print("WARNING: non-local host binding requested. This sidecar exposes read-only telemetry context.")
        if not args.auth_token and not args.no_auth_token:
            print("Refusing non-local host without --auth-token or explicit --no-auth-token.")
            return 2
    state = ContextState(args)
    if state.cache.session is None:
        print("No session selected. Use --session or --latest-session.")
        return 2
    server = ThreadingHTTPServer((args.host, args.port), ContextRequestHandler)
    server.context_state = state
    print(f"Read-only context service listening on http://{args.host}:{args.port}")
    print(f"session: {state.cache.session}")
    print("endpoints: GET /health /schema /status /capabilities /watches /recordings, POST /context /context/batch /watch-request")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("context service stopped")
    finally:
        server.server_close()
    return 0


def oneshot(args) -> int:
    state = ContextState(args)
    context = state.load_context(force=True)
    try:
        payload = json.loads(args.oneshot_request)
    except json.JSONDecodeError as exc:
        print(json.dumps(error_payload(f"invalid JSON request: {exc}"), separators=(",", ":")))
        return 1
    response = handle_context_request(context, payload, state)
    print(json.dumps(response, separators=(",", ":"), sort_keys=False))
    return 0 if response.get("status") != "FAIL" else 1


def fabric_for_cli(args) -> knowledge_fabric.KnowledgeFabric:
    if getattr(args, "context_json", None):
        try:
            payload = json.loads(Path(args.context_json).read_text(encoding="utf-8-sig"))
        except Exception as error:  # noqa: BLE001
            return knowledge_fabric.KnowledgeFabric.from_status(
                {"schema": "context_json_load_error.v1", "status": "FAIL", "warnings": [f"{type(error).__name__}: {error}"]}
            )
        return knowledge_fabric.KnowledgeFabric.from_status(payload)
    if args.session or args.latest_session:
        state = ContextState(args)
        context = state.load_context(force=True)
        return knowledge_fabric.KnowledgeFabric.from_status(context)
    return knowledge_fabric.fabric_from_live(
        daemon_url=args.daemon_url,
        snapshot_url=args.snapshot_url,
        timeout=args.live_timeout,
        include_projection=True,
        include_collision=True,
        max_objects=args.world_max_objects,
    )


def print_json_response(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False, default=str))
    return 0 if payload.get("status") != "FAIL" else 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_output(args: list[str], *, max_lines: int = 24) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception as error:  # noqa: BLE001
        return [f"unavailable: {type(error).__name__}"]
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return []
    return output.splitlines()[:max_lines]


def _one_line(value: Any, *, max_chars: int = 220) -> str:
    if value is None or value == "":
        return "unknown"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    sensitive_terms = ("password", "passwd", "token", "secret", "api_key", "apikey", "authorization", "credential")
    lowered = text.lower()
    if any(term in lowered for term in sensitive_terms):
        return "[REDACTED]"
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _bullet_lines(items: list[str], *, empty: str = "none", max_items: int = 12) -> list[str]:
    safe_items = [_one_line(item, max_chars=260) for item in items[:max_items] if str(item).strip()]
    if not safe_items:
        return [f"- {empty}"]
    lines = [f"- {item}" for item in safe_items]
    if len(items) > max_items:
        lines.append(f"- ... {len(items) - max_items} more omitted")
    return lines


def _world_location_text(location: Any) -> str:
    if not isinstance(location, dict):
        return "unknown"
    world = location.get("worldLocation") if isinstance(location.get("worldLocation"), dict) else location
    x = world.get("worldX")
    y = world.get("worldY")
    plane = world.get("plane", location.get("plane", 0))
    if x is None or y is None:
        return "unknown"
    return f"{x},{y},{plane}"


def _format_chatgpt_handoff(args, fabric: knowledge_fabric.KnowledgeFabric) -> str:
    handoff = fabric.handoff_summary()
    handoff_data = handoff.get("data") if isinstance(handoff.get("data"), dict) else {}
    blocker = fabric.explain_current_blocker()
    blocker_data = blocker.get("data") if isinstance(blocker.get("data"), dict) else {}
    debug = fabric.query_current_debug_context(profile=getattr(args, "profile", "woodcutting"), limit=min(3, int(args.max_candidates or 3)))
    debug_data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
    live_status = debug_data.get("liveStatus") if isinstance(debug_data.get("liveStatus"), dict) else {}
    phase = live_status.get("phase") if isinstance(live_status.get("phase"), dict) else {}
    readiness = debug_data.get("readiness") if isinstance(debug_data.get("readiness"), dict) else {}
    action_readiness = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}

    branch = _git_output(["branch", "--show-current"], max_lines=1)
    commit = _git_output(["log", "--oneline", "-1"], max_lines=1)
    status_lines = _git_output(["status", "--short"], max_lines=20)
    changed_files = status_lines or ["working tree clean"]

    current_blocker = blocker_data.get("primaryBlockerSummary") or (handoff_data.get("currentBlocker") or {}).get("summary")
    recommended_next = blocker_data.get("recommendedNextStep") or (handoff_data.get("currentBlocker") or {}).get("recommendedNextStep")
    question = getattr(args, "handoff_question", None) or "Given this bounded evidence, what is the safest next debugging or implementation step?"
    tests_run = getattr(args, "handoff_tests_run", None) or "Not inferred by helper; include recent focused test results before sending if they matter."

    lines = [
        "PASTE_TO_CHATGPT:",
        "Context:",
        f"- Repo: {_repo_root()}",
        f"- Branch: {_one_line(branch[0] if branch else 'unknown')}",
        f"- Commit: {_one_line(commit[0] if commit else 'unknown')}",
        f"- Source: {_one_line(fabric.source)}",
        f"- Session: {_one_line((fabric.freshness() or {}).get('sessionPath'))}",
        f"- Phase/intent: {_one_line(phase.get('phase'))} / {_one_line(phase.get('currentIntent') or phase.get('activeIntent') or blocker_data.get('currentIntent'))}",
        f"- Location: {_world_location_text(live_status.get('location'))}",
        f"- Inventory: {_one_line(live_status.get('inventory'))}",
        "What I tried:",
        "- Local tools first: Knowledge Fabric handoff summary, current-debug-context, and current-blocker.",
        "- No secrets, massive logs, screenshots, live sessions, or full JSON dumps are included.",
        "Evidence:",
        f"- current-debug-context status: {_one_line(debug.get('status'))}",
        f"- current-blocker status: {_one_line(blocker.get('status'))}",
        f"- blocker category: {_one_line(blocker_data.get('primaryBlockerCategory'))}",
        f"- safeToRunBoundedLiveAction: {_one_line(blocker_data.get('safeToRunBoundedLiveAction'))}",
        f"- action execution allowed: {_one_line(action_readiness.get('executionAllowed'))}",
        f"- routeContextApplicable: {_one_line(readiness.get('routeContextApplicable') if readiness else blocker_data.get('routeContextApplicable'))}",
        f"- staleRouteContextSuppressed: {_one_line(readiness.get('staleRouteContextSuppressed') if readiness else blocker_data.get('staleRouteContextSuppressed'))}",
        "Files changed:",
        *_bullet_lines(changed_files, empty="working tree clean"),
        "Tests run:",
        f"- {_one_line(tests_run, max_chars=500)}",
        "Current blocker:",
        f"- {_one_line(current_blocker, max_chars=500)}",
        "Specific question:",
        f"- {_one_line(question, max_chars=500)}",
        "Options I\u2019m considering:",
        f"- Run the recommended local query: {_one_line(handoff_data.get('recommendedNextDiagnosticQuery'))}",
        f"- Follow the blocker recommendation: {_one_line(recommended_next)}",
        "- Pause if this requires user preference, credentials, or a safety/input decision.",
        "My recommended next step:",
        f"- {_one_line(recommended_next or handoff_data.get('recommendedNextCodingTarget'), max_chars=500)}",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _arg_was_supplied(option: str, argv: list[str] | None = None) -> bool:
    values = sys.argv[1:] if argv is None else argv
    return any(value == option or value.startswith(option + "=") for value in values)


def _daemon_query_error(fabric: knowledge_fabric.KnowledgeFabric) -> str | None:
    status = fabric.daemon_status if isinstance(fabric.daemon_status, dict) else {}
    if not status:
        return "daemon /status returned an empty payload"
    if status.get("schema") == "http_fetch_error.v1" or status.get("status") == "FAIL":
        return str(status.get("error") or status.get("message") or "daemon /status request failed")
    return None


def _live_daemon_status_gaps(fabric: knowledge_fabric.KnowledgeFabric) -> tuple[list[str], list[str]]:
    status = fabric.daemon_status if isinstance(fabric.daemon_status, dict) else {}
    warnings: list[str] = []
    missing: list[str] = []
    if not status.get("sessionPath"):
        warnings.append("daemon /status did not include sessionPath")
        missing.append("daemon.sessionPath")
    if status.get("latestTick") is None:
        warnings.append("daemon /status did not include latestTick")
        missing.append("daemon.latestTick")
    return warnings, missing


def _add_live_query_metadata(
    response: dict[str, Any],
    *,
    context_source: str,
    daemon_url: str | None = None,
    snapshot_url: str | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    daemon_query_error: str | None = None,
) -> dict[str, Any]:
    response["sourceUsed"] = context_source
    response["contextSource"] = context_source
    response["daemonUrl"] = daemon_url
    response["snapshotUrl"] = snapshot_url
    response["fileSessionFallbackUsed"] = fallback_used
    response["freshnessSource"] = "daemon_status+plugin_snapshot" if context_source == "live_daemon" else context_source
    if fallback_reason:
        response["fileSessionFallbackReason"] = fallback_reason
    if daemon_query_error:
        response["daemonQueryError"] = daemon_query_error
    return response


def _status_from_payloads(payloads: list[dict[str, Any]]) -> str:
    status = "PASS"
    for payload in payloads:
        if isinstance(payload, dict):
            status = combine_status(status, str(payload.get("status") or "PASS"))
    return status


def _named_query_prefers_live_daemon(args, needs: list[str]) -> bool:
    if getattr(args, "daemon_url_explicit", False):
        return True
    if getattr(args, "session", None) or getattr(args, "latest_session", False):
        return False
    return any(str(need).startswith("knowledge_") for need in needs)


def build_live_named_query_response(args, query_name: str, needs: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    daemon_url = str(getattr(args, "daemon_url", None) or DEFAULT_DAEMON_URL)
    snapshot_url = str(getattr(args, "snapshot_url", None) or DEFAULT_SNAPSHOT_URL)
    max_candidates = int(getattr(args, "max_candidates", 3) or 3)
    max_response_bytes = int(getattr(args, "max_response_bytes", 1_000_000) or 1_000_000)
    try:
        fabric = knowledge_fabric.fabric_from_live(
            daemon_url=daemon_url,
            snapshot_url=snapshot_url,
            timeout=getattr(args, "live_timeout", 1.0),
            include_projection=True,
            include_collision=True,
            max_objects=getattr(args, "world_max_objects", 160),
        )
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"

    daemon_error = _daemon_query_error(fabric)
    if daemon_error:
        return None, daemon_error
    status_warnings, status_missing = _live_daemon_status_gaps(fabric)

    payloads: list[dict[str, Any]] = []
    response: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "requestId": None,
        "generatedAtUtc": query.utc_now(),
        "latestTick": fabric.daemon_status.get("latestTick"),
        "status": "PASS",
        "freshness": fabric.freshness(),
        "warnings": list(status_warnings),
        "missingCapabilities": list(status_missing),
        "sourceFilesSummary": {
            "allRequiredPresent": True,
            "missingFiles": [],
            "staleFiles": [],
            "liveFilesPresent": False,
            "fileCount": 0,
        },
    }
    _add_live_query_metadata(
        response,
        context_source="live_daemon",
        daemon_url=daemon_url,
        snapshot_url=snapshot_url,
        fallback_used=False,
    )

    if "knowledge_fabric_status" in needs:
        payload = fabric.status()
        response["knowledgeFabricStatus"] = payload
        payloads.append(payload)
    if "knowledge_current_debug_context" in needs:
        payload = fabric.query_current_debug_context(profile=getattr(args, "profile", "woodcutting"), limit=max_candidates)
        response["knowledgeCurrentDebugContext"] = payload
        payloads.append(payload)
    if "knowledge_current_blocker" in needs:
        payload = fabric.explain_current_blocker()
        response["knowledgeCurrentBlocker"] = payload
        payloads.append(payload)
    if "knowledge_navigation_decision_trace" in needs:
        payload = fabric.query_navigation_decision_trace(limit=max_candidates)
        response["knowledgeNavigationDecisionTrace"] = payload
        payloads.append(payload)
    if "knowledge_data_quality_report" in needs:
        payload = fabric.data_quality_report(limit=max_candidates)
        response["knowledgeDataQualityReport"] = payload
        payloads.append(payload)
    if "knowledge_data_source_inventory" in needs:
        payload = fabric.data_source_inventory()
        response["knowledgeDataSourceInventory"] = payload
        payloads.append(payload)
    if "knowledge_query_coverage_matrix" in needs:
        payload = fabric.query_coverage_matrix()
        response["knowledgeQueryCoverageMatrix"] = payload
        payloads.append(payload)
    if "knowledge_coverage_report" in needs:
        payload = fabric.coverage_report(limit=max_candidates)
        response["knowledgeCoverageReport"] = payload
        payloads.append(payload)
    if "knowledge_task_script_runtime_evidence" in needs:
        payload = fabric.query_task_script_runtime_evidence()
        response["knowledgeTaskScriptRuntimeEvidence"] = payload
        payloads.append(payload)
    if "knowledge_task_failure_classification" in needs:
        payload = fabric.classify_task_failure()
        response["knowledgeTaskFailureClassification"] = payload
        payloads.append(payload)
    if "knowledge_task_step_readiness" in needs:
        payload = fabric.assess_task_script_step()
        response["knowledgeTaskStepReadiness"] = payload
        payloads.append(payload)
    if "knowledge_task_run_readiness" in needs:
        payload = fabric.assess_task_script_run()
        response["knowledgeTaskRunReadiness"] = payload
        payloads.append(payload)
    if "external_knowledge_status" in needs:
        payload = external_knowledge.knowledge_status()
        response["externalKnowledgeStatus"] = payload
        payloads.append(payload)
    if "knowledge_handoff_summary" in needs:
        payload = fabric.handoff_summary()
        response["knowledgeHandoffSummary"] = payload
        payloads.append(payload)

    response["status"] = _status_from_payloads(payloads)
    response["confidence"] = 0.7 if response["status"] == "PASS" else 0.4 if response["status"] == "WARN" else 0.0
    warnings: list[str] = list(response["warnings"])
    missing: list[str] = list(response["missingCapabilities"])
    for payload in payloads:
        warnings.extend(str(item) for item in (payload.get("warnings") or []) if item)
        missing.extend(str(item) for item in (payload.get("missingCapabilities") or []) if item)
    response["warnings"] = sorted(set(warnings))
    response["missingCapabilities"] = sorted(set(missing))
    response["serviceTimingMillis"] = round(sum(float((payload.get("performanceStats") or {}).get("queryTimeMs") or 0.0) for payload in payloads), 3)
    return enforce_response_size(response, max_response_bytes, "compact"), None


def build_file_named_query_response(
    args,
    needs: list[str],
    *,
    context_source: str,
    daemon_query_error: str | None = None,
) -> dict[str, Any]:
    state = ContextState(args)
    context = state.load_context(force=True)
    response = build_context_response(
        context,
        {
            "schema": REQUEST_SCHEMA,
            "needs": needs,
            "maxCandidates": args.max_candidates,
            "responseMode": "compact",
        },
        default_max_candidates=args.max_candidates,
        max_response_bytes=args.max_response_bytes,
        compact_include_source_files=args.compact_include_source_files,
        compact_liveness_examples=args.compact_include_liveness_examples,
    )
    fallback_used = context_source == "file_session_fallback"
    return _add_live_query_metadata(
        response,
        context_source=context_source,
        daemon_url=getattr(args, "daemon_url", None),
        snapshot_url=getattr(args, "snapshot_url", None),
        fallback_used=fallback_used,
        fallback_reason="live daemon query failed; using file-session context" if fallback_used else None,
        daemon_query_error=daemon_query_error,
    )


def build_named_query_response(args, query_name: str, needs: list[str]) -> dict[str, Any]:
    if _named_query_prefers_live_daemon(args, needs):
        live_response, daemon_query_error = build_live_named_query_response(args, query_name, needs)
        if live_response is not None:
            return live_response
        return build_file_named_query_response(
            args,
            needs,
            context_source="file_session_fallback",
            daemon_query_error=daemon_query_error,
        )
    return build_file_named_query_response(args, needs, context_source="file_session")


def capture_script_authoring_context_cli(args) -> int:
    fabric = fabric_for_cli(args)
    payload = fabric.capture_script_authoring_context(
        profile=args.profile,
        task_name=args.task_name,
        reason=args.reason,
        limit=args.max_candidates,
    )
    return print_json_response(payload)


def capture_replay_scenario_cli(args) -> int:
    fabric = fabric_for_cli(args)
    payload = fabric.capture_replay_scenario(
        profile=args.profile,
        reason=args.reason,
        limit=args.max_candidates,
    )
    return print_json_response(payload)


def replay_scenario_cli(args) -> int:
    return print_json_response(knowledge_fabric.replay_scenario(args.replay_scenario, limit=args.max_candidates))


def diff_debug_context_cli(args) -> int:
    return print_json_response(knowledge_fabric.diff_debug_context(args.diff_debug_context[0], args.diff_debug_context[1]))


def handoff_summary_json_cli(args) -> int:
    return print_json_response(fabric_for_cli(args).handoff_summary())


def handoff_summary_cli(args) -> int:
    fabric = fabric_for_cli(args)
    print(_format_chatgpt_handoff(args, fabric), end="")
    return 0


def data_quality_report_cli(args) -> int:
    return print_json_response(fabric_for_cli(args).data_quality_report(limit=args.max_candidates))


def data_source_inventory_cli(args) -> int:
    return print_json_response(fabric_for_cli(args).data_source_inventory())


def query_coverage_matrix_cli(args) -> int:
    return print_json_response(fabric_for_cli(args).query_coverage_matrix())


def coverage_report_cli(args) -> int:
    return print_json_response(fabric_for_cli(args).coverage_report(limit=args.max_candidates))


def probe_task_cli(args) -> int:
    return print_json_response(
        fabric_for_cli(args).probe_task(
            args.probe_task,
            profile=args.profile,
            limit=args.max_candidates,
            capture_bundle=bool(args.probe_task_capture),
        )
    )


def external_knowledge_status_cli(args) -> int:
    return print_json_response(external_knowledge.knowledge_status())


def external_lookup_item_id_cli(args) -> int:
    return print_json_response(external_knowledge.lookup_item_id(args.external_lookup_item_id))


def external_search_item_cli(args) -> int:
    return print_json_response(external_knowledge.search_item(args.external_search_item, limit=args.max_candidates))


def external_lookup_object_cli(args) -> int:
    return print_json_response(external_knowledge.lookup_object(args.external_lookup_object))


def external_get_skill_requirement_cli(args) -> int:
    return print_json_response(external_knowledge.get_skill_requirement(args.external_get_skill_requirement))


def external_lookup_area_cli(args) -> int:
    return print_json_response(external_knowledge.lookup_area(args.external_lookup_area))


def external_search_wiki_cli(args) -> int:
    return print_json_response(
        external_knowledge.search_wiki(
            args.external_search_wiki,
            allow_refresh=bool(args.external_refresh),
            limit=args.max_candidates,
        )
    )


def external_refresh_item_map_cli(args) -> int:
    return print_json_response(external_knowledge.refresh_item_map(limit=args.external_refresh_limit))


def _snapshot_health_url(snapshot_url: str) -> str:
    text = str(snapshot_url or "").strip() or "http://127.0.0.1:8893/snapshot"
    if text.endswith("/snapshot"):
        return text[: -len("/snapshot")] + "/health"
    return text.rstrip("/") + "/health"


def pipeline_health_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    manifest = safe_load_json(PIPELINE_MANIFEST_PATH, {"schema": "osrs_telemetry_pipeline_manifest.v1", "components": []})
    config_keys = safe_load_json(CONFIG_KEYS_PATH, {"schema": "osrs_telemetry_config_keys.v1"})
    components = [item for item in manifest.get("components") or [] if isinstance(item, dict)]
    active_components = [item.get("id") for item in components if item.get("active") is True]
    disabled_components = [item.get("id") for item in components if item.get("active") is False]
    sessions_root = get_sessions_dir(getattr(args, "sessions_dir", None))

    try:
        import maintenance

        legacy_report = maintenance.live_packets_report(sessions_root, top=10)
    except Exception as error:  # noqa: BLE001
        legacy_report = {
            "schema": "legacy_live_packets_report_error.v1",
            "status": "FAIL",
            "error": f"{type(error).__name__}: {error}",
            "legacyLivePacketFilesPresent": None,
            "legacyLivePacketTotalMb": None,
            "livePacketsRuntimeRemoved": True,
            "ndjsonRuntimeRemoved": True,
            "jsonlRuntimeRemoved": True,
            "livePacketWriterActive": False,
        }

    daemon_health = knowledge_fabric.fetch_json(str(args.daemon_url).rstrip("/") + "/health", timeout=getattr(args, "live_timeout", 1.0))
    daemon_status = knowledge_fabric.fetch_json(str(args.daemon_url).rstrip("/") + "/status", timeout=getattr(args, "live_timeout", 1.0))
    snapshot_health = knowledge_fabric.fetch_json(_snapshot_health_url(str(args.snapshot_url)), timeout=getattr(args, "live_timeout", 1.0))
    recommendations: list[str] = []
    if legacy_report.get("legacyLivePacketFilesPresent"):
        recommendations.append("Run maintenance.py --prune-legacy-live-packets --dry-run, then --apply only if you approve deletion.")
    if daemon_health.get("status") == "FAIL":
        recommendations.append("Start or rebind live_core_daemon.py on 8890 before live validation.")
    if snapshot_health.get("status") == "FAIL":
        recommendations.append("Enable/recover the RuneLite plugin snapshot endpoint on 8893 before live validation.")
    if not recommendations:
        recommendations.append("No immediate cleanup step is required.")

    payload = {
        "schema": PIPELINE_HEALTH_SCHEMA,
        "status": "WARN" if daemon_health.get("status") == "FAIL" or snapshot_health.get("status") == "FAIL" or legacy_report.get("legacyLivePacketFilesPresent") else "PASS",
        "activeComponents": active_components,
        "disabledRemovedComponents": disabled_components,
        "componentCount": len(components),
        "manifestPath": str(PIPELINE_MANIFEST_PATH),
        "configKeyRegistryPath": str(CONFIG_KEYS_PATH),
        "configUi": {
            "configGroup": config_keys.get("configGroup"),
            "activeExposedKeys": config_keys.get("activeExposedKeys") or [],
            "developerHiddenKeys": config_keys.get("developerHiddenKeys") or [],
            "retiredKeys": config_keys.get("retiredKeys") or [],
            "migration": config_keys.get("migration") or {},
        },
        "staleConfigKeys": config_keys.get("retiredKeys") or [],
        "unknownFilesOrModules": [
            "Historical offline JSONL tooling remains for review; it is not the retired live_packets runtime archive.",
        ],
        "legacyLivePackets": legacy_report,
        "livePacketsRuntimeRemoved": True,
        "ndjsonRuntimeRemoved": True,
        "jsonlRuntimeRemoved": True,
        "livePacketWriterActive": False,
        "directQueryStatus": {
            "daemonHealth": daemon_health,
            "daemonStatusSummary": {
                "status": daemon_status.get("status"),
                "schema": daemon_status.get("schema"),
                "gameState": daemon_status.get("gameState") or (daemon_status.get("baseline") or {}).get("gameState"),
                "latestTick": daemon_status.get("latestTick"),
                "sessionPath": daemon_status.get("sessionPath"),
            },
            "snapshotHealth": snapshot_health,
        },
        "arduinoStatus": {
            "status": "NOT_CHECKED",
            "reason": "pipeline-health is read-only and does not open COM ports",
        },
        "recommendedCleanupSteps": recommendations,
        "queryTimeMs": round((time.perf_counter() - started) * 1000.0, 3),
        "capHit": False,
        "truncated": False,
        "objectCount": len(components),
        "sourceAgeMs": 0,
    }
    payload["responseBytes"] = len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))
    return payload


def pipeline_health_cli(args) -> int:
    return print_json_response(pipeline_health_payload(args))


def _write_jsonl_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=False, default=str))
            handle.write("\n")


def _recovery_click_attempts(payload: dict[str, Any], command: list[str]) -> list[dict[str, Any]]:
    machine = _dict(payload.get("recoveryStateMachine"))
    classification = _dict(machine.get("failureClassification"))
    attempts = _list(machine.get("clickAttempts"))
    records: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts):
        item = _dict(attempt)
        records.append(
            {
                "schema": "loaded_scene_recovery_attempt.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": item.get("attemptIndex", index),
                "command": command,
                "status": payload.get("status"),
                "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
                "blocker": payload.get("blocker"),
                "recoveryFailureClass": classification.get("failureClass"),
                "stateBefore": item.get("stateBefore"),
                "selectedRecoveryAction": item.get("selectedRecoveryAction"),
                "clickEvidence": item.get("clickEvidence"),
                "stateAfter": item.get("stateAfter"),
                "transitionSuccess": bool(item.get("transitionSuccess")),
                "reason": item.get("reason") or classification.get("reason"),
            }
        )
    for index, attempt in enumerate(_list(machine.get("relaunchAttempts")), start=len(records)):
        item = _dict(attempt)
        records.append(
            {
                "schema": "loaded_scene_recovery_attempt.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": item.get("attemptIndex", index),
                "command": command,
                "status": payload.get("status"),
                "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
                "blocker": payload.get("blocker"),
                "recoveryFailureClass": payload.get("recoveryFailureClass"),
                "stateBefore": item.get("stateBefore"),
                "selectedRecoveryAction": item.get("selectedRecoveryAction"),
                "startGameCommand": item.get("startGameCommand"),
                "startGameCommandSource": item.get("startGameCommandSource"),
                "launchMode": item.get("launchMode"),
                "relaunchResult": item.get("relaunchResult"),
                "stateAfter": item.get("stateAfter"),
                "transitionSuccess": bool(item.get("transitionSuccess")),
                "reason": item.get("reason") or classification.get("reason"),
            }
        )
    if not records:
        records.append(
            {
                "schema": "loaded_scene_recovery_attempt.v1",
                "generatedAtUtc": utc_now(),
                "attemptIndex": 0,
                "command": command,
                "status": payload.get("status"),
                "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
                "blocker": payload.get("blocker"),
                "recoveryFailureClass": payload.get("recoveryFailureClass"),
                "initialState": _dict(payload.get("initialState")),
                "finalState": _dict(payload.get("finalState")),
                "actionsTaken": _list(payload.get("actionsTaken")),
                "warnings": _list(payload.get("warnings")),
            }
        )
    return records


def write_loaded_scene_recovery_artifacts(args, payload: dict[str, Any]) -> dict[str, str]:
    root = Path(getattr(args, "recovery_artifact_dir", "") or Path(__file__).resolve().parents[1] / "bot_runs" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_loaded_scene_recovery")
    root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve())] + sys.argv[1:]
    latest_state = {
        "schema": "loaded_scene_latest_recovery_state.v1",
        "generatedAtUtc": utc_now(),
        "status": payload.get("status"),
        "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
        "blocker": payload.get("blocker"),
        "recoveryFailureClass": payload.get("recoveryFailureClass"),
        "recoveryFailureReason": payload.get("recoveryFailureReason"),
        "disconnectedLoopDetected": bool(payload.get("disconnectedLoopDetected")),
        "relaunchRequired": bool(payload.get("relaunchRequired")),
        "relaunchAttempted": bool(payload.get("relaunchAttempted")),
        "launchMode": payload.get("launchMode"),
        "launchModeReason": payload.get("launchModeReason"),
        "launchModeWarnings": payload.get("launchModeWarnings") or [],
        "relaunchCommand": payload.get("relaunchCommand"),
        "startGameCommand": payload.get("startGameCommand"),
        "startGameCommandSource": payload.get("startGameCommandSource"),
        "relaunchResult": payload.get("relaunchResult"),
        "relaunchSucceeded": bool(payload.get("relaunchSucceeded")),
        "loadedSceneAfterRelaunch": bool(payload.get("loadedSceneAfterRelaunch")),
        "loginScreenAfterRelaunch": bool(payload.get("loginScreenAfterRelaunch")),
        "finalHotGameState": payload.get("finalHotGameState"),
        "finalLoadedSceneVerified": bool(payload.get("finalLoadedSceneVerified")),
        "finalWorldObjectCount": payload.get("finalWorldObjectCount"),
        "finalTick": payload.get("finalTick"),
        "finalExportSeq": payload.get("finalExportSeq"),
        "initialState": _dict(payload.get("initialState")),
        "finalState": _dict(payload.get("finalState")),
        "recoveryStateMachine": _dict(payload.get("recoveryStateMachine")),
    }
    summary = {
        "schema": "loaded_scene_recovery_summary.v1",
        "generatedAtUtc": utc_now(),
        "status": payload.get("status"),
        "loadedSceneVerified": bool(payload.get("loadedSceneVerified")),
        "blocker": payload.get("blocker"),
        "rawBlocker": payload.get("rawBlocker"),
        "recoveryFailureClass": payload.get("recoveryFailureClass"),
        "recoveryFailureReason": payload.get("recoveryFailureReason"),
        "disconnectedLoopDetected": bool(payload.get("disconnectedLoopDetected")),
        "relaunchRequired": bool(payload.get("relaunchRequired")),
        "relaunchAttempted": bool(payload.get("relaunchAttempted")),
        "launchMode": payload.get("launchMode"),
        "launchModeReason": payload.get("launchModeReason"),
        "launchModeWarnings": payload.get("launchModeWarnings") or [],
        "relaunchCommand": payload.get("relaunchCommand"),
        "startGameCommand": payload.get("startGameCommand"),
        "startGameCommandSource": payload.get("startGameCommandSource"),
        "relaunchResult": payload.get("relaunchResult"),
        "relaunchSucceeded": bool(payload.get("relaunchSucceeded")),
        "loadedSceneAfterRelaunch": bool(payload.get("loadedSceneAfterRelaunch")),
        "loginScreenAfterRelaunch": bool(payload.get("loginScreenAfterRelaunch")),
        "finalHotGameState": payload.get("finalHotGameState"),
        "finalLoadedSceneVerified": bool(payload.get("finalLoadedSceneVerified")),
        "finalWorldObjectCount": payload.get("finalWorldObjectCount"),
        "finalTick": payload.get("finalTick"),
        "finalExportSeq": payload.get("finalExportSeq"),
        "manualActionRequired": payload.get("manualActionRequired"),
        "nextRecommendation": payload.get("nextRecommendation"),
        "command": command,
        "payload": payload,
    }
    latest_path = root / "latest_recovery_state.json"
    summary_path = root / "recovery_summary.json"
    attempts_path = root / "recovery_attempts.jsonl"
    atomic_write_json(latest_path, latest_state)
    atomic_write_json(summary_path, summary)
    _write_jsonl_records(attempts_path, _recovery_click_attempts(payload, command))
    return {
        "folder": str(root),
        "latestRecoveryState": str(latest_path),
        "recoverySummary": str(summary_path),
        "recoveryAttempts": str(attempts_path),
    }


def ensure_loaded_scene_cli(args) -> int:
    import liveness_recovery_core

    payload = liveness_recovery_core.ensure_loaded_scene(
        daemon_url=args.daemon_url,
        snapshot_url=args.snapshot_url,
        backend="arduino",
        arduino_port=args.arduino_port,
        max_total_ms=int(max(1.0, float(args.liveness_max_total_seconds or 120.0)) * 1000.0),
        max_attempts_per_state=max(1, int(args.liveness_max_attempts_per_state or 2)),
        allow_jagex_launcher=bool(args.allow_jagex_launcher_automation),
        allow_credentials=False,
    )
    if not bool(getattr(args, "no_recovery_artifacts", False)):
        payload["recoveryArtifacts"] = write_loaded_scene_recovery_artifacts(args, payload)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0 if payload.get("status") in {"loaded_scene_ready", "recovered_loaded_scene"} else 1


def query_oneshot(args) -> int:
    query_name = str(args.query or "").strip().lower().replace("_", "-")
    if query_name == "pipeline-health":
        return print_json_response(pipeline_health_payload(args))
    needs = NAMED_QUERY_NEEDS.get(query_name)
    if needs is None:
        print(json.dumps(error_payload(f"unsupported query: {args.query}"), separators=(",", ":")))
        return 2
    response = build_named_query_response(args, query_name, needs)
    print(json.dumps(response, separators=(",", ":"), sort_keys=False))
    return 0 if response.get("status") != "FAIL" else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only local context request/response sidecar for live telemetry files.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest telemetry session.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8890, help="Bind port. Default: 8890.")
    parser.add_argument("--reload-interval", type=float, default=0.5, help="Minimum seconds between live file stat checks. Default: 0.5.")
    parser.add_argument("--max-candidates", type=int, default=3, help="Default maximum candidates per response.")
    parser.add_argument("--compact", action="store_true", help="Prefer compact responses unless request asks otherwise.")
    parser.add_argument("--debug", action="store_true", help="Enable HTTP request logging.")
    parser.add_argument("--allow-nonlocal-host", action="store_true", help="Allow binding to a host other than localhost/127.0.0.1.")
    parser.add_argument("--auth-token", help="Require X-Context-Token header for requests.")
    parser.add_argument("--no-auth-token", action="store_true", help="Explicitly run without an auth token.")
    parser.add_argument("--oneshot-request", help="Process one context_request.v1 JSON string and exit.")
    parser.add_argument("--query", help="Run a named compact query such as current-debug-context, current-blocker, navigation-decision-trace, task-script-run-readiness, or knowledge-fabric-status and exit.")
    parser.add_argument("--capture-script-authoring-context", action="store_true", help="Capture script_authoring_context.v1 bundle and exit.")
    parser.add_argument("--capture-replay-scenario", action="store_true", help="Capture replay_scenario.v1 bundle and exit.")
    parser.add_argument("--replay-scenario", help="Replay a replay_scenario.v1 file offline and exit.")
    parser.add_argument("--diff-debug-context", nargs=2, metavar=("BUNDLE_A", "BUNDLE_B"), help="Diff two debug context/replay/script-authoring bundles and exit.")
    parser.add_argument("--handoff-summary", action="store_true", help="Print a redacted PASTE_TO_CHATGPT consultation handoff block and exit.")
    parser.add_argument("--handoff-summary-json", action="store_true", help="Print machine-readable knowledge_fabric_handoff_summary.v1 JSON and exit.")
    parser.add_argument("--handoff-question", help="Specific question to include in the --handoff-summary PASTE_TO_CHATGPT block.")
    parser.add_argument("--handoff-tests-run", help="Short test result summary to include in the --handoff-summary PASTE_TO_CHATGPT block.")
    parser.add_argument("--data-quality-report", action="store_true", help="Print data_quality_report.v1 and exit.")
    parser.add_argument("--data-source-inventory", action="store_true", help="Print data_source_inventory.v1 and exit.")
    parser.add_argument("--query-coverage-matrix", action="store_true", help="Print query_coverage_matrix.v1 and exit.")
    parser.add_argument("--coverage-report", action="store_true", help="Print coverage_report.v1 and exit.")
    parser.add_argument("--pipeline-health", action="store_true", help="Print pipeline_health.v1 and exit.")
    parser.add_argument("--ensure-loaded-scene", action="store_true", help="Recover known RuneLite liveness states, verify loaded scene, and rebind daemon.")
    parser.add_argument("--probe-task", help="Run read-only task probe for a task description and exit.")
    parser.add_argument("--probe-task-capture", action="store_true", help="Capture a script_authoring_context bundle during --probe-task.")
    parser.add_argument("--external-knowledge-status", action="store_true", help="Print external_knowledge_status.v1 and exit.")
    parser.add_argument("--external-lookup-item-id", help="Cache-first advisory external item-id lookup and exit.")
    parser.add_argument("--external-search-item", help="Cache-first advisory external item name search and exit.")
    parser.add_argument("--external-search-wiki", help="Cache-first OSRS Wiki search. Use --external-refresh to call the API explicitly.")
    parser.add_argument("--external-lookup-object", help="Cache-first advisory external object lookup and exit.")
    parser.add_argument("--external-get-skill-requirement", help="Cache-first advisory skill requirement lookup and exit.")
    parser.add_argument("--external-lookup-area", help="Cache-first advisory area/location lookup and exit.")
    parser.add_argument("--external-refresh", action="store_true", help="Allow explicit external API refresh for external lookup commands that support it.")
    parser.add_argument("--external-refresh-item-map", action="store_true", help="Explicitly refresh OSRS Wiki price item mapping cache and exit.")
    parser.add_argument("--external-refresh-limit", type=int, help="Optional item mapping refresh row limit for validation/tests.")
    parser.add_argument("--profile", default="woodcutting", help="Profile for Knowledge Fabric bundle/query commands. Default: woodcutting.")
    parser.add_argument("--task-name", help="Task name for script-authoring bundle commands.")
    parser.add_argument("--reason", help="Reason label for captured bundle commands.")
    parser.add_argument("--daemon-url", default=DEFAULT_DAEMON_URL, help="Live daemon URL for live Knowledge Fabric CLI commands.")
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL, help="Plugin snapshot URL for live Knowledge Fabric CLI commands.")
    parser.add_argument("--arduino-port", default="COM6", help="Arduino serial bridge port used by --ensure-loaded-scene.")
    parser.add_argument("--allow-jagex-launcher-automation", action="store_true", help="Allow Jagex Launcher automation for --ensure-loaded-scene; credentials are still never typed.")
    parser.add_argument("--liveness-max-total-seconds", type=float, default=120.0, help="Maximum total seconds for --ensure-loaded-scene.")
    parser.add_argument("--liveness-max-attempts-per-state", type=int, default=2, help="Maximum attempts per known liveness state for --ensure-loaded-scene.")
    parser.add_argument("--recovery-artifact-dir", help="Optional output folder for --ensure-loaded-scene recovery artifacts.")
    parser.add_argument("--no-recovery-artifacts", action="store_true", help="Do not write recovery artifacts for --ensure-loaded-scene.")
    parser.add_argument("--context-json", help="Use a saved context/status JSON file for Knowledge Fabric CLI commands.")
    parser.add_argument("--live-timeout", type=float, default=1.0, help="HTTP timeout for live Knowledge Fabric CLI commands.")
    parser.add_argument("--world-max-objects", type=int, default=160, help="Max world objects requested by live Knowledge Fabric CLI commands.")
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000, help="Compact response size guard. Default: 1000000.")
    parser.add_argument("--compact-include-source-files", action="store_true", help="Include full sourceFiles even for compact responses.")
    parser.add_argument("--compact-include-liveness-examples", type=int, default=0, help="Recently unavailable examples to include in compact liveness responses. Default: 0.")
    args = parser.parse_args()
    args.daemon_url_explicit = _arg_was_supplied("--daemon-url")
    return args


def main() -> int:
    args = parse_args()
    if args.capture_script_authoring_context:
        return capture_script_authoring_context_cli(args)
    if args.capture_replay_scenario:
        return capture_replay_scenario_cli(args)
    if args.replay_scenario:
        return replay_scenario_cli(args)
    if args.diff_debug_context:
        return diff_debug_context_cli(args)
    if args.handoff_summary_json:
        return handoff_summary_json_cli(args)
    if args.handoff_summary:
        return handoff_summary_cli(args)
    if args.data_quality_report:
        return data_quality_report_cli(args)
    if args.data_source_inventory:
        return data_source_inventory_cli(args)
    if args.query_coverage_matrix:
        return query_coverage_matrix_cli(args)
    if args.coverage_report:
        return coverage_report_cli(args)
    if args.pipeline_health:
        return pipeline_health_cli(args)
    if args.ensure_loaded_scene:
        return ensure_loaded_scene_cli(args)
    if args.probe_task:
        return probe_task_cli(args)
    if args.external_knowledge_status:
        return external_knowledge_status_cli(args)
    if args.external_lookup_item_id:
        return external_lookup_item_id_cli(args)
    if args.external_search_item:
        return external_search_item_cli(args)
    if args.external_search_wiki:
        return external_search_wiki_cli(args)
    if args.external_lookup_object:
        return external_lookup_object_cli(args)
    if args.external_get_skill_requirement:
        return external_get_skill_requirement_cli(args)
    if args.external_lookup_area:
        return external_lookup_area_cli(args)
    if args.external_refresh_item_map:
        return external_refresh_item_map_cli(args)
    if args.query:
        return query_oneshot(args)
    if args.oneshot_request:
        return oneshot(args)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())

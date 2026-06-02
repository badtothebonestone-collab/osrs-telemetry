from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace
from typing import Any

import context_service
import external_knowledge
import external_knowledge_cache
import knowledge_fabric
import task_script_api


SERVER_NAME = "osrs-telemetry-knowledge-fabric"
SERVER_VERSION = "0.1.0"
DEFAULT_DAEMON_URL = "http://127.0.0.1:8890"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8893/snapshot"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=False, default=str)


def tool_definitions() -> list[dict[str, Any]]:
    string = {"type": "string"}
    number = {"type": "number"}
    integer = {"type": "integer"}
    location_schema = {
        "type": "object",
        "properties": {
            "worldX": integer,
            "worldY": integer,
            "plane": integer,
        },
    }
    return [
        {
            "name": "get_live_status",
            "description": "Read the current 8890 daemon status.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string}},
        },
        {
            "name": "get_world_model_summary",
            "description": "Query the live plugin world-model summary through 8893.",
            "inputSchema": {"type": "object", "properties": {"snapshotUrl": string, "maxObjects": integer}},
        },
        {
            "name": "get_knowledge_fabric_status",
            "description": "Build indexes from live daemon/plugin data and return fabric status.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string, "maxObjects": integer}},
        },
        {
            "name": "get_current_debug_context",
            "description": "Return the compact aggregate live context Codex should query first for debugging.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string, "maxObjects": integer},
            },
        },
        {
            "name": "query_resource_candidates",
            "description": "Return compact live resource candidates with level-gating and projection context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": string,
                    "location": location_schema,
                    "limit": integer,
                    "daemonUrl": string,
                    "snapshotUrl": string,
                },
            },
        },
        {
            "name": "query_service_candidates",
            "description": "Return live, session, and static service candidates. Static/session anchors are advisory.",
            "inputSchema": {"type": "object", "properties": {"serviceType": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "query_route_objects",
            "description": "Return live route-transition/service-route objects independent of resource caps.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "query_path_frontier",
            "description": "Return compact collision/frontier data toward an optional goal.",
            "inputSchema": {"type": "object", "properties": {"goal": location_schema, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "query_view_quality",
            "description": "Return projection/view-quality inputs for a route/resource/service intent.",
            "inputSchema": {"type": "object", "properties": {"intent": string, "goal": location_schema, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "query_navigation_decision_trace",
            "description": "Summarize navigation_decision_trace.v1 records from the latest action trace or supplied records/actionTrace. Read-only; no trace writer or live input.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "records": {"type": "array", "items": {"type": "object"}},
                    "actionTrace": {"type": "object"},
                    "limit": integer,
                    "daemonUrl": string,
                    "snapshotUrl": string,
                },
            },
        },
        {
            "name": "explain_current_blocker",
            "description": "Explain the current live blocker from daemon/fabric status.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_latest_action_trace",
            "description": "Return latest action-trace entries indexed from the active session.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_action_input_visibility",
            "description": "Return Codex-visible planned action, target, coordinate conversion, hover/click proof, HumanInputController, Arduino/input-integrity phase evidence, and readiness context. Read-only; no raw input tools are exposed.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_latest_visual_bundle",
            "description": "Return latest visual debug bundle summaries indexed from the active session.",
            "inputSchema": {"type": "object", "properties": {"reason": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "search_session_memory",
            "description": "Search current-session memory. Results are advisory and not executable by themselves.",
            "inputSchema": {"type": "object", "properties": {"kind": string, "contains": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "search_static_library",
            "description": "Search static routes, target classes, profiles, skill requirements, and service anchors.",
            "inputSchema": {"type": "object", "properties": {"search": string, "limit": integer}},
        },
        {
            "name": "list_available_profiles",
            "description": "List known task/target profiles from the static library.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "describe_profile",
            "description": "Describe one target profile and its target classes.",
            "inputSchema": {"type": "object", "properties": {"profile": string, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_target_classes",
            "description": "List target classes, optionally scoped to a profile.",
            "inputSchema": {"type": "object", "properties": {"profile": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_known_actions",
            "description": "List known actions for static target classes.",
            "inputSchema": {"type": "object", "properties": {"targetClass": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_service_routes",
            "description": "List static service routes, optionally scoped to a profile.",
            "inputSchema": {"type": "object", "properties": {"profile": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "describe_route",
            "description": "Describe a static service route and current live route evidence.",
            "inputSchema": {"type": "object", "properties": {"routeId": string, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "explain_required_telemetry_for_task",
            "description": "Explain which telemetry is needed to automate a task safely.",
            "inputSchema": {"type": "object", "properties": {"taskName": string, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "query_scene_for_new_task_keywords",
            "description": "Search the loaded scene by object names/actions to help author a new task.",
            "inputSchema": {"type": "object", "properties": {"keywords": {"type": ["string", "array"]}, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "suggest_profile_skeleton_from_scene",
            "description": "Suggest a review-required profile skeleton from loaded-scene evidence.",
            "inputSchema": {"type": "object", "properties": {"description": string, "keywords": {"type": ["string", "array"]}, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_objects_by_action",
            "description": "List loaded-scene objects with an action string.",
            "inputSchema": {"type": "object", "properties": {"action": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_objects_by_name",
            "description": "List loaded-scene objects whose name contains text.",
            "inputSchema": {"type": "object", "properties": {"nameContains": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "export_task_context_bundle",
            "description": "Export a compact read-only bundle for future script/profile authoring.",
            "inputSchema": {"type": "object", "properties": {"profile": string, "task": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "capture_script_authoring_context",
            "description": "Capture a compact local script-authoring evidence bundle. No input or clicks are exposed.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": string, "taskName": string, "reason": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string},
            },
        },
        {
            "name": "capture_replay_scenario",
            "description": "Capture an offline replay scenario for candidate/readiness/blocker debugging. No live input is replayed.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": string, "reason": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string},
            },
        },
        {
            "name": "replay_scenario",
            "description": "Replay a previously captured scenario offline without live input.",
            "inputSchema": {"type": "object", "properties": {"path": string, "limit": integer}},
        },
        {
            "name": "get_data_quality_report",
            "description": "Report freshness, cap/truncation, missing section, and confidence information for current live data.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_data_source_inventory",
            "description": "Return the source inventory for live, debug, replay, static, and external OSRS data.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_query_coverage_matrix",
            "description": "Return the query coverage matrix showing how Codex answers common live/script-authoring questions.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_coverage_report",
            "description": "Report whether data needed for the current intent is present, stale, capped, or missing.",
            "inputSchema": {"type": "object", "properties": {"intent": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_pipeline_health",
            "description": "Report the current official pipeline, retired components, config UI keys, and legacy live-packet disk status.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string, "timeout": number}},
        },
        {
            "name": "probe_task",
            "description": "Read-only task probe for future script/profile authoring. It does not expose input execution.",
            "inputSchema": {"type": "object", "properties": {"taskDescription": string, "profile": string, "limit": integer, "captureBundle": {"type": "boolean"}, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "get_task_script_api_spec",
            "description": "Return the high-level task script API contract, allowed primitives, safety policies, and woodcut_bank example.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "validate_task_script",
            "description": "Validate a high-level task script without live input or raw click/key exposure.",
            "inputSchema": {"type": "object", "properties": {"script": {"type": ["object", "string"]}}},
        },
        {
            "name": "compile_task_script",
            "description": "Compile a high-level task script into existing profile/action proposal intents. Read-only; no live input.",
            "inputSchema": {"type": "object", "properties": {"script": {"type": ["object", "string"]}}},
        },
        {
            "name": "explain_script_plan",
            "description": "Explain the compiled task script plan, evidence gates, canonical input pipeline, and failure classifications.",
            "inputSchema": {"type": "object", "properties": {"script": {"type": ["object", "string"]}}},
        },
        {
            "name": "get_task_script_evidence_plan",
            "description": "Return the read-only variable/change evidence plan a task script must prove during replay or bounded live validation.",
            "inputSchema": {"type": "object", "properties": {"script": {"type": ["object", "string"]}}},
        },
        {
            "name": "get_task_script_runtime_evidence",
            "description": "Return current read-only runtime values for task evidence variables such as inventory, resourceCount, bankOpen, hover, click, location, route progress, and phase/intent.",
            "inputSchema": {"type": "object", "properties": {"script": {"type": ["object", "string"]}, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "compare_task_script_runtime_evidence",
            "description": "Compare before/after task runtime evidence snapshots and report which live variables changed, plus input-integrity blockers. Read-only; no live input.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "before": {"type": "object"},
                    "after": {"type": "object"},
                    "script": {"type": ["object", "string"]},
                    "primitive": string,
                },
            },
        },
        {
            "name": "classify_task_failure",
            "description": "Classify current or supplied evidence into the before-patching failure taxonomy. Read-only; labels operator injected events separately from live-action hard blockers.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "evidence": {"type": "object"},
                    "currentBlocker": {"type": "object"},
                    "debugContext": {"type": "object"},
                    "runtimeEvidence": {"type": "object"},
                    "comparison": {"type": "object"},
                    "actionInputVisibility": {"type": "object"},
                    "actionTrace": {"type": "object"},
                    "externalKnowledge": {"type": "object"},
                    "errorText": string,
                    "daemonUrl": string,
                    "snapshotUrl": string,
                },
            },
        },
        {
            "name": "suggest_task_template",
            "description": "Suggest a high-level task script template such as woodcut_bank. Read-only; external facts remain advisory.",
            "inputSchema": {"type": "object", "properties": {"taskDescription": string, "profile": string}},
        },
        {
            "name": "probe_task_from_scene",
            "description": "Probe the loaded scene and suggest a high-level task template. Read-only; live truth remains RuneLite/8893/WorldModel/8890.",
            "inputSchema": {"type": "object", "properties": {"taskDescription": string, "profile": string, "limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_widgets",
            "description": "List compact widget/dialogue/bank UI evidence observed in daemon status.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_inventory_items",
            "description": "List compact inventory item evidence observed in daemon status.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_npcs",
            "description": "List compact NPC evidence observed in the loaded scene.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "list_seen_ground_items",
            "description": "List compact ground-item evidence observed in the loaded scene.",
            "inputSchema": {"type": "object", "properties": {"limit": integer, "daemonUrl": string, "snapshotUrl": string}},
        },
        {
            "name": "external_knowledge_status",
            "description": "Return external OSRS knowledge cache/source status. No live input is exposed.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "external_lookup_item_id",
            "description": "Cache-first advisory lookup for an OSRS item id.",
            "inputSchema": {"type": "object", "properties": {"itemId": {"type": ["integer", "string"]}}},
        },
        {
            "name": "external_lookup_item",
            "description": "Cache-first advisory item-name search.",
            "inputSchema": {"type": "object", "properties": {"name": string, "limit": integer}},
        },
        {
            "name": "external_search_wiki",
            "description": "Cache-first OSRS Wiki search. External refresh requires the explicit allowRefresh flag.",
            "inputSchema": {"type": "object", "properties": {"query": string, "allowRefresh": {"type": "boolean"}, "limit": integer}},
        },
        {
            "name": "external_lookup_object",
            "description": "Cache-first advisory object lookup.",
            "inputSchema": {"type": "object", "properties": {"name": string}},
        },
        {
            "name": "external_lookup_npc",
            "description": "Cache-first advisory NPC lookup.",
            "inputSchema": {"type": "object", "properties": {"name": string}},
        },
        {
            "name": "external_lookup_area",
            "description": "Cache-first advisory area/location lookup.",
            "inputSchema": {"type": "object", "properties": {"name": string}},
        },
        {
            "name": "external_get_skill_requirement",
            "description": "Cache-first advisory skill requirement lookup.",
            "inputSchema": {"type": "object", "properties": {"name": string}},
        },
        {
            "name": "diff_debug_context",
            "description": "Compare two current-debug-context, script-authoring, or replay bundles.",
            "inputSchema": {"type": "object", "properties": {"bundleA": string, "bundleB": string}},
        },
        {
            "name": "get_handoff_summary",
            "description": "Return a concise ready-to-paste handoff for the current live state.",
            "inputSchema": {"type": "object", "properties": {"daemonUrl": string, "snapshotUrl": string}},
        },
    ]


def resource_definitions() -> list[dict[str, Any]]:
    return [
        {"uri": "osrs://live/status", "name": "Live status", "mimeType": "application/json"},
        {"uri": "osrs://live/world-model-summary", "name": "World model summary", "mimeType": "application/json"},
        {"uri": "osrs://live/knowledge-fabric-status", "name": "Knowledge Fabric status", "mimeType": "application/json"},
        {"uri": "osrs://live/current-debug-context", "name": "Current debug context", "mimeType": "application/json"},
        {"uri": "osrs://live/current-blocker", "name": "Current blocker explanation", "mimeType": "application/json"},
        {"uri": "osrs://debug/current-context", "name": "Current debug context", "mimeType": "application/json"},
        {"uri": "osrs://debug/blocker", "name": "Current blocker explanation", "mimeType": "application/json"},
        {"uri": "osrs://debug/action-input-visibility", "name": "Action/input visibility context", "mimeType": "application/json"},
        {"uri": "osrs://debug/navigation-decision-trace", "name": "Navigation decision trace summary", "mimeType": "application/json"},
        {"uri": "osrs://debug/latest-script-authoring-context", "name": "Latest script authoring context bundle", "mimeType": "application/json"},
        {"uri": "osrs://debug/latest-replay-scenario", "name": "Latest replay scenario", "mimeType": "application/json"},
        {"uri": "osrs://debug/data-quality-report", "name": "Data quality report", "mimeType": "application/json"},
        {"uri": "osrs://debug/coverage", "name": "Coverage report", "mimeType": "application/json"},
        {"uri": "osrs://debug/pipeline-health", "name": "Pipeline health", "mimeType": "application/json"},
        {"uri": "osrs://live/route-context", "name": "Route context", "mimeType": "application/json"},
        {"uri": "osrs://live/resource-candidates", "name": "Resource candidates", "mimeType": "application/json"},
        {"uri": "osrs://live/service-candidates", "name": "Service candidates", "mimeType": "application/json"},
        {"uri": "osrs://session/memory", "name": "Session memory", "mimeType": "application/json"},
        {"uri": "osrs://session/observations", "name": "Session observations", "mimeType": "application/json"},
        {"uri": "osrs://debug/latest-bundle", "name": "Latest visual debug bundles", "mimeType": "application/json"},
        {"uri": "osrs://library/routes", "name": "Static service routes", "mimeType": "application/json"},
        {"uri": "osrs://library/targets", "name": "Static target library", "mimeType": "application/json"},
        {"uri": "osrs://library/profiles", "name": "Static target profiles", "mimeType": "application/json"},
        {"uri": "osrs://library/actions", "name": "Known target actions", "mimeType": "application/json"},
        {"uri": "osrs://library/data-sources", "name": "Data source inventory", "mimeType": "application/json"},
        {"uri": "osrs://library/query-coverage", "name": "Query coverage matrix", "mimeType": "application/json"},
        {"uri": "osrs://script-api/spec", "name": "High-level task script API spec", "mimeType": "application/json"},
        {"uri": "osrs://script-api/woodcut-bank-example", "name": "woodcut_bank task script example", "mimeType": "application/json"},
        {"uri": "osrs://script-api/woodcut-bank-evidence-plan", "name": "woodcut_bank runtime evidence plan", "mimeType": "application/json"},
        {"uri": "osrs://script-api/runtime-evidence", "name": "Current task runtime evidence", "mimeType": "application/json"},
        {"uri": "osrs://script-api/failure-classification", "name": "Current task failure classification", "mimeType": "application/json"},
        {"uri": "osrs://external/items", "name": "External item lookup cache", "mimeType": "application/json"},
        {"uri": "osrs://external/item-map", "name": "External item ID/name map", "mimeType": "application/json"},
        {"uri": "osrs://external/wiki-cache", "name": "External wiki cache status", "mimeType": "application/json"},
        {"uri": "osrs://external/skill-requirements", "name": "External skill requirements", "mimeType": "application/json"},
        {"uri": "osrs://external/source-status", "name": "External source status", "mimeType": "application/json"},
    ]


def _fabric(args: dict[str, Any]) -> knowledge_fabric.KnowledgeFabric:
    return knowledge_fabric.fabric_from_live(
        daemon_url=str(args.get("daemonUrl") or DEFAULT_DAEMON_URL),
        snapshot_url=str(args.get("snapshotUrl") or DEFAULT_SNAPSHOT_URL),
        max_objects=int(args.get("maxObjects") or 160),
        include_projection=bool(args.get("includeProjection", False)),
        include_collision=bool(args.get("includeCollision", False)),
        timeout=float(args.get("timeout") or 1.0),
    )


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = _dict(arguments)
    try:
        if name == "get_live_status":
            payload = knowledge_fabric.fetch_json(str(args.get("daemonUrl") or DEFAULT_DAEMON_URL).rstrip("/") + "/status")
        elif name == "get_world_model_summary":
            payload = _fabric(args).query_world_summary()
        elif name == "get_knowledge_fabric_status":
            payload = _fabric(args).status()
        elif name == "get_current_debug_context":
            payload = _fabric(args).query_current_debug_context(
                profile=str(args.get("profile") or "woodcutting"),
                limit=args.get("limit"),
            )
        elif name == "query_resource_candidates":
            payload = _fabric(args).query_resource_candidates(
                profile=str(args.get("profile") or "woodcutting"),
                location=_dict(args.get("location")) or None,
                limit=args.get("limit"),
            )
        elif name == "query_service_candidates":
            payload = _fabric(args).query_service_candidates(service_type=str(args.get("serviceType") or "bank"), limit=args.get("limit"))
        elif name == "query_route_objects":
            payload = _fabric(args).query_route_objects(limit=args.get("limit"))
        elif name == "query_path_frontier":
            payload = _fabric(args).query_path_frontier(goal=_dict(args.get("goal")) or None, limit=args.get("limit"))
        elif name == "query_view_quality":
            payload = _fabric(args).query_view_quality(intent=str(args.get("intent") or "unknown"), goal=_dict(args.get("goal")) or None)
        elif name == "query_navigation_decision_trace":
            payload = _fabric(args).query_navigation_decision_trace(
                records=args.get("records") if isinstance(args.get("records"), list) else None,
                action_trace=_dict(args.get("actionTrace")) or None,
                limit=args.get("limit"),
            )
        elif name == "explain_current_blocker":
            payload = _fabric(args).explain_current_blocker()
        elif name == "get_latest_action_trace":
            fabric = _fabric(args)
            payload = {
                "schema": "knowledge_fabric_latest_action_trace.v1",
                "data": fabric.debug_evidence.get("latestActionTraces", []),
                "source": "debug_evidence_index",
            }
        elif name == "get_action_input_visibility":
            payload = _fabric(args).query_action_input_visibility()
        elif name == "get_latest_visual_bundle":
            payload = _fabric(args).query_debug_evidence(reason=args.get("reason"), limit=args.get("limit"))
        elif name == "search_session_memory":
            payload = _fabric(args).query_session_memory(
                kind=args.get("kind"),
                filters={"contains": args.get("contains")} if args.get("contains") else {},
                limit=args.get("limit"),
            )
        elif name == "search_static_library":
            payload = knowledge_fabric.query_static_library(search=args.get("search"), limit=args.get("limit"))
        elif name == "list_available_profiles":
            payload = _fabric(args).list_available_profiles(limit=args.get("limit"))
        elif name == "describe_profile":
            payload = _fabric(args).describe_profile(str(args.get("profile") or "woodcutting"))
        elif name == "list_target_classes":
            payload = _fabric(args).list_target_classes(profile=args.get("profile"), limit=args.get("limit"))
        elif name == "list_known_actions":
            payload = _fabric(args).list_known_actions(target_class=args.get("targetClass"), limit=args.get("limit"))
        elif name == "list_service_routes":
            payload = _fabric(args).list_service_routes(profile=args.get("profile"), limit=args.get("limit"))
        elif name == "describe_route":
            payload = _fabric(args).describe_route(str(args.get("routeId") or ""))
        elif name == "explain_required_telemetry_for_task":
            payload = _fabric(args).explain_required_telemetry_for_task(str(args.get("taskName") or "woodcutting"))
        elif name == "query_scene_for_new_task_keywords":
            payload = _fabric(args).query_scene_for_new_task_keywords(args.get("keywords") or "", limit=args.get("limit"))
        elif name == "suggest_profile_skeleton_from_scene":
            payload = _fabric(args).suggest_profile_skeleton_from_scene(
                description=args.get("description"),
                keywords=args.get("keywords"),
            )
        elif name == "list_seen_objects_by_action":
            payload = _fabric(args).list_seen_objects_by_action(str(args.get("action") or ""), limit=args.get("limit"))
        elif name == "list_seen_objects_by_name":
            payload = _fabric(args).list_seen_objects_by_name(str(args.get("nameContains") or ""), limit=args.get("limit"))
        elif name == "export_task_context_bundle":
            payload = _fabric(args).export_task_context_bundle(profile=args.get("profile"), task=args.get("task"), limit=args.get("limit"))
        elif name == "capture_script_authoring_context":
            payload = _fabric(args).capture_script_authoring_context(
                profile=str(args.get("profile") or "woodcutting"),
                task_name=args.get("taskName"),
                reason=args.get("reason"),
                limit=args.get("limit"),
            )
        elif name == "capture_replay_scenario":
            payload = _fabric(args).capture_replay_scenario(
                profile=str(args.get("profile") or "woodcutting"),
                reason=args.get("reason"),
                limit=args.get("limit"),
            )
        elif name == "replay_scenario":
            payload = knowledge_fabric.replay_scenario(str(args.get("path") or ""), limit=args.get("limit"))
        elif name == "get_data_quality_report":
            payload = _fabric(args).data_quality_report(limit=args.get("limit"))
        elif name == "get_data_source_inventory":
            payload = _fabric(args).data_source_inventory()
        elif name == "get_query_coverage_matrix":
            payload = _fabric(args).query_coverage_matrix()
        elif name == "get_coverage_report":
            payload = _fabric(args).coverage_report(intent=args.get("intent"), limit=args.get("limit"))
        elif name == "get_pipeline_health":
            payload = context_service.pipeline_health_payload(
                SimpleNamespace(
                    daemon_url=str(args.get("daemonUrl") or DEFAULT_DAEMON_URL),
                    snapshot_url=str(args.get("snapshotUrl") or DEFAULT_SNAPSHOT_URL),
                    live_timeout=float(args.get("timeout") or 1.0),
                    sessions_dir=args.get("sessionsDir"),
                )
            )
        elif name == "probe_task":
            payload = _fabric(args).probe_task(
                str(args.get("taskDescription") or ""),
                profile=str(args.get("profile") or "woodcutting"),
                limit=args.get("limit"),
                capture_bundle=bool(args.get("captureBundle", False)),
            )
        elif name == "get_task_script_api_spec":
            payload = task_script_api.script_api_spec()
        elif name == "validate_task_script":
            payload = task_script_api.validate_task_script(args.get("script") or {})
        elif name == "compile_task_script":
            payload = task_script_api.compile_task_script(args.get("script") or {})
        elif name == "explain_script_plan":
            payload = task_script_api.explain_script_plan(args.get("script") or {})
        elif name == "get_task_script_evidence_plan":
            payload = task_script_api.build_task_script_evidence_plan(args.get("script") or task_script_api.woodcut_bank_template())
        elif name == "get_task_script_runtime_evidence":
            payload = _fabric(args).query_task_script_runtime_evidence(args.get("script") or task_script_api.woodcut_bank_template())
        elif name == "compare_task_script_runtime_evidence":
            payload = task_script_api.compare_task_runtime_evidence_snapshots(
                _dict(args.get("before")),
                _dict(args.get("after")),
                script=args.get("script"),
                primitive=args.get("primitive"),
            )
        elif name == "classify_task_failure":
            payload = _fabric(args).classify_task_failure(
                _dict(args.get("evidence")) or None,
                current_blocker=_dict(args.get("currentBlocker")) or None,
                debug_context=_dict(args.get("debugContext")) or None,
                runtime_evidence=_dict(args.get("runtimeEvidence")) or None,
                comparison=_dict(args.get("comparison")) or None,
                action_input_visibility=_dict(args.get("actionInputVisibility")) or None,
                action_trace=_dict(args.get("actionTrace")) or None,
                external_knowledge=_dict(args.get("externalKnowledge")) or None,
                error_text=args.get("errorText"),
            )
        elif name == "suggest_task_template":
            payload = task_script_api.suggest_task_template(args.get("taskDescription"), profile=args.get("profile"))
        elif name == "probe_task_from_scene":
            payload = _fabric(args).probe_task_from_scene(
                str(args.get("taskDescription") or ""),
                profile=str(args.get("profile") or "woodcutting"),
                limit=args.get("limit"),
            )
        elif name == "list_seen_widgets":
            payload = _fabric(args).list_seen_widgets(limit=args.get("limit"))
        elif name == "list_seen_inventory_items":
            payload = _fabric(args).list_seen_inventory_items(limit=args.get("limit"))
        elif name == "list_seen_npcs":
            payload = _fabric(args).list_seen_npcs(limit=args.get("limit"))
        elif name == "list_seen_ground_items":
            payload = _fabric(args).list_seen_ground_items(limit=args.get("limit"))
        elif name == "external_knowledge_status":
            payload = external_knowledge.knowledge_status()
        elif name == "external_lookup_item_id":
            payload = external_knowledge.lookup_item_id(args.get("itemId"))
        elif name == "external_lookup_item":
            payload = external_knowledge.search_item(str(args.get("name") or ""), limit=int(args.get("limit") or 10))
        elif name == "external_search_wiki":
            payload = external_knowledge.search_wiki(
                str(args.get("query") or ""),
                allow_refresh=bool(args.get("allowRefresh", False)),
                limit=int(args.get("limit") or 5),
            )
        elif name == "external_lookup_object":
            payload = external_knowledge.lookup_object(str(args.get("name") or ""))
        elif name == "external_lookup_npc":
            payload = external_knowledge.lookup_npc(str(args.get("name") or ""))
        elif name == "external_lookup_area":
            payload = external_knowledge.lookup_area(str(args.get("name") or ""))
        elif name == "external_get_skill_requirement":
            payload = external_knowledge.get_skill_requirement(str(args.get("name") or ""))
        elif name == "diff_debug_context":
            payload = knowledge_fabric.diff_debug_context(str(args.get("bundleA") or ""), str(args.get("bundleB") or ""))
        elif name == "get_handoff_summary":
            payload = _fabric(args).handoff_summary()
        else:
            payload = {"schema": "mcp_tool_error.v1", "status": "FAIL", "error": f"unknown tool: {name}"}
    except Exception as error:  # noqa: BLE001
        payload = {"schema": "mcp_tool_error.v1", "status": "FAIL", "error": f"{type(error).__name__}: {error}"}
    return {
        "content": [{"type": "text", "text": _json_text(payload)}],
        "isError": payload.get("status") == "FAIL",
    }


def read_resource(uri: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = _dict(arguments)
    fabric = None
    if uri.startswith("osrs://live/") or uri.startswith("osrs://session/") or uri.startswith("osrs://debug/") or uri in {"osrs://library/data-sources", "osrs://library/query-coverage", "osrs://script-api/runtime-evidence"}:
        fabric = _fabric(args)
    if uri == "osrs://live/status":
        payload = knowledge_fabric.fetch_json(str(args.get("daemonUrl") or DEFAULT_DAEMON_URL).rstrip("/") + "/status")
    elif uri == "osrs://live/world-model-summary":
        payload = fabric.query_world_summary() if fabric else {}
    elif uri == "osrs://live/knowledge-fabric-status":
        payload = fabric.status() if fabric else {}
    elif uri == "osrs://live/current-debug-context":
        payload = fabric.query_current_debug_context() if fabric else {}
    elif uri == "osrs://live/current-blocker":
        payload = fabric.explain_current_blocker() if fabric else {}
    elif uri == "osrs://debug/current-context":
        payload = fabric.query_current_debug_context() if fabric else {}
    elif uri == "osrs://debug/blocker":
        payload = fabric.explain_current_blocker() if fabric else {}
    elif uri == "osrs://debug/action-input-visibility":
        payload = fabric.query_action_input_visibility() if fabric else {}
    elif uri == "osrs://debug/navigation-decision-trace":
        payload = fabric.query_navigation_decision_trace() if fabric else {}
    elif uri == "osrs://debug/latest-script-authoring-context":
        payload = knowledge_fabric.latest_artifact(fabric.session_path if fabric else None, "script_authoring_context")
    elif uri == "osrs://debug/latest-replay-scenario":
        payload = knowledge_fabric.latest_artifact(fabric.session_path if fabric else None, "replay_scenarios")
    elif uri == "osrs://debug/data-quality-report":
        payload = fabric.data_quality_report() if fabric else {}
    elif uri == "osrs://debug/coverage":
        payload = fabric.coverage_report() if fabric else {}
    elif uri == "osrs://debug/pipeline-health":
        payload = context_service.pipeline_health_payload(
            SimpleNamespace(
                daemon_url=DEFAULT_DAEMON_URL,
                snapshot_url=DEFAULT_SNAPSHOT_URL,
                live_timeout=1.0,
                sessions_dir=None,
            )
        )
    elif uri == "osrs://live/route-context":
        status = fabric.daemon_status if fabric else {}
        brain = _dict(status.get("brain"))
        payload = {
            "schema": "knowledge_fabric_route_context_resource.v1",
            "serviceRouteContext": brain.get("serviceRouteContext") or status.get("serviceRouteContext"),
            "returnRouteContext": brain.get("returnRouteContext") or status.get("returnRouteContext"),
            "pathingContext": brain.get("pathingContext") or status.get("pathingContext"),
        }
    elif uri == "osrs://live/resource-candidates":
        payload = fabric.query_resource_candidates() if fabric else {}
    elif uri == "osrs://live/service-candidates":
        payload = fabric.query_service_candidates() if fabric else {}
    elif uri == "osrs://session/memory":
        payload = fabric.session_memory if fabric else {}
    elif uri == "osrs://session/observations":
        payload = fabric.query_session_memory(limit=25) if fabric else {}
    elif uri == "osrs://debug/latest-bundle":
        payload = fabric.query_debug_evidence(limit=5) if fabric else {}
    elif uri == "osrs://library/routes":
        payload = {"schema": "static_routes_resource.v1", "routes": knowledge_fabric.load_static_library().get("routes", [])}
    elif uri == "osrs://library/targets":
        payload = {
            "schema": "static_targets_resource.v1",
            "targets": knowledge_fabric.load_static_library().get("targetLibrary", []),
            "skillRequirements": knowledge_fabric.load_static_library().get("skillRequirements", {}),
        }
    elif uri == "osrs://library/profiles":
        payload = {
            "schema": "static_profiles_resource.v1",
            "profiles": knowledge_fabric.load_static_library().get("targetProfiles", []),
        }
    elif uri == "osrs://library/actions":
        library = knowledge_fabric.load_static_library()
        payload = {
            "schema": "static_actions_resource.v1",
            "actions": [
                {
                    "classId": item.get("classId"),
                    "displayName": item.get("displayName"),
                    "knownActions": list(dict.fromkeys((item.get("usefulActions") or []) + (item.get("actionContains") or []))),
                }
                for item in library.get("targetLibrary", [])
                if isinstance(item, dict)
            ],
        }
    elif uri == "osrs://library/data-sources":
        payload = fabric.data_source_inventory() if fabric else {}
    elif uri == "osrs://library/query-coverage":
        payload = fabric.query_coverage_matrix() if fabric else {}
    elif uri == "osrs://script-api/spec":
        payload = task_script_api.script_api_spec()
    elif uri == "osrs://script-api/woodcut-bank-example":
        payload = task_script_api.woodcut_bank_template()
    elif uri == "osrs://script-api/woodcut-bank-evidence-plan":
        payload = task_script_api.build_task_script_evidence_plan(task_script_api.woodcut_bank_template())
    elif uri == "osrs://script-api/runtime-evidence":
        payload = fabric.query_task_script_runtime_evidence() if fabric else {}
    elif uri == "osrs://script-api/failure-classification":
        payload = fabric.classify_task_failure() if fabric else {}
    elif uri == "osrs://external/items":
        root = external_knowledge_cache.ensure_cache()
        payload = external_knowledge_cache.read_json(root / "item_name_map.json", {"schema": "external_item_name_map.v1", "itemsByName": {}})
    elif uri == "osrs://external/item-map":
        root = external_knowledge_cache.ensure_cache()
        payload = external_knowledge_cache.read_json(root / "item_id_map.json", {"schema": "external_item_id_map.v1", "items": {}})
    elif uri == "osrs://external/wiki-cache":
        root = external_knowledge_cache.ensure_cache()
        payload = {"schema": "external_wiki_cache_resource.v1", "cachePath": str(root / "wiki_page_cache"), "status": external_knowledge.knowledge_status()}
    elif uri == "osrs://external/skill-requirements":
        root = external_knowledge_cache.ensure_cache()
        payload = external_knowledge_cache.read_json(root / "skill_requirements.json", {"schema": "external_skill_requirements.v1", "requirements": {}})
    elif uri == "osrs://external/source-status":
        payload = external_knowledge.knowledge_status()
    else:
        payload = {"schema": "mcp_resource_error.v1", "status": "FAIL", "error": f"unknown resource: {uri}"}
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": _json_text(payload)}]}


def _jsonrpc_result(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_jsonrpc(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = _dict(message.get("params"))
    if method == "initialize":
        return _jsonrpc_result(
            message_id,
            {
                "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _jsonrpc_result(message_id, {"tools": tool_definitions()})
    if method == "tools/call":
        return _jsonrpc_result(message_id, call_tool(str(params.get("name") or ""), _dict(params.get("arguments"))))
    if method == "resources/list":
        return _jsonrpc_result(message_id, {"resources": resource_definitions()})
    if method == "resources/read":
        return _jsonrpc_result(message_id, read_resource(str(params.get("uri") or ""), _dict(params.get("arguments"))))
    if method == "ping":
        return _jsonrpc_result(message_id, {})
    if message_id is None:
        return None
    return _jsonrpc_error(message_id, -32601, f"method not found: {method}")


def _write_framed(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded)
    sys.stdout.buffer.flush()


def _read_framed_messages() -> Any:
    stream = sys.stdin.buffer
    while True:
        line = stream.readline()
        if not line:
            return
        if line.strip().startswith(b"{"):
            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            continue
        if not line.lower().startswith(b"content-length:"):
            continue
        try:
            length = int(line.split(b":", 1)[1].strip())
        except ValueError:
            continue
        while True:
            blank = stream.readline()
            if blank in (b"\r\n", b"\n", b""):
                break
        body = stream.read(length)
        if not body:
            return
        try:
            yield json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            continue


def serve_stdio() -> None:
    for message in _read_framed_messages():
        if not isinstance(message, dict):
            continue
        response = handle_jsonrpc(message)
        if response is not None:
            _write_framed(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only MCP adapter for OSRS telemetry Knowledge Fabric.")
    parser.add_argument("--stdio", action="store_true", help="Run as an MCP stdio server.")
    parser.add_argument("--list-tools", action="store_true", help="Print tool definitions as JSON.")
    parser.add_argument("--list-resources", action="store_true", help="Print resource definitions as JSON.")
    parser.add_argument("--call-tool", help="Call one tool once and print its JSON result.")
    parser.add_argument("--arguments", default="{}", help="JSON object for --call-tool.")
    args = parser.parse_args(argv)
    if args.stdio:
        serve_stdio()
        return 0
    if args.list_tools:
        print(_json_text({"tools": tool_definitions()}))
        return 0
    if args.list_resources:
        print(_json_text({"resources": resource_definitions()}))
        return 0
    if args.call_tool:
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as error:
            print(_json_text({"status": "FAIL", "error": str(error)}))
            return 2
        result = call_tool(args.call_tool, arguments if isinstance(arguments, dict) else {})
        print(result["content"][0]["text"])
        return 1 if result.get("isError") else 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

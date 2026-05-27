# Analysis Examples

This file is the current live-development quick reference. Older examples that
used the compact live packet file bridge have been retired.

## Runtime Source Of Truth

Normal live operation is Snapshot No-File:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

The live stack is:

```text
RuneLite PluginSnapshotEndpoint 8893
-> WorldModelCache / client_tick_hot
-> live_core_daemon.py on 8890
-> Knowledge Fabric / readiness / action proposal
-> executor / HumanInputController when explicitly executing
```

The removed stack was:

```text
live_packets\live-*.ndjson
live_packets\live-*.jsonl
live packet index/tail readers
compact packet file-source runtime fallback
compact stream/file mirror runtime fallback
```

Those files are legacy disk cleanup only. They cannot be enabled as a normal
runtime source. Use:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
```

Deletion requires an explicit apply command:

```powershell
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply
```

## Pipeline Health And Plugin UI

The canonical cleanup/source-of-truth manifest is:

- `telemetry-viewer/docs/current_pipeline_manifest.md`
- `telemetry-viewer/pipeline_manifest.json`
- `telemetry-viewer/config_keys.json`

Use the pipeline health query before chasing stale config or old files:

```powershell
python telemetry-viewer\context_service.py --pipeline-health
python telemetry-viewer\context_service.py --query pipeline-health
```

The normal RuneLite plugin settings are now limited to Core, Snapshot Endpoint,
and Overlay controls. Developer diagnostics are hidden from the normal UI, and
retired workflow/raw-recording/frame-capture keys are cleaned on plugin startup.
Do not ask users to enable NDJSON, JSONL, compact packet file, or live packet
archive settings; those settings are retired.

## Query-First Debugging

When the question is "what is happening right now?", query the live context
before reading logs or source files:

```powershell
python telemetry-viewer\context_service.py --query current-debug-context
python telemetry-viewer\context_service.py --query explain-current-blocker
python telemetry-viewer\mcp_server.py --call-tool get_live_status
python telemetry-viewer\mcp_server.py --call-tool get_knowledge_fabric_status
python telemetry-viewer\mcp_server.py --call-tool explain_current_blocker
```

Then ask for focused evidence:

```powershell
python telemetry-viewer\mcp_server.py --call-tool query_resource_candidates
python telemetry-viewer\mcp_server.py --call-tool query_service_candidates
python telemetry-viewer\mcp_server.py --call-tool query_route_objects
python telemetry-viewer\mcp_server.py --call-tool query_path_frontier
python telemetry-viewer\mcp_server.py --call-tool query_view_quality
python telemetry-viewer\mcp_server.py --call-tool get_latest_action_trace
python telemetry-viewer\mcp_server.py --call-tool get_latest_visual_bundle
```

Native Codex MCP registration is optional. If `codex mcp list` is blocked by
the Windows environment, the local adapter commands above remain the supported
read-only query path.

## Explicit Bounded JSON Artifacts

JSON remains a normal format for explicit bounded context/debug output. These
are intentionally preserved:

- `target_library.json`, `target_profiles.json`, and `service_routes.json`
- latest-state overlay/input files such as `overlay_debug_state.json` and
  `input_integrity_status.json`
- `current_debug_context` query output
- `script_authoring_context.v1` bundles
- `replay_scenario.v1` bundles
- `data_quality_report.v1`
- `debug_context_diff.v1`
- `knowledge_fabric_handoff_summary.v1`
- sparse visual debug bundle summaries
- `session_memory.json` when session-scoped and bounded
- MCP tool/resource JSON responses

The rule is simple: explicit, bounded, query/debug JSON is valid; unbounded
append-only live packet archives are retired.

## Current Debug Context

Use this as the first one-command state capture:

```powershell
python telemetry-viewer\context_service.py --query current-debug-context
```

Expected sections include live status, readiness, action readiness,
world-model summary, Knowledge Fabric status, current blocker, proposal,
resource/service/route candidates, path frontier, view quality, overlay health,
input integrity, latest trace/bundle summaries, session memory, static library
summary, performance, and cap warnings.

## Replay And Script Authoring

Capture a replayable offline scenario:

```powershell
python telemetry-viewer\context_service.py --capture-replay-scenario --profile woodcutting --reason route_pathing_blocker
python telemetry-viewer\context_service.py --replay-scenario C:\path\to\replay_scenario.json
```

Capture future-script context:

```powershell
python telemetry-viewer\context_service.py --capture-script-authoring-context --profile woodcutting
```

These bundles can include screenshots, world-model summaries, route/resource/
service censuses, collision/frontier summaries, projection audit, view quality,
overlay state, input integrity, action trace excerpts, session memory, and
static library excerpts. They are one-shot evidence bundles and do not create
the retired packet archive.

## Live Validation

Use readiness as the gate:

```powershell
python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting
```

For a bounded Arduino-backed action run, keep the normal executor path:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend arduino --arduino-port COM6 --arduino-require-monitor --input-profile steady --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --summary-every-action --final-reconcile-ms 3000 --final-reconcile-game-ticks 8 --resource-reconcile-ms 4000 --resource-reconcile-game-ticks 8 --pacing-profile natural --target-hover-failure-limit 2 --target-suppression-ms 2500 --max-total-actions 5 --max-consecutive-timeouts 2 --capture-debug-screenshots --screenshot-on-failure --screenshot-on-timeout --max-debug-screenshots 10 --overlay-passive --post-test-focus-target powershell
```

Live execution must report `directBackendBypassCount=0`; Arduino monitor and
firmware safety must pass when required.

## Maintenance

Old packet files may still exist under historical sessions. They are not live
truth and are not read by the runtime. Report them with:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
```

Cleanup is dry-run by default and scoped to
`%USERPROFILE%\.osrs-telemetry\sessions`:

```powershell
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply
```

## Data Inventory And External Knowledge

Codex should inspect the data catalog before guessing where a fact lives:

```powershell
python telemetry-viewer\context_service.py --query data-source-inventory
python telemetry-viewer\context_service.py --query query-coverage-matrix
python telemetry-viewer\context_service.py --coverage-report
```

External OSRS facts are advisory and cache-first. They are useful for item IDs,
object labels, skill requirements, location names, and future script profiles,
but live RuneLite/WorldModel evidence remains authoritative.

```powershell
python telemetry-viewer\context_service.py --external-knowledge-status
python telemetry-viewer\context_service.py --external-lookup-item-id 1511
python telemetry-viewer\context_service.py --external-search-item "logs"
python telemetry-viewer\context_service.py --external-get-skill-requirement "Oak"
python telemetry-viewer\context_service.py --external-lookup-object "Staircase"
python telemetry-viewer\context_service.py --external-search-wiki "Lumbridge Castle bank"
```

The wiki/API path is never used in executor hot loops. API calls require an
explicit refresh flag or refresh command and use a descriptive User-Agent with
serial, cache-first behavior.

## Task Probe

For a new script idea, start with a read-only task probe:

```powershell
python telemetry-viewer\context_service.py --probe-task "woodcutting and bank logs" --profile woodcutting
```

It inspects loaded-scene objects/actions/widgets/inventory, static libraries,
external cache facts, route/service priors, skill requirements, and suggests a
review-required profile skeleton. It sends no mouse, keyboard, menu, or click
input.

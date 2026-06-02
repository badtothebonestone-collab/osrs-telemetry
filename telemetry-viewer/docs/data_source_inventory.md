# Data Source Inventory

See also `current_pipeline_manifest.md` and `../pipeline_manifest.json` for the
current official component list, plugin config surface, and retired components.
`context_service.py --pipeline-health` exposes the same high-level health view
for Codex/direct query use.

Schema: `data_source_inventory.v1`

The live execution source of truth is still RuneLite:

- `8893 PluginSnapshotEndpoint`
- `WorldModelCache`
- `8890 live_core_daemon/context_service`
- executor readiness plus HumanInputController

External OSRS knowledge is advisory enrichment only. It can label IDs, suggest skill requirements, and help write profiles, but it cannot make a target executable and it must not be queried from hot executor loops.

## Query

```powershell
python telemetry-viewer\context_service.py --query data-source-inventory
python telemetry-viewer\context_service.py --data-source-inventory
```

MCP resource/tool:

- `get_data_source_inventory`
- `osrs://library/data-sources`

## Sources

| Source | Type | Producer | Consumers | Schema | Freshness | Caps | Runtime Critical | Disk Growth | Internet |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8893 PluginSnapshotEndpoint | live | RuneLite plugin | daemon, Knowledge Fabric, diagnostics | `plugin_snapshot_response.v1` | tick/source age | response sizing | yes | no | no |
| WorldModelCache | live loaded scene | Java cache | endpoint, Knowledge Fabric | `world_model_snapshot.v1` | tick/age | object/projection caps | yes | no | no |
| client_tick_hot | live hot state | RuneLite plugin | hover/menu/click proof | `client_tick_hot.v1` | client tick age | menu tail | yes | no | no |
| 8890 daemon/context | live context | Python daemon | Codex, MCP, executor context | `context_status.v1` | daemon/plugin freshness | response caps | yes | no | no |
| overlay_debug_state | latest-state debug | daemon overlay writer | RuneLite overlay, bundles | `telemetry_overlay_debug_state.v1` | write age | marker limits | no | overwritten | no |
| input_integrity_status | latest-state safety with phase-aware summaries | input monitor plus Knowledge Fabric phase labeling | readiness, summaries, current-debug context, action-input visibility | `input_integrity_status.v1` / `input_integrity_phase_report.v1` | generated time | none | yes for live action | overwritten | no |
| navigation_decision_trace | latest action-trace route decision evidence | executor action trace | route/pathing regression diagnosis | `navigation_decision_trace_summary.v1` | latest action trace/session freshness | context row limit | no | no | no |
| visual_debug_bundle | explicit debug | executor/bundle support | Codex evidence | bundle summaries | created time | screenshot/bundle caps | no | bounded by flags | no |
| replay_scenario | explicit replay | Knowledge Fabric | offline replay | `replay_scenario.v1` | created time | query limit | no | explicit only | no |
| script_authoring_context | explicit authoring | Knowledge Fabric | future scripts | `script_authoring_context.v1` | created time | query limit | no | explicit only | no |
| task_script_api | script authoring contract | `task_script_api.py` | validators, compiler, MCP/direct tools | `task_script_api_spec.v1` | static code version | validation limits | no | no | no |
| task_script_runtime_evidence | read-only runtime evidence with proof eligibility | Knowledge Fabric | lifecycle proof comparison | `task_runtime_evidence.v1` / `task_runtime_evidence_integrity.v1` | source evidence freshness and loaded-scene proof | evidence variable catalog; `--query task-script-runtime-evidence` | no | no | no |
| task_failure_classification | read-only failure diagnosis | `task_script_api.py` | Codex before-patching classification | `task_failure_classification.v1` | source evidence freshness | supplied evidence sections; `--query task-failure-classification` | no | no | no |
| task_step_readiness | read-only script step gate | Knowledge Fabric and `task_script_api.py` | Codex before bounded script/operator requests | `task_step_readiness.v1` | runtime proof eligibility plus readiness/action evidence freshness | compiled step count; `--query task-script-step-readiness` | no | no | no |
| task_run_readiness | read-only script lifecycle gate | Knowledge Fabric and `task_script_api.py` | Codex before selecting/requesting the next high-level primitive | `task_run_readiness.v1` | runtime/readiness/action/navigation evidence freshness | compiled step count, inferred primitive; `--query task-script-run-readiness` | no | no | no |
| session_memory | session memory | Knowledge Fabric | advisory anchors | `session_memory.v1` | session/tick | ring buffers | no | bounded | no |
| static libraries | static | repo JSON | profiles/routes/targets | `static_knowledge_library.v1` | version hash | query limit | no | no | no |
| external OSRS knowledge cache | advisory external | explicit refresh/manual seed | task probe, ID/name resolver | `external_knowledge_sources.v1` | source refresh/cache age | cache/query caps | no | capped | explicit only |
| maintenance/disk report | maintenance | `maintenance.py` | cleanup | `legacy_live_packets_report.v1` | report time | top file cap | no | no | no |

## Retired Packet Archive

`live_packets\`, `live-*.ndjson`, and `live-*.jsonl` are retired runtime paths. Normal runtime must not create or consume them. Old files are legacy cleanup only:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
```

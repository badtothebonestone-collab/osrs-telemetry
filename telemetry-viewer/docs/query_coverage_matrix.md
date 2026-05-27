# Query Coverage Matrix

For pipeline/config cleanup questions, use:

```powershell
python telemetry-viewer\context_service.py --pipeline-health
```

This reports active components, removed components, retired config keys, legacy
live packet disk status, and 8893/8890 health when available.

Schema: `query_coverage_matrix.v1`

Codex should run `get_current_debug_context` first for live questions, then use the narrower query named by the blocker or coverage report.

## Query

```powershell
python telemetry-viewer\context_service.py --query query-coverage-matrix
python telemetry-viewer\context_service.py --query-coverage-matrix
```

MCP resource/tool:

- `get_query_coverage_matrix`
- `osrs://library/query-coverage`

## Coverage

| Question | Direct Query | MCP | Source Data | Expected Schema | Confidence | Common Gap |
| --- | --- | --- | --- | --- | --- | --- |
| What is happening now? | `--query current-debug-context` | `get_current_debug_context` | daemon, world model, readiness | `knowledge_fabric_current_debug_context.v1` | high when fresh | idle/stale scene |
| What is blocking progress? | `--query current-blocker` | `explain_current_blocker` | readiness, traces, route/view data | `knowledge_fabric_current_blocker_explanation.v1` | medium-high | missing route/frontier evidence |
| What resource targets exist? | `query_resource_candidates` | `query_resource_candidates` | resource census, projection, external requirements | `knowledge_fabric_resource_candidates.v1` | high in loaded scene | projection cap/stale client tick |
| What service objects exist? | `query_service_candidates` | `query_service_candidates` | service census, static routes | `knowledge_fabric_service_candidates.v1` | high if service is loaded | static anchors are advisory |
| What route objects exist? | `query_route_objects` | `query_route_objects` | route census | `knowledge_fabric_route_objects.v1` | high in loaded scene | route object off-scene |
| What collision/pathing frontier exists? | `query_path_frontier` | `query_path_frontier` | collision/frontier query | `knowledge_fabric_path_frontier.v1` | medium-high | collision unavailable |
| What camera/view issue exists? | `query_view_quality` | `query_view_quality` | projection audit/view inputs | `knowledge_fabric_view_quality.v1` | medium | occlusion is heuristic |
| What widgets/UI are open? | `list_seen_widgets` | `list_seen_widgets` | daemon widget/dialogue/bank state | `knowledge_fabric_seen_widgets.v1` | medium | compact widget state |
| What item/object/NPC ID is this? | external lookup flags | `external_lookup_*` | external cache/static library | `external_*_lookup.v1` | advisory | cache miss |
| What should a future script include? | `--probe-task` | `probe_task` | scene, static library, external cache | `task_probe_report.v1` | medium | loaded scene missing |

## Rules

- External knowledge labels and explains; it does not execute.
- Static route priors remain advisory until the live world model, projection, and hover/menu evidence verify an executable target.
- MCP is read-only and does not expose click/input execution.
- No query path should create `live_packets`, NDJSON, or JSONL live archives.

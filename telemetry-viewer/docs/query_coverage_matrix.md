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
| What did the navigation decision trace say? | `--query navigation-decision-trace` / `query_navigation_decision_trace` | `query_navigation_decision_trace` / `osrs://debug/navigation-decision-trace` | latest action-trace navigationDecisionTrace or supplied records | `navigation_decision_trace_summary.v1` | high when trace present | trace disabled or no latest action trace |
| What camera/view issue exists? | `query_view_quality` | `query_view_quality` | projection audit/view inputs | `knowledge_fabric_view_quality.v1` | medium | occlusion is heuristic |
| What widgets/UI are open? | `list_seen_widgets` | `list_seen_widgets` | daemon widget/dialogue/bank state | `knowledge_fabric_seen_widgets.v1` | medium | compact widget state |
| What did Codex know about the planned click/input? | `get_action_input_visibility` | `get_action_input_visibility` / `osrs://debug/action-input-visibility` | latest action trace or current action proposal plus readiness input geometry, coordinate conversion, HumanInputController, input integrity phase report | `action_input_visibility_context.v1` | high for planned point when proposal and input geometry are present | derived proposal points have no movement/hover proof until live action trace exists |
| What item/object/NPC ID is this? | external lookup flags | `external_lookup_*` | external cache/static library | `external_*_lookup.v1` | advisory | cache miss |
| What should a future script include? | `--probe-task` | `probe_task` | scene, static library, external cache | `task_probe_report.v1` | medium | loaded scene missing |
| What high-level primitives can a script use? | Knowledge Fabric task script API | `get_task_script_api_spec` / `osrs://script-api/spec` | task script API spec | `task_script_api_spec.v1` | high | none |
| Is this task script valid? | Knowledge Fabric task script API | `validate_task_script` | task script JSON | `task_script_validation.v1` | high | raw-input fields or unbounded loops |
| What existing engine actions will this script use? | Knowledge Fabric task script API | `compile_task_script` / `explain_script_plan` | task script JSON, task policy | `task_script_plan.v1` | high | unknown primitive or missing evidence |
| Which variables must prove the script changed state? | Knowledge Fabric task script API | `get_task_script_evidence_plan` / `osrs://script-api/woodcut-bank-evidence-plan` | task script JSON | `task_script_evidence_plan.v1` | high | missing lifecycle variable coverage |
| What are the current values for script evidence variables? | `--query task-script-runtime-evidence` / Knowledge Fabric runtime evidence | `get_task_script_runtime_evidence` / `osrs://script-api/runtime-evidence` | daemon, readiness, client tick, action visibility | `task_runtime_evidence.v1` | high when loaded scene is fresh | manual login or stale liveness |
| Did before/after evidence prove a step changed state? | Knowledge Fabric task script API | `compare_task_script_runtime_evidence` | two runtime evidence snapshots | `task_runtime_evidence_comparison.v1` | high with fresh before/after snapshots | missing after evidence or live input hard blocker |
| How should a failed script/live attempt be classified before patching? | `--query task-failure-classification` / Knowledge Fabric task script API | `classify_task_failure` / `osrs://script-api/failure-classification` | current or supplied blocker/runtime/action evidence | `task_failure_classification.v1` | medium-high with fresh evidence | missing evidence bundle |
| Is the next high-level script step ready to request? | `--query task-script-step-readiness` / Knowledge Fabric task script API | `assess_task_script_step` / `osrs://script-api/step-readiness` | compiled script plus runtime/readiness/action-input/navigation evidence | `task_step_readiness.v1` | medium-high with fresh evidence | manual login, readiness, input integrity, or suspicious navigation trace |
| What high-level script primitive should be considered next? | `--query task-script-run-readiness` / Knowledge Fabric task script API | `assess_task_script_run` / `osrs://script-api/run-readiness` | compiled script plus runtime/readiness/action-input/navigation evidence | `task_run_readiness.v1` | medium-high with fresh evidence | manual login, stale liveness, readiness, input integrity, or suspicious navigation trace |
| Can the current scene inform a script template? | Knowledge Fabric scene probe | `probe_task_from_scene` | loaded scene, static library, external cache | `task_scene_probe.v1` | medium | stale/missing loaded scene |

## Rules

- External knowledge labels and explains; it does not execute.
- Static route priors remain advisory until the live world model, projection, and hover/menu evidence verify an executable target.
- MCP is read-only and does not expose click/input execution.
- High-level task script tools compile into existing action proposal/readiness paths; they do not add raw mouse/key tools.
- Script step readiness must be assessed before bounded requests; it may name `request_bounded_live_step`, `request_watcher_step`, or `request_liveness_recovery`, but it does not execute them.
- Script run readiness may infer the next primitive from lifecycle evidence, but it still delegates actual permission to the step-readiness gate.
- Script run readiness reports lifecycle evidence integrity; route, phase, planned-action, and inventory context are advisory-only while loaded scene proof is missing or unverified, or manual login is required.
- Script lifecycle success requires before/after live evidence for inventory, resource count, bank-open state, hover/click proof, location, route progress, and phase/intent.
- Failure classification must use the phase-aware input report: operator-phase injected events are not script failure, while live-action injected/lower-IL or direct-backend-bypass deltas are hard blockers.
- Route/pathing patches should use `query_navigation_decision_trace` to inspect decision, reason, distance, route step, and suspicious-decision evidence before changing behavior.
- No query path should create `live_packets`, NDJSON, or JSONL live archives.

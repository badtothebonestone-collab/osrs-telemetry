# High-Level Task Script API

Schema: `task_script.v1`

The task script API is a thin authoring layer over the existing engine. It validates and compiles simple primitives into current profile/action-proposal concepts; it does not execute input and does not create a second motor pipeline.

## Primitives

Allowed primitives:

- `collect`
- `interact`
- `walk_to`
- `bank`
- `deposit`
- `close_bank`
- `return_to_resource`
- `wait_for_evidence`
- `recover_loaded_scene`
- `repeat_until`

Every live-capable primitive compiles toward the existing path:

`action proposal -> readiness -> hover/menu proof -> HumanInputController -> ArduinoHIDBackend -> input integrity -> lifecycle verification`

`wait_for_evidence`, `repeat_until`, and `recover_loaded_scene` are watcher/recovery primitives. They do not issue raw input.

## Safety Contract

- MCP/direct tools may validate, compile, explain, template, and probe scripts.
- MCP/direct tools must not expose raw arbitrary `mouseDown`, `mouseUp`, `keyDown`, `keyUp`, or `click` operations.
- Bounded operator requests are high-level only: `request_liveness_recovery`, `request_bounded_live_step`, `request_watcher_step`, `request_input_integrity_reset`, `request_pointer_calibration`, and `request_safe_stop_all`.
- Operator/debug phases may contain injected events from Computer Use or manual interaction. Rebaseline before live action.
- During the live action window, injected/lower-IL deltas or `directBackendBypassCount > 0` are blockers.
- External OSRS facts are cache-first advisory enrichment. Live truth remains RuneLite / 8893 / WorldModel / 8890.

## MCP And Direct Queries

Tools:

- `get_task_script_api_spec`
- `validate_task_script`
- `compile_task_script`
- `explain_script_plan`
- `get_task_script_evidence_plan`
- `get_task_script_runtime_evidence`
- `compare_task_script_runtime_evidence`
- `classify_task_failure`
- `assess_task_script_step`
- `assess_task_script_run`
- `suggest_task_template`
- `probe_task_from_scene`

Resources:

- `osrs://script-api/spec`
- `osrs://script-api/woodcut-bank-example`
- `osrs://script-api/woodcut-bank-evidence-plan`
- `osrs://script-api/runtime-evidence`
- `osrs://script-api/failure-classification`
- `osrs://script-api/step-readiness`
- `osrs://script-api/run-readiness`

Direct Python surface:

- `KnowledgeFabric.validate_task_script(script)`
- `KnowledgeFabric.compile_task_script(script)`
- `KnowledgeFabric.explain_script_plan(script)`
- `KnowledgeFabric.task_script_evidence_plan(script)`
- `KnowledgeFabric.query_task_script_runtime_evidence(script)`
- `KnowledgeFabric.compare_task_script_runtime_evidence(before, after, script=..., primitive=...)`
- `KnowledgeFabric.classify_task_failure(evidence=...)`
- `KnowledgeFabric.assess_task_script_step(script, step_index=..., primitive=...)`
- `KnowledgeFabric.assess_task_script_run(script)`
- `KnowledgeFabric.suggest_task_template(task_description, profile=...)`
- `KnowledgeFabric.probe_task_from_scene(task_description, profile=..., limit=...)`

Named context queries:

- `python telemetry-viewer\context_service.py --query task-script-runtime-evidence`
- `python telemetry-viewer\context_service.py --query task-failure-classification`
- `python telemetry-viewer\context_service.py --query task-script-step-readiness`
- `python telemetry-viewer\context_service.py --query task-script-run-readiness`
- `python telemetry-viewer\context_service.py --query run-readiness`

## Runtime Evidence

Scripts compile with a `runtimeEvidencePlan` that names the live variables each primitive must prove. The current required woodcut-bank lifecycle variables are:

- `inventory`
- `resourceCount`
- `bankOpen`
- `menuOptionClicked`
- `hoverTarget`
- `location`
- `routeProgress`
- `phaseIntent`

Use `get_task_script_runtime_evidence` before and after one bounded live step, then call `compare_task_script_runtime_evidence` with the primitive name to compare those variables. A script step is not proven by an external fact, a static route prior, or a proposed action alone; it needs fresh live RuneLite / 8893 / WorldModel / 8890 evidence. For live input steps, the comparison also checks action-input visibility and input-integrity phase counts. A nonzero live-action injected/lower-IL delta or direct backend bypass is reported as a hard blocker.

Use `classify_task_failure` before patching. With no supplied evidence it classifies the current Knowledge Fabric/debug/runtime bundle; with explicit evidence it accepts current blocker, debug context, runtime evidence, before/after comparison, action-input visibility, action trace, external knowledge, or error text. It is read-only and never executes input.

Use `assess_task_script_step` before requesting a bounded script/operator step. It compiles the script, selects a step by `stepIndex` or `primitive`, and combines runtime evidence, action readiness, input-integrity phase evidence, failure classification, and navigation decision trace summaries. It reports `requestAllowedNow`, the required bounded request name, blockers, expected runtime variables, and the canonical pre/post live checklists. It is read-only and never executes the request.

Use `assess_task_script_run` when Codex needs the whole lifecycle view first. It compiles the script, reads runtime and action-input visibility evidence, infers the next primitive to consider, then nests the normal step-readiness result for that primitive. It exposes planned action/target/screen point, coordinate conversion, Arduino calibration, HumanInputController and cursor traces, hover/MenuOptionClicked proof, phase-aware input integrity, latest action/debug evidence, liveness recovery, watcher decisions, target view state, candidate state, and readiness evidence when those fields are present. It also reports `task_lifecycle_evidence_integrity.v1`, which labels route, phase, planned-action, and inventory fields as advisory-only when loaded scene proof is missing or unverified, or manual login is required. It is read-only and never executes the request.

## Example

The canonical example is:

`telemetry-viewer/examples/woodcut_bank_task_script.json`

It uses only high-level primitives to recover loaded scene state, collect resources until inventory is full, bank/deposit, close the bank, return to the resource area, and wait for resource readiness evidence.

## Failure Classification

When a script or live attempt fails, classify before patching:

- `code/data truth bug`
- `coordinate_transform_error`
- `arduino_movement_error`
- `target_aimpoint_error`
- `target/hover/menu mismatch`
- `stale liveness/plugin bug`
- `game-state/user-login blocker`
- `external knowledge/cache miss`
- `operator-phase injected-input noise`
- `runtime file/disk issue`

Coordinate issues are not automatically Arduino issues. If the requested physical point is wrong, fix coordinate conversion. If the point is correct but the cursor lands wrong, inspect Arduino calibration. If the cursor lands right but hover/menu is wrong, inspect target/aimpoint/candidate logic. If liveness is stale, use loaded-scene recovery rather than rediscovering known login/disconnect flows manually.

`classify_task_failure` reports operator-phase injected events as `operator-phase injected-input noise` when the live-action delta window is clean. A live-action injected/lower-IL delta or direct backend bypass is a hard blocker and should trigger STOP_ALL/DISARM/STATUS before any further live action.

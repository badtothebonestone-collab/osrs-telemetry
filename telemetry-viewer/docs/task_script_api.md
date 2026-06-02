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
- `suggest_task_template`
- `probe_task_from_scene`

Resources:

- `osrs://script-api/spec`
- `osrs://script-api/woodcut-bank-example`

Direct Python surface:

- `KnowledgeFabric.validate_task_script(script)`
- `KnowledgeFabric.compile_task_script(script)`
- `KnowledgeFabric.explain_script_plan(script)`
- `KnowledgeFabric.suggest_task_template(task_description, profile=...)`
- `KnowledgeFabric.probe_task_from_scene(task_description, profile=..., limit=...)`

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

# Analyzer Contracts

Daily live uses one Python sidecar process, `live_core_daemon.py`. Inside that
process, analyzers are small in-memory interpreters. They do not own transport,
file output, services, or game interaction.

## Shared Rules

Analyzers must not:

- read compact packet files
- read rolling live JSON files
- call the plugin snapshot endpoint
- start services or subprocesses
- write JSON, NDJSON, logs, history, or per-tick files
- execute actions
- emit click, input, menu, movement, or action fields
- mutate RuneLite client or game state

Analyzers may:

- receive the current in-memory daemon snapshot/context
- produce in-memory context objects
- produce concise warnings and status fields
- report missing capabilities
- call pure helper modules such as `resource_progress.py` and
  `intent_stabilizer.py`

Every analyzer context carries the same contract fields:

- `status`: `PASS`, `WARN`, or `FAIL`
- `warnings`: human-readable warnings
- `missing_capabilities`: normalized capability IDs
- `source_tick`: source tick when known
- `retained_from_previous`: true when last-good state was retained
- `timing_millis`: analyzer timing when measured

The Python dataclasses keep snake_case fields and expose camelCase aliases
(`missingCapabilities`, `sourceTick`, `retainedFromPrevious`, and
`timingMillis`) plus `contract_payload()` for status/API-style consumers.

## Capability Names

Capability IDs are normalized through `telemetry-viewer\capabilities.py`.

Canonical names include:

- `inventory.items`
- `inventory.resource_counts`
- `inventory.deltas`
- `target.candidates`
- `target.best`
- `target.intent`
- `navigation.local_collision_window`
- `navigation.full_pathfinding`
- `activity.animation`
- `activity.animation_frame`
- `activity.explicit_movement_state`
- `overlay.intent_markers`
- `plugin_snapshot.watch_values`
- `service.actions` (optional read-only context only, never emitted as commands)

Legacy aliases such as `inventoryDeltas`, `animationFrame`,
`explicitMovementState`, `fullPathfinding`, and `watch_values` should be
normalized before display. Daily output should not show duplicate capability
warnings for the same missing capability.

## Generic Task State

`telemetry-viewer\task_state.py` defines the shared read-only task phase model.
The current woodcutting brain keeps its task-specific `phase` output, and also
emits `genericTaskState` so future tasks can use common phases without adding
new daemon branches.

Generic phases are:

- `observe`
- `select_target`
- `target_selected`
- `wait_for_result`
- `inventory_full`
- `navigate_to_service`
- `service_available`
- `service_interaction_pending`
- `goal_complete`
- `blocked`
- `needs_more_context`
- `unknown`

`genericTaskState` includes task, phase, previous phase, confidence, reason,
active intent, selected target key, required capabilities, missing
capabilities, blocking conditions, observation needs, goal progress, and
`noActionEmitted=true`. It must not include action, click, input, menu, mouse,
keyboard, invoke, or execute fields.

Target roles are distinct:

- `activeIntentTarget`: the target, if any, that the current generic phase is
  focused on now.
- `availableTarget`: a useful visible target from context that is not
  necessarily the current intent.
- `previousIntentTarget`: the last known target before a task phase transition,
  when one is available.

When task-specific output reports a full inventory, `genericTaskState` consults
the selected task policy before choosing an active intent. Full inventory is a
condition, not a hardcoded banking transition:

- `needs_service` means the active target is cleared and service context such
  as a bank can be reported read-only.
- `process_inventory` means the active target is cleared and process context
  such as firemaking or dropping can be reported read-only.
- `continue_task` means full inventory is noted but does not by itself clear a
  valid active target.
- `observe_only` means no transition or service request is made.

When `phase=goal_complete`, `activeIntent=none` and the selected target is
cleared. Any service or process context remains interpretation only; this model
does not execute service, process, inventory, or navigation actions.

Service and process context is policy-gated. `woodcutting_bank` is allowed to
ask the service analyzer for already-visible bank/deposit candidates. Firemake,
drop, combat, and observe policies do not run or warn about service candidates.
`woodcutting_firemake` and `woodcutting_drop` are allowed to ask the process
inventory analyzer for held-resource context. These analyzers report what is
visible or missing only; they do not bank, burn, drop, use items, navigate, or
interact.

## Task Policy Model

`telemetry-viewer\task_policy.py` defines the read-only policy model used by
the generic task state. Policies are loaded from
`telemetry-viewer\task_policies.json` with built-in defaults as a fallback.

Current policies:

| Policy | Inventory expectation | Full inventory strategy | Disposition | Service/process | Generic result when full |
| --- | --- | --- | --- | --- | --- |
| `woodcutting_bank` | `must_have_space` | `needs_service` | `bank` | service `bank` | `inventory_full` / `needs_service`, active target cleared |
| `woodcutting_firemake` | `must_have_space` | `process_inventory` | `burn` | process `firemaking` | `inventory_full` / `process_inventory`, active target cleared |
| `woodcutting_drop` | `must_have_space` | `process_inventory` | `drop` | process `drop` | `inventory_full` / `process_inventory`, active target cleared |
| `combat_default` | `may_start_full` | `continue_task` | `keep` | none | `target_selected` / `continue_task` when a target exists |
| `observe_only` | `unknown` | `observe_only` | `none` | none | `observe` / `observe` |

`live_core_daemon.py` selects a policy with `--task-policy`. The daily
woodcutting default is explicit: `woodcutting_bank`. Policy output must not
contain action, click, input, menu, or execute fields.

`telemetry-viewer\task_policies.json` is static configuration. It is loaded
through the cached `task_policy.py` policy registry and must not be written by
the live daemon loop. Policy, task-state, service, process, and analyzer runtime
state stays in memory. Diagnostics may print JSON to stdout when `--json` is
passed, but they must not create policy history JSONL, per-tick task state JSON,
analyzer output JSON files, or other rolling live files.

## Task Transition QA

`telemetry-viewer\diagnose_task_transition.py` verifies policy-driven task
state flows with synthetic in-memory fixtures. It does not need RuneLite,
session files, compact packets, or daemon debug files. The diagnostic reports
expected versus actual generic phase, active intent, relevant analyzer context,
navigation context, overlay selected-marker expectation, and
`noActionEmitted`.

Covered scenarios include:

- woodcutting bank policy with inventory not full and tree target available
- woodcutting bank policy with full inventory and service missing
- woodcutting bank policy with full inventory and reachable bank booth visible
- firemaking policy with logs plus tinderbox present or missing
- drop policy with logs held
- combat policy with full inventory and an active target
- observe-only with full inventory

`--from-daemon` reads the current daemon `/status` only and summarizes the live
phase, active intent, service/process/navigation contexts, and active target
state. `--json` prints to stdout only.

## `analyzers\live_state.py`

Purpose: shared dataclasses for in-memory analyzer inputs/outputs.

Inputs: none at runtime beyond constructor values.

Outputs: `LiveInputSnapshot`, `LiveSourceStatus`, `InventoryContext`,
`TargetContext`, `NavigationContext`, `NavigationIntentContext`, `ActivityContext`,
`IntentOverlayContext`, `BrainContext`, `LiveAnalysisResult`, and common
contract helpers.

Forbidden side effects: all I/O, network, processes, and action emission.

Performance expectation: negligible; this module is structure only.

Future expansion: add new shared fields here only when multiple analyzers need
them.

## `inventory_analyzer.py`

Purpose: normalize the current inventory payload and delegate daily resource
progress to `resource_progress.py`.

Inputs: current daemon response, inventory payload, `ResourceProgressState`,
resource definition, and optional goal count.

Outputs: `InventoryContext` with normalized inventory, progress result,
matched slots, missing inventory capabilities, and last-good retention flags.

Forbidden side effects: no file reads/writes, no endpoint calls, no progress
math outside `resource_progress.py`.

Allowed warnings: invalid inventory snapshot, retained previous progress,
invalid resource slot, or missing inventory capabilities.

Performance expectation: linear in current inventory slots.

Future expansion: add new resource definitions through `resource_progress.py`;
do not fork progress math per task.

## `target_analyzer.py`

Purpose: summarize already-built candidates and expose raw best/nearest target
information without rebuilding candidates.

Inputs: candidate list already produced by the daemon and optional class/profile
filter.

Outputs: `TargetContext` with candidates, raw best target, nearest target, top
candidates, and candidate capability status.

Forbidden side effects: no packet reads, endpoint calls, or candidate building.

Allowed warnings: no candidates or no best target in current analysis.

Performance expectation: linear in current candidates, bounded by daemon input.

Future expansion: support generic target classes such as trees, rocks, bankers,
NPCs, ground items, tiles, UI targets, and inventory slots through common
candidate fields.

## `navigation_analyzer.py`

Purpose: summarize navigation and reachability fields that already exist in the
current candidate/context payload.

Inputs: navigation payload and candidate list.

Outputs: `NavigationContext` with collision availability and reachable/blocked
counts.

Forbidden side effects: no pathfinding expansion or scene reads.

Allowed warnings: missing local collision window or optional full pathfinding.

Performance expectation: linear in current candidates.

Future expansion: new navigation capabilities should be exposed as
capabilities, not ad-hoc warnings.

## `navigation_intent_analyzer.py`

Purpose: describe read-only navigation intent context for current service,
process, or resource transitions.

Inputs: current player context, target context, service context, process
inventory context, existing navigation/reachability context, and generic task
state. The analyzer consumes only in-memory data that the daemon has already
built.

Outputs: `NavigationIntentContext` with whether navigation context is relevant,
why, target kind, destination target, distance, direct reachability, path
length if already available, collision-window availability, warnings, and
missing capabilities.

Policy behavior:

- `woodcutting_bank` with full inventory and an observed service candidate
  reports that service target as the destination context.
- `woodcutting_bank` with no observed service candidate reports
  `service_target_missing` and waits for service target context.
- `woodcutting_firemake` and `woodcutting_drop` remain local
  `process_inventory` context and do not request service navigation.
- A reachable selected resource target does not need navigation context.
- An unreachable selected target reports `target_unreachable`, but still emits
  no route, movement, click, or interaction command.

Forbidden side effects: no file reads/writes, endpoint calls, pathfinding
expansion, route generation, movement commands, or action/click/input/menu
fields.

Allowed warnings: service destination missing, local context says target is
blocked, local collision window missing, or full pathfinding context is not
available.

Performance expectation: constant time over already-selected context objects;
it must not rescan candidates or request more data.

## `activity_analyzer.py`

Purpose: separate current activity from recent task signals.

Inputs: activity payload and recent events.

Outputs: `ActivityContext` with current state, recent task signals, liveness,
and optional movement/animation capability status.

Forbidden side effects: no input polling, no game state mutation, no action
recommendations.

Allowed warnings: unknown current activity only when the payload cannot provide
a state.

Performance expectation: linear in the small recent-event list.

Future expansion: add task signals as observations, not current activity
overloads.

## `intent_overlay_analyzer.py`

Purpose: convert stabilized intent and candidate context into generic overlay
markers.

Inputs: current context, brain decision, stabilized intent result, and daemon
overlay args.

Outputs: `IntentOverlayContext` with selected target marker, backup markers,
generic marker list, and overlay status.

Forbidden side effects: no overlay file write, no extra input reads, no context
rebuild, no action/click/input/menu fields.

Allowed warnings: no selected marker when a target is required, or marker
diagnostics.

Performance expectation: linear in the current candidate slice; daily mode
should consider only the bounded top candidates supplied by the daemon.

Future expansion: add generic marker types such as waypoint, destination tile,
NPC, UI target, and inventory slot without making the plugin task-specific.

## `brain_context_analyzer.py`

Purpose: wrap daemon-specific brain evaluation and status extraction.

Inputs: context response, brain state, task, goal count, event limit, and reset
status.

Outputs: `BrainContext` with `brain_decision.v1`, updated brain state, compact
status fields, normalized missing capabilities, and retention flags.

The brain decision also includes `genericTaskState` when available. The
analyzer treats it as read-only interpretation data; it does not execute or
recommend actions.

Forbidden side effects: no action emission, no endpoint calls, no file writes.

Allowed warnings: warnings already produced by `brain_core.py`.

Performance expectation: small and bounded by current context/candidate data.

Future expansion: new task interpretation should flow through brain/context
objects and capabilities rather than growing `live_core_daemon.py`.

## `service_analyzer.py`

Purpose: report read-only service context only when the selected task policy
requires service.

Inputs: the resolved task policy and the current in-memory candidate list.

Outputs: `ServiceContext` with service-required status, service type, candidate
count, candidates grouped by service type, best/nearest visible service
candidate, reachable and unknown-reachability counts, and sanitized service
candidates. Candidate detection can use class, name, id, and already-present
candidate metadata. The current read-only service target families are
`bank_service`, `banker`, `bank_booth`, `bank_chest`, `deposit_box`, and
`deposit_chest`. Names such as `Banker`, `Bank booth`, `Bank chest`, `Deposit
box`, `Bank deposit box`, and `Deposit chest` are enough to classify a
candidate when a generic `bank_related` class is all that is available.

If optional service metadata is absent, the analyzer reports `service.actions`
as an optional missing capability; it does not fail when class/name/id is
enough. Action/menu metadata is never emitted in `ServiceContext`.

Forbidden side effects: no file reads/writes, no endpoint calls, no navigation,
no interaction, no action/click/input/menu fields.

Allowed warnings: service required by policy but no matching service candidate
is currently visible.

Performance expectation: linear in the current candidate slice.

Future expansion: add service target families such as bankers, booths, and
deposit boxes as context classifications only.

## `process_inventory_analyzer.py`

Purpose: report read-only inventory processing context when the selected policy
uses `process_inventory`.

Inputs: the resolved task policy and current `InventoryContext`.

Outputs: `ProcessInventoryContext` with process type, resource disposition,
held-resource count, resource availability, and read-only process context such
as tinderbox present/missing/unknown for firemaking.

Forbidden side effects: no clicking, dropping, burning, menu use, inventory
mutation, file writes, endpoint calls, or action fields.

Allowed warnings: process context is requested but no matching held resources
are visible, or firemaking context cannot confirm a tinderbox.

Performance expectation: constant time over normalized inventory progress.

Future expansion: add richer process context capabilities without adding an
execution path.

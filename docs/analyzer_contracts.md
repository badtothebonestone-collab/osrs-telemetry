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

Legacy aliases such as `inventoryDeltas`, `animationFrame`,
`explicitMovementState`, `fullPathfinding`, and `watch_values` should be
normalized before display. Daily output should not show duplicate capability
warnings for the same missing capability.

## `analyzers\live_state.py`

Purpose: shared dataclasses for in-memory analyzer inputs/outputs.

Inputs: none at runtime beyond constructor values.

Outputs: `LiveInputSnapshot`, `LiveSourceStatus`, `InventoryContext`,
`TargetContext`, `NavigationContext`, `ActivityContext`,
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

Forbidden side effects: no action emission, no endpoint calls, no file writes.

Allowed warnings: warnings already produced by `brain_core.py`.

Performance expectation: small and bounded by current context/candidate data.

Future expansion: new task interpretation should flow through brain/context
objects and capabilities rather than growing `live_core_daemon.py`.

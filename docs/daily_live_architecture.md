# Daily Live Architecture

Daily live is intentionally boring.

```text
RuneLite plugin
-> Daily Stable Compact: compact-packets stable source
   OR Daily Snapshot No-File: PluginLiveCache / PluginSnapshotEndpoint (experimental)
-> live_core_daemon.py
-> in-memory context state
-> optional overlay_debug_state.json with brain intent markers
-> daemon context / brain / dashboard endpoints
```

The RuneLite plugin remains a read-only sensor/cache adapter. Python remains the
sidecar context and brain layer. No daily component clicks, types, invokes
menus, moves the player, mutates game/client state, or emits game actions.

## Daily Path

Daily mode uses:

- `live_control_panel.py`
- `live_core_daemon.py`
- `live_config_doctor.py`
- `run_daily_gauntlet.py`
- compact packet files as the stable input source, or the experimental snapshot
  no-file source when explicitly selected
- in-memory daemon context state
- optional capped `interaction_geometry\live\overlay_debug_state.json` in
  intent mode

Daily mode does not require:

- `live_target_processor.py` as a separate process
- `context_service.py` as a separate process
- `live_context_query.py` as a separate process
- rolling `live_status.json`, `live_candidates.jsonl`, or `live_context_index.json`
- raw ticks, raw events, or frames
- screenshot, crop, or perception capture
- compact-stream
- plugin-snapshot unless explicitly using Daily Snapshot No-File or testing the
  experimental path

Daily Stable Compact command:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode compact-packets --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

Daily Snapshot No-File command (experimental):

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

Daily Snapshot No-File with a startup mission preset:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --human-dashboard --summary --benchmark
```

Task policy examples:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_bank --goal-count 5 --summary --benchmark
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_firemake --goal-count 5 --summary --benchmark
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_drop --goal-count 5 --summary --benchmark
```

Debug file writes remain off unless `--write-debug-live-files` is explicitly
supplied.

## Runtime Control

Mission presets can be applied at daemon startup with
`live_core_daemon.py --preset ...` or changed while the daemon is running with
`control_live_daemon.py --preset ...`. Startup `--preset` is a convenience
alias that resolves through `mission_presets.py` to safe runtime fields. An
explicit startup `--goal-count` overrides the preset goal, and an explicit
`--task-policy` overrides the preset policy with a warning:
`task policy overridden by explicit --task-policy`.

`diagnose_task_transition.py` still uses task policy names such as
`woodcutting_bank` and `woodcutting_firemake`, not mission preset names.

The daily daemon exposes local-only read-only control endpoints:

- `GET /control`
- `POST /control`

These endpoints change only sidecar brain/context configuration in memory. They
can apply named mission presets, switch `taskPolicy`, adjust `goalCount`,
toggle `observeOnly`, reset the resource-progress baseline, and tune overlay
mode/backups without restarting the daemon. They do not persist config, write
live JSON/NDJSON, click, walk, bank, burn, drop, use items, invoke menus, or
mutate game/client state.

Action-like control fields are rejected. The accepted fields are:

```text
missionPreset
taskPolicy
goalCount
observeOnly
resetBrainState
brainEnabled
overlayEnabled
overlayMode
overlayBackupCandidates
```

CLI examples:

```text
python telemetry-viewer\control_live_daemon.py --get
python telemetry-viewer\control_live_daemon.py --preset woodcut_bank --goal-count 5 --reset-brain-state
python telemetry-viewer\control_live_daemon.py --preset woodcut_firemake
python telemetry-viewer\control_live_daemon.py --preset observe_only
python telemetry-viewer\control_live_daemon.py --set-policy woodcutting_firemake --goal-count 5 --reset-brain-state
python telemetry-viewer\control_live_daemon.py --set-policy woodcutting_bank
```

`--json` prints to stdout only.

## Mission Control View

`live_control_panel.py` includes a Mission Control section for the daily daemon.
It polls the existing local daemon endpoints:

- `GET /health`
- `GET /status`
- `GET /control`

The panel shows daemon health, daily mode, input source, active mission preset,
active task policy, goal count, generic phase, active intent, progress,
inventory-full state, service/process/navigation needs, selected overlay
marker, warning count, no-file status, overlay status, and `noActionEmitted`
safety status.

Mission Control can post safe runtime-control updates for named mission
presets, task policy, goal count, observe-only mode, overlay mode/backups, and
one-shot baseline reset. The mission presets are sidecar brain/context presets
only:

- `woodcut_bank`
- `woodcut_firemake`
- `woodcut_drop`
- `observe_only`
- `combat_default`

The quick buttons route through those mission presets:

- Woodcut Bank
- Woodcut Firemake
- Woodcut Drop
- Combat Default
- Observe Only

These controls affect sidecar brain/context interpretation only. They do not
click, walk, bank, burn, drop, use items, interact, invoke menus, persist
config, or write live JSON/NDJSON. If the daemon is unavailable, the panel shows
`daemon not reachable` and points the user at Snapshot No-File startup.

## Internal Analyzer Architecture

`live_core_daemon.py` is the single daily sidecar process. Inside that process,
small in-memory analyzers now keep the responsibilities separated:

- `analyzers\live_state.py` defines the shared `LiveAnalysisResult` and context
  containers.
- `analyzers\target_analyzer.py` summarizes already-built candidates and keeps
  raw best/nearest target interpretation generic.
- `analyzers\navigation_analyzer.py` summarizes existing collision and
  reachability fields without doing new pathfinding.
- `analyzers\navigation_intent_analyzer.py` describes read-only destination
  and reachability context for service/process/resource transitions.
- `analyzers\activity_analyzer.py` keeps current activity separate from recent
  task signals such as target depletion.
- `analyzers\intent_overlay_analyzer.py` builds selected/backup intent markers,
  deduplicates targets, and merges the best available geometry into the
  selected marker.
- `analyzers\brain_context_analyzer.py` wraps daemon-specific brain evaluation
  and status fields.
- `analyzers\inventory_analyzer.py` normalizes inventory snapshots and delegates
  progress math to `resource_progress.py`.
- `capabilities.py` normalizes capability names and status values so daily
  warnings do not duplicate old aliases such as `inventoryDeltas` and
  `inventory.deltas`.
- `task_state.py` maps task-specific brain phases into generic read-only task
  phases for future task support.
- `task_policy.py` defines how task policies interpret conditions such as a
  full inventory.
- `analyzers\service_analyzer.py` and
  `analyzers\process_inventory_analyzer.py` report read-only service/process
  context only when the active policy asks for it.

These analyzers do not poll inputs, call RuneLite, read compact packet files,
write JSON/NDJSON, or start services. They consume the snapshot/context already
built by the daemon and return in-memory data. The only daily file write remains
the optional `overlay_debug_state.json` when `--write-overlay-state` is enabled.
Analyzer contracts are documented in `docs\analyzer_contracts.md`; future task
support should add capabilities and analyzer output fields there instead of
adding ad-hoc daemon logic.

## Generic Task State Model

The woodcutting brain now emits both the existing task-specific phase and a
generic `genericTaskState` payload. The generic state uses common phases such
as `target_selected`, `wait_for_result`, `inventory_full`, `goal_complete`,
`blocked`, and `needs_more_context`.

This is interpretation only. It does not add banking, mining, navigation
behavior, action execution, click targets, menu calls, input, or any new daily
file output. The intent overlay reads the generic `activeIntent` when present,
so a selected woodcutting tree still renders as the selected intent today while
future bankers, waypoints, UI targets, or inventory slots can share the same
phase model later.

`activeIntentTarget`, `availableTarget`, and `previousIntentTarget` are separate
roles. A full inventory is a condition, not a hardcoded "go bank" state. The
selected task policy decides what it means:

| Policy | Full inventory meaning | Active intent | Target behavior |
| --- | --- | --- | --- |
| `woodcutting_bank` | resources need bank service | `needs_service` | clear selected tree; service context only |
| `woodcutting_firemake` | logs would be processed by firemaking | `process_inventory` | clear selected tree; process context only |
| `woodcutting_drop` | logs would be processed by dropping | `process_inventory` | clear selected tree; process context only |
| `combat_default` | full inventory can be expected | `continue_task` | do not clear current target solely due to inventory |
| `observe_only` | condition is observed only | `observe` | no service/process transition |

When a woodcutting policy needs service or processing, the active selected tree
is cleared and any tree remains only as previous/available context. The overlay
therefore stops drawing a tree as `selected_target` until the generic phase
returns to target selection. If `woodcutting_bank` is active and the current
candidate context already contains a bank booth, banker, bank chest, deposit
box, deposit chest, or other bank-service candidate, the service analyzer can
surface that candidate as read-only service context and the overlay can draw it
as `Service: <name>`. Firemake/drop policies do not run service analysis; they
show read-only process context instead. No policy executes banking, burning,
dropping, navigation, or inventory actions.

`task_policies.json` is a small static config file. The live daemon caches the
policy registry and keeps policy/task/analyzer state in memory. Daily runtime
must not write per-tick policy JSON, task state JSON, analyzer JSON, policy
history JSONL, or any new rolling live files. The only daily file write remains
optional `overlay_debug_state.json` when overlay state is enabled. Policy
diagnostics with `--json` print to stdout only.

Service/process context is policy-gated:

- `woodcutting_bank`: `service_analyzer.py` scans only the current in-memory
  candidate list for bank-service candidates and reports best/nearest context,
  candidates grouped by type, and reachability counts.
- `woodcutting_firemake`: `process_inventory_analyzer.py` reports held logs and
  whether a tinderbox is present, missing, or unknown from the current
  inventory snapshot.
- `woodcutting_drop`: `process_inventory_analyzer.py` reports held resources
  for drop disposition only.
- `combat_default` and `observe_only`: no service/process analyzer warning is
  produced solely because inventory is full.

These summaries are context, not commands. They do not include click/input/menu
fields and they do not interact with the game.

Navigation intent context is also read-only. When `woodcutting_bank` reaches a
full-inventory `needs_service` phase and a bank-service candidate is already
visible in the current context, the daemon reports that candidate as the
destination context with distance, direct reachability, collision-window
availability, and any missing navigation capability such as
`navigation.full_pathfinding`. If no service candidate is visible, it reports
that it is waiting for service target context. Firemaking and drop policies do
not request service navigation; their next context remains local
`process_inventory`. A selected resource target that is reachable does not need
navigation context; an unreachable target is reported as unreachable, but no
movement, route, waypoint, click, or interaction command is produced.

Task transition QA verifies these policy flows with synthetic in-memory
fixtures:

```text
python telemetry-viewer\diagnose_task_transition.py --policy woodcutting_bank --scenario service_visible
python telemetry-viewer\diagnose_task_transition.py --policy woodcutting_firemake --scenario firemake_ready
python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
```

It reports expected versus actual generic phase, active intent,
service/process/navigation context, overlay selected-marker expectation, and
`noActionEmitted`. Synthetic mode does not read sessions or require RuneLite;
daemon observer mode reads `/status` only. JSON output is stdout-only.

Service target matching is conservative and read-only. The service analyzer
recognizes already-built candidates with class IDs or inferred types
`bank_service`, `banker`, `bank_booth`, `bank_chest`, `deposit_box`, and
`deposit_chest`. It can also infer those types from names such as `Banker`,
`Bank booth`, `Bank chest`, `Deposit box`, `Bank deposit box`, and `Deposit
chest`. Existing action metadata may help classify a candidate if present, but
that metadata is stripped from all outputs. If no bank-service candidate is
visible, the brain reports `Service candidate: not observed` and
`Missing/needed context: bank_service candidate`; the overlay may show only a
compact warning marker, not a fake service target.

## Daily Source Modes

1. Daily Stable Compact uses `live_packets\live-*.ndjson`. It is the stable
   fallback and remains the safest daily source.
2. Daily Snapshot No-File uses `PluginLiveCache` through
   `PluginSnapshotEndpoint` on localhost. It is experimental and is intended to
   stop continuous compact NDJSON writes by setting `emitCompactLivePackets=false`
   and `compactLivePacketsRequiredForLive=false` while keeping raw ticks,
   events, frames, screenshots, crops, compact-stream, and rolling daemon debug
   files off.
3. Debug Audit is intentionally disk-heavy. Raw/frames/crops/compact files are
   allowed only when explicitly doing audit or dataset work.

## Advanced And Legacy Paths

These remain available but are not the daily workflow:

- `live_target_processor.py`
- `context_service.py`
- `live_context_query.py`
- `target_geometry_inspector.py`
- batch geometry builders
- dataset builders
- diagnose scripts
- plugin-snapshot testing
- compact-stream testing
- raw/debug/audit recording
- screenshot/crop/perception dataset tooling

Use the Live Control Panel's Advanced section for these paths. Plugin-snapshot
and compact-stream must stay clearly labelled `EXPERIMENTAL`.

## Resource Progress

Daily woodcutting progress is computed by `resource_progress.py`.

- `itemId=None` never counts.
- `inventory.items` wins over `resourceCounts`.
- `resourceCounts` fallback cannot fabricate slots.
- First valid snapshot establishes the baseline.
- Daily gained-since-start is monotonic held-vs-baseline until reset.
- Same snapshot repeated does not change progress.
- Invalid/missing snapshots retain the last valid progress briefly instead of
  flickering to zero.
- Goal completion is based on displayed progress reaching the goal count.

No other daily module should maintain an independent woodcutting progress
counter.

## Daily Overlay

Daily overlay mode is brain intent, not broad candidate debug. Candidate context
can still track nearby trees internally, but `overlay_debug_state.json` writes
`overlay_intent_state.v1` markers by default:

- `selected_target` for the current stabilized brain intent
- up to two `backup_candidate` markers
- warning markers when the brain has no reachable target

`intent_stabilizer.py` is a tiny in-memory jitter filter inside
`live_core_daemon.py`. It uses the candidates and brain/context state already
built in the current daemon loop. It does not make another request, read compact
packet files, run a separate service, or write history files.

The stabilizer has a hard-switch path and a soft jitter-filter path. Hard
switches happen immediately when the task/profile or active intent changes,
the current target has an explicit depleted/stale/despawned/unreachable signal,
a higher-priority interrupt appears, or the brain forces a task transition.
Soft retention is only used when the same task/intent is still valid and the
current target is briefly absent from the current candidate slice or a nearby
alternative is only briefly or slightly better. The default transient-missing
grace is two ticks, so a one-tick candidate/projection gap does not make the
selected overlay jump to another tree set.

The daemon status and overlay diagnostic report both the raw best target and
the stabilized selected target. They also expose the current switch reason,
missing-tick count, whether grace was used, and the last few in-memory switch
decisions. This history is not written as a new rolling file.

Intent markers carry stable target identity such as object key, id/hash,
world/scene/local location, and a last-known aim point. The RuneLite overlay
uses that identity to resolve the current scene object during render when
possible and draws the live object clickbox before falling back to stored hull
or bounds geometry. Tile projection is only a fallback, so a tile-centered
marker means the object clickbox could not be resolved for that frame. If live
reprojection is unavailable, the overlay falls back to last-known geometry or
aim point and then label-only drawing.

Before writing intent overlay state, the daemon deduplicates selected and backup
markers by stable target identity (`objectKey`, hash, id plus world tile, or id
plus scene tile). If a backup/candidate has better geometry for the selected
object, that geometry is merged into the selected marker and the duplicate
backup is suppressed.

When the brain task changes away from woodcutting, woodcutting tree markers are
not retained. Backup markers are also stabilized: selected target identity is
excluded from backups, and previous backup identities are preferred while still
valid so the full overlay set does not reorder wildly every tick. Visual QA can
use `--overlay-mode candidates`; debug/audit can use `--overlay-mode debug`.

## Daily Workflow

1. Apply Daily Live Preset.
2. Start RuneLite Dev.
3. Start Daily Live Stable Compact, or explicitly choose Daily Snapshot No-File
   when testing the experimental no-file path.
4. Use overlay, daemon context endpoints, dashboard, or brain output.
5. Run Config Doctor or Daily Gauntlet when something looks stale.
6. Stop All.

Main daily buttons:

- Apply Daily Live Preset
- Start RuneLite Dev
- Start Daily Live Stable Compact
- Start Daily Live Snapshot No-File EXPERIMENTAL
- Stop All
- Config Doctor
- Daily Gauntlet
- Open Latest Session Folder

# Analysis Examples

## Daily Workflow

For everyday live testing, use the control panel:

```text
python telemetry-viewer\live_control_panel.py
```

Then click **Start Daily Live Stable Compact** for the stable file bridge, or
**Start Daily Live Snapshot No-File EXPERIMENTAL** when intentionally testing
the no-file snapshot path. Both start one read-only Python daemon that keeps
live context in memory, serves the context API on localhost, and writes only the
tiny overlay state when requested. It does not click, type, invoke menus, or
execute actions.

One-click Windows entrypoints are also available from the repository root:

```text
start_live_control_panel.bat
Start-LiveControlPanel.ps1
start_normal_live_stack.bat
Start-NormalLiveStack.ps1
```

Daily has two source modes:

- **Daily Stable Compact** uses `--daily-mode compact-packets --input-source
  compact-packets` and the compact NDJSON bridge. This remains the stable
  fallback.
- **Daily Snapshot No-File** uses `--daily-mode snapshot-no-files
  --input-source plugin-snapshot --plugin-snapshot-tier hot`. It is
  experimental, uses `PluginLiveCache`/`PluginSnapshotEndpoint`, and expects the
  compact NDJSON file mirror to be disabled unless intentionally used as a debug
  mirror.

The legacy compact-packet file stack remains available under the control
panel's Advanced buttons. Direct compact stream is experimental and should be
tested explicitly before relying on it.

### Runtime Control

`live_core_daemon.py` also accepts startup mission presets:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --human-dashboard --summary --benchmark
```

Startup `--preset` resolves through `mission_presets.py`. Explicit
`--goal-count` overrides the preset goal. Explicit `--task-policy` still works
and overrides the preset policy with a warning.

Once `live_core_daemon.py` is running, you can change the same read-only
brain/context settings without restarting the daemon:

```text
python telemetry-viewer\control_live_daemon.py --get
python telemetry-viewer\control_live_daemon.py --preset woodcut_bank --goal-count 5 --reset-brain-state
python telemetry-viewer\control_live_daemon.py --preset woodcut_firemake
python telemetry-viewer\control_live_daemon.py --preset observe_only
python telemetry-viewer\control_live_daemon.py --set-policy woodcutting_firemake --goal-count 5 --reset-brain-state
python telemetry-viewer\control_live_daemon.py --set-policy woodcutting_bank
```

The Live Control Panel exposes the same local `/control` endpoint with a
mission preset dropdown, task policy dropdown, goal-count field, observe-only
toggle, runtime apply button, and reset-baseline button. Mission presets are
sidecar brain/context presets only:

- `woodcut_bank`
- `woodcut_firemake`
- `woodcut_drop`
- `observe_only`
- `combat_default`

These controls mutate only daemon memory. They do not click, walk, bank, burn,
drop, use items, invoke menus, persist config, or write live JSON/NDJSON files.
JSON mode prints to stdout only.

The panel's Mission Control section also polls `/health`, `/status`, and
`/control` so the daily state is visible without opening separate terminals. It
shows daemon health, mode/source, current mission preset, current policy,
generic phase, active intent, progress, inventory-full state,
service/process/navigation needs, overlay selection, warning count, and
`noActionEmitted`. Quick policy buttons such as Woodcut Firemake or Observe
Only post `missionPreset` payloads; they do not operate the game.

One-shot Mission Snapshot for bug reports or before/after comparisons:

```text
python telemetry-viewer\mission_snapshot.py --daemon-url http://127.0.0.1:8890
python telemetry-viewer\mission_snapshot.py --daemon-url http://127.0.0.1:8890 --json
python telemetry-viewer\mission_snapshot.py --daemon-url http://127.0.0.1:8890 --output .\debug\mission_snapshot.json
```

This fetches `/health`, `/status`, and `/control` once, then exits. Default
output is stdout only. `--json` prints one JSON object to stdout only. `--output`
writes exactly one JSON file to the explicit path; it does not create NDJSON or
reintroduce continuous runtime JSON files.

`diagnose_task_transition.py` uses policy names such as `woodcutting_bank`, not
mission preset names such as `woodcut_bank`.

By default, the daemon does not write these rolling legacy live files:

```text
interaction_geometry\live\*.json
interaction_geometry\live\live_event_timeline.jsonl
```

If `--write-overlay-state` is enabled, it writes only:

```text
interaction_geometry\live\overlay_debug_state.json
```

Stable normal live RuneLite config:

- **Emit compact live packets**: ON.
- **Stream also writes files**: ON if compact stream is enabled.
- Normal processor input: `--input-source compact-packets --require-compact-packets`.
- `compact-stream`: experimental transport testing only.

Daily Snapshot No-File RuneLite config:

- **Recording mode**: `LIVE_COMPACT_ONLY`.
- **Emit compact live packets**: OFF.
- **Compact packets required for live**: OFF.
- **Plugin snapshot endpoint**: ON at `127.0.0.1:8893`.
- Raw ticks/events, frames, screenshots, crops, perception, and compact-stream:
  OFF.

When the compact packet file mirror is enabled, Java also writes:

```text
live_packets\live-*.ndjson
live_packets\live_packet_index.json
```

Normal live mode does not require raw tick JSONL, raw event JSONL, or frames.
Use DEBUG_RECORDING mode when you want full raw data for replay, audit, batch
geometry, or training datasets.
Screenshot, crop, and perception image tooling is also advanced/debug-only; it
does not run in the Daily Live daemon unless you intentionally start those
batch tools from Advanced.

## Internal Analyzer Architecture

Daily Live still runs one sidecar process: `live_core_daemon.py`. The daemon now
delegates interpretation to small in-memory analyzers under
`telemetry-viewer\analyzers\`:

- inventory/progress preparation delegates to `resource_progress.py`
- target, navigation, and activity summaries consume the current daemon context
- brain context evaluation wraps `brain_core.py`
- intent overlay construction emits selected/backup markers without writing
  extra files

Analyzer modules do not poll the plugin, read compact packet files, start
services, or write JSON/NDJSON. They return one shared in-memory analysis result
for the daemon to serve through the existing context/status endpoints.

## Task Policy Model

Inventory full is interpreted by task policy, not by a hardcoded banking rule.
The current policy is selected with `--task-policy`:

- `woodcutting_bank`: full inventory means read-only `needs_service` context for
  a bank.
- `woodcutting_firemake`: full inventory means read-only `process_inventory`
  context for firemaking/burning logs.
- `woodcutting_drop`: full inventory means read-only `process_inventory`
  context for dropping logs.
- `combat_default`: full inventory can be expected and does not by itself clear
  the active target.
- `observe_only`: full inventory is only observed.

Policy examples for Snapshot No-File daily:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_bank --goal-count 5 --summary --benchmark
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_firemake --goal-count 5 --summary --benchmark
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --task-policy woodcutting_drop --goal-count 5 --summary --benchmark
```

This is still interpretation only. The daemon does not bank, burn, drop,
navigate, click, type, invoke menus, or mutate game/client state.

Service/process context is also policy-gated and read-only:

- `woodcutting_bank` runs service context over the current in-memory candidate
  list only. It can report visible bank booths, bankers, bank chests, deposit
  boxes, deposit chests, or generic bank-service candidates, plus candidates by
  type, best/nearest, reachable count, and unknown-reachability count.
- `woodcutting_firemake` runs process inventory context only. It reports held
  logs and whether the current inventory snapshot shows a tinderbox as present,
  missing, or unknown.
- `woodcutting_drop` runs process inventory context only and reports held
  resources for drop disposition.
- `combat_default` and `observe_only` do not run service/process analyzers just
  because inventory is full.

These summaries do not include action, click, input, or menu fields.

Navigation intent context is policy-gated read-only context too:

- `woodcutting_bank` with a visible service candidate reports destination,
  distance, reachability, collision-window availability, and missing navigation
  capabilities if any.
- `woodcutting_bank` with no visible service candidate reports that it is
  waiting for service target context.
- `woodcutting_firemake` and `woodcutting_drop` do not request service
  navigation; they remain local `process_inventory` context.
- A reachable selected resource target does not need navigation context. An
  unreachable selected target is reported as unreachable, without generating a
  route, waypoint, movement command, click, or interaction.

Pathing Context v1 is the next read-only layer. It uses the destination from
navigation intent and the local collision window already in daemon memory to
summarize destination tile, final approach tile, local reachability, path
length, next waypoint, and a capped predicted local path preview. The preview
is labeled predicted and is for visualization/debug context only. It is not a
walk command, click target, route execution, or guaranteed OSRS movement.
When an exact interaction tile is unknown, `finalApproachTile` is the predicted
local endpoint or adjacent approach tile, and `navigation.interaction_tile`
remains a missing capability.

Daily overlay shows only destination and next waypoint markers by default when
pathing is relevant. Debug overlay mode can show capped `predicted_path_tile`
markers for visual QA.

Pathing QA Matrix uses synthetic in-memory fixtures and does not require
RuneLite, sessions, compact packets, or live files. It is useful for checking
the local prediction model before comparing a live overlay:

```text
python telemetry-viewer\diagnose_pathing_matrix.py
python telemetry-viewer\diagnose_pathing_matrix.py --json
python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890
```

The matrix covers straight cardinal paths, guarded diagonal shortcuts,
corner-blocked diagonals, object/service final approach tiles, destination
outside the collision window, plane mismatch, blocked paths, and path cap
behavior. JSON mode prints one object to stdout only.

Service matching is conservative. It accepts service class/type IDs
`bank_service`, `banker`, `bank_booth`, `bank_chest`, `deposit_box`, and
`deposit_chest`, or equivalent visible names such as `Bank booth`, `Banker`,
`Deposit box`, `Bank deposit box`, and `Deposit chest`. If action metadata is
already present it can be used as read-only classification metadata.

When a task policy requires service context, the daemon keeps resource target
selection profile-scoped while also exposing a separate bounded
`serviceCandidateInputs` view to the service analyzer. This allows a
woodcutting profile to keep best/nearest tree selection tree-focused while
still seeing already-visible bank/deposit candidates for read-only service
context.

Policy matrix:

| Policy | Inventory full strategy | Result |
| --- | --- | --- |
| `woodcutting_bank` | `needs_service`, disposition `bank` | active intent `needs_service`, service needed `bank`, target cleared |
| `woodcutting_firemake` | `process_inventory`, disposition `burn` | active intent `process_inventory`, process needed `firemaking`, target cleared |
| `woodcutting_drop` | `process_inventory`, disposition `drop` | active intent `process_inventory`, process needed `drop`, target cleared |
| `combat_default` | `continue_task`, disposition `keep` | active intent `continue_task`; full inventory is expected/allowed |
| `observe_only` | `observe_only` | no transition; observe the condition only |

Policy diagnostic:

```text
python telemetry-viewer\diagnose_task_policy.py --policy woodcutting_bank --task woodcutting --inventory-full true --resource-count 28 --goal-count 5
python telemetry-viewer\diagnose_task_policy.py --policy woodcutting_firemake --task woodcutting --inventory-full true --resource-count 28 --goal-count 5 --json
python telemetry-viewer\diagnose_navigation_intent.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --policy woodcutting_bank
python telemetry-viewer\diagnose_service_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_bank_ui_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_bank_ui_context.py --from-daemon --daemon-url http://127.0.0.1:8890 --json
python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --scenario bank_closed_return_memory
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --json
python telemetry-viewer\diagnose_pathing_matrix.py
python telemetry-viewer\diagnose_pathing_matrix.py --json
python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890 --json
```

Bank UI / Service State Context v1 begins after service arrival. For
`woodcutting_bank`, service-ready with no readable bank UI remains
`service_available`, readable bank UI becomes `service_open`, and a visible
bank pin becomes `blocked` with reason `bank_pin_required`. The plugin snapshot
payload is compact bank/interface telemetry in the live cache only; it does not
open, close, deposit, withdraw, type, click widgets, invoke menus, or add a
rolling JSON/NDJSON output.

Task transition QA uses synthetic in-memory fixtures, so it does not require
RuneLite, sessions, compact packets, or live files:

```text
python telemetry-viewer\diagnose_task_transition.py --policy woodcutting_bank --scenario service_visible
python telemetry-viewer\diagnose_task_transition.py --policy woodcutting_firemake --scenario firemake_ready
python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
```

The matrix covers not-full woodcutting target selection, full-inventory bank
policy with service visible/missing, firemaking ready/missing tinderbox, drop
context, combat with full inventory, and observe-only full inventory. JSON mode
prints to stdout only.

Full woodcut-bank cycle QA summarizes the current live daemon state in one
read-only report, from resource collection through service pathing, bank UI,
bank operation, close-bank readiness, post-bank reacquisition, remembered
resource-return destination, and return to resource targeting:

```text
python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890 --json
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20 --json
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20 --json
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --scenario bank_closed_return_memory
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --json
```

Use `run_woodcut_bank_live_qa.py` for one-command live QA after future
woodcut_bank changes. It checks the plugin snapshot login state, daemon
health, full-cycle context, cycle history, service/path, bank UI/operation,
return/resource-return state, overlay selection, and gauntlet-style semantic
deferrals without writing files.

Use `diagnose_woodcut_bank_scenarios.py` for fixed synthetic state-machine
coverage without RuneLite or live daemon state. It reuses the existing
woodcut-bank cycle classifier and service analyzer logic and prints to stdout
only.

`task_policies.json` is static config. The live daemon must not write it or
create per-tick policy, task-state, analyzer, JSONL, or rolling live output
files. Diagnostic `--json` output is stdout-only unless a future command
explicitly documents otherwise.

## Live Config Doctor And Presets

`live_config_doctor.py` checks the current live workflow against a named preset.
It reads existing rolling files and localhost health endpoints only; it does not
change RuneLite config, click, type, invoke menus, or mutate game/client state.

Daily preset:

- `inputSourceActive=compact-packets`
- `recordingMode=LIVE_COMPACT_ONLY`
- raw ticks, raw events, and frames disabled
- screenshot, crop, and perception capture disabled
- compact packets available and recent
- compact stream not active
- plugin-snapshot not the daily input
- `windowTicks` around `10`
- `candidateOutputWindow=latest`
- `livenessMode=delta`
- overlay target limit `10` or lower if overlay is enabled
- `budgetExceeded=false`
- `writeFailures=0`

Visual QA preset:

- compact-packets remains the preferred stable input
- overlay is allowed
- overlay target limit should stay around `25` or lower
- clickable hull and collision-window visualization are okay
- frames are optional when intentionally enabled
- DEBUG_RECORDING gets a warning if it appears accidental

Debug Audit preset:

- raw ticks and frames may be enabled
- compact packets can still be written
- live performance warnings are less important than capture fidelity
- disk growth is expected, so stop recording when the audit capture is complete

Plugin Snapshot Experimental preset:

- plugin snapshot endpoint health should be `PASS`
- running input should be `plugin-snapshot`
- hot tier is recommended for realtime testing
- projection field mode should be `compact`
- `maxProjectionRefs` should stay around `100` for hot tier
- compact-packets should remain available as the fallback
- expanded/audit tiers in realtime get warnings
- active time above 100 ms gets a warning

Run the doctor:

```text
python telemetry-viewer\live_config_doctor.py --latest-session --mode daily --fix-suggestions
python telemetry-viewer\live_config_doctor.py --latest-session --mode snapshot_no_file --fix-suggestions
python telemetry-viewer\live_config_doctor.py --latest-session --mode visual_qa --fix-suggestions
python telemetry-viewer\live_config_doctor.py --latest-session --mode debug_audit --fix-suggestions
python telemetry-viewer\live_config_doctor.py --latest-session --mode plugin_snapshot_experimental --fix-suggestions
python telemetry-viewer\live_config_doctor.py --latest-session --mode daily --json
```

The Live Control Panel includes a **Config Doctor** button and a small
PASS/WARN/FAIL badge for the selected preset. The top warnings are shown in the
session header so common misconfigurations are visible before they break the
live stack.

Workflow preset application is available two ways:

- RuneLite config: select **Workflow preset**, then toggle **Apply workflow
  preset**. If **Preview preset only** is enabled, the plugin logs the preview
  and does not save the preset changes.
- Live Control Panel: click **Apply Daily Live Preset**, **Apply Visual QA
  Preset**, **Apply Debug Audit Preset**, or **Apply Plugin Snapshot Preset**.
  The panel applies its own command defaults immediately. If the local preset
  endpoint is enabled, it previews the whitelisted Java config changes, asks for
  confirmation, applies them, then runs the doctor.

Presets change telemetry/plugin/tool settings only. They do not click, type,
invoke menus, execute commands in-game, or mutate RuneLite client/game state.
The Java applier only writes fixed values for whitelisted `osrs-telemetry`
config keys; it has no arbitrary key/value edit route.

Preset endpoint commands:

```powershell
Invoke-RestMethod http://127.0.0.1:8893/presets

$request = @{
  schema = "telemetry_preset_request.v1"
  preset = "DAILY_LIVE"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/preset/preview" -Body $request -ContentType "application/json"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/preset/apply" -Body $request -ContentType "application/json"
```

Preset names:

- `DAILY_LIVE`: compact packet file bridge, `LIVE_COMPACT_ONLY`, raw/debug
  capture off, compact-stream off, plugin-snapshot off, small overlay cap.
- `DAILY_SNAPSHOT_NO_FILE`: `LIVE_COMPACT_ONLY`, compact packet file mirror off,
  plugin snapshot endpoint enabled on `127.0.0.1:8893`, compact-stream off, raw
  and frame capture off. Experimental no-file daily path.
- `VISUAL_QA`: compact packet file bridge, overlay enabled, clickable hull
  geometry allowed and capped, compact-stream off.
- `DEBUG_AUDIT`: `DEBUG_RECORDING`, raw ticks/events and frames enabled, compact
  packets still enabled, disk-growth warning expected.
- `PLUGIN_SNAPSHOT_EXPERIMENTAL`: `LIVE_COMPACT_ONLY`, compact packets enabled
  as fallback, plugin snapshot endpoint enabled on `127.0.0.1:8893`,
  compact-stream off.

Recommended order if you run the pieces manually:

1. Start RuneLite dev.
2. Check live setup.
3. Start the streamlined live daemon.
4. Open the human dashboard, brain, or visual inspector against the daemon's
   context API.

```text
python telemetry-viewer\check_live_setup.py --latest-session --require-compact-packets
python telemetry-viewer\inspect_live_packets.py --latest-session --summary
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode compact-packets --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --watch-human --interval 1 --events 5
```

## Live Control Panel

`telemetry-viewer\live_control_panel.py` is the everyday launcher. It keeps a
bounded log for each helper process, shows latest session and compact packet
status, warns when sessions or packet files look stale, and only stops helper
processes it started.

Key buttons:

- **Apply Daily Live Preset** applies the compact-packet daily defaults.
- **Start RuneLite Dev** launches the dev client.
- **Start Daily Live Stable Compact** starts the daily in-memory daemon against
  compact-packets. It serves `/health`, `/status`, `/context`, `/summary`, and
  `/brain` from memory and avoids rolling live file writes by default. Daily
  overlay output uses brain intent markers, not the full candidate list.
- **Start Daily Live Snapshot No-File EXPERIMENTAL** starts the same daemon
  against the plugin snapshot endpoint and expects compact NDJSON writing to be
  disabled by the RuneLite preset.
- **Stop All** stops panel-started helper processes.
- **Config Doctor** runs the selected preset check and prints copy/paste fix
  suggestions.
- **Daily Gauntlet** runs strict daily invariants, duplicate process checks, and
  required-context-domain checks.
- **Open Latest Session Folder** opens the latest telemetry session.

Legacy live processor, legacy context service, plugin-snapshot testing,
compact-stream testing, debug audit, inspectors, and batch builders are under
Advanced.

## Streamlined Live Daemon

`live_core_daemon.py` is the daily-mode replacement for the separate
`live_target_processor.py` + `context_service.py` + rolling live-file chain. It
reuses the existing snapshot/compact-packet conversion, candidate ranking,
liveness, navigation, context response, and brain evaluation helpers, but keeps
the current state in memory.

It exposes context-service-compatible endpoints directly:

- `GET /health`
- `GET /schema`
- `GET /status`
- `GET /summary?task=woodcutting`
- `GET /brain?task=woodcutting`
- `POST /context`
- `POST /context/batch`
- `POST /brain`

Daily command:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

If `--goal-count` is omitted, the brain runs in observe-only mode. It will show
the current held log count when inventory items are available, but it will not
accumulate `gained since start` or print `gained / unknown`.

Reset the in-process or file-backed brain baseline with:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --reset-brain-state --summary --benchmark
```

Use a state file only when you intentionally want progress to survive daemon
restarts. In PowerShell, prefer:

```powershell
$brainState = Join-Path $env:USERPROFILE ".osrs-telemetry\brain_state_woodcutting.json"
```

Then pass `--brain-state-file "$brainState"`. The daemon scopes that file to the
session path, task, goal count, and resource group, and resets the baseline if
those change. Avoid literal `%USERPROFILE%` in PowerShell; that syntax is not
expanded there.

Plugin snapshot only:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source plugin-snapshot --plugin-snapshot-tier hot --context-port 8890 --write-overlay-state --summary --benchmark
```

Compact packet fallback only:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --summary --benchmark
```

Debug file writes are off by default. Add `--write-debug-live-files` only when
you intentionally need the old rolling files such as `live_status.json`,
`live_candidates.jsonl`, `live_context_index.json`,
`live_navigation_summary.json`, and `live_event_timeline.jsonl`.
Daily compact mode intentionally has frame recording off, so missing frame-path
warnings are hidden from compact human output unless you switch to visual
QA/debug workflows.

## Brain Progress Idempotency

Daily woodcutting progress is intentionally simple. `held logs` is the current
inventory snapshot, and `gained since start` is the best valid held-vs-baseline
increase seen since the current baseline was established:

```text
max(previous gained since start, current held logs - baseline held logs, 0)
```

The daily brain does not use cumulative gained/removed counters or slot-diff
history. Those fields are ignored from old state until a reliable inventory
delta event source is proven safe.

When you pass `--reset-brain-state`, the first valid inventory snapshot only
establishes the baseline:

```text
held logs: 5
baseline held logs: 5
gained since start: 0 / 5
source: baseline initialized
```

The same inventory signature is idempotent: repeatedly reading the same
snapshot cannot produce `0/5 -> 5/5 -> 10/5`. Moving logs between slots is not a
gain because the held count is unchanged. If a transient stale, skipped, or
partial poll reports fewer logs, daily progress keeps the previous valid
`gained since start` value instead of flickering to `0 / goal`. To start a new
goal, reset the brain state or start a new session/state.

Existing held logs are inventory context, not goal progress. They only become
progress if a later snapshot shows more held logs than the reset baseline.

A progress snapshot is considered valid only when the daemon/brain has a
session identity, latest tick, inventory signature, known held count, and either
real inventory items or sufficient task resource counts. Resource counts without
an inventory signature can still describe `held logs`, but they cannot establish
a baseline.

If an old state file contains `observedGained`, `observedRemoved`,
`cumulativeGained`, or `cumulativeLostOrRemoved`, those values are discarded and
the brain reports:

```text
old cumulative progress history ignored; daily progress uses held-vs-baseline snapshot count
```

When the streamlined daemon is running with writes off, use the daemon-backed
diagnostic:

```text
python telemetry-viewer\diagnose_brain_progress.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --goal-count 5
```

Use the file-backed diagnostic only when `--write-debug-live-files` is enabled
or when you intentionally want to inspect old rolling live files:

```text
python telemetry-viewer\diagnose_brain_progress.py --latest-session --task woodcutting --goal-count 5 --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```
- **Start Mock Brain Rehearsal** runs the read-only future-brain rehearsal
  client.
- **Debug Audit Tools** launches the batch pipeline for DEBUG_RECORDING
  sessions and warns that normal live sessions intentionally omit raw ticks.

The mode dropdown selects the doctor preset: Daily, Visual QA, Debug Audit, or
Plugin Snapshot Experimental. Start Normal Live Stack always applies Daily
compact-packet defaults.

The tool registry at `docs\tool_registry.md` and
`telemetry-viewer\tool_registry.json` maps scripts to daily, advanced debug,
legacy file pipeline, batch audit, experimental, or deprecated lanes.

## Recording Modes

RuneLite config is grouped into sections for daily use:

- **Normal Live**: telemetry enabled, recording mode, compact packets, output
  directory, and pinned session preservation.
- **Visual QA Overlay**: optional read-only overlay settings.
- **Frames / Visual Capture**: screenshot and frame capture settings.
- **Debug / Audit Recording**: disk-heavy raw tick/event/frame options.
- **Retention / Storage**: compact packet and session storage caps.
- **Advanced / Experimental**: scene capture, projection, and low-level packet
  settings.

Modes:

- `LIVE_COMPACT_ONLY`: normal live use; compact packets and rolling live files,
  no raw ticks/events/frames.
- `LIVE_COMPACT_WITH_FRAMES`: compact live plus limited frames for visual QA.
- `DEBUG_RECORDING`: full raw tick/event/frame recording for audit, replay,
  batch geometry, and training data.
- `HYBRID_DEBUG`: future/sampled debug mode when enabled by config.

Debug audit flow:

```text
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest-with-frames 25 --profile broad_qa --limit 2000 --open-inspector
```

When normal live mode is active, batch/debug builders should report that raw
tick recording is disabled instead of treating missing raw files as a live
failure.

The export tool writes generated summaries under the selected session:

```text
exports\session_index.json
exports\tick_summary.jsonl
exports\event_summary.jsonl
exports\frame_index_summary.jsonl
```

`telemetry-viewer\replay_viewer.py` is a local browser-based replay viewer for
already-collected telemetry. It is read-only and uses `telemetry_paths.py` for
segmented canonical sessions and legacy flat fallback where applicable:

```text
python telemetry-viewer\replay_viewer.py
python telemetry-viewer\replay_viewer.py --session "C:\path\to\session"
python telemetry-viewer\replay_viewer.py --sessions-dir "C:\path\to\sessions"
python telemetry-viewer\replay_viewer.py --port 8765
```

## Compact Live NDJSON Bridge

Compact live packets are the default live bridge between the RuneLite read-only
sensor/cache adapter and Python sidecars. The original file bridge remains
available and Java can write small append-only packets under:

```text
live_packets\live-*.ndjson
live_packets\live_packet_index.json
live_packets\latest_segment.txt
```

The packet stream contains observed facts only: baseline state, scene deltas,
projection summaries, inventory/equipment summaries, activity facts, and writer
health. Python still owns target libraries, profiles, scoring, task
interpretation, context responses, QA tooling, and future vision/model work.
The compact file bridge does not add overlays, input hooks, clicking, menu
invocation, automation, or direct network requirements.

## Plugin Snapshot Bridge

The plugin snapshot bridge is an experimental read-only pull bridge for future
sidecars. It is disabled by default and normal live still uses
`--input-source compact-packets --require-compact-packets`.

The RuneLite plugin keeps a `PluginLiveCache` of copied compact payloads for
baseline, scene delta, projection, inventory, inventory delta, activity,
navigation, collision window, writer health, and future watch values. The
optional endpoint serves only those cached copies; request handlers do not call
RuneLite `Client` APIs, scene scans, projection methods, widget traversal, or
`clientThread.invoke`.

RuneLite config:

- **Enable plugin snapshot endpoint**: OFF by default.
- **Snapshot host**: `127.0.0.1`.
- **Snapshot port**: `8893`.
- **Snapshot auth token**: optional local header token.
- **Snapshot max projection refs**: caps projection payload size.
- **Snapshot max response bytes**: rejects oversized responses.
- **Allow non-local snapshot host**: leave OFF.
- **Snapshot endpoint in normal live**: experimental opt-in only.

The bridge has `/health`, `/schema`, and `/snapshot` endpoints. `/snapshot`
uses `plugin_snapshot_request.v1` and returns `plugin_snapshot_response.v1` from
cached compact payloads. It is still a telemetry bridge only: no file serving,
commands, input, menu routes, or game-state mutation.

Manual checks after enabling the endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8893/health
Invoke-RestMethod http://127.0.0.1:8893/schema

$request = @{
  schema = "plugin_snapshot_request.v1"
  needs = @("baseline", "projection", "inventory", "navigation", "collision_window", "writer_health")
  maxAgeTicks = 5
  maxProjectionRefs = 100
  includeGeometry = $false
  responseMode = "compact"
  projectionFieldMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
```

Python can now test this bridge with the experimental
`--input-source plugin-snapshot` mode. It requests cached payloads over
localhost, converts them into the same synthetic compact tick shape used by the
compact packet file reader, and then runs the normal candidate/context pipeline.
It is not the default live path yet. Until `plugin-snapshot-vs-file` comparison
passes in your setup, the compact packet file bridge remains the stable daily
path and compact-stream remains experimental.

Snapshot projection conversion is intentionally schema-tolerant. Python accepts
cached projection refs from `visibleObjectRefs`, `visibleSceneObjectRefs`,
`projectedRefs`, `refs`, `targets`, and packet-envelope payloads, then normalizes
the fields into the same synthetic tick shape used by compact packet files.
`projection refs capped` means the endpoint returned a bounded, prioritized
slice of projected refs. Compact snapshot responses minimize projection refs to
candidate-building fields by default and omit heavy hull/debug geometry unless
explicitly requested. If `/snapshot` returns `response_too_large`, the endpoint
is available, but the requested bounded response still exceeded
`pluginSnapshotMaxResponseBytes`; lower `--plugin-snapshot-max-projection-refs`
or raise the RuneLite endpoint byte cap carefully.

Snapshot requests now have working-set tiers rather than one architectural cap:

- `hot`: small, fast working set. Default request cap is 100 refs and the goal
  is current best/nearest target awareness.
- `expanded`: broader working set. Default request cap is 500 refs and it is
  useful when hot has too few candidates or a brain needs wider context.
- `audit`: large bounded debug working set. Default request cap is 2000 refs,
  still limited by endpoint config and response byte limits.

The live processor sends optional request hints such as `profileHint`,
`classHint`, `targetTypeHint`, `requireOnScreen`, `requireGeometryAvailable`,
`desiredClasses`, and `maxCandidatesHint`. Java uses only cached projection
fields for generic prioritization before capping; it does not call RuneLite APIs
from the request handler and it does not move Python scoring into the plugin.
Future brain clients should ask for the smallest sufficient tier, then escalate
to `expanded` when the hot working set is not enough. Compact packet files and
debug/audit recordings remain the broad fallback paths.

Plugin snapshot input source:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source plugin-snapshot --plugin-snapshot-tier hot --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --input-source plugin-snapshot --plugin-snapshot-tier expanded --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Compare snapshot output to the stable compact packet file bridge:

```text
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources plugin-snapshot-vs-file --profile woodcutting --latest 5
```

Diagnose snapshot conversion if comparison fails:

```text
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500 --dump-synthetic-shape
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --tier-sweep
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500 --json
```

The diagnostic prints the projection payload keys, ref-list path, first ref
keys, missing field counts, number of refs converted to world targets, profile
matches, reject reasons, and whether compact packet files have projection refs
that the snapshot response did not provide.
`--dump-synthetic-shape` compares the internal plugin-snapshot synthetic tick
against the compact-packet synthetic tick before candidate building. This helps
separate a wrong-path conversion bug from a projection cap/order issue.
`--tier-sweep` checks hot, expanded, and audit tiers, then recommends the
smallest tier that keeps best/nearest awareness with useful candidate breadth.

Plugin-snapshot performance remains experimental. The hot tier is intended to
provide a fast best/nearest working set, not full scene awareness. Compact
packet files remain the default normal-live bridge until repeated hot-tier runs
stay under the live budget in the local setup.

When `--benchmark` is enabled, `live_status.json` and the console summary break
plugin-snapshot active time into exclusive buckets where practical:

- `pluginSnapshotHttpRequestMillis`
- `pluginSnapshotResponseReadMillis`
- `pluginSnapshotJsonParseMillis`
- `pluginSnapshotEndpointServiceMillis`
- `pluginSnapshotConvertMillis`
- `pluginSnapshotPrefilterMillis`
- `pluginSnapshotWorldBuildMillis`
- `pluginSnapshotCandidateSelectMillis`
- `pluginSnapshotOutputSerializeMillis`
- `pluginSnapshotOutputWriteMillis`
- `pluginSnapshotOverlayStateWriteMillis`
- `pluginSnapshotStatusWriteMillis`
- `pluginSnapshotTotalActiveMillis`
- `pluginSnapshotBottleneck`

`pluginSnapshotBottleneck` names the largest bucket, such as
`endpoint_service`, `http_request`, `json_parse`, `world_build`,
`candidate_select`, or `output_write`. If a snapshot tick is unchanged, the live
processor skips candidate rebuilding and heavy output rewrites, then increments
`pluginSnapshotTicksSkippedAsUnchanged`. If the candidate set signature is
unchanged, it keeps the previous candidate/world-target output and reports
`pluginSnapshotCandidateOutputSkippedUnchanged=true` plus the estimated skipped
bytes.

## Compact Live TCP Stream

The first direct compact stream is a local TCP NDJSON server in the RuneLite
plugin. It publishes the same compact packet envelope as the file bridge, one
JSON packet per line. It binds to `127.0.0.1` by default, rejects non-loopback
hosts, uses a bounded queue, and drops stream packets rather than blocking
RuneLite.

RuneLite config:

- **Emit compact live stream**: enable the local stream publisher.
- **Compact stream host**: default `127.0.0.1`.
- **Compact stream port**: default `8891`.
- **Compact stream queue size**: bounded pending stream packet queue.
- **Compact stream circuit breaker**: pauses stream publishing if stream writes
  or queue pressure are unhealthy.
- **Compact stream max write ms**: stream worker write time budget before the
  circuit breaker trips.
- **Compact stream pause seconds**: temporary stream disable period after a
  circuit breaker trip.
- **Stream also writes files**: keep `live_packets` as a debug mirror while
  streaming.

Stream mode is read-only. It does not click, type, invoke menus, execute
actions, or mutate client/game state.
If **Stream also writes files** is off, the stable `compact-packets` file bridge
will have no latest segment and strict normal live will fail until the file
mirror is re-enabled or you explicitly choose experimental stream mode.

The stream consumer groups packets by tick and waits until each stream tick has
the minimum candidate-building packet set, currently baseline plus projection.
If a baseline-only tick arrives first, the live processor keeps the previous
good candidates and reports the missing packet type instead of flickering to an
empty target list. Stream socket wait/reconnect time is reported separately from
active processing time, so `activeMs` reflects context work rather than
connection waiting.

Stream mode should be treated as experimental until stream-vs-file comparison
passes for your current setup. The compact packet file bridge remains the
stable fallback/debug mirror.

If overlay targets disappear, candidate count drops to zero, or status reports
missing `live_baseline_packet.v1` / `live_projection_packet.v1` while
`inputSourceActive=compact-stream`, switch back to `compact-packets`.

Experimental stream command. Keep fallback enabled while testing:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-stream --stream-fallback-to-compact-packets --compact-stream-host 127.0.0.1 --compact-stream-port 8891 --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Compare stream output to the compact packet file mirror:

```text
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources stream-vs-file --profile woodcutting --latest 5
```

Useful stream diagnostics in `live_status.json` include:

- `compactStreamPacketsByType`
- `compactStreamLatestTickByType`
- `compactStreamMissingRequiredTypesForLatestTick`
- `compactStreamTickBufferSize`
- `compactStreamTicksWaitingForProjection`
- `compactStreamReadMillis`
- `compactStreamParseMillis`
- `compactStreamWaitMillis`
- `compactStreamReconnectMillis`
- `compactStreamSocketTimeouts`
- `compactStreamProjectionPacketsSeen`
- `compactStreamCanBuildCandidates`
- `streamFallbackToFile`
- `streamFallbackReason`
- `compactLiveStreamPacketsOfferedByType`
- `compactLiveStreamPacketsSentByType`
- `compactLiveStreamPacketsDroppedByType`
- `compactLiveStreamCircuitBreakerTripped`

Auto mode now prefers recent compact packet files, then the experimental stream
only when the file bridge is unavailable or stale, then raw tick JSONL:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --compact-stream-port 8891 --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Use the file bridge command as a fallback/debug mirror:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

The RuneLite config option **Emit compact live packets** defaults on for new
configurations. If an older saved profile has it disabled, turn it back on for
normal live mode. Then check the current session and inspect the packet files:

```text
python telemetry-viewer\check_live_setup.py --latest-session
python telemetry-viewer\inspect_live_packets.py --latest-session --summary
python telemetry-viewer\inspect_live_packets.py --session "C:\path\to\session" --summary
python telemetry-viewer\inspect_live_packets.py --latest-session --tail
```

Raw JSONL recording is still the debug/audit/training path. The compact packet
bridge is the first step toward letting the live processor consume small
baseline/delta packets instead of rereading giant raw tick snapshots.

The packet files are segmented by size and retention-pruned so live runs do not
create unbounded disk usage. `live_packet_index.json` records the retained
segments, latest tick/sequence, packet counts by type, retention settings, and
pruned segment count. `latest_segment.txt` points tailing tools at the active
segment.

Useful reader commands:

```text
python telemetry-viewer\inspect_live_packets.py --latest-session --summary
python telemetry-viewer\inspect_live_packets.py --latest-session --latest-only --summary
python telemetry-viewer\inspect_live_packets.py --latest-session --tail --packet-type live_baseline_packet.v1
python telemetry-viewer\inspect_live_packets.py --latest-session --since-sequence 1000 --max-lines 50
```

`live_target_processor.py` consumes compact packet files by default through
`--input-source auto`; the direct compact stream is experimental and must pass
stream-vs-file comparison before daily use. Raw ticks/screenshots remain the
authoritative debug/audit path and are still available with
`--input-source raw-ticks`.

Compact packet live processor commands:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources --profile woodcutting --latest 5
```

`--input-source auto` prefers compact packet files when `live_packet_index.json`
and recent packet segments are present. It only tries the experimental compact
stream when packet files are unavailable or stale, otherwise it falls back to raw
tick JSONL with a clear warning. Use
`--require-compact-packets` when you want a compact transport rather than raw
fallback.
The rolling live output schema stays the same, so `context_service.py`,
`live_context_query.py`, and the live inspector continue reading
`interaction_geometry\live`.

## Default Live Input: Compact Packets

Compact packets are now the normal live input path. Raw tick JSONL remains for
debugging, complete audits, replay, and training datasets. Auto mode prefers
compact packets, and the rolling live files written by `live_target_processor.py`
stay compatible with `context_service.py`.

Use these commands for the default live flow:

```text
python telemetry-viewer\check_live_setup.py --latest-session
python telemetry-viewer\inspect_live_packets.py --latest-session --summary
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
python telemetry-viewer\context_service.py --latest-session --port 8890
```

If compact packets are missing, `check_live_setup.py` explains the missing
pieces and the live processor reports the raw-tick fallback reason. Screenshots
and raw ticks can still grow when debug recording is enabled; compact packet
retention only bounds `live_packets`.

To build a derived perception dataset for the newest session:

```text
python telemetry-viewer\build_perception_dataset.py
```

To initialize a new session from a reusable calibration profile:

```text
python telemetry-viewer\build_perception_dataset.py --calibration-profile "C:\path\to\screen_regions_profile.json"
```

`perception\tick_bundles.jsonl` contains one derived record per tick. Each
bundle joins the authoritative tick JSON with nearby event context, the
session-relative frame path, frame existence at build time, and frame-index
timing when available. `perception\screen_regions.json` is the session-local
normalized region map for review tooling; it does not crop or edit frame
images.

Screen-region profiles are tab-aware. `baseRegions` are always-valid areas such
as the full frame, game viewport, minimap, chatbox, side panel, tabs, compass,
and orb area. `tabProfiles` are side-tab-specific areas. Inventory, equipment,
prayer, magic, and other side-panel tabs need separate crop profiles because the
same side-panel pixels represent different widgets depending on which tab is
open.

When `screen_regions.json` must be created, profile loading uses this order:

1. Existing session `perception\screen_regions.json`
2. `--calibration-profile "C:\path\to\profile.json"`
3. `telemetry-viewer\calibration_profiles\default_screen_regions.json`
4. Built-in approximate fallback regions

The perception dataset is read-only derived data from existing telemetry and
performs no automation, clicking, input hooks, overlays, or client-state
mutation.

## Simplified Calibration And Dataset Flow

1. Calibrate from the launcher:

   ```text
   python telemetry-viewer\telemetry_launcher.py
   ```

   Click **Start Calibration Mode**, edit regions, then click **Save Default
   Profile** for future sessions or **Save Session Profile** for this session
   only.

2. Label active tab ranges with `telemetry-viewer\tab_labels.json`, or use the
   replay labeling UI if present.

3. Build perception:

   ```text
   python telemetry-viewer\build_perception_dataset.py
   ```

4. Generate disposable test crops from the launcher with **Generate Test
   Crops**. Test crops are preview/verification data, not the final training
   dataset. They are written under:

   ```text
   perception\test_crops\<run_id>\
   ```

   Prior test crop runs are preserved unless explicitly cleared.

5. Build persistent training data:

   ```text
   python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
   python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
   ```

   Training crops are durable derived data under:

   ```text
   training_data\crops\
   ```

   The builder is non-destructive by default. It appends new examples or skips
   duplicate keys. Only `--rebuild` clears `training_data` before rebuilding.

6. Inspect status:

   ```text
   python telemetry-viewer\dataset_status.py
   ```

7. Review generated training examples in a local browser:

   ```text
   python telemetry-viewer\training_dataset_inspector.py
   python telemetry-viewer\training_dataset_inspector.py --port 8790
   python telemetry-viewer\training_dataset_inspector.py --session "C:\path\to\session"
   ```

   Open `http://127.0.0.1:8790/`. The inspector shows summary cards,
   filters, crop thumbnails, labels, telemetry summaries, source frames when
   available, and a detail panel for the selected example. You are not
   expected to manually review every crop. Use **Review Queue**, start with
   **Balanced by regionProfile**, size `100`, and `cropExists=true`, then mark
   examples **Good**, **Bad Crop**, **Wrong Label**, or **Unsure**. If training
   data is missing, it shows: `Run python
   telemetry-viewer\build_training_dataset.py first.`

   QA review buttons append local metadata to:

   ```text
   training_data\review_labels.jsonl
   ```

   `review_labels.jsonl` is append-only QA metadata. It does not modify raw
   telemetry, does not overwrite `training_manifest.jsonl`, does not alter
   `training_index.json`, and does not modify crops.

   Missing crop diagnostics separate manifest examples from actual crop files.
   If many crops are missing, filter to `cropExists=true`. Only use
   `--rebuild` when you intentionally want to replace persistent
   `training_data`. `--include-missing-crops` is for diagnostics only, not
   normal training data.

8. Export a curated manifest for later model/training experiments:

   ```text
   python telemetry-viewer\export_curated_training_dataset.py
   python telemetry-viewer\export_curated_training_dataset.py --reviewed-only
   python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
   ```

   You do not need to review every crop. By default, review labels act as
   vetoes: unreviewed examples with existing crops are included, `good`
   examples are included, and latest `bad_crop`, `wrong_label`, or `unsure`
   reviews are excluded. `--reviewed-only` is the strict mode that includes
   only latest `good` reviews.

   Curated output is written to:

   ```text
   training_data\curated\curated_manifest.jsonl
   training_data\curated\curated_index.json
   ```

   `curated_manifest.jsonl` is the clean selected list for later experiments.
   Exporting it does not copy crops, delete crops, modify
   `training_manifest.jsonl`, or modify `review_labels.jsonl`.

9. Inspect derived UI/world target geometry alignment:

   ```text
   python telemetry-viewer\target_geometry_inspector.py
   ```

   Open `http://127.0.0.1:8800/`. The inspector overlays existing
   `interaction_geometry\ui_targets.jsonl` and
   `interaction_geometry\world_targets.jsonl` records on retained frame images.
   It is a local QA viewer only. It does not send input, perform mouse actions,
   interact with RuneLite, or modify telemetry, geometry, crops, or frame
   images. Missing retained frames are shown as a placeholder with the geometry
   still available for inspection.

   If target records exist but no frame image appears, run:

   ```text
   python telemetry-viewer\dataset_status.py
   ```

   Compare the geometry target tick range in the inspector with the retained
   frame tick range in status output. Geometry files may reference older frame
   paths after retention has kept only newer JPGs. Rebuild the derived UI/world
   geometry for currently retained frames:

   ```text
   python telemetry-viewer\build_world_target_geometry.py --target-type npc --only-on-screen --latest-with-frames 100
   python telemetry-viewer\build_ui_target_geometry.py --include-base-regions --latest-with-frames 100
   python telemetry-viewer\target_geometry_inspector.py
   ```

   `--latest-with-frames` checks actual frame files under `frames\` instead of
   trusting stale derived `frame.exists` metadata. If no retained frame files
   remain, collect a fresh session or raise the RuneLite config storage caps
   before collecting a longer QA run.

   World target records include conservative derived labels such as
   `targetRole`, `targetCategory`, and `targetTags`. Examples include
   interactable targets such as banks, doors, trees, ladders, furnaces, ranges,
   and altars; entity targets such as NPCs and players; item targets for ground
   items; and obstacle/navigation geometry such as walls, fences, counters,
   building pieces, and tiles. Obstacle and wall geometry is kept because it can
   be useful for navigation/pathing analysis, but the inspector can hide it with
   role/category/tag filters without deleting any records.

   If a useful object is visible but appears as a fallback label such as
   `SceneObject[1276]`, add a derived label override in:

   ```text
   telemetry-viewer\target_name_overrides.json
   ```

   Example:

   ```json
   {
     "sceneObjects": {
       "1276": {
         "name": "Tree",
         "role": "interactable",
         "category": "tree",
         "tags": ["tree", "clickable_candidate"],
         "notes": "Known visible tree object in this session."
       }
     },
     "groundItems": {},
     "npcs": {}
   }
   ```

   Overrides affect only derived names, roles, categories, and tags when
   `build_world_target_geometry.py` is rerun. They do not change raw telemetry,
   geometry, frame images, RuneLite state, or actions. If scenarios or ranked
   candidates should use new overrides, rerun world geometry, candidate
   selection, and then the scenario builder. To identify candidate object IDs
   for overrides:

   ```text
   python telemetry-viewer\inspect_target_geometry.py --target-type sceneObject --fallback-only --limit 50
   python telemetry-viewer\inspect_target_geometry.py --target-type sceneObject --unclassified --limit 50
   python telemetry-viewer\inspect_target_geometry.py --unclassified-scene-objects --large-only --top-ids --limit 20
   ```

   The `--unclassified-scene-objects` shortcut focuses on visible fallback
   scene objects with usable geometry and unknown/decorative classification.
   `--large-only --top-ids` groups the largest repeated IDs and prints a
   suggested override snippet. Some visible tree-shaped objects are decorative
   or non-interactable; only add confirmed useful IDs to the override file.

   The target geometry inspector and scenario inspector also include an
   **Add/Edit Override** panel. Select an unlabeled scene object, NPC, or ground
   item row, adjust the suggested name/role/category/tags, and click **Save
   Override**. The local API writes only
   `telemetry-viewer\target_name_overrides.json`; it does not modify raw
   telemetry, frame images, geometry files, RuneLite state, or actions. After
   saving, copy the rebuild commands shown in the panel so the derived world
   geometry, target candidates, and scenario dataset pick up the new label.
   The target geometry inspector also has **Copy Override Snippet** for quickly
   copying a selected scene-object ID into the JSON file by hand.

## From Raw Capture To Useful Target Candidates

The target pipeline has three read-only layers with different jobs:

- `interaction_geometry\world_targets.jsonl` is the broad world geometry layer.
  Use it when you want to inspect coverage, visibility, raw projected objects,
  walls, tiles, NPCs, ground items, and unclassified scene objects.
- `interaction_geometry\ui_targets.jsonl` is the calibrated UI geometry layer.
  It describes inventory/equipment/prayer/magic/base UI regions as geometry,
  not actions.
- `interaction_geometry\target_candidates.jsonl` is the filtered and scored
  candidate layer. It is useful for QA and downstream analysis, but it may be
  intentionally much smaller than `world_targets.jsonl` because profiles,
  semantic filters, dedupe, UI-blocked exclusion, and limits can reduce it.

Reusable target classes live in:

```text
telemetry-viewer\target_library.json
```

The library uses schema `target_library.v1`. Each class can match target types,
roles, categories, object IDs, target names, actions, and tags. Initial classes
include trees, oak/willow trees, rocks, fishing spots, doors, walls, NPCs,
players, ground items, bank-related targets, navigation tiles, unknown scene
objects, and unclassified scene objects. This is a labeling and scoring aid
only; it does not change raw capture, geometry, client state, or input.

Reusable profiles live in:

```text
telemetry-viewer\target_profiles.json
```

The profile file uses schema `target_profiles.v1`. Profiles define which target
classes/types/roles/categories to include, whether targets must be on-screen or
have geometry, whether UI-blocked candidates should be excluded, the default
limit, and scoring weights. Current profiles are:

- `broad_qa`: broad visual/debug QA with minimal semantic filtering.
- `woodcutting`: tree/oak/willow candidate QA.
- `navigation_qa`: walls, doors, obstacles, and tile geometry QA.
- `npc_qa`: NPC/player geometry QA.
- `ground_item_qa`: ground item geometry QA.
- `ui_qa`: UI region/slot/spell/prayer geometry QA.

Candidate records now include a stable packet-style surface for future batch or
live-feed consumers: `recordSchema`, `tick`, `source`, `targetKey`/`objectKey`,
`classId`, target type/name/id/hash, role/category/tags, world/scene/local
coordinates, `preferredGeometryType`, `aimPoint`, `geometrySummary`,
`distanceTiles`, `uiBlocked`, `blockingUiRegions`, `qualityScore`,
`qualityTier`, `positiveSignals`, `negativeSignals`, `rejectReasons`,
`profileId`, and `selectedByProfile`. Unknown values are omitted or null.

`qualityTier` is derived from the candidate quality score:

- `excellent`
- `good`
- `questionable`
- `poor`

Positive signals include things like `onScreen`, `geometryAvailable`,
`hasClickbox`, `hasConvexHull`, `hasCanvasTilePolygon`,
`knownTargetClass`, `preferredGeometryAvailable`, `nearPlayer`, and
`profileMatch`. Negative signals include `offScreen`, `missingGeometry`,
`missingClickbox`, `fallbackName`, `unclassified`, `duplicateCandidate`,
`uiBlocked`, `mostlyOffFrame`, and `notProfileMatch` when applicable.

UI-blocked detection uses existing `ui_targets.jsonl` geometry. When a world
candidate aim point lands inside major UI regions such as the minimap, chatbox,
side panel, tabs, inventory, equipment, prayer, or magic regions, the candidate
gets `uiBlocked=true`, `blockingUiRegions`, and `blockedReason`. This does not
remove the candidate unless a profile or CLI flag asks for it. Use
`--exclude-ui-blocked` when you want a clean candidate list for visual QA.

Example commands:

```text
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest 25 --profile broad_qa --limit 2000
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest-with-frames 25 --profile woodcutting --exclude-ui-blocked --limit 500 --open-inspector
python telemetry-viewer\run_target_geometry_pipeline.py --session "<session>" --range 155 179 --profile woodcutting --exclude-ui-blocked --limit 500
python telemetry-viewer\build_world_target_geometry.py --session "<session>" --latest 25 --target-type all
python telemetry-viewer\build_ui_target_geometry.py --session "<session>" --latest-with-frames 25 --include-base-regions --include-all-tab-profiles
python telemetry-viewer\select_target_candidates.py --session "<session>" --latest 25 --target-type all --profile broad_qa --limit 2000 --summary
python telemetry-viewer\select_target_candidates.py --session "<session>" --latest 25 --target-type all --profile woodcutting --exclude-ui-blocked --limit 500 --summary
python telemetry-viewer\summarize_candidate_quality.py --session "<session>" --latest 25 --profile woodcutting
python telemetry-viewer\target_geometry_inspector.py --session "<session>"
```

`run_target_geometry_pipeline.py` is the one-command orchestrator. It prints the
selected session and every command before running it, stops on the first failed
step, and prints a concise step summary. Pass `--session` for an explicit
session, or pass `--latest-session` when you intentionally want the newest
available session. Use `--dry-run` to inspect the command plan without writing
derived geometry files. The pipeline order is world targets, UI targets, ranked
candidates, coverage diagnostic, candidate quality summary, and finally the
local inspector if `--open-inspector` is set.

`broad_qa` is for visual inspection. Task profiles such as `woodcutting` are
read-only task candidate QA profiles, not automation. The project still does not
generate mouse movement, clicks, keyboard input, menus, overlays, or gameplay
actions.

## Target Coverage Diagnostics

`telemetry-viewer\diagnose_target_coverage.py` is a read-only coverage report
for understanding where world targets disappear across the pipeline:

```text
raw tick snapshot -> world targets -> target candidates -> scenario dataset -> inspector filters
```

It does not generate actions, mouse movement, clicks, keyboard input, menu
actions, overlays, or RuneLite state changes. It reads existing telemetry and
derived JSONL files, then reports raw counts, derived target counts, candidate
and scenario counts, tick/frame alignment, viewport-sector coverage, best-effort
identity matching, trace filters, and source-code hints for caps or filters.

Visible objects may be absent from overlays for several different reasons:

- Java capture may be capped before all scene objects are written.
- The scene scan may be radius-limited around the local player.
- Some object layers or background scenery may not be captured.
- Projection geometry may be unavailable for a record.
- `onScreen` or projection filters may remove otherwise captured objects.
- Candidate or scenario rules may intentionally narrow the queue.
- Retained frame files may no longer overlap older geometry ticks.
- Inspector filters or max-draw settings may hide records already in the file.
- Some visible scenery may be non-`TileObject` background/model/paint data.

If `interaction_geometry\world_targets.jsonl` is missing, the derived world
geometry builder has not been run for that session, failed, or wrote to a
different session. Build it before blaming the inspector:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --range 30 30 --target-type all
python telemetry-viewer\select_target_candidates.py --session "C:\path\to\session" --tick 30 --target-type all --limit 500 --summary
```

Use `world_targets.jsonl` as the broad geometry/visibility layer. It should
preserve captured NPC/player/object/tile geometry for QA. Use
`target_candidates.jsonl` as the filtered candidate layer. A large
`worldTargets -> targetCandidates` drop is expected when `--limit`, semantic
filters, or scenario rules are active; it does not by itself mean broad capture
is sparse. For broad QA, inspect world targets first. For task-specific QA,
inspect candidates.

Tick selection examples:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --all-ticks --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --latest 25 --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --range 32 56 --target-type all
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --latest-with-frames 25 --target-type all
```

The builder summary prints `selected by`, `selection source`, selected tick
count, selected tick range, retained frame tick range, and selected frame tick
count. `--latest-with-frames` selects retained-frame ticks joined back to raw
ticks; the other selectors use raw tick records.

For visual QA against retained frames, prefer a frame-aware slice:

```text
python telemetry-viewer\build_world_target_geometry.py --session "C:\path\to\session" --target-type all --latest-with-frames 100
python telemetry-viewer\select_target_candidates.py --session "C:\path\to\session" --latest 100 --target-type all --only-on-screen --geometry-available --limit 500 --summary
```

Newer raw ticks may include `sceneCaptureSummary`, a diagnostic-only counter
object. It reports scene capture mode, bounded scan radius or full-plane scan,
configured max scene object cap, scanned tile bounds, how many scene objects
were seen versus captured, and how many game/wall/decorative/ground objects
were skipped by the cap. For example, it lets the diagnostic say that Java saw
600 scene objects but emitted only 250 because the payload cap was hit, or that
`FULL_CURRENT_PLANE_DIAGNOSTIC` saw and captured all scene objects in the
selected scan. The summary is counters only; it does not add overlays, input,
clicks, menu actions, automation, or client-state mutation.

Example commands:

```text
python telemetry-viewer\diagnose_target_coverage.py --latest 25
python telemetry-viewer\diagnose_target_coverage.py --all-ticks
python telemetry-viewer\diagnose_target_coverage.py --tick 201
python telemetry-viewer\diagnose_target_coverage.py --tick 201 --json
python telemetry-viewer\diagnose_target_coverage.py --latest 25 --scenario tree_cutting
python telemetry-viewer\diagnose_target_coverage.py --object-id 1276
python telemetry-viewer\diagnose_target_coverage.py --near 3200 3200 5
python telemetry-viewer\diagnose_target_coverage.py --project-root C:\Users\stone\osrs-telemetry\example-plugin --latest 5
```

If Java capture is proven to be the bottleneck, prefer debug-only expanded
capture, explicit skip counters, or event-maintained object inventories before
considering full-scene every-tick scans. Full-scene scanning is intentionally a
last resort because it can be expensive and noisy.

## Scene Capture Raw-Force Diagnostic Modes

The RuneLite plugin has a **Scene capture mode** config for short read-only dev
captures:

- `LOCAL_DEFAULT`: preserves the old behavior. It scans the current plane around
  the local player with radius `12` and caps scene objects at `250` per tick.
- `WIDE_DIAGNOSTIC`: scans radius `32` on the current plane and raises the scene
  object cap to `10000`. Use it for short diagnostic sessions when local capture
  is capped.
- `FULL_CURRENT_PLANE_DIAGNOSTIC`: scans every tile on the current plane and
  raises the scene object cap to `25000`. Use it only for short raw-force
  coverage checks.
- `STATIC_SCENE_INDEX_DIAGNOSTIC`: builds a read-only current-plane scene object
  memory/index, maintains it with object spawn/despawn events where available,
  and emits compact `sceneObjectDeltas` plus projected `visibleSceneObjectRefs`
  instead of repeating every unchanged static object in `sceneObjects` each tick.
  This is the preferred long diagnostic mode after raw-force completeness has
  been validated.

The heavy modes stay read-only, but they can produce much larger JSONL tick
files. Expect higher disk usage, slower derived builders, more inspector load,
and greater writer queue pressure. Later optimization should use static scene
indexing or deduplication instead of repeating unchanged scenery every tick.

Raw ticks include `sceneCaptureSummary` when scene capture runs. Check:

- `sceneCaptureMode`
- `fullCurrentPlaneScan`
- `configuredRadius`
- `configuredMaxSceneObjects`
- `scanWidth` / `scanHeight`
- `sceneObjectsSeen`
- `sceneObjectsCaptured`
- `sceneObjectsSkippedByCap`
- `sceneObjectCapHit`
- `captureRatio`
- layer counts such as `gameObjectsSeen`, `gameObjectsCaptured`, and
  `gameObjectsSkippedByCap`
- performance pressure fields such as `sceneCaptureDurationMillis`,
  `snapshotBuildDurationMillis`, `writerQueueSize`, and `writerDroppedRecords`

In `STATIC_SCENE_INDEX_DIAGNOSTIC`, also check `sceneIndexSummary` and
`sceneProjectionSummary`: index/present object counts, new/updated/despawned
counts, full-resync ticks, resync reason, projection refresh mode, objects
updated/reused, visible refs, and projection duration. `objectKey` identifies a
scene object by plane, world/scene tile, layer, id, hash when available, and
orientation, so same-id objects at different locations stay distinct.

`build_world_target_geometry.py` supports both legacy/full snapshot
`sceneObjects` and static-index ticks. In static mode it uses
`visibleSceneObjectRefs` for per-tick world targets and writes
`interaction_geometry\scene_static_index.jsonl` with one compact record per
unique scene object. The world geometry index reports `sourceSchema`,
`objectKeySupport`, static index counts, and per-tick projected object counts.

Example raw-force QA workflow:

1. Set **Scene capture mode** to `WIDE_DIAGNOSTIC` or
   `FULL_CURRENT_PLANE_DIAGNOSTIC` in the plugin config.
2. Collect a short session.
3. Build world targets:

   ```text
   python telemetry-viewer\build_world_target_geometry.py --session "<session>" --latest 25 --target-type all
   ```

   Use `--all-ticks` instead of `--latest 25` for a whole-session build, or
   `--range START END` for a fixed slice. If the summary says only one tick was
   selected, the raw session or selector only provided one tick. If you need
   retained-frame overlap for visual QA, use `--latest-with-frames N`.

4. Select ranked candidates:

   ```text
   python telemetry-viewer\select_target_candidates.py --session "<session>" --target-type all --limit 500 --summary
   ```

   Candidate selection deduplicates same-tick same-object/same-aim records by
   default before applying `--limit`. When `objectKey` is present, it is the
   primary identity key, so same-id objects at different locations remain
   separate. Use `--limit 0` or `--no-limit` for unlimited output after dedupe.
   Use `--no-dedupe` only when debugging raw duplicate inputs.

5. Suggest manual label/category overrides for fallback objects:

   ```text
   python telemetry-viewer\suggest_target_overrides.py --session "<session>" --limit 25
   ```

   This read-only helper prints fallback/unclassified scene object IDs, sample
   locations/object keys, and manual `target_name_overrides.json` skeletons. It
   does not edit override files automatically.

6. Diagnose capture and pipeline coverage:

   ```text
   python telemetry-viewer\diagnose_target_coverage.py --session "<session>" --latest 25 --performance --project-root "C:\Users\stone\osrs-telemetry\example-plugin"
   ```

7. Verify cap-hit ticks, objects seen/captured/skipped by cap, capture ratio,
   world target count, and candidate count.

10. Select ranked target candidates from existing geometry:

   ```text
   python telemetry-viewer\select_target_candidates.py --category bank --only-on-screen --geometry-available
   ```

   This writes:

   ```text
   interaction_geometry\target_candidates.jsonl
   interaction_geometry\target_candidates_index.json
   ```

   Candidate selection ranks existing UI/world geometry records and preserves the
   best available aim geometry, preferring clickboxes, then hulls, tile polygons,
   UI boxes, and points. The output is a read-only handoff/analysis layer: it
   does not send mouse input, create click commands, invoke menus, interact with
   RuneLite, or modify raw telemetry or frame images.
   When player and target world positions are available, the ranker also records
   Chebyshev/Manhattan tile distances and prefers closer entity/NPC candidates;
   distance is a moderate tie-breaker for interactable world objects.
   The ranker collapses duplicate-looking records with the same tick, target
   type, id, world location, and aim point before applying `--limit`; the summary
   reports matching targets before dedupe, duplicates removed, candidates before
   limit, and final candidate count.
   `target_geometry_inspector.py` can load these candidate files and draw ranked
   aim points/preferred geometry alongside the raw UI/world overlays.

11. Export a read-only target handoff:

   ```text
   python telemetry-viewer\export_target_handoff.py --category bank --limit 10
   ```

   This writes:

   ```text
   interaction_geometry\handoff\latest_candidates.json
   interaction_geometry\handoff\latest_candidates.jsonl
   interaction_geometry\handoff\handoff_index.json
   ```

   The handoff files contain ranked candidate geometry for external analysis or
   private-server experiments. They preserve aim points, preferred geometry, and
   scoring reasons, but they do not contain mouse movement, click commands,
   keyboard input, menu actions, or automation instructions.

12. Build a read-only scenario dataset:

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario bank_area
   ```

   Scenarios group useful ranked target candidates by purpose. The first
   template, `bank_area`, selects visible bank-related candidates such as bank
   booths, deposit boxes, bankers, and deposit targets, then preserves nearby
   obstacle/navigation context when available.

   A second template, `goblin_area`, selects visible Goblin NPC candidates and
   preserves nearby obstacle/navigation context:

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario goblin_area
   python telemetry-viewer\scenario_inspector.py --scenario goblin_area --port 8810
   ```

   A third template, `tree_cutting`, selects visible tree scene-object
   candidates and preserves nearby obstacle/navigation context. It is for
   read-only tree target geometry QA only; it does not chop trees, click, send
   input, or generate actions.

   ```text
   python telemetry-viewer\build_scenario_dataset.py --scenario tree_cutting
   python telemetry-viewer\scenario_inspector.py --scenario tree_cutting --port 8810
   ```

   Scenario output is written to:

   ```text
   scenario_datasets\bank_area.jsonl
   scenario_datasets\scenario_index.json
   ```

   Scenario records are geometry/context only. They do not generate mouse
   movement, clicks, keyboard input, menu actions, client-state mutation, or
   automation. Target candidates are ranked geometry records, not commands.
   The scenario builder de-duplicates selected candidates per tick before
   applying `limitPerTick`, so repeated records for the same visible object are
   collapsed into one scenario candidate.

   To visually QA a scenario dataset, run:

   ```text
   python telemetry-viewer\scenario_inspector.py --scenario bank_area
   ```

   Open `http://127.0.0.1:8810/`. The scenario inspector overlays selected
   candidates and optional obstacle/navigation context on retained frame images.
   Context overlays are quiet by default, and the inspector can hide tile
   context or limit context target types so selected targets remain readable.
   It is read-only scenario QA: browser clicks only select rows or overlay
   details inside the local page, and the tool does not interact with RuneLite,
   modify telemetry, modify frame images, or generate input/actions.

Save behavior:

- **Save Default Profile** writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json` and
  initializes future sessions.
- **Save Session Profile** writes
  `sessions\<session_id>\perception\screen_regions.json` and affects only that
  session.
- Existing sessions keep their own session-local profile unless explicitly
  overwritten.

To prepare tick-aligned visual review records from the derived perception
dataset:

```text
python telemetry-viewer\prepare_visual_perception.py
```

The default mode writes `perception\visual_perception_index.json` and
`perception\visual_tick_records.jsonl` with normalized and pixel screen-region
metadata only. It uses Python standard library code and does not crop images.

To attempt derived crop files for a small sample:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --limit 25
```

By default, visual prep includes `baseRegions` only unless you provide an active
tab or ask for all tab profiles. Use an explicit tab when you know which side
tab is visible in the selected frames:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab auto
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab prayer
```

`--active-tab auto` uses the active side-tab inference generated into
`perception\tick_bundles.jsonl`. Detection priority is manual override, widget
inference, event inference, visual fallback if implemented, then `unknown`.
Review inference output with:

```text
python telemetry-viewer\inspect_tab_detection.py --limit 25
```

Manual overrides such as `--active-tab prayer` apply that profile to every
selected tick and mark the active-tab source as `manual`. When auto inference
returns `unknown`, visual prep includes `baseRegions` only and records skipped
tab profiles unless `--include-all-tab-profiles` is set.

To include every tab profile in one derived pass:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
```

For sessions where old frame images may have been deleted by retention, select
newer or existing-frame records:

```text
python telemetry-viewer\prepare_visual_perception.py --latest 25 --only-existing-frames
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames
python telemetry-viewer\prepare_visual_perception.py --generate-crops --generate-grid-slots --latest 25 --only-existing-frames
```

`--latest` selects the newest matching ticks. `--only-existing-frames` is useful
when older tick bundles still exist but their source frame images were already
removed by retention. Crop mode now prefers existing-frame ticks by default
when possible so `--generate-crops --limit N` is less likely to spend the whole
sample on missing retained frames. `--generate-grid-slots` derives slot crops
for grid regions, such as inventory, only when explicitly requested.

Profile-aware test crops are grouped by run id and profile name:

```text
perception\test_crops\<run_id>\tick-XXXXXXXX\base\chatbox.jpg
perception\test_crops\<run_id>\tick-XXXXXXXX\inventory\inventoryGrid.jpg
perception\test_crops\<run_id>\tick-XXXXXXXX\prayer\prayerGrid.jpg
```

Crop mode requires Pillow to already be available. The script does not install
dependencies. If Pillow is unavailable, it prints a warning and continues in
metadata-only mode. Generated visual-prep crops are disposable test crops under
`perception\test_crops\<run_id>\`; source frame images and raw telemetry files
are not modified. The visual prep tool is read-only derived analysis data and
performs no automation, clicking, input hooks, overlays, menu actions, or
client-state mutation.

Screen regions may use typed records:

- `rect`: normalized rectangle box.
- `circle`: normalized center plus radius.
- `ellipse`: normalized center plus X/Y radii and optional rotation metadata.
- `grid`: normalized outer box plus rows, columns, and slot count.

Old-style `{ "x": ..., "y": ..., "w": ..., "h": ... }` regions are still read
as rectangles. Inventory grids should use `rows=7`, `cols=4`, and
`slotCount=28` under `tabProfiles.inventory`; visual prep derives the 28 slot
boxes from the calibrated grid geometry. Equipment and prayer can use their own
grid or slot regions under `tabProfiles.equipment` and `tabProfiles.prayer`.

If crop boxes look off, render region-calibration previews:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame
```

Open the generated overlay and contact sheet under:

```text
perception\region_calibration\
```

Adjust a region with a pixel nudge:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --nudge inventory 20 -10 0 15 --output-calibrated
```

Or set normalized values directly:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --set-region inventory 0.700 0.300 0.250 0.400 --output-calibrated
```

Calibration is a read-only preview unless `--write-screen-regions` is used. It
writes proposed values to
`perception\region_calibration\calibrated_screen_regions.json`, and only
overwrites the derived `perception\screen_regions.json` when explicitly asked:

```text
python telemetry-viewer\calibrate_screen_regions.py --latest-existing-frame --nudge inventory 20 -10 0 15 --write-screen-regions
```

After accepting calibrated regions, rerun visual perception crops:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames
```

For a local browser calibration UI:

```text
python telemetry-viewer\calibrate_screen_regions.py --interactive --latest-existing-frame
```

Open:

```text
http://127.0.0.1:8770/
```

The simplest workflow is to start from the launcher:

1. Run `python telemetry-viewer\telemetry_launcher.py`.
2. Click **Start Core Stack**.
3. Log into RuneLite.
4. Click **Start Calibration Mode**.
5. If needed, click **Refresh to newest frame**.
6. Calibrate base regions and the active tab profile, such as inventory,
   prayer, or equipment.
7. Use **Save Session Profile** for this session only, or **Save Default
   Profile** for future sessions.
8. Click **Generate Test Crops**.
9. Inspect the latest test crop run under the current session perception
   folder.

The launcher Calibration section includes **Start Calibration Mode**, **Open
Calibration UI**, **Generate Test Crops**, **Open Latest Test Crops**, and
**Open Calibration Profile Folder**. The Dataset section includes **Build
Training Dataset**, **Build Training Dataset Rebuild**, and **Open Training
Data Folder**.

The UI lets you refresh to the newest frame, use the latest existing frame,
drag boxes on the captured frame, edit pixel `x/y/w/h` values, switch region
type between `rect`, `circle`, `ellipse`, and `grid`, select **Base regions**
or a tab profile, show base and active-tab regions with distinct styling, add
custom tab profiles, add new region categories, rename or duplicate regions,
delete regions, and edit tags. Adding a region while the Inventory profile is
selected adds it under `tabProfiles.inventory`.

Persistence behavior:

- **Save Session Profile** writes only the selected session's
  `sessions\<session_id>\perception\screen_regions.json`.
- **Save Default Profile** writes
  `telemetry-viewer\calibration_profiles\default_screen_regions.json`.
- Future sessions initialize from the default profile when
  `build_perception_dataset.py` creates their first `screen_regions.json`.
- Existing sessions keep their own session-local calibration unless explicitly
  overwritten.
- **Load default profile** loads that profile into the UI without overwriting
  the session file.

Test crops are disposable previews under
`sessions\<session_id>\perception\test_crops\<run_id>\`. They are not the final
dataset. Persistent training data lives under
`sessions\<session_id>\training_data\`, and training crops live under
`sessions\<session_id>\training_data\crops\`. Normal training builds skip
duplicates and do not wipe previous training data; only `--rebuild` clears and
rebuilds `training_data`.

For smaller reviewable datasets, prefer:

```text
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
```

The `review` preset drops broad low-value base crops such as full frame,
viewport, side panel, and tabs. The `focused-ui` preset concentrates on side-tab
profiles plus useful base context such as chatbox and minimap.

Use **Save calibrated copy** for
`perception\region_calibration\calibrated_screen_regions.json`, and only click
**Write screen_regions.json** when you are ready to update the derived session
`perception\screen_regions.json`.

For inventory calibration, add or select a grid region and set:

```text
rows=7
cols=4
slotCount=28
```

Then rerun visual perception crops:

```text
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --generate-grid-slots --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
```

Mouse clicks in the calibration UI are local to the browser page. The UI does
not interact with RuneLite or the game client. It does not modify raw telemetry
or source frame images, and it only edits the derived
`perception\screen_regions.json` after the explicit write button is clicked.

The calibration tool uses existing frame images only. It does not modify raw
telemetry or source frame images, and Pillow must already be available because
the tool does not install dependencies.

The replay viewer includes a read-only **Analysis** panel derived from the
existing tick, event, frame, and frame-index telemetry. It does not collect new
gameplay data and does not add overlays, input hooks, clicking, menu
manipulation, automation, recommendations, or client-state mutation.

The Analysis panel provides:

- Summary cards for session, tick, event, and frame statistics, including frame
  write-delay diagnostics when available.
- A compact per-tick timeline that can jump the main replay view to the selected
  tick without reloading the page.
- Timeline filters for event category, `eventType` text, ticks with events, and
  frame/capture issues.
- Combat Events, Inventory/Skilling Events, and UI/Menu Events quick panels for
  inspection and replay review only.
- Internally scrolling tables so the frame display and replay controls remain
  usable while reviewing longer sessions.

The right side of the replay viewer is organized into State, Analysis, Events,
and Raw tabs. State shows the selected tick and frame timing, Analysis shows the
derived session/timeline view, Events shows nearby event records, and Raw keeps
tick/event JSON collapsed until opened.

Keyboard shortcuts are local to the replay page and are ignored while typing in
search or jump inputs:

- `ArrowLeft` / `ArrowRight`: previous or next tick.
- `Space`: play or pause replay.
- `S`, `A`, `E`, `R`: switch to State, Analysis, Events, or Raw.

Example questions:

- When did HP drop?
  Compare `hpBoosted` across consecutive tick summaries.

- What NPC was I interacting with?
  Inspect `interactingTarget` on tick summaries, or `InteractingChanged` events.

- What item container changed?
  Filter event summaries where `eventType == "ItemContainerChanged"`.

- What menu options were available?
  Filter event summaries where `eventType == "MenuOpened"` and inspect the
  compact summary or the source event payload.

- What prayers were active?
  Read `activePrayerNames` from tick summaries.

- What was nearby when an event happened?
  Join `event_summary.tickId` to `tick_summary.tickId`, then inspect nearby
  entity/object counts or the original tick record.

- Is there a screenshot for a tick?
  Read `framePath`, `frameExists`, `framePending`,
  `frameExpiredOrMissing`, `frameCaptureStatus`, and `frameCaptureSource` from
  `exports\tick_summary.jsonl`. Missing files with a historical `framePath`
  usually mean frame retention has expired the image.
  If `frameCaptureSource` is `SCREEN_RECTANGLE`, check `frameCaptureWarning`
  because overlapping windows may appear in that frame.

- How long did the frame write take?
  Read `frameWritten`, `frameWriteDelayMs`, `frameTotalLatencyMs`, and
  `frameIndexStatus` from `exports\tick_summary.jsonl` when available. For
  earlier pipeline timing, read `frameCaptureLatencyMs` and
  `frameQueueLatencyMs`. For the full lifecycle record, inspect
  `exports\frame_index_summary.jsonl` or the raw source sidecar at
  `frames\frame_index.jsonl`.

- Why does `frameExists` briefly show false?
  Frame writes are asynchronous. `frameCaptureStatus == "QUEUED"` means the
  frame capture/write was requested. For the newest active tick, `framePending
  == true` means the image may still be arriving inside the shared freshness
  grace window. For older ticks, a missing frame is reported as
  `frameExpiredOrMissing == true`.

- Why do deleted or expired frames appear in validation?
  `validate_session.py` reports deleted/expired frame-index counts so retention
  behavior is visible. Those records are informational by themselves; the
  original tick remains valid unless there is a real JSON/schema/required-field
  problem.

## Rolling live target processor

The batch target pipeline remains the best path for durable debug sessions,
training data, and reproducible QA:

```text
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest 25 --profile broad_qa --limit 2000
```

For local live QA, use `live_target_processor.py`. It uses compact live packets
by default, keeps a rolling in-memory tick window, converts the current window
to world target geometry and ranked candidate packets, and writes rolling
derived files under:

```text
sessions\<session_id>\interaction_geometry\live\
```

The live processor does not click, send input, invoke menus, route actions, or
mutate client state. It also does not permanently archive every tick by default;
the live files represent the current rolling window and are safe to overwrite.
Full snapshot debug capture modes remain available for short diagnostic runs.

Primary outputs:

- `live_world_targets.jsonl`: rolling world target geometry records.
- `live_ui_targets.jsonl`: optional copied UI target records for the window.
- `live_candidates.jsonl`: rolling ranked candidate packets.
- `live_tick_summary.jsonl`: per-tick live processing summaries.
- `live_status.json`: current processor status, counts, warnings, and latest
  tick.
- `live_index.json`: paths and metadata for the rolling live files.

Example one-shot live QA pass:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile broad_qa --window-ticks 100 --once --summary
```

Example follow-mode woodcutting QA pass:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --window-ticks 100 --follow --exclude-ui-blocked --limit 500 --summary
```

`--exclude-ui-blocked` automatically includes existing `ui_targets.jsonl` records
for the rolling window when they are available, because UI blockers are derived
from calibrated UI geometry.

Open the inspector against rolling live output:

```text
python telemetry-viewer\target_geometry_inspector.py --session "<session>" --live
```

Use the batch pipeline when you need stable files for analysis or training. Use
the rolling live processor when you want the latest targets/candidates to update
while a short read-only telemetry session is being collected.

## Fast live context layer

The live processor is candidate/profile-first by default. Future consumers
should prefer the small live context files:

- `live_baseline_state.json`: latest compact player/camera/frame/scene/cache
  and candidate summary.
- `live_context_index.json`: query-ready best/nearest candidates by class,
  candidate counts, and live file paths.
- `live_candidates.jsonl`: selected read-only target context packets.
- `live_navigation_summary.json`: current read-only navigation/collision
  readiness summary.
- `live_performance_summary.json`: rolling latency statistics for recent live
  processor updates.
- `live_status.json`: timing, output byte counts, source cap status, and live
  health.

`live_world_targets.jsonl` is no longer the primary live output. Full broad
world target output can be huge in full current-plane sessions because it
contains every captured object/tile target. Suppressing or limiting this file
does not mean source scene knowledge is lost: raw ticks and scene summaries are
still read, source cap status is reported, and cached/profile-filtered world
records still feed candidate selection.

World target output policy:

- `--emit-world-targets none`: do not write `live_world_targets.jsonl`.
- `--emit-world-targets candidates`: write only world records backing selected
  candidates. This is the recommended default.
- `--emit-world-targets profile`: write profile-matching world targets before
  final candidate limit.
- `--emit-world-targets visible`: write on-screen world targets, capped by
  `--world-target-output-limit`.
- `--emit-world-targets full`: write the broad world layer. This is debug-only
  and can be very large.

Startup/follow behavior:

- `--startup-backfill-ticks N` limits initial catch-up. Default is `10`.
- `--no-startup-backfill` starts from the current end of the live input and
  only processes newly appended records.
- `--process-existing` processes the current rolling window before following.
- Follow mode reuses cached per-tick work for older ticks and processes only
  newly appended ticks unless `--force-window-rebuild` is passed.

`live_status.json` includes timing buckets and byte counts: file discovery,
tail read, line split, JSON parse, raw tick ingest, baseline, activity,
inventory delta, liveness update, world target build/filter, candidate
selection, context index, UI target load, output serialization/write, total
wall time, output bytes, `budgetExceeded`, and `warningUpdateExceeded`.
`live_performance_summary.json` keeps the last 100 update samples in memory and
writes rolling `avgTotalMs`, `p50TotalMs`, `p90TotalMs`, `p95TotalMs`,
`maxTotalMs`, average candidate/write time, raw seen/processed/coalesced
counts, write failures, and recommendations.

Fast one-shot woodcutting context test:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --once --startup-backfill-ticks 5 --window-ticks 5 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Fast follow mode:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

With UI blocking:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --startup-backfill-ticks 25 --window-ticks 100 --limit 500 --include-ui-targets --exclude-ui-blocked --emit-world-targets candidates --summary --benchmark
```

Debug full world output:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source raw-ticks --profile broad_qa --once --window-ticks 5 --limit 2000 --emit-world-targets full --summary --benchmark
```

Live inspector:

```text
python telemetry-viewer\target_geometry_inspector.py --session "<session>" --live
```

Read-only query helper:

```text
python telemetry-viewer\live_context_query.py --session "<session>" --summary
python telemetry-viewer\live_context_query.py --session "<session>" --nearest tree --json
python telemetry-viewer\live_context_query.py --session "<session>" --nearest oak_tree --max-distance 30 --json
python telemetry-viewer\live_context_query.py --session "<session>" --baseline --json
```

The query helper only reads live files. It does not click, send input, invoke
menus, perform routing, or generate actions.

## Live Context QA

`telemetry-viewer\live_context_query.py` is a read-only mock context oracle for
the rolling live files. It validates whether the current telemetry can answer
brain-facing context questions such as where the player is, which useful target
candidates are nearby, whether a candidate is on screen, whether it has an aim
point telemetry field, whether that point is UI-blocked, whether the live feed
is fresh, and whether source scene knowledge appears complete.

It does not execute actions, choose clicks, send mouse or keyboard input,
manipulate menus, interact with RuneLite, or mutate telemetry. Screen
coordinates and aim points are reported only as read-only telemetry fields for
QA and future consumers.

Response schemas are versioned:

- `live_context_summary.v1` for `--summary`
- `live_context_answer.v1` for `--nearest` and `--best`
- `live_task_context.v1` for `--task woodcutting`
- `live_context_self_test.v1` for `--self-test`

Use `--summary` to inspect baseline state, freshness, player location,
candidate counts, source cap status, processor budget status, and live file
warnings. Use `--nearest tree` or `--best tree` to inspect candidate quality.
Use `--task woodcutting` to check whether the live files can answer the core
woodcutting context questions without implying any action. Use `--self-test`
before longer experiments to confirm the baseline/status/context/candidate
files are readable, fresh, and source capture is not capped.

Human output is compact by default. Use `--fields normal` or `--verbose` to
print top candidates, and `--fields full` when you want expanded details.
`--top N` controls how many candidates are shown in normal/full output. JSON
output is also compact by default; use `--fields full` or `--verbose` for the
full payload, or `--compact-json` to explicitly request compact JSON. Add
`--benchmark` to include query read/parse/select timing.

Example commands:

```text
python telemetry-viewer\live_context_query.py --latest-session --summary
python telemetry-viewer\live_context_query.py --latest-session --nearest tree --json
python telemetry-viewer\live_context_query.py --latest-session --best tree --json
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --json
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --fields normal --top 3
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --json --compact-json
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --benchmark
python telemetry-viewer\live_context_query.py --latest-session --self-test
python telemetry-viewer\live_context_query.py --latest-session --watch --nearest tree --interval 1
```

## Local Collision Window And Reachability QA

Navigation readiness is read-only context. It does not move the player, click,
send input, invoke menus, route actions, or execute movement. It answers
whether the current telemetry knows enough to reason about navigation later.

Compact live packets include `live_navigation_packet.v1` when compact packets
are enabled. The packet carries player world/scene/local tile fields, collision
map dimensions, blocked-tile counts, and a collision hash/signature. Normal live
mode also emits `live_collision_window_packet.v1`, a bounded local collision
window around the player. The optional `live_collision_grid_packet.v1` is
debug-only and disabled by default; normal live QA does not emit the full
`104x104` collision grid.

The live processor writes:

```text
interaction_geometry\live\live_navigation_summary.json
```

Important fields:

- `collisionKnown`: collision summary was available.
- `playerTileKnown`: player scene tile was known.
- `mapWidth` / `mapHeight`: local collision map dimensions.
- `blockedMovementTileCount` / `blockedFullTileCount`: compact obstacle counts.
- `collisionHash`: summary hash for detecting collision-map changes.
- `collisionWindowAvailable`: local collision flags are available around the
  player.
- `collisionWindowRadius` / `collisionWindowBounds` / `collisionWindowHash`:
  window metadata.
- `reachabilityComputed`: `true` when local window reachability was attempted.
- `fullCollisionGridAvailable`: whether a debug full-grid packet was present.

Candidate packets may include a compact `navigation` object with
`playerTileKnown`, `targetTileKnown`, `samePlane`, `distanceTiles`, and
`directReachability`. With a local collision window, the processor performs a
small conservative 4-direction BFS from the player tile to the target tile or a
walkable adjacent tile. Results are read-only observations:
`reachable`, `blocked`, or `unknown`.

If the local collision window is available, context responses return
`navigationReadiness.status="local"` and candidate packets carry per-candidate
reachability details. If only the collision summary is available, the status is
`summary`. If collision data is missing, the task report returns `unknown`.
Full pathfinding remains a future capability and is reported separately from
local reachability.

Useful checks:

```text
python telemetry-viewer\inspect_live_packets.py --latest-session --summary
python telemetry-viewer\check_live_setup.py --latest-session
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Context request with navigation readiness:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("baseline", "best:tree", "nearest:tree", "inventory", "activity", "liveness", "navigation_readiness", "diagnostics")
  maxCandidates = 1
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

## Generic task state model

Daily brain output now keeps the existing woodcutting-specific phase and also
adds `genericTaskState`.

Example generic phases:

- `target_selected`: a current target intent exists.
- `wait_for_result`: the task appears busy or recently changed and should keep
  observing.
- `inventory_full`: inventory state blocks the current gather loop.
- `goal_complete`: displayed goal progress reached the requested goal count.
- `blocked`: candidates exist but the local context says they are blocked or
  unreachable.
- `needs_more_context`: context is stale, missing, or has no candidates.

This is read-only interpretation. It does not execute actions, click, type,
invoke menus, or create new daily files. Future banking, mining, waypoint, UI,
or inventory-slot tasks should map their task-specific interpretation into
these generic phases instead of adding one-off daemon branches.

Target wording:

- `activeIntentTarget` is the thing the current generic phase is focused on.
- `availableTarget` is useful context that may still be visible.
- `previousIntentTarget` is the last known target before a task transition.

For `inventory_full`, the active intent is `needs_service`. The tree that was
being watched may still be shown as previous or available context, but it is no
longer the current target and the daily intent overlay should not draw it as
`selected_target`. Service targets will be supplied later by a read-only
analyzer; no banking action is added here.

## Brain Resource Progress Idempotency

Woodcutting progress is computed by `telemetry-viewer\resource_progress.py`.
That module is the single source of truth for resource item counting, baseline
initialization, daily held-vs-baseline progress, and old-state repair.

Daily rules:

- Inventory items win over `inventory.resourceCounts` when both are present.
- `itemId = null` never counts as a real log item.
- The first valid inventory snapshot after reset establishes the baseline and
  counts as `0 / goal`.
- The same inventory signature is idempotent: it does not add gained or removed
  progress on repeated polls.
- Moving a log between slots is not a gain.
- Daily gained logs are `max(0, currentHeldCount - baselineHeldCount)`.
- Daily mode does not use cumulative gained/removed counters.
- Old or partial brain state without the current progress schema is treated as
  untrusted; unreliable gained/removed counters are cleared.

With daemon writes off, use the daemon API for diagnostics instead of stale
rolling live files:

```text
python telemetry-viewer\diagnose_brain_progress.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --goal-count 5
```

Daily daemon reset:

```powershell
$brainState = Join-Path $env:USERPROFILE ".osrs-telemetry\brain_state_woodcutting.json"
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --reset-brain-state --brain-state-file "$brainState" --summary --benchmark
```

## Recording modes

Compact packets are now the normal live substrate. Raw tick/event JSONL and
screenshots are optional debug/audit outputs, not required by
`live_target_processor.py`, `context_service.py`, the human dashboard, or the
debug overlay when compact packets are active.

RuneLite config modes:

- `LIVE_COMPACT_ONLY`: normal live use. Writes compact live packets and small
  session metadata. Full raw ticks, raw events, and frames are suppressed by
  mode.
- `LIVE_COMPACT_WITH_FRAMES`: compact live packets plus bounded frame capture
  at the live frame interval for visual QA. Full raw ticks/events remain off.
- `DEBUG_RECORDING`: preserves full raw tick JSONL, event JSONL, frame capture,
  dictionaries, manifest data, and existing batch/audit workflows.
- `HYBRID_DEBUG`: reserved for compact packets plus sampled debug snapshots.
  The current pass keeps normal live and full debug behavior separate.

`manifest.json` and live writer-health packets expose
`recordingMode`, `rawTickRecordingEnabled`, `rawEventRecordingEnabled`,
`frameRecordingEnabled`, `compactPacketRecordingEnabled`, and written/suppressed
counters. Missing raw ticks are expected in `LIVE_COMPACT_ONLY`; use
`DEBUG_RECORDING` when you need batch builders, replay/debug datasets, or full
audit/training files.

Normal live:

```text
python telemetry-viewer\check_live_setup.py --latest-session --require-compact-packets
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Debug audit:

```text
Set RuneLite Telemetry Collector recording mode to DEBUG_RECORDING, collect a session, then run:
python telemetry-viewer\run_target_geometry_pipeline.py --latest-session --latest-with-frames 25 --profile broad_qa --limit 2000 --open-inspector
```

Batch/debug builders that require raw ticks will print a clear message when a
compact-only live session is selected.

## Human-Readable Live Context Summary

JSON responses are for machines. The human summary is for quick read-only QA
while the live processor and context service are running. It summarizes current
context such as player location, inventory, best tree, reachability, liveness,
and diagnostics. It does not click, execute actions, send input, manipulate
menus, or mutate game/client state.

One-shot mission-control summary:

```text
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --human
```

Shorter one-screen summary:

```text
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --compact-human
```

Refreshing terminal summary:

```text
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --watch-human --interval 1
```

Reachability-focused human report:

```text
python telemetry-viewer\live_context_query.py --latest-session --reachability --class-id tree --human --top 5
```

Context service friendly endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8890/summary?task=woodcutting
```

The same endpoint can return the underlying compact `context_response.v1`:

```powershell
Invoke-RestMethod "http://127.0.0.1:8890/summary?task=woodcutting&format=json&top=3"
```

## Live Event Timeline

`live_target_processor.py` writes a bounded read-only event timeline:

```text
interaction_geometry\live\live_event_timeline.jsonl
```

Each line uses schema `live_context_event.v1` and records important state
changes without producing actions or instructions. Event fields include
`generatedAtUtc`, `tick`, `eventType`, `severity`, `summary`, `details`,
`relatedCandidate`, `previousValue`, `currentValue`, `source`, and `profile`.
The timeline is capped by `--event-timeline-limit` (default 200; `--event-limit`
remains a compatibility alias) so it does not grow forever. Use
`--disable-event-timeline` when you want the processor to skip timeline output
for a diagnostic run.

Typical event types include:

- `best_candidate_changed`
- `nearest_candidate_changed`
- `candidate_count_changed`
- `target_liveness_changed`
- `target_depleted`
- `liveness_suppressed_candidate`
- `depleted_candidate_suppressed`
- `candidate_revived`
- `best_candidate_aim_point_changed`
- `inventory_changed`
- `inventory_free_slots_changed`
- `inventory_full_changed`
- `activity_state_changed`
- `woodcutting_state_changed`
- `player_animation_changed`
- `interacting_target_changed`
- `reachability_changed`
- `best_candidate_reachability_changed`
- `nearest_candidate_reachability_changed`
- `collision_window_availability_changed`
- `target_outside_collision_window`
- `warning_status_changed`
- `source_cap_changed`
- `budget_exceeded_changed`
- `write_failures_changed`
- `input_source_changed`
- `compact_packet_fallback_changed`
- `live_freshness_changed`

The human dashboard shows recent events by default:

```text
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --human --events 5
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --watch-human --interval 1 --events 5
python telemetry-viewer\live_context_query.py --latest-session --events-only --events 20
python telemetry-viewer\live_context_query.py --latest-session --events-only --events 20 --json
```

Live processor timeline controls:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --event-timeline-limit 200 --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

The context service can return recent events for machine consumers:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("baseline", "best:tree", "nearest:tree", "events", "diagnostics")
  maxCandidates = 1
  maxEvents = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

## Mock Brain Rehearsal

`mock_brain_rehearsal.py` is a read-only rehearsal client for future
brain-style consumers. It queries `context_service.py` over localhost, reads the
compact `context_response.v1`, and classifies the current task phase without
clicking, typing, walking, manipulating menus, or executing actions.

Woodcutting phases include:

- `no_context`
- `stale_context`
- `no_target_observed`
- `target_available`
- `target_unreachable`
- `target_depleted`
- `likely_busy`
- `likely_idle`
- `inventory_full`
- `waiting_for_respawn`
- `unknown`

Use it to verify whether the current compact context is enough for future task
reasoning. Output is intentionally observational and ends with
`No action emitted.`

`phase` is the current interpreted task state. `substate` is a recent or
contextual signal, such as `recent_target_depletion_observed`, that should not
be confused with the current best target state. Inventory changes are normally
reported as a substate unless they block the task, while `inventory_full`
remains a blocking phase.

The report separates:

- current phase
- current target state
- recent task signals
- recent system signals
- blocking conditions
- missing capabilities

Task events are shown by default. System/health events such as budget toggles,
write failures, source cap changes, and input source changes are counted but
hidden unless `--show-system-events` or `--event-priority all` is used.

## Mock Brain Activity Classification

`likely_busy` requires positive current evidence:

- an explicit interacting target is present
- an active non-idle animation is present
- woodcutting state is `likely_chopping`

`UNKNOWN`, `null`, empty, or missing interaction values are not treated as busy.
`animation = null` means the animation is unknown, and `animation = -1` or `0`
means no active animation. Recent target depletion and inventory changes are
reported as task signals/substates; they do not override the current phase when
a reachable replacement target is available.

Useful substates include:

- `recent_target_depletion_observed`
- `recent_inventory_change`
- `liveness_assumed`
- `movement_unknown`
- `activity_unknown`
- `no_explicit_busy_evidence`
- `candidate_temporarily_empty`

`target_available` means a valid target exists and there is no true busy
evidence. Missing or unknown activity fields lower confidence and are surfaced
as substates instead of forcing `likely_busy`.

Start `context_service.py` first, then run the rehearsal client.

One-shot human rehearsal:

```text
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --human
```

Refreshing rehearsal:

```text
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --watch --interval 1
```

Machine-readable rehearsal:

```text
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --json
```

Include system events:

```text
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --human --show-system-events
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --json --event-priority all
```

Request suggested missing read-only watches explicitly:

```text
python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --request-missing-watches
```

## Brain Core MVP

`brain_core.py` is the first real external brain core. It behaves like a
future out-of-process brain client: it talks to `context_service.py` over
localhost, keeps lightweight task memory, classifies the current woodcutting
phase, decides which observations are still needed, and emits no action
commands.

The brain core is read-only. It does not click, type, move, invoke menus,
manipulate RuneLite, or call any hands/input layer. Its output is internal
state only: `internalNextState`, `observationNeeds`, `blockingConditions`, and
optional bounded `suggestedWatchRequests`.

One-shot:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --human
```

Watch:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --watch --interval 1
```

Machine-readable decision:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --json
```

Persist brain memory:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --watch --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```

Allow it to request suggested bounded read-only watches:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --request-missing-watches
```

Current phases:

- `no_context`
- `stale_context`
- `setup_observing`
- `target_available`
- `likely_busy`
- `monitoring_progress`
- `goal_complete`
- `target_depleted`
- `waiting_for_respawn`
- `inventory_changed`
- `inventory_full`
- `no_target_observed`
- `blocked_or_unreachable`
- `missing_capability`
- `unknown`

`likely_busy` requires positive current evidence such as an explicit
interacting target, active animation, or a woodcutting-like activity state.
Unknown interaction or missing animation data does not make the brain busy.
Recent depletion with a valid replacement stays a substate rather than
becoming the current phase.

## Brain Goal Completion

`goal_complete` is a read-only interpretation that means observed resource
progress reached or exceeded `--goal-count`. It does not click, move, type,
invoke menus, or execute any action. It only changes the brain's internal
reported state.

When the goal is complete, `brain_decision.v1` includes:

- `goalComplete: true`
- `goalProgress.complete: true`
- `goalProgress.gainedSinceStart`
- `internalNextState: hold_goal_complete_state`

`goal_complete` overrides ordinary live states such as `target_available`,
`likely_busy`, and `monitoring_progress`, but stale or unavailable context is
reported first. Daily woodcutting completion is based on the held-vs-baseline
inventory snapshot count and is labeled with
`progressSource=inventory_snapshot_held_vs_baseline`.

Example:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --watch --interval 1
```

## Brain Resource Progress Tracking

Woodcutting progress is tracked by read-only inventory resource counts. The
brain counts known log item IDs from `inventory.items`, stores the starting
count in `brain_state.v1`, and reports daily goal progress as current held logs
minus baseline held logs. It does not use `freeSlots`, `totalItemQuantity`, old
cumulative counters, or slot-diff history as daily goal progress.

Tracked woodcutting resource IDs live in:

```text
telemetry-viewer\task_resources.json
```

The default `woodcutting_logs` group includes:

- Logs: `1511`
- Oak logs: `1521`
- Willow logs: `1519`
- Maple logs: `1517`
- Yew logs: `1515`
- Magic logs: `1513`

Daily progress uses:

```text
progressSource=inventory_snapshot_held_vs_baseline
```

If inventory items are missing, progress remains unknown and the brain reports
that `inventory.items` is needed. Dropping or depositing logs can reduce the
daily held-vs-baseline progress because the goal means "hold N more logs than
the reset baseline." Moving a log between slots does not count as gaining
another log because the total resource quantity is unchanged.

Use the progress diagnostic when a slot appears in `matchedSlots` but the brain
does not increase progress:

```text
python telemetry-viewer\diagnose_brain_progress.py --latest-session --task woodcutting --goal-count 5 --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```

Watch woodcutting progress:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --watch --interval 1
```

Watch with persisted state:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --watch --interval 1 --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```

Reset the persisted progress baseline:

```text
python telemetry-viewer\brain_core.py --task woodcutting --goal-count 5 --reset-state --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```

## Capability Registry and Watch Requests

The context service exposes a read-only capability registry and a bounded watch
request lane for future brain-style clients. A client can ask what observations
are available now, what is missing, and whether a missing field has a safe
watch definition.

Capability registry:

```powershell
Invoke-RestMethod http://127.0.0.1:8890/capabilities
```

Watch library and active watch requests:

```powershell
Invoke-RestMethod http://127.0.0.1:8890/watches
```

Bounded read-only watch request:

```powershell
$request = @{
  schema = "context_watch_request.v1"
  task = "woodcutting"
  watches = @(
    @{
      alias = "example_state"
      type = "builtin"
      id = "inventory.summary"
      sampleMode = "on_change"
      ttlTicks = 500
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/watch-request" -Body $request -ContentType "application/json"
```

Context request with watches/capabilities:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("watches", "watch:inventory_summary", "capability:watch_values.java_runtime")
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

Watch requests are typed, TTL-limited, capped, and observation-only. The first
implementation writes the bounded request file for future Java-side support and
exposes builtin watch values derived from existing compact packets in
`interaction_geometry\live\live_watch_values.json`. Java dynamic varbit/varp
watch polling is intentionally reported as a future capability until the plugin
implements that read-only poller.

## Live Control Panel

The live control panel is a small Windows-friendly Tkinter launcher for the
read-only live workflow. It starts and monitors local helper processes, shows
bounded logs, and polls the current live status files. It does not click, type,
invoke menus, execute actions, mutate RuneLite/game state, or add overlays.

Start it from the plugin root:

```text
python telemetry-viewer\live_control_panel.py
```

Recommended startup order:

1. Click `Start Normal Live Stack`.
2. Wait for compact packets and live setup to report PASS/WARN.
3. Enable the RuneLite debug overlay only if visual QA is desired.
4. Use `Stop All` to stop helper processes started by the panel.

Useful buttons:

- `Start Normal Live Stack`: starts RuneLite dev if needed, waits for compact packets, runs setup, and starts the live processor, context service, and human dashboard.
- `Restart Live Stack`: restarts the live sidecars without killing unrelated processes.
- `Start RuneLite Dev`: runs `.\gradlew.bat run`.
- `Check Live Setup`: runs `check_live_setup.py --latest-session`.
- `Inspect Compact Packets`: runs `inspect_live_packets.py --latest-session --summary`.
- `Start Live Processor`: starts the compact-packet realtime live processor using the selected profile/options.
- `Start Context Service`: starts `context_service.py --latest-session --port <port>`.
- `Start Human Dashboard`: starts the refreshing human dashboard.
- `Human Dashboard with Events`: starts the dashboard with an explicit recent-event count.
- `Event Timeline`: shows only recent live timeline events.
- `Start Mock Brain Rehearsal`: runs the read-only phase classifier against the context service.
- `Debug Audit Tools`: launches the batch audit pipeline and warns that raw ticks are required.
- `Start Live Inspector`: starts the browser-based live geometry inspector.
- `Health Check`: queries `http://127.0.0.1:<port>/health`.
- `Request Context Once`: POSTs a compact woodcutting context request and prints a readable summary.
- `Stop Selected` / `Stop All`: terminates only helper processes started by this panel.

The panel prints each command before starting it. Logs are capped to the latest
lines so the UI stays responsive during longer live sessions.

## Telemetry Debug Overlay

The telemetry debug overlay is an optional RuneLite overlay for visual QA. It
is disabled by default and only draws read-only observations from:

```text
interaction_geometry\live\overlay_debug_state.json
```

Daily `live_core_daemon.py` writes this tiny file from brain intent markers by
default. It draws the selected target plus a small backup set, while candidate
context remains internal. Visual QA can switch to candidate markers with
`--overlay-mode candidates`; debug/audit can use `--overlay-mode debug`.

The selected daily marker is stabilized in memory before the overlay state is
written. Raw best-candidate ranking may jitter for a tick, but the visible
intent marker stays on the current valid target unless a hard switch condition
appears, such as a task/profile/intent change, explicit depleted/stale/despawned
or unreachable state, forced switch, inventory/task transition, or
higher-priority interrupt. If the selected target is simply absent from the
current candidate slice for a short transient gap, the daemon keeps the
last-known selected identity and marks `retainedDueToGrace=true`; the default
grace is two ticks. The stabilizer reads only the daemon's current
candidates/context and writes no history files.

Intent markers include stable world/scene/local identity when available. The
RuneLite overlay uses that identity to resolve the current scene object during
render, so camera movement does not have to wait for the next telemetry tick to
move the label/crosshair. For scene-object markers it prefers live object
clickbox geometry, then stored hull/clickbox/bounds payloads, then tile
projection, then last-known aim points.

The daemon also deduplicates selected and backup markers before writing overlay
state. If the selected marker is aim-point-only but a duplicate candidate has
clickable hull or clickbox geometry, that geometry is merged into the selected
marker and the duplicate backup is omitted. Backup candidates are stabilized
separately: the selected target is excluded, and previous backup identities are
preferred while they remain valid so the small overlay set does not reorder on
minor score jitter.

For intent flicker debugging:

```text
python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent --top 10
```

The diagnostic prints the raw best target, stabilized target, stable tick count,
missing tick count, grace-retention flag, switch reason, and the last few
in-memory switch audit entries. It can report when a selected target changed
briefly and reverted.

The overlay can draw:

- selected brain intent markers
- backup candidate markers
- candidate aim points in visual/debug modes
- compact bounds or clickable hull/small polygons when available
- labels with class, distance, reachability, and liveness
- a small read-only status panel
- collision-window summary when enabled
- one latest event summary when `Debug overlay latest event` is enabled

It does not click, type, invoke menus, execute actions, or mutate game/client
state.

Usage:

1. Start RuneLite dev.
2. Enable `Telemetry debug overlay` in the plugin config.
3. Start the streamlined live daemon so `overlay_debug_state.json` is refreshed.
4. Compare the overlay with the human dashboard and live inspector.

Start the daily daemon with intent overlay output:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

Advanced visual QA candidate overlay:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode candidates --overlay-debug-target-limit 25 --human-dashboard --brain-task woodcutting --summary --benchmark
```

If the overlay needs a specific state file, set `Debug overlay state path` to
the full path of `overlay_debug_state.json` for the active session. Leaving it
blank uses the current telemetry session when available.

### Clickable Hull Overlay

Clickable hull visualization draws the observed on-screen clickbox/clickable
area shape for a target when that geometry is available. This is read-only
visual QA: it does not click, send input, invoke menus, or execute actions.

Overlay geometry settings:

- `Debug overlay geometry mode`: `CLICKABLE_HULL`, `BOUNDS`,
  `HULL_AND_BOUNDS`, `TILE_POLYGON`, `AIM_ONLY`, or `ALL_GEOMETRY_DEBUG`.
- `Debug overlay clickable hull`: draw observed clickbox/clickable hull
  polygons when available.
- `Debug overlay bounds`: draw rectangle fallback/bounds.
- `Debug overlay tile polygon`: optional tile polygon debug drawing.

Fallback order:

```text
live object clickbox -> clickableHull -> clickboxPolygon -> convexHull -> canvasTilePolygon -> bounds -> live tile fallback -> aim point
```

Normal compact packets stay lean. Polygon geometry is included only for capped
visible projection refs when `compactLiveIncludeClickableHull`,
`compactLiveIncludeCanvasTilePolygon`, `compactLiveIncludeConvexHull`, or
`compactLiveIncludeHeavyGeometry` is enabled. The optional RuneLite debug overlay
also requests the same capped geometry when it is enabled with clickable hull or
tile-polygon drawing. `compactLiveGeometryMaxRefs` limits how many compact
projection refs can carry polygons per tick. The live processor also has
`--overlay-debug-hull-limit`, so it can draw many read-only target boxes while
copying polygon hulls only for the highest-ranked overlay targets.

To make clickable hulls appear:

1. Enable `Telemetry Debug Overlay`.
2. Set `Debug overlay geometry mode` to `CLICKABLE_HULL`, `HULL_AND_BOUNDS`, or
   `ALL_GEOMETRY_DEBUG`.
3. Keep `Debug overlay clickable hull` enabled.
4. If hulls are still missing, enable `compactLiveIncludeClickableHull` directly
   or temporarily enable `compactLiveIncludeHeavyGeometry` for visual QA.
5. Keep `compactLiveGeometryMaxRefs` modest, such as 50.

Diagnostic command:

```text
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --top 10
python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent --top 10
```

The diagnostic reports clickable hull count, clickbox polygon count,
convex-hull fallback count, bounds-only count, aim-only count, compact geometry
config/cap counters, geometry source counts, and missing hull reasons for the
inspected targets. The concrete Java -> compact packet -> Python candidate ->
overlay state handoff is documented in `docs/clickable_hull_pipeline.md`.
With `--intent`, it also reports selected/backups, duplicate selected-in-backup
identities, selected geometry source, and whether marker merging failed.

If hulls appear only on odd corner/edge objects, use the geometry diagnostic to
compare the top candidates, overlay state, and latest compact projection refs:

```text
python telemetry-viewer\diagnose_overlay_geometry.py --latest-session --class-id tree --top 25
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --top 25
```

`diagnose_overlay_geometry.py` reports whether each top candidate has a matching
compact projection ref with hull geometry by `objectKey`, by stable fallback
keys (`hash`, `id + world tile + plane`, or `id + scene tile + plane`), or no
matching hull at all. Its summary includes total overlay targets, targets with
hulls, whether the best and nearest targets have hulls, hull rank buckets
(`rank1`, `ranks2to5`, `ranks6to10`, `ranks11plus`), the Java compact geometry
cap, emitted hull refs, matched hull refs, and unused hull refs. It calls out
whether hulls are reaching top candidates, going to lower-priority targets, being
limited by Java clickbox availability, or failing candidate matching.

Geometry matching order:

```text
objectKey -> hash -> id/worldX/worldY/plane/kind -> id/sceneX/sceneY/plane/kind
```

Bounds fallback can still happen when RuneLite returns no clickbox for that
object, the target is outside the visible canvas/viewport, the compact geometry
cap is hit, the overlay hull limit is intentionally lower than the overlay
target limit, or hull geometry is disabled.

## Debugging Overlay Reachability Labels

Overlay labels combine two independent observations:

- `blocked`, `R`, or `?` describes local collision-window reachability.
- `assumed` describes liveness when delta mode has no direct depletion/despawn
  evidence for the current candidate.

So `BLOCK assumed` means collision reachability appears blocked while liveness
is only assumed. `R assumed` means the candidate is locally reachable and the
target is assumed live. A reachable assumed target should not be colored red.

Use the overlay diagnostic to compare what the overlay is drawing against the
live candidate/context files:

```text
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --top 20
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --name-contains Oak --top 20
python telemetry-viewer\live_context_query.py --latest-session --reachability --class-id tree --top 10 --human
```

Useful filters:

```text
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --show-blocked --top 20
python telemetry-viewer\diagnose_overlay_state.py --latest-session --class-id tree --show-reachable --top 20
python telemetry-viewer\live_context_query.py --latest-session --reachability --class-id tree --name-contains Oak --show-blocked --top 20
```

If the diagnostic reports stale overlay ticks, the RuneLite overlay may be
reading an older session path. Set `Debug overlay state path` to the current
session's `overlay_debug_state.json` or leave it blank when RuneLite and the
live processor are on the same active session.

## Candidate Reachability QA

Candidate reachability QA is a read-only report over the per-candidate
navigation fields written by the live processor. It helps verify that local
collision-window reachability looks structurally sane for visible candidates.
It does not click, move, execute paths, manipulate menus, or emit movement
commands.

Human report:

```text
python telemetry-viewer\live_context_query.py --latest-session --reachability --class-id tree --top 10
```

JSON report:

```text
python telemetry-viewer\live_context_query.py --latest-session --reachability --class-id tree --top 10 --json
```

The report includes the latest tick, player scene tile, collision window
radius/bounds, candidate counts inside and outside the collision window, and
reachable/blocked/unknown counts. Top candidate rows include class/name/id,
world and scene tile, distance, screen/geometry/liveness fields, aim point, and
the read-only reachability observation.

The context service also accepts `reachability:<classId>` needs:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("baseline", "best:tree", "nearest:tree", "reachability:tree", "navigation_readiness")
  maxCandidates = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

## Activity, Inventory, And Target Liveness QA

The live processor also writes:

```text
interaction_geometry\live\live_activity_state.json
```

Schema `live_activity_state.v1` is a compact read-only interpretation layer for
current player state, inventory changes, and target liveness. It helps validate
whether future systems can observe context such as idle/busy, possible chopping,
inventory changes, inventory fullness, and whether a previous best target became
stale, despawned, or depleted. It does not click, execute actions, send input,
manipulate menus, or mutate client state.

Target liveness is heuristic. The processor uses existing scene object deltas,
`objectKey`, object id/hash, world/scene location, visible object references,
object names/actions, and target-library depletion hints. If a tree-like object
despawns or a same-location replacement looks like a stump/depleted variant, the
old candidate is marked with `targetLiveState` such as `recently_despawned` or
`depleted_or_stump` and is temporarily suppressed from active candidate output.
If a tree-like object returns at the same identity/location, the suppression is
cleared. The static scene index is not deleted; this is only active candidate
liveness filtering.

Candidate packets may include:

- `targetLiveState`
- `targetLiveStateConfidence`
- `targetLiveEvidence`
- `lastSeenTick`
- `lastChangedTick`
- `lastDespawnedTick`
- `replacementObjectId`
- `replacementObjectName`
- `suppressUntilTick`
- `suppressReason`

Inventory state is based on observed inventory item IDs/quantities in the tick
window. It reports signatures, free/filled slots, whether the inventory changed
this tick or recently, and compact item deltas. `filledSlots` is occupied
inventory slots, `freeSlots` is empty inventory slots, and `inventoryFull` is
derived from `freeSlots == 0` when the slot count is known. `itemCount` is kept
as a compatibility alias for the total quantity sum; newer outputs also include
`totalItemQuantity` and `inventorySlotCount` so slot occupancy and stack
quantity are not confused. Compact packet live sessions can also emit
`live_inventory_delta_packet.v1` when the observed signature changes; Python
uses that packet plus rolling tick comparison to populate
`recentInventoryDeltas`. If item names are unavailable it reports item IDs and
quantities.

Activity state is based on observed animation, pose animation, interacting
target facts, and compact activity packet transition fields such as
`previousAnimation`, `changedFields`, and `eventSource`. These are still just
observations. Animation alone is not proof of a task, but paired with nearby
tree candidates and inventory/liveness evidence it can support a cautious
`woodcutting_possible` or `likely_chopping` label.

The woodcutting state heuristic is intentionally cautious. It can report
`likely_idle`, `likely_chopping`, `likely_moving`, `inventory_changed`,
`inventory_full`, `target_depleted`, `target_stale`, or `unknown`, with
confidence and evidence. These are observations, not instructions.

Commands:

```text
python telemetry-viewer\live_context_query.py --latest-session --activity
python telemetry-viewer\live_context_query.py --latest-session --inventory
python telemetry-viewer\live_context_query.py --latest-session --liveness
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --human
python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --json
python telemetry-viewer\live_context_query.py --latest-session --self-test
```

### Inventory Slot Correctness

RuneLite inventory is slot-based. Normal live mode preserves filled backpack
slot indexes as `slot` values, usually `0..27`. Empty slots may be omitted from
compact item lists, but filled item entries must keep their original slot
number. Resource progress never uses list position as the slot number; it sums
matching item IDs across all emitted filled slots.

Woodcutting progress tracks these read-only resource IDs:

- Logs: `1511`
- Oak logs: `1521`
- Willow logs: `1519`
- Maple logs: `1517`
- Yew logs: `1515`
- Magic logs: `1513`
- Combined `woodcutting_logs`: all of the above

The live processor also writes `inventory.resourceCounts` when inventory is
known. For `woodcutting_logs`, the compact state includes `count`, `byItemId`,
`matchedItemIds`, and `matchedSlots`, so a future brain does not need to infer
progress from array position. If a slot-specific issue appears, use:

```text
python telemetry-viewer\diagnose_inventory_slots.py --latest-session --resource woodcutting_logs
python telemetry-viewer\diagnose_inventory_slots.py --latest-session --resource woodcutting_logs --json
```

The diagnostic prints a slot table for `0..27`, resource counts, duplicate slot
entries, invalid slot indexes, and inventory summary consistency warnings. It is
read-only and does not execute any game action.

For brain progress while `live_core_daemon.py` is running with writes off, use:

```text
python telemetry-viewer\diagnose_brain_progress.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --goal-count 5
```

For old file-backed diagnostics, first run with `--write-debug-live-files`, then
use:

```text
python telemetry-viewer\diagnose_brain_progress.py --latest-session --task woodcutting --goal-count 5 --state-file "%USERPROFILE%\.osrs-telemetry\brain_state_woodcutting.json"
```

That report compares current inventory items, `resourceCounts`, matched slots,
the persisted brain baseline, held-vs-baseline progress, invalid matched slots,
and whether old cumulative fields were ignored. It is useful when a slot is
matched but `gainedSinceStart` does not move.

Strict daemon diagnostics:

```text
python telemetry-viewer\diagnose_brain_progress.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --goal-count 5 --strict
```

Daily progress retains the last valid held-vs-baseline result when a poll has an
invalid or incomplete inventory snapshot. Missing inventory items, missing
signatures, and impossible counted slots such as `itemId=null` must not drop
progress to `0/N`. A real valid inventory snapshot with fewer held logs can
still reduce progress because the daily goal means "hold N more logs than
baseline."

Run the daily gauntlet when output flickers or looks stale:

```text
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --strict --check-processes
```

The gauntlet is read-only. It checks daemon health and warns if duplicate daily
daemons, the legacy live processor, or a separate context service appear to be
running at the same time.

For short experiments where trees may be chopped/depleted, run the processor
with the default suppression window or tune it explicitly:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 50 --limit 100 --no-ui-targets --emit-world-targets candidates --depleted-suppress-ticks 20 --summary --benchmark
```

## Realtime live mode versus complete live mode

`live_target_processor.py` now has two latency modes:

- `--latency-mode realtime` prioritizes the latest useful context. If compact
  packet or raw tick backlog grows, it can coalesce intermediate ticks and
  process only the newest tick or newest small batch for live output.
- `--latency-mode complete` processes every selected tick in order. Use it for
  QA/debug runs where completeness of the derived rolling output matters more
  than latency. Complete audit mode is expected to be slower and is labelled
  separately in console output and `live_status.json`.

Coalescing live output is not capture limiting. The raw tick files and static
scene index summaries can remain complete while realtime output writes only the
latest profile-specific candidates. `live_status.json` reports
`sourceSceneKnowledgeComplete`, `sourceCapHit`, `sceneObjectsSeen`,
`sceneObjectsCaptured`, and `sceneObjectsSkippedByCap` so you can tell whether
source coverage is intact.

Realtime mode also applies early profile prefiltering before building full world
target packets. For example, the woodcutting profile cheaply rejects non-tree
scene objects before detailed candidate geometry is built. This keeps the active
profile fast without reducing the Java static scene index or raw capture.

Important status fields:

- `latencyMode`
- `candidateOutputWindow`
- `coalescedBacklogTicks`
- `processedTickIds`
- `latestRawTickSeen`
- `latestTickProcessed`
- `worldTargetSourceRecordsConsidered`
- `worldTargetsPrefilteredOut`
- `classificationCacheHits` / `classificationCacheMisses`
- `candidateTickCacheHits` / `candidateTickCacheMisses`
- `timingMode`, currently `exclusive`
- `modeLabel`, `auditMode`, and `realtimeMode`
- `auditDurationMillis` for complete audit mode
- `realtimeDurationMillis`, `targetUpdateMillis`, and `budgetExceeded` for
  realtime mode

## Realtime backlog coalescing

Realtime mode is allowed to skip intermediate ticks for live latency. The raw
session can still contain those ticks; coalescing only means they were not
fully parsed and derived into live candidate output during that poll.

Status fields use precise language:

- `rawRecordsSeenThisPoll`: complete raw tick lines observed in the latest poll.
- `rawRecordsFullyParsedThisPoll`: raw tick lines parsed into Python records.
- `rawRecordsSkippedBeforeParse`: complete tick lines consumed without JSON
  parsing because realtime mode kept only the newest ticks.
- `rawRecordsFullyProcessed`: ticks fully derived into live world/candidate
  context.
- `coalescedBeforeParse`: ticks skipped by the fast tailer before expensive
  parsing.
- `coalescedAfterParse`: ticks skipped after parsing because the rolling window
  still exceeded the realtime per-update limit.

Use realtime mode for current context. Use complete mode when auditing every
tick matters. Coalescing is not capture loss, not a Java scene cap, and not a
static scene index limit.

## Compact packet input mode

`live_target_processor.py` supports five input sources:

- `--input-source raw-ticks`: current raw tick JSONL tailing path. Use it for
  offline complete audits, old sessions, and schema debugging.
- `--input-source compact-stream`: read compact packets from the local
  `127.0.0.1:<port>` TCP NDJSON stream. This is experimental until
  stream-vs-file comparison passes in your setup.
- `--input-source compact-packets`: read `live_packets\live-*.ndjson` through
  the compact packet reader. This builds candidates from compact baseline,
  scene-delta, projection, inventory, activity, and writer-health packets
  without requiring a full raw `TickSnapshot`.
- `--input-source plugin-snapshot`: experimental pull mode. It asks the
  opt-in RuneLite snapshot endpoint for cached compact payloads over localhost,
  converts them to a synthetic compact tick, and reuses the normal candidate
  pipeline. It does not call RuneLite APIs from Python and does not execute
  actions.
- `--input-source auto`: default. Prefer compact packet files when a live packet
  index/latest segment exists and is recent. Try the experimental stream only
  when packet files are unavailable or stale, otherwise fall back to raw ticks
  with an explicit warning. It does not prefer plugin-snapshot unless
  `--auto-prefer-plugin-snapshot` is explicitly passed.

For normal live QA, use compact packet files. Raw ticks remain useful for
offline audits, replay/debug work, and old sessions.
`--require-compact-packets` is the strict check: it requires a compact transport
and prevents raw tick fallback.

Compact mode consumes these packet types:

- `live_baseline_packet.v1`
- `live_scene_delta_packet.v1`
- `live_projection_packet.v1`
- `live_inventory_packet.v1`
- `live_inventory_delta_packet.v1`
- `live_activity_packet.v1`
- `live_navigation_packet.v1`
- `live_collision_window_packet.v1`
- `live_collision_grid_packet.v1` when debug full-grid emission is enabled
- `live_writer_health_packet.v1`

Missing compact fields are reported as warnings or missing capabilities instead
of silently switching to broad raw scene processing. Profiles that need target
families not yet emitted as compact packets, such as NPC or ground-item QA, may
still require raw tick mode until those compact packet types exist.

Useful status fields:

- `inputSourceRequested`
- `inputSourceActive`
- `compactStreamHost`
- `compactStreamPort`
- `compactStreamConnected`
- `compactStreamReconnects`
- `compactStreamPacketsSeen`
- `compactStreamPacketsProcessed`
- `compactPacketsAvailable`
- `compactPacketsRecent`
- `compactPacketIndexPath`
- `rawTicksAvailable`
- `inputFallbackReason`
- `defaultLiveInputPreference`
- `compactPacketsSeen`
- `compactPacketsProcessed`
- `compactPacketsCoalesced`
- `compactPacketLastSequence`
- `compactPacketLatestSegment`
- `compactPacketRolloverCount`
- `compactPacketReadErrors`
- `pluginSnapshotAvailable`
- `pluginSnapshotStatus`
- `pluginSnapshotLatestTick`
- `pluginSnapshotWarnings`
- `pluginSnapshotMissingCapabilities`
- `pluginSnapshotRequestMillis`
- `pluginSnapshotParseMillis`
- `pluginSnapshotConvertMillis`
- `pluginSnapshotResponseBytes`
- `pluginSnapshotPayloadTypes`
- `pluginSnapshotProjectionRefs`
- `pluginSnapshotProjectionCapped`
- `pluginSnapshotProjectionRefListPath`
- `pluginSnapshotRefsConverted`
- `pluginSnapshotFieldPresentCounts`
- `pluginSnapshotFieldMissingCounts`
- `pluginSnapshotConversionWarnings`
- `pluginSnapshotWorldTargetsBuilt`
- `pluginSnapshotCandidatesBeforeFilters`
- `pluginSnapshotCandidateRejectReasons`
- `pluginSnapshotTicksSkippedAsUnchanged`
- `pluginSnapshotEndpointErrors`
- `pluginSnapshotTimeouts`

Compact realtime woodcutting:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Auto input mode:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Strict compact mode:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Compare compact-packet and raw-tick candidate output:

```text
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources --profile woodcutting --latest 5
```

Compare plugin snapshot and compact packet file output:

```text
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources plugin-snapshot-vs-file --profile woodcutting --latest 5
```

Diagnose plugin snapshot conversion:

```text
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500 --dump-synthetic-shape
```

The live status timing buckets are exclusive where practical and do not include
poll sleep time. Useful fields include `fileDiscoverMillis`, `tailReadMillis`,
`lineSplitMillis`, `jsonParseMillis`, `rawTickIngestMillis`,
`livenessUpdateMillis`, `inventoryDeltaMillis`, `classificationCacheMillis`,
`candidateSelectMillis`, `outputSerializeMillis`, `outputWriteMillis`,
`consolePrintMillis`, `totalExclusiveMillis`, and `totalWallMillis`.
For plugin-snapshot runs, also inspect `pluginSnapshotBottleneck` and the
plugin-snapshot-specific request, parse, conversion, prefilter, build, select,
and output-write buckets listed in the Plugin Snapshot Bridge section.

Use `--quiet` to suppress routine console output, `--verbose` for expanded
startup/summary information, and `--log-every N` to print only every Nth follow
update. The compact follow line reports `rawSeen`, `processed`, `coalesced`,
`worldBuilt`, `candidates`, `totalMs`, rolling `p95`, budget status, and write
failures.

Fast realtime woodcutting:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Complete QA processing:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source raw-ticks --profile woodcutting --once --latency-mode complete --startup-backfill-ticks 25 --window-ticks 25 --limit 500 --emit-world-targets candidates --summary --benchmark
```

Complete audit mode; expected to be slower.

Debug broad world:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source raw-ticks --profile broad_qa --once --latency-mode complete --window-ticks 5 --limit 2000 --emit-world-targets full --summary --benchmark
```

## Read-only context service

`context_service.py` is a local Python sidecar for compact brain-facing context
queries. It reads the rolling live files under
`interaction_geometry\live`, caches parsed state in memory, and serves
`context_request.v1` to `context_response.v1` over localhost HTTP. It does not
click, send input, manipulate menus, execute actions, mutate RuneLite, or mutate
game state. It is localhost-only by default and is intended to stabilize the
read-only request/response contract before any Java bridge is added.

Raw JSON/session recording remains the debug, audit, and training path. The
service is only a compact observation layer over the current live files.

Check live setup:

```text
python telemetry-viewer\check_live_setup.py --latest-session
```

Start realtime live processor:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Start context service:

```text
python telemetry-viewer\context_service.py --latest-session --port 8890
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8890/health
```

Request context:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("baseline", "best:tree", "inventory", "activity", "liveness")
  maxCandidates = 1
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

Oneshot:

```text
python telemetry-viewer\context_service.py --latest-session --oneshot-request "{\"schema\":\"context_request.v1\",\"task\":\"woodcutting\",\"needs\":[\"baseline\",\"best:tree\"],\"maxCandidates\":1,\"responseMode\":\"compact\"}"
```

Useful endpoints:

- `GET /health`
- `GET /schema`
- `GET /status`
- `POST /context`
- `POST /context/batch`

## Realtime liveness and compact context responses

Realtime target liveness is intentionally budgeted. It should preserve complete
source scene knowledge while limiting only the live processor's liveness work
and compact output.

Liveness modes:

- `off`: skip liveness checks and mark candidate liveness as unknown.
- `basic`: direct candidate/cache lookup only; no rolling-window or visible-ref
  scan.
- `delta`: realtime default. Uses the latest processed tick's
  `sceneObjectDeltas` plus keyed unavailable-target cache. If no direct
  depletion/despawn evidence is seen, candidates are marked `live_assumed`.
- `full`: complete audit behavior. It may scan broader visible/source state and
  is expected to be slower.

Liveness wording:

- `live` means direct live/present evidence is available.
- `live_assumed` means delta mode saw no direct depletion/despawn evidence for
  the current candidate. This is not reported as unknown when liveness is
  healthy.
- `unknown` means liveness is off, missing, or unavailable.
- `degraded` means budget limits, stale/depleted/despawned evidence, or data
  gaps affected liveness reliability.

`live_status.json` reports liveness timing and budget fields including
`livenessMode`, `livenessBudgetMs`, `livenessBudgetExceeded`,
`livenessDegraded`, `livenessCandidatesChecked`,
`livenessCandidatesSkippedByBudget`, `recentlyUnavailableCount`,
`recentlyUnavailablePruned`, and `recentlyUnavailableCacheOverLimit`.

Compact context responses omit bulky `sourceFiles` and full
`recentlyUnavailableTargets` by default. They return `sourceFilesSummary` and a
small liveness summary instead. Use `responseMode = "normal"` for capped
liveness examples or `responseMode = "full"` for full details. Output limiting
is not source capture limiting; `sourceSceneKnowledgeComplete` and
`sourceCapHit` remain the source coverage signals.

Realtime no liveness:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode off --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Realtime delta liveness:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark
```

Complete audit full liveness:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source raw-ticks --profile woodcutting --once --latency-mode complete --liveness-mode full --startup-backfill-ticks 25 --window-ticks 25 --limit 500 --emit-world-targets candidates --summary --benchmark
```

Compact context request:

```powershell
$request = @{
  schema = "context_request.v1"
  task = "woodcutting"
  needs = @("baseline", "best:tree", "nearest:tree", "inventory", "activity", "liveness", "diagnostics")
  maxCandidates = 1
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8890/context" -Body $request -ContentType "application/json"
```

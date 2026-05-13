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

Debug file writes remain off unless `--write-debug-live-files` is explicitly
supplied.

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

# Daily Live Architecture

Daily live is intentionally boring.

```text
RuneLite plugin
-> compact-packets stable source
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
- compact packet files as the stable input source
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
- plugin-snapshot unless explicitly testing the experimental path

Daily command:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

Debug file writes remain off unless `--write-debug-live-files` is explicitly
supplied.

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

- `selected_target` for the current brain-selected tree
- up to two `backup_candidate` markers
- warning markers when the brain has no reachable target

When the brain task changes away from woodcutting, woodcutting tree markers are
not retained. Visual QA can use `--overlay-mode candidates`; debug/audit can use
`--overlay-mode debug`.

## Daily Workflow

1. Apply Daily Live Preset.
2. Start RuneLite Dev.
3. Start Streamlined Live Daemon.
4. Use overlay, daemon context endpoints, dashboard, or brain output.
5. Run Config Doctor or Daily Gauntlet when something looks stale.
6. Stop All.

Main daily buttons:

- Apply Daily Live Preset
- Start RuneLite Dev
- Start Streamlined Live Daemon
- Stop All
- Config Doctor
- Daily Gauntlet
- Open Latest Session Folder

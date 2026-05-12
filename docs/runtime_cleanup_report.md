# Runtime Cleanup Report

Date: 2026-05-12

## Daily Live Runtime

Daily Live now has one intended lane:

```text
RuneLite plugin
-> compact packet files
-> telemetry-viewer\live_core_daemon.py
-> in-memory context/brain state
-> optional interaction_geometry\live\overlay_debug_state.json
```

The daily daemon command is:

```text
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --input-source compact-packets --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --brain-task woodcutting --goal-count 5 --summary --benchmark
```

## Audit Findings

1. Daily Live runs the RuneLite plugin as a read-only compact telemetry sensor and `live_core_daemon.py` as the Python sidecar.
2. Daily Live writes compact packet files from the plugin. The daemon writes no rolling live debug files by default.
3. Raw ticks are not written in `LIVE_COMPACT_ONLY` unless debug raw tick override is explicitly enabled.
4. Raw events are not written in `LIVE_COMPACT_ONLY` unless debug raw event override is explicitly enabled.
5. Frames/screenshots are not captured in daily `LIVE_COMPACT_ONLY`; the Daily preset also sets `captureScreenshots=false` and `debugRecordFrames=false`.
6. Crop/perception tools are Python batch/debug tooling only. They do not run in the daily daemon.
7. Compact-stream defaults off and is labelled experimental.
8. Plugin-snapshot defaults off for daily input and is labelled experimental.
9. The old `live_target_processor.py` plus `context_service.py` chain is not required for Daily Live. It remains a legacy file pipeline.
10. Daily woodcutting progress is owned by `resource_progress.py`. Other modules consume, display, persist, or diagnose `ResourceProgressResult` rather than maintaining daily cumulative counters.

## Daily Expected State

- `recordingMode=LIVE_COMPACT_ONLY`
- compact packet files enabled
- raw ticks disabled
- raw events disabled
- frames/screenshots disabled
- crop/perception capture not active
- compact-stream disabled/inactive
- plugin-snapshot not daily input
- `live_core_daemon.py` active
- daemon debug live file writes off
- optional overlay state in `intent` mode

## Guardrails

`live_config_doctor.py` and `run_daily_gauntlet.py` warn/fail daily mode when they see:

- raw tick recording
- raw event recording
- frame recording
- screenshot capture
- crop capture
- perception capture
- compact-stream active/enabled
- plugin-snapshot as daily input
- legacy live processor or context service running alongside the daemon
- rolling debug writes from the daemon
- invalid resource progress invariants
- counted resource slots without a real `itemId`
- action/input/click/menu-shaped fields in exposed context or brain payloads

## Files Moved

No files were moved in this pass.

`telemetry-viewer\legacy\README.md` remains as the quarantine location for clearly obsolete prototype scripts. The current audit did not find a safe move candidate that was both obsolete and unreferenced. Per the cleanup rule, uncertain files were left in place and categorized instead of moved.

## Files Kept As Advanced/Debug

- Visual perception and crop builders/inspectors are kept as `advanced_debug` or `batch_audit`.
- `telemetry_launcher.py` is kept as `deprecated` compatibility tooling.
- `test_telemetry_paths.py` is kept as `deprecated` top-level smoke tooling.
- Plugin-snapshot and compact-stream tools are kept as `experimental`.

## Notes

The repository currently contains generated/untracked local state in `%USERPROFILE%\`. It was not moved or deleted because it may contain user-created state from an earlier path-expansion mistake. It should be inspected manually before cleanup.

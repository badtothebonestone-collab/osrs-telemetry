# Tool Registry

The daily workflow has one stable lane and one explicit experimental no-file
lane:

```text
RuneLite plugin -> compact-packets -> live_core_daemon.py -> in-memory context -> brain intent overlay
RuneLite plugin -> PluginLiveCache / PluginSnapshotEndpoint -> live_core_daemon.py -> in-memory context -> brain intent overlay
```

The machine-readable registry lives at:

```text
telemetry-viewer\tool_registry.json
```

It classifies tools into the same groups used by the Live Control Panel. Daily
mode should show only the daily tools. Everything else stays available under
Advanced or as command-line debug/audit tooling.

## Daily

These are the only tools that belong in the main daily view.

| Tool | Purpose | Command |
| --- | --- | --- |
| `live_control_panel.py` | Simple launcher, Mission Control status view, and safe runtime-control surface for the daily daemon. | `python telemetry-viewer\live_control_panel.py` |
| `live_core_daemon.py` | Streamlined daily daemon: stable compact-packets input or explicit experimental snapshot no-file input, in-memory context, writes off by default, optional brain intent overlay state. Accepts startup `--preset` as a safe mission-preset alias. | `python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode compact-packets --input-source compact-packets --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 10 --human-dashboard --summary --benchmark` |
| `control_live_daemon.py` | Local read-only runtime control for daemon mission presets, task policy, goal count, observe-only mode, and baseline reset. Prints JSON to stdout only when requested. | `python telemetry-viewer\control_live_daemon.py --get` |
| `mission_snapshot.py` | One-shot Mission Snapshot diagnostic for bug reports and before/after comparisons. Reads `/health`, `/status`, and `/control` once, prints to stdout by default, and writes one JSON file only when `--output` is explicit. | `python telemetry-viewer\mission_snapshot.py --daemon-url http://127.0.0.1:8890` |
| `live_config_doctor.py` | Preset-aware PASS/WARN/FAIL check for daily settings. | `python telemetry-viewer\live_config_doctor.py --latest-session --mode daily --fix-suggestions` or `--mode snapshot_no_file` |
| `run_daily_gauntlet.py` | Strict daily sanity check for daemon health, process conflicts, progress invariants, and unsafe fields. | `python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode compact-packets --strict --check-processes` |

Daily support modules are hidden from the UI but remain part of the daily lane:

- `resource_progress.py`: single source of truth for woodcutting progress.
- `brain_core.py`: read-only brain interpretation.
- `task_policy.py` and `task_policies.json`: read-only task policy model for
  interpreting conditions such as a full inventory.
- `runtime_control.py`: in-memory model and validation for safe daemon
  `/control` updates.
- `mission_presets.py`: named sidecar brain/context presets for safe runtime
  control updates; no game actions.
- `capabilities.py`: capability status/alias registry for analyzer and output
  consistency.
- `analyzers\*.py`: in-memory daemon analyzers for inventory, targets,
  navigation, navigation intent, pathing context, activity, service/process
  context, brain context, and intent overlay marker construction.
- `live_context_format.py`: human output formatting.
- `live_packet_reader.py`, `telemetry_paths.py`, and
  `navigation_reachability.py`: compact-packet/session/reachability helpers.

## Advanced Debug

Diagnostics and inspectors that are useful when daily output looks wrong:

- `check_live_setup.py`
- `inspect_live_packets.py`
- `diagnose_brain_progress.py`
- `diagnose_inventory_slots.py`
- `diagnose_overlay_state.py`
- `diagnose_overlay_geometry.py`
- `diagnose_pathing_context.py`
- `diagnose_target_coverage.py`
- `run_stabilization_suite.py`
- visual/perception/tab inspection helpers

These tools are safe to hide from the daily view because they are not required
to start or watch the daily daemon.

## Legacy File Pipeline

The old three-process chain is retained for compatibility and debugging, but it
is not the daily workflow:

| Tool | Role |
| --- | --- |
| `live_target_processor.py` | Reads compact packets and writes rolling live JSON files. |
| `context_service.py` | Serves context from those rolling files. |
| `live_context_query.py` | Human query/dashboard helper over rolling files or context service. |
| `mock_brain_rehearsal.py` | Legacy context-service rehearsal client. |

Use these only when you intentionally need rolling files such as
`live_status.json`, `live_candidates.jsonl`, or `live_context_index.json`.

## Batch Audit

Batch/debug tools remain in place for DEBUG_RECORDING sessions, replay, visual
QA, geometry building, and dataset work:

- `run_target_geometry_pipeline.py`
- `build_world_target_geometry.py`
- `build_ui_target_geometry.py`
- `select_target_candidates.py`
- `target_geometry_inspector.py`
- `inspect_target_geometry.py`
- dataset builders and inspectors
- replay/viewer/export/validation tools

These are disk-heavy or offline tools and should not be part of the daily
button set.

## Experimental

These paths are intentionally hidden from daily mode and must be labelled
`EXPERIMENTAL` in the UI:

- Daily Snapshot No-File / plugin-snapshot input mode and
  `diagnose_plugin_snapshot.py`
- compact-stream transport testing

`compact-packets` remains the daily stable source/fallback. Daily Snapshot
No-File is experimental and must be selected explicitly; it expects the compact
NDJSON file mirror to be disabled and the plugin snapshot endpoint to pass
health checks. Compact-stream should only be selected explicitly for transport
or comparison testing.

## Deprecated

Compatibility tools and uncertain scripts are not deleted automatically. They
stay out of the daily UI until a reference check proves they can be moved or
removed.

- `telemetry_launcher.py`
- top-level `test_telemetry_paths.py`

See `docs\cleanup_report.md` for what was kept, moved, or left alone.
See `docs\runtime_cleanup_report.md` for the Daily Live runtime audit and
guardrail summary.

# Cleanup Report

This pass focused on daily live stability and workflow clarity. No user session
data was touched, and no source script was deleted.

## Files Moved

None.

`telemetry-viewer\legacy\README.md` was kept as a quarantine marker for future
cleanup, but no script was moved into it. The current rule is conservative:
only move a script after imports, tests, docs, and local workflows prove it is
obsolete.

## Daily Files Kept

These files are part of the official daily path:

- `telemetry-viewer\live_control_panel.py`
- `telemetry-viewer\live_core_daemon.py`
- `telemetry-viewer\live_config_doctor.py`
- `telemetry-viewer\run_daily_gauntlet.py`
- `telemetry-viewer\run_stabilization_suite.py`
- `telemetry-viewer\resource_progress.py`
- `telemetry-viewer\brain_core.py`
- `telemetry-viewer\live_context_format.py`
- `telemetry-viewer\live_packet_reader.py`
- `telemetry-viewer\navigation_reachability.py`
- `telemetry-viewer\telemetry_paths.py`
- `telemetry-viewer\task_resources.json`
- `telemetry-viewer\target_library.json`
- `telemetry-viewer\target_profiles.json`

Daily live now means:

```text
RuneLite plugin -> compact-packets -> live_core_daemon.py -> in-memory context
```

The daemon may write the tiny overlay state when requested, but rolling legacy
live files stay off by default.

## Marked Legacy

These are still useful, but they are no longer the daily workflow:

- `telemetry-viewer\live_target_processor.py`
- `telemetry-viewer\context_service.py`
- `telemetry-viewer\live_context_query.py`
- `telemetry-viewer\mock_brain_rehearsal.py`

They are kept under the Live Control Panel's Advanced section as the legacy
file-based stack.

## Marked Advanced Debug

These remain available for diagnosis and inspection:

- `telemetry-viewer\check_live_setup.py`
- `telemetry-viewer\inspect_live_packets.py`
- `telemetry-viewer\diagnose_brain_progress.py`
- `telemetry-viewer\diagnose_inventory_slots.py`
- `telemetry-viewer\diagnose_overlay_state.py`
- `telemetry-viewer\diagnose_overlay_geometry.py`
- `telemetry-viewer\diagnose_target_coverage.py`
- visual/perception/tab inspection helpers

## Marked Batch/Audit

These remain for DEBUG_RECORDING, offline analysis, geometry building, dataset
work, replay, and visual QA:

- `telemetry-viewer\run_target_geometry_pipeline.py`
- `telemetry-viewer\build_world_target_geometry.py`
- `telemetry-viewer\build_ui_target_geometry.py`
- `telemetry-viewer\select_target_candidates.py`
- `telemetry-viewer\target_geometry_inspector.py`
- `telemetry-viewer\inspect_target_geometry.py`
- dataset builders and inspectors
- replay/viewer/export/validation tools

## Marked Experimental

These are intentionally hidden from the daily mode and labelled
`EXPERIMENTAL`:

- plugin-snapshot input and comparison diagnostics
- compact-stream transport testing

`compact-packets` remains the daily stable source/fallback.

## Left Alone Because Uncertain

The following are compatibility or uncertain scripts and were not moved:

- `telemetry-viewer\telemetry_launcher.py`
- `telemetry-viewer\test_telemetry_paths.py`

They are documented as `deprecated` in
`telemetry-viewer\tool_registry.json`, but they stay in place until a stronger
reference check says they can be quarantined.

## References Checked

The cleanup reviewed:

- Live Control Panel command construction and labels.
- Daily gauntlet process/conflict checks.
- Tool registry documentation and JSON metadata.
- Existing test references for legacy tools.
- Current imports and docs references where practical.

No tests, plugin code, target libraries, profiles, or batch/audit tools were
deleted or moved.

# Cleanup Report

This report classifies the current project files before any cleanup or
deprecation. No user session data is touched by this cleanup pass.

## A. Normal Live Workflow

These files are part of the daily compact-packet live path and should be kept:

- `src/main/java/com/osrstelemetry/LivePacket.java`
- `src/main/java/com/osrstelemetry/LivePacketWriter.java`
- `src/main/java/com/osrstelemetry/TelemetryPlugin.java`
- `src/main/java/com/osrstelemetry/TelemetryConfig.java`
- `src/main/java/com/osrstelemetry/TelemetryRecordingMode.java`
- `telemetry-viewer/check_live_setup.py`
- `telemetry-viewer/inspect_live_packets.py`
- `telemetry-viewer/live_packet_reader.py`
- `telemetry-viewer/live_target_processor.py`
- `telemetry-viewer/context_service.py`
- `telemetry-viewer/live_context_query.py`
- `telemetry-viewer/live_context_format.py`
- `telemetry-viewer/mock_brain_rehearsal.py`
- `telemetry-viewer/navigation_reachability.py`
- `telemetry-viewer/live_control_panel.py`
- `telemetry-viewer/target_library.json`
- `telemetry-viewer/target_profiles.json`
- `start_live_control_panel.bat`
- `start_normal_live_stack.bat`
- `Start-LiveControlPanel.ps1`
- `Start-NormalLiveStack.ps1`

## B. Debug / Audit Workflow

These tools are intentionally preserved for DEBUG_RECORDING sessions, batch
geometry builds, replay, diagnostics, and future training data:

- `telemetry-viewer/run_target_geometry_pipeline.py`
- `telemetry-viewer/build_world_target_geometry.py`
- `telemetry-viewer/build_ui_target_geometry.py`
- `telemetry-viewer/select_target_candidates.py`
- `telemetry-viewer/diagnose_target_coverage.py`
- `telemetry-viewer/export_session.py`
- `telemetry-viewer/export_target_handoff.py`
- `telemetry-viewer/replay_viewer.py`
- `telemetry-viewer/viewer.py`
- `telemetry-viewer/validate_session.py`
- `telemetry-viewer/build_perception_dataset.py`
- `telemetry-viewer/build_scenario_dataset.py`
- `telemetry-viewer/build_training_dataset.py`
- `telemetry-viewer/export_curated_training_dataset.py`
- `telemetry-viewer/prepare_visual_perception.py`
- `telemetry-viewer/training_dataset_inspector.py`
- `telemetry-viewer/dataset_status.py`
- `telemetry-viewer/scenario_inspector.py`
- `telemetry-viewer/label_ranges.py`
- `telemetry-viewer/suggest_target_overrides.py`
- `telemetry-viewer/summarize_candidate_quality.py`

## C. Visual QA Workflow

These files support visual QA and are retained:

- `src/main/java/com/osrstelemetry/TelemetryDebugOverlay.java`
- `src/main/java/com/osrstelemetry/TelemetryDebugOverlayMode.java`
- `telemetry-viewer/target_geometry_inspector.py`
- `telemetry-viewer/inspect_target_geometry.py`
- `telemetry-viewer/diagnose_overlay_state.py`
- `telemetry-viewer/calibrate_screen_regions.py`
- `telemetry-viewer/inspect_perception.py`
- `telemetry-viewer/inspect_tab_detection.py`
- `telemetry-viewer/tab_detection.py`
- `telemetry-viewer/tab_detection_rules.json`
- `telemetry-viewer/tab_labels.json`
- `telemetry-viewer/tab_profile_names.py`
- `telemetry-viewer/calibration_profiles/default_screen_regions.json`

## D. Legacy / Deprecated Candidates

| Path | Reference check | Proposed action |
| --- | --- | --- |
| `telemetry-viewer/telemetry_launcher.py` | Still intentionally present as an older launcher; not used by the daily flow. | Keep and document as legacy compatibility. |
| `telemetry-viewer/test_telemetry_paths.py` | Older top-level path smoke script; test suite uses `telemetry-viewer/tests/test_telemetry_paths.py`. | Keep for now; consider moving to `legacy` only after confirming no local workflow uses it. |
| `telemetry-viewer/inspect_target_geometry.py` | Overlaps with `target_geometry_inspector.py`, but remains useful for debug inspection. | Keep as debug/audit helper. |
| Dataset/scenario builders | Not part of normal live, but referenced by training/debug workflows. | Keep. |

No source script met the deletion bar in this pass. The safe cleanup action is
limited to generated cache folders/files.

## E. Generated Data That Should Not Be In Repo

Generated artifacts that are safe to remove from the workspace when present:

- `__pycache__`
- `.pytest_cache`
- `.mypy_cache`
- temporary local output folders
- accidental session artifacts

Do not delete user sessions under:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

## Cleanup Decision

- No source files were deleted.
- No debug/audit tools were removed.
- Generated Python cache folders may be removed after verification.
- `live_control_panel.py` is now the recommended daily launcher.

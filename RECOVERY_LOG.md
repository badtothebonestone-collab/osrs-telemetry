# Recovery Log

## Current branch and worktree

- Branch: `recovery/r4-readonly-live-readiness-fixtures`
- Worktree path: `C:\Users\badto\OneDrive\Documents\osrs-telemetry-recovery`
- Original repo status summary: this recovery folder was created by cloning because the starting directory was not inside a Git repository. No original repo path or uncommitted original worktree diff was captured. The recovery worktree already had `_recovery/` untracked from the initial evidence capture before this audit log was added.

## Files that appear authoritative

- `AGENTS.md` - current Codex guardrails. It says to read `PROJECT_STATE.md`, `CURRENT_GOAL.md`, and `docs/INDEX.md`, keeps the coordinate-first/query-first architecture, forbids anti-detection/randomization, forbids direct `pyautogui`/`pydirectinput` live input, and says to use focused tests plus `python telemetry-viewer\run_stabilization_suite.py` if code changes.
- `docs/INDEX.md` - active/reference/archive map. This is the clearest current source for avoiding stale plans.
- `PROJECT_STATE.md` - current live-loop state and stabilization priority.
- `CURRENT_GOAL.md` - immediate navigation-trace goal, but it is goal-specific and should not be treated as permanent architecture.
- `docs/tool_registry.md` - human-readable daily tool summary and canonical daily commands.
- `telemetry-viewer\tool_registry.json` - machine-readable registry. It marks `live_control_panel.py`, `live_core_daemon.py`, and `run_daily_gauntlet.py` as daily, `run_stabilization_suite.py` as advanced debug, and `telemetry_launcher.py` / `test_telemetry_paths.py` as deprecated.
- `telemetry-viewer\docs\current_pipeline_manifest.md` and `telemetry-viewer\pipeline_manifest.json` - current query-first, snapshot-backed pipeline manifest.
- `telemetry-viewer\docs\live_stack_architecture.md` - current live stack guardrail. It explicitly says it is not a request to add new runtime behavior.
- `telemetry-viewer\docs\task_script_api.md` - current high-level task-script contract. It forbids raw arbitrary mouse/key/click tools and requires live evidence for script progress.
- `telemetry-viewer\docs\query_coverage_matrix.md` and `telemetry-viewer\docs\data_source_inventory.md` - current query/data-source maps.
- `docs\analyzer_contracts.md` - active analyzer contracts. It repeatedly says analyzers must not emit click/input/menu/action fields.
- `src\main\java\com\osrstelemetry\TelemetryPlugin.java` - RuneLite plugin entry point.
- `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java` - active read-only 8893 snapshot endpoint.
- `src\main\java\com\osrstelemetry\WorldModelCache.java` - active loaded-scene/world-model cache.
- `telemetry-viewer\live_core_daemon.py` - daily read-only in-memory daemon on 8890.
- `telemetry-viewer\context_service.py` - query-first current context, Knowledge Fabric, pipeline health, handoff, and loaded-scene recovery CLI.
- `telemetry-viewer\live_readiness_core.py`, `telemetry-viewer\live_readiness.py`, and `telemetry-viewer\diagnose_live_readiness.py` - current readiness gate surfaces.
- `telemetry-viewer\liveness_recovery_core.py` and `telemetry-viewer\run_runelite_bootstrap.py` - current reusable loaded-scene recovery/bootstrap surfaces, but they are sharp because they can use bounded startup input when executed with the right flags.
- `telemetry-viewer\input_control\executor.py` and `telemetry-viewer\execute_next_action.py` - current executor and CLI. They are authoritative but dangerous to follow blindly because `--execute` can issue live input.
- `telemetry-viewer\run_stabilization_suite.py` - current broad Python compile/test suite.
- `build.gradle`, `settings.gradle`, `runelite-plugin.properties`, and `gradle\wrapper\gradle-wrapper.properties` - Gradle/Runelite build metadata.
- `src\test\java\com\osrstelemetry\*.java` and `telemetry-viewer\tests\*.py` - current test surfaces.

## Files that appear stale, contradictory, obsolete, or dangerous to follow blindly

- `docs\codex_handoff_current.md` - marked `Historical Reference Only`, but internally still says "New Codex chats should read `AGENTS.md` first, then this file" and contains many current-sounding commands. This can confuse future runs.
- `docs\cleanup_report.md`, `docs\runtime_cleanup_report.md`, `telemetry-viewer\docs\cleanup_inventory.md`, `telemetry-viewer\docs\pipeline_cleanup_inventory.md`, and `telemetry-viewer\docs\pristine_audit_report.md` - all marked historical or cleanup inventory. Useful as reference, dangerous as current instructions.
- `telemetry-viewer\docs\cleanup_inventory.md` - especially confusing because it is historical but contains detailed "canonical" classifications and commands, including at least one bounded execution command using `--backend pyautogui`.
- `telemetry-viewer\docs\pristine_audit_report.md` - historical audit report with dated process state, COM6 assumptions, 8893 status, and old test counts. It should not be treated as current live evidence.
- `README.md` and `telemetry-viewer\README.md` - useful overview, but they say most users should use `telemetry_launcher.py`, while the current registry marks `live_control_panel.py` as daily and `telemetry_launcher.py` as deprecated.
- `Start-NormalLiveStack.ps1`, `start_normal_live_stack.bat`, `Start-LiveControlPanel.ps1`, and `start_live_control_panel.bat` - hard-code `C:\Users\stone\osrs-telemetry\example-plugin`, which is not this worktree.
- `CLAUDE.md` - only contains `@AGENTS.md`; it is an alias, not standalone guidance.
- `telemetry-viewer\legacy\README.md` - says nothing has been moved there yet. The legacy folder exists mostly as a quarantine policy, not as a current runtime path.
- `telemetry-viewer\inspect_live_packets.py` - registry says it is a retired compatibility shim. It should not be used as live truth.
- `telemetry-viewer\test_telemetry_paths.py` - deprecated compatibility wrapper; formal tests live under `telemetry-viewer\tests`.
- Any doc command with `maintenance.py --prune-legacy-live-packets --apply` - deletion-capable maintenance command. Do not run during recovery unless explicitly authorized.
- Any command with `execute_next_action.py --execute`, `run_runelite_bootstrap.py --execute`, `--allow-software-input`, `--unsafe-allow-pyautogui-live`, or `--allow-jagex-launcher-automation` - live or unsafe/debug execution surface. Treat as dangerous without explicit instruction.

## Entrypoints found

| Path | Apparent purpose | Status | Manual setup? | Bypasses intended workflow? |
| --- | --- | --- | --- | --- |
| `gradlew`, `gradlew.bat` | Gradle wrapper for tests/build/dev run. | current | Java/Gradle dependencies | no |
| `build.gradle` task `run` | Starts RuneLite developer runtime using test classpath and `TelemetryPluginTest`. | current but dev-launch only | yes, RuneLite/dev environment | no, but can be mistaken for loaded-scene proof |
| `build.gradle` task `test` | Java test suite. | current | dependency download | no |
| `build.gradle` task `shadowJar` | Fat jar build. | current/unknown for recovery | dependency download | no |
| `Start-NormalLiveStack.ps1` | Starts live control panel with auto-start normal live. | stale/dangerous | yes | yes, hard-coded old path |
| `start_normal_live_stack.bat` | Batch version of normal live stack start. | stale/dangerous | yes | yes, hard-coded old path |
| `Start-LiveControlPanel.ps1` | Starts live control panel. | stale/dangerous | yes | yes, hard-coded old path |
| `start_live_control_panel.bat` | Batch version of live control panel start. | stale/dangerous | yes | yes, hard-coded old path |
| `telemetry-viewer\live_control_panel.py` | Daily UI/control panel for live daemon and helper processes. | current | yes, local runtime/ports/session | no, but it can launch subprocesses |
| `telemetry-viewer\live_core_daemon.py` | Read-only in-memory daily daemon on 8890 from plugin snapshot/WorldModel. | current | yes, 8893 plugin endpoint/session | no |
| `telemetry-viewer\context_service.py` | Query/context service, Knowledge Fabric, pipeline health, handoff, external cache, and `--ensure-loaded-scene`. | current | sometimes, depending on query | no for query; recovery mode can invoke bounded recovery |
| `telemetry-viewer\control_live_daemon.py` | Local runtime control for daemon state/presets. | current | daemon running | no |
| `telemetry-viewer\run_daily_gauntlet.py` | Daily sanity check for daemon/context/process conflicts. | current | daemon/session for live check | no |
| `telemetry-viewer\run_woodcut_bank_live_qa.py` | Read-only woodcut-bank live QA runner over endpoints/diagnostics. | current | daemon and 8893 endpoint | no |
| `telemetry-viewer\diagnose_live_readiness.py` | Action readiness and execution-gate diagnostic. | current | daemon/session for live check | no |
| `telemetry-viewer\diagnose_woodcutting_candidates.py` | Candidate/highlighter source diagnostic. | current | daemon/session for live check | no |
| `telemetry-viewer\diagnose_action_proposal.py` | Read-only action proposal diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_action_lifecycle.py` | Read-only action lifecycle diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_input_geometry.py` | Read-only input/canvas geometry diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_mouse_movement.py` | Pure movement-planner diagnostic. | current | no live scene required for synthetic use | no |
| `telemetry-viewer\diagnose_service_context.py` | Bank/service context diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_pathing_context.py` | Pathing/service-ready diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_task_transition.py` | Policy/task transition diagnostic. | current | daemon or synthetic inputs | no |
| `telemetry-viewer\diagnose_task_policy.py` | Task policy diagnostic. | current | no for synthetic use | no |
| `telemetry-viewer\diagnose_bank_ui_context.py` | Bank UI context diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_bank_operation_context.py` | Bank operation diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_close_bank_context.py` | Close-bank readiness diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_return_to_resource_context.py` | Return-to-resource diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_post_bank_reacquisition_context.py` | Post-bank reacquisition diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_resource_return_context.py` | Resource return destination diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_cycle_history.py` | Woodcut-bank cycle history diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_woodcut_bank_cycle.py` | Full cycle diagnostic. | current | daemon/status | no |
| `telemetry-viewer\diagnose_woodcut_bank_scenarios.py` | Synthetic in-memory cycle scenario suite. | current | no | no |
| `telemetry-viewer\diagnose_overlay_state.py` | Overlay/live candidate consistency audit. | useful but non-canonical | session/live files | no |
| `telemetry-viewer\diagnose_overlay_geometry.py` | Overlay geometry/hull audit. | useful but non-canonical | session/live files | no |
| `telemetry-viewer\diagnose_target_coverage.py` | Deep raw/world/candidate coverage audit. | audit, not daily | session/files | no |
| `telemetry-viewer\diagnose_plugin_snapshot.py` | Plugin snapshot transport/conversion diagnostic. | experimental | 8893 endpoint | no |
| `telemetry-viewer\check_live_setup.py` | Setup doctor. | current helper | session/plugin/processes | no |
| `telemetry-viewer\live_config_doctor.py` | Live workflow/config doctor. | current helper | session/plugin/processes | no |
| `telemetry-viewer\mcp_server.py` | Read-only local MCP-style Knowledge Fabric adapter. | current | optional daemon/session | no |
| `telemetry-viewer\brain_core.py` | Read-only brain interpretation layer. | current/imported by daemon | context input | no |
| `telemetry-viewer\mission_snapshot.py` | One-shot daemon/control snapshot. | current | daemon running | no |
| `telemetry-viewer\runtime_control.py` | Runtime control model used by daemon/control CLI. | current module | imported | no |
| `telemetry-viewer\mission_presets.py` | Static mission presets. | current module | imported | no |
| `telemetry-viewer\task_script_api.py` | High-level task-script validation/compile/readiness API. | current | optional daemon for live evidence queries | no |
| `telemetry-viewer\knowledge_fabric.py` | Query-first aggregate/debug layer. | current | optional daemon/session | no |
| `telemetry-viewer\live_context_query.py` | Live context QA/query helper. | current/compat | session/live files | no |
| `telemetry-viewer\live_target_processor.py` | Candidate/live file processor; active/legacy hybrid. | current but risky for daily if used outside daemon binding | session/daemon | can bypass daily daemon if used with blind latest-session |
| `telemetry-viewer\execute_next_action.py` | Dry-run/explain or execute one bounded action/loop. | current/dangerous | yes, daemon/readiness/backend; Arduino for normal live | no when gated; software override can bypass intended live input policy |
| `telemetry-viewer\input_control\executor.py` | Executor implementation. | current/dangerous | imported by CLI/tests | no when gated; direct imports can bypass CLI guardrails |
| `telemetry-viewer\input_control\human_input_controller.py` | Motor governor. | current | backend required | no |
| `telemetry-viewer\input_control\backend_arduino_hid.py` | Normal live HID backend. | current | Arduino/COM/firmware | no |
| `telemetry-viewer\input_control\backend_pyautogui.py` | Software backend. | debug-only dangerous | desktop focus | yes if allowed for live |
| `telemetry-viewer\input_control\backend_pydirectinput.py` | Software backend. | debug-only dangerous | package/desktop focus | yes if allowed for live |
| `telemetry-viewer\input_control\arduino_monitor.py` | Arduino Raw Input/injection monitor. | current | Windows/Arduino | no |
| `telemetry-viewer\liveness_recovery_core.py` | Reusable loaded-scene recovery controller. | current/dangerous | 8893/8890/window/backend | no when Arduino/gated; can start/rebind daemon and invoke bootstrap |
| `telemetry-viewer\run_runelite_bootstrap.py` | RuneLite dev bootstrap/recovery helper. | current/dangerous | RuneLite window, backend, optional Arduino | no for saved known surfaces; can click startup surfaces with `--execute` |
| `telemetry-viewer\tools\run_traced_dev_cycle.py` | Bounded navigation-traced dev cycle wrapper. | current/dangerous in run mode | config, daemon, readiness, backend | delegates to existing recovery/executor; run mode can execute |
| `telemetry-viewer\telemetry_launcher.py` | Older telemetry GUI launcher. | deprecated | yes | can launch processes; not current daily UI |
| `telemetry-viewer\viewer.py` | Recorded telemetry viewer. | current batch/viewer | session files | no |
| `telemetry-viewer\replay_viewer.py` | Local replay browser API. | current batch/viewer | session files | no |
| `telemetry-viewer\latest_state.py` | Latest-state JSON writer. | current batch/helper | session files | no |
| `telemetry-viewer\validate_session.py` | Session validation. | current check | session files | no |
| `telemetry-viewer\dataset_status.py` | Dataset/session status. | current check | session files | no |
| `telemetry-viewer\build_perception_dataset.py` | Builds derived perception dataset. | current batch | session frames/calibration | no |
| `telemetry-viewer\prepare_visual_perception.py` | Prepares visual perception crops/bundles. | current batch | frames/calibration | no |
| `telemetry-viewer\build_training_dataset.py` | Builds training data. | current batch | session/perception/labels | no |
| `telemetry-viewer\export_curated_training_dataset.py` | Exports curated manifest. | current batch | training data | no |
| `telemetry-viewer\training_dataset_inspector.py` | Local browser inspector for training data. | current batch/viewer | training data | no |
| `telemetry-viewer\calibrate_screen_regions.py` | Calibration preview/server. | current batch/tool | frames/browser | no gameplay input |
| `telemetry-viewer\build_world_target_geometry.py` | Builds world target geometry. | current batch | session ticks/frames | no |
| `telemetry-viewer\build_ui_target_geometry.py` | Builds UI target geometry. | current batch | session/perception/config | no |
| `telemetry-viewer\select_target_candidates.py` | Builds/selects candidates from geometry. | current batch | geometry/profiles | no |
| `telemetry-viewer\run_target_geometry_pipeline.py` | Orchestrates geometry build steps. | current batch | session/build args | no gameplay input |
| `telemetry-viewer\target_geometry_inspector.py` | Browser visual inspector for geometry/highlighter. | current visual QA | session/daemon | no |
| `telemetry-viewer\inspect_target_geometry.py` | Static target geometry CLI inspector. | non-canonical helper | files | no |
| `telemetry-viewer\inspect_perception.py` | Perception output inspector. | helper | files | no |
| `telemetry-viewer\inspect_tab_detection.py` | Tab detection inspector. | helper | files | no |
| `telemetry-viewer\scenario_inspector.py` | Scenario/override inspector. | helper | files | no |
| `telemetry-viewer\build_scenario_dataset.py` | Scenario dataset builder. | helper/batch | sessions/templates | no |
| `telemetry-viewer\export_session.py` | Session export. | helper/batch | session files | no |
| `telemetry-viewer\export_target_handoff.py` | Target handoff export. | helper/batch | geometry/candidates | no |
| `telemetry-viewer\capture_bootstrap_template.py` | Captures bootstrap button template. | setup helper | desktop screenshot/window | no gameplay input, but desktop-sensitive |
| `telemetry-viewer\maintenance.py` | Retired packet archive report/prune. | maintenance only | sessions dir | report/dry-run no; `--apply` deletes old packet files |
| `telemetry-viewer\inspect_live_packets.py` | Retired live packet compatibility shim. | stale/deprecated | none | no |
| `telemetry-viewer\test_telemetry_paths.py` | Legacy path smoke wrapper. | deprecated | none | no |
| `src\main\java\com\osrstelemetry\TelemetryPlugin.java` | RuneLite plugin entry point. | current | RuneLite dev client | no, read-only telemetry/overlay |
| `src\main\java\com\osrstelemetry\PluginSnapshotEndpoint.java` | Localhost snapshot API. | current | plugin enabled | no |
| `src\main\java\com\osrstelemetry\WorldModelCache.java` | World model query/cache. | current | plugin/client state | no |

## Markdown instruction problems

- Duplicated active instruction paths:
  - `AGENTS.md` says read `PROJECT_STATE.md`, `CURRENT_GOAL.md`, and `docs/INDEX.md`.
  - `docs\INDEX.md` says the active path is `AGENTS.md`, `PROJECT_STATE.md`, `CURRENT_GOAL.md`, `docs\INDEX.md`.
  - `docs\codex_handoff_current.md` is historical but still says new chats should read it after `AGENTS.md`.
- Contradictory launcher guidance:
  - `README.md` and `telemetry-viewer\README.md` say most users should use `telemetry_launcher.py`.
  - `telemetry-viewer\tool_registry.json` says `live_control_panel.py` is daily and `telemetry_launcher.py` is deprecated.
- Giant context-heavy docs:
  - `docs\telemetry_schema.md` is more than 2,000 lines.
  - `docs\codex_handoff_current.md` is about 800 lines and historical.
  - `telemetry-viewer\docs\live_stack_architecture.md` and `telemetry-viewer\docs\live_outputs.md` are large architecture/output references.
- Old milestone/audit docs still sound current:
  - `docs\codex_handoff_current.md` has "Current" in the filename and many current-sounding instructions despite the historical banner.
  - `telemetry-viewer\docs\pristine_audit_report.md` reports dated process/live state and next work.
  - `telemetry-viewer\docs\cleanup_inventory.md` is historical but uses "canonical" heavily.
- Docs that should probably become archived reference only:
  - Already marked historical: `docs\codex_handoff_current.md`, `docs\cleanup_report.md`, `docs\runtime_cleanup_report.md`, `telemetry-viewer\docs\cleanup_inventory.md`, `telemetry-viewer\docs\pipeline_cleanup_inventory.md`, `telemetry-viewer\docs\pristine_audit_report.md`.
  - Likely needs clearer top-banner or relocation: top-level launcher wrappers and any README references that still point users to deprecated `telemetry_launcher.py`.

## Current test/check commands

Discovered test/build commands:

```powershell
.\gradlew.bat test
.\gradlew.bat build
.\gradlew.bat run
.\gradlew.bat shadowJar
./gradlew test
./gradlew build
./gradlew run
python telemetry-viewer\run_stabilization_suite.py
python telemetry-viewer\test_telemetry_paths.py
python telemetry-viewer\tests\test_telemetry_paths.py
```

`telemetry-viewer\run_stabilization_suite.py` expands to many explicit commands:

- `python -m py_compile ...` for core, analyzer, daemon, context, Knowledge Fabric, MCP, executor, input-control, bootstrap, diagnostics, and suite files.
- `python telemetry-viewer\tests\<test_file>.py` for the Python test files under `telemetry-viewer\tests`.

Discovered Python test files runnable by the suite or directly:

```text
telemetry-viewer\tests\test_action_lifecycle.py
telemetry-viewer\tests\test_action_proposal.py
telemetry-viewer\tests\test_activity_analyzer.py
telemetry-viewer\tests\test_analyzer_contracts.py
telemetry-viewer\tests\test_arduino_live_input_policy.py
telemetry-viewer\tests\test_bank_operation_analyzer.py
telemetry-viewer\tests\test_bank_operation_diagnostic.py
telemetry-viewer\tests\test_bank_ui_analyzer.py
telemetry-viewer\tests\test_bank_ui_diagnostic.py
telemetry-viewer\tests\test_bootstrap_vision.py
telemetry-viewer\tests\test_bootstrap_window.py
telemetry-viewer\tests\test_brain_context_analyzer.py
telemetry-viewer\tests\test_brain_core.py
telemetry-viewer\tests\test_candidate_core.py
telemetry-viewer\tests\test_capture_bootstrap_template.py
telemetry-viewer\tests\test_check_live_setup.py
telemetry-viewer\tests\test_client_tick_core.py
telemetry-viewer\tests\test_close_bank_analyzer.py
telemetry-viewer\tests\test_close_bank_diagnostic.py
telemetry-viewer\tests\test_context_service.py
telemetry-viewer\tests\test_cycle_history.py
telemetry-viewer\tests\test_diagnose_action_proposal.py
telemetry-viewer\tests\test_diagnose_brain_progress.py
telemetry-viewer\tests\test_diagnose_input_geometry.py
telemetry-viewer\tests\test_diagnose_inventory_slots.py
telemetry-viewer\tests\test_diagnose_mouse_movement.py
telemetry-viewer\tests\test_diagnose_overlay_geometry.py
telemetry-viewer\tests\test_diagnose_overlay_state.py
telemetry-viewer\tests\test_diagnose_pathing_context.py
telemetry-viewer\tests\test_diagnose_service_context.py
telemetry-viewer\tests\test_diagnose_target_coverage.py
telemetry-viewer\tests\test_diagnose_task_policy.py
telemetry-viewer\tests\test_dialogue_core.py
telemetry-viewer\tests\test_human_input_controller.py
telemetry-viewer\tests\test_input_control_executor.py
telemetry-viewer\tests\test_input_geometry.py
telemetry-viewer\tests\test_input_integrity.py
telemetry-viewer\tests\test_inspect_live_packets.py
telemetry-viewer\tests\test_intent_overlay_analyzer.py
telemetry-viewer\tests\test_intent_stabilizer.py
telemetry-viewer\tests\test_inventory_analyzer.py
telemetry-viewer\tests\test_knowledge_fabric.py
telemetry-viewer\tests\test_live_config_doctor.py
telemetry-viewer\tests\test_live_context_query.py
telemetry-viewer\tests\test_live_control_panel.py
telemetry-viewer\tests\test_live_core_contracts.py
telemetry-viewer\tests\test_live_core_daemon.py
telemetry-viewer\tests\test_live_readiness.py
telemetry-viewer\tests\test_live_target_processor.py
telemetry-viewer\tests\test_liveness_recovery_core.py
telemetry-viewer\tests\test_maintenance.py
telemetry-viewer\tests\test_mission_presets.py
telemetry-viewer\tests\test_mission_snapshot.py
telemetry-viewer\tests\test_mock_brain_rehearsal.py
telemetry-viewer\tests\test_mouse_movement.py
telemetry-viewer\tests\test_navigation_analyzer.py
telemetry-viewer\tests\test_navigation_intent_analyzer.py
telemetry-viewer\tests\test_navigation_reachability.py
telemetry-viewer\tests\test_pathing_analyzer.py
telemetry-viewer\tests\test_pathing_matrix.py
telemetry-viewer\tests\test_post_bank_reacquisition_analyzer.py
telemetry-viewer\tests\test_post_bank_reacquisition_diagnostic.py
telemetry-viewer\tests\test_process_inventory_analyzer.py
telemetry-viewer\tests\test_resource_progress.py
telemetry-viewer\tests\test_resource_return_analyzer.py
telemetry-viewer\tests\test_resource_return_diagnostic.py
telemetry-viewer\tests\test_return_to_resource_analyzer.py
telemetry-viewer\tests\test_return_to_resource_diagnostic.py
telemetry-viewer\tests\test_run_daily_gauntlet.py
telemetry-viewer\tests\test_run_traced_dev_cycle.py
telemetry-viewer\tests\test_run_woodcut_bank_live_qa.py
telemetry-viewer\tests\test_runelite_bootstrap.py
telemetry-viewer\tests\test_runtime_control.py
telemetry-viewer\tests\test_safe_aimpoint_core.py
telemetry-viewer\tests\test_service_analyzer.py
telemetry-viewer\tests\test_service_route_core.py
telemetry-viewer\tests\test_target_analyzer.py
telemetry-viewer\tests\test_target_candidate_dedupe.py
telemetry-viewer\tests\test_target_view_core.py
telemetry-viewer\tests\test_task_policy.py
telemetry-viewer\tests\test_task_script_api.py
telemetry-viewer\tests\test_task_state.py
telemetry-viewer\tests\test_task_transitions.py
telemetry-viewer\tests\test_visual_debug_bundle.py
telemetry-viewer\tests\test_woodcut_bank_cycle_diagnostic.py
telemetry-viewer\tests\test_woodcut_bank_scenarios.py
telemetry-viewer\tests\test_woodcutting_candidate_diagnostic.py
telemetry-viewer\tests\test_world_model_core.py
```

Discovered Java test files run by Gradle:

```text
src\test\java\com\osrstelemetry\ClientTickHotStateTest.java
src\test\java\com\osrstelemetry\CompactLiveEmissionPolicyTest.java
src\test\java\com\osrstelemetry\PluginLiveCacheTest.java
src\test\java\com\osrstelemetry\PluginSnapshotEndpointTest.java
src\test\java\com\osrstelemetry\TelemetryConfigKeysTest.java
src\test\java\com\osrstelemetry\TelemetryDebugOverlayTest.java
src\test\java\com\osrstelemetry\TelemetryPluginTest.java
src\test\java\com\osrstelemetry\TelemetryPresetApplierTest.java
src\test\java\com\osrstelemetry\TelemetryRecordingModeTest.java
```

Discovered live/readiness/check commands:

```powershell
python telemetry-viewer\context_service.py --pipeline-health
python telemetry-viewer\context_service.py --query pipeline-health
python telemetry-viewer\context_service.py --query current-debug-context
python telemetry-viewer\context_service.py --query explain-current-blocker
python telemetry-viewer\context_service.py --query navigation-decision-trace
python telemetry-viewer\context_service.py --query task-script-run-readiness
python telemetry-viewer\context_service.py --query task-script-runtime-evidence
python telemetry-viewer\context_service.py --query task-failure-classification
python telemetry-viewer\context_service.py --query task-script-step-readiness
python telemetry-viewer\context_service.py --query run-readiness
python telemetry-viewer\context_service.py --query data-source-inventory
python telemetry-viewer\context_service.py --query query-coverage-matrix
python telemetry-viewer\context_service.py --coverage-report
python telemetry-viewer\context_service.py --handoff-summary
python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting
python telemetry-viewer\diagnose_woodcutting_candidates.py --latest-session --profile woodcutting --top 20 --show-rejections
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\live_config_doctor.py --latest-session --mode daily --fix-suggestions
python telemetry-viewer\check_live_setup.py --latest-session
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
python telemetry-viewer\mcp_server.py --list-tools
python telemetry-viewer\mcp_server.py --list-resources
python telemetry-viewer\mcp_server.py --call-tool get_live_status
python telemetry-viewer\mcp_server.py --call-tool get_knowledge_fabric_status
python telemetry-viewer\mcp_server.py --call-tool explain_current_blocker
python telemetry-viewer\mcp_server.py --call-tool get_pipeline_health
python telemetry-viewer\mcp_server.py --call-tool get_current_debug_context
```

Discovered build/export/batch QA commands:

```powershell
python telemetry-viewer\build_perception_dataset.py
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset focused-ui --latest 500 --generate-grid-slots
python telemetry-viewer\build_training_dataset.py --preset review --latest 500 --generate-grid-slots --rebuild
python telemetry-viewer\export_curated_training_dataset.py
python telemetry-viewer\export_curated_training_dataset.py --reviewed-only
python telemetry-viewer\export_curated_training_dataset.py --split train,val,test --seed 123
python telemetry-viewer\dataset_status.py
python telemetry-viewer\training_dataset_inspector.py
python telemetry-viewer\training_dataset_inspector.py --port 8790
python telemetry-viewer\training_dataset_inspector.py --session "C:\path\to\session"
python telemetry-viewer\calibrate_screen_regions.py --interactive --latest-existing-frame
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab auto
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab inventory
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --active-tab prayer
python telemetry-viewer\prepare_visual_perception.py --generate-crops --latest 25 --only-existing-frames --include-all-tab-profiles
python telemetry-viewer\inspect_tab_detection.py --limit 25
python telemetry-viewer\build_ui_target_geometry.py
python telemetry-viewer\build_ui_target_geometry.py --include-base-regions --latest-with-frames 100
python telemetry-viewer\build_world_target_geometry.py
python telemetry-viewer\build_world_target_geometry.py --only-on-screen --target-type sceneObject
python telemetry-viewer\build_world_target_geometry.py --target-type npc --only-on-screen --latest-with-frames 100
python telemetry-viewer\target_geometry_inspector.py
python telemetry-viewer\validate_session.py
python telemetry-viewer\export_session.py
python telemetry-viewer\latest_state.py
python telemetry-viewer\viewer.py
python telemetry-viewer\replay_viewer.py
```

Discovered execution-capable commands that are not safe audit checks:

```powershell
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --dry-run --explain-target --verify-coordinates
python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend arduino --arduino-port COM6 --arduino-require-monitor --input-profile steady --execute --verify-after-action --wait-for-ready 30 --hover-confirm-target --hover-confirm-timeout-ms 120 --hover-poll-ms 10 --hover-position-tolerance 3 --summary-every-action --final-reconcile-ms 3000 --final-reconcile-game-ticks 8 --resource-reconcile-ms 4000 --resource-reconcile-game-ticks 8 --pacing-profile natural --target-hover-failure-limit 2 --target-suppression-ms 2500 --max-total-actions 5 --max-consecutive-timeouts 2 --capture-debug-screenshots --screenshot-on-failure --screenshot-on-timeout --max-debug-screenshots 10 --overlay-passive --post-test-focus-target powershell
python telemetry-viewer\context_service.py --latest-session --ensure-loaded-scene
python telemetry-viewer\run_runelite_bootstrap.py --ensure-loaded-scene
python telemetry-viewer\execute_next_action.py --auto-recover-loaded-scene
python telemetry-viewer\execute_next_action.py --backend arduino --arduino-port COMx --arduino-stop-all
python telemetry-viewer\execute_next_action.py --input-integrity-self-test-no-move --backend arduino --arduino-port COMx --no-overlay
python telemetry-viewer\execute_next_action.py --arduino-pointer-calibration-test --backend arduino --arduino-port COMx --allowed-window calibration --no-click
python telemetry-viewer\execute_next_action.py --arduino-movement-diagnostics --backend arduino --arduino-port COMx
```

If unclear: the repo does not appear to define a single short "blessed smoke test" command that both verifies loaded-scene proof and guarantees no gameplay input. The closest non-executing gates are `diagnose_live_readiness.py`, `run_daily_gauntlet.py`, and the task-script readiness queries.

## Suspected causes of looping

- Multiple competing entrypoints: `telemetry_launcher.py`, `live_control_panel.py`, `run_runelite_bootstrap.py`, `context_service.py --ensure-loaded-scene`, `tools\run_traced_dev_cycle.py`, and `execute_next_action.py` all appear relevant to live recovery, but only some are daily/current.
- Stale markdown with current-sounding language: `docs\codex_handoff_current.md` and historical audit/cleanup files still include commands and "next step" language.
- Hidden required command chains: daily operation appears to require RuneLite dev client, 8893 PluginSnapshotEndpoint, 8890 daemon, Knowledge Fabric/readiness, input integrity, and sometimes Arduino COM setup. These dependencies are scattered across docs and scripts.
- No single blessed runner for recovery-safe audit mode: there are strong checks, but no obvious one-command "audit only, no live input, no source writes" runner.
- Blind `--latest-session` risk: active architecture docs warn that newest filesystem session can be stale compared with daemon/latest-live/highlighter state.
- Dev launch can be mistaken for real readiness: `gradlew run` starts a development client but does not prove authenticated loaded-scene state.
- Loaded-scene proof is stricter than stale tick freshness: tests and recovery code require game state, fresh client tick hot state, world-model objects, and no pending play panel. Future loops can happen if a run proceeds from freshness alone.
- Runtime behavior depends on manual environment facts: COM ports, Arduino firmware, VM focus, RuneLite/Jagex windows, and current login state are partly external to the repo and can be carried in chat memory instead of durable repo state.
- Execution-capable flags are mixed into examples: docs include safe diagnostics next to `--execute`, `--allow-software-input`, and Jagex launcher override examples, making copy/paste risky.
- The top-level launch wrappers hard-code an old path, so using them from this worktree can silently operate elsewhere or fail confusingly.
- Some test commands directly run individual test files, while the broader suite has its own embedded command matrix. This makes it easy to under-test or overrun irrelevant checks.

## Proposed recovery plan

1. Keep this recovery branch audit-only until the authoritative runbook is clarified.
2. Do not move or delete historical docs yet. First add clear archive banners or an index-only warning to the confusing historical files.
3. Pick one current "read first" path and make README/tool registry/docs index agree on it.
4. Pick one daily launcher path. Based on the current registry, prefer `live_control_panel.py` over `telemetry_launcher.py`, but do not change code yet.
5. Replace or quarantine the hard-coded top-level Windows wrappers after explicit approval.
6. Create a short recovery-safe command list that separates read-only checks, recovery controllers, and execution-capable commands.
7. Define a non-executing smoke gate for live readiness that proves `loadedSceneVerified=true` without sending gameplay input.
8. Document that `gradlew run` is only a dev-client launch and is not loaded-scene proof.
9. Keep `context_service.py --ensure-loaded-scene` documented as the existing recovery path, but label it as recovery-capable and environment-dependent.
10. After documentation cleanup only, run documentation diff review first; run tests/checks only if source/runtime code is changed later.

## Instruction stabilization update - 2026-06-14

Changed active documentation only:

- Replaced `AGENTS.md` with a short durable rule file under 150 lines.
- Replaced `PROJECT_STATE.md` with current facts only.
- Added `MILESTONES.md` with only R1, R2, and R3 recovery milestones.
- Added `docs\archive\README.md` to define archived docs as reference only.

No runtime/source files were changed. No files were deleted. No files were moved.

Active instruction source of truth is now:

- `AGENTS.md` - durable Codex rules.
- `PROJECT_STATE.md` - current repo facts.
- `MILESTONES.md` - current recovery build order.
- `RECOVERY_LOG.md` - recovery history.

## Needs human review

These files were not moved because their current status or final home still needs human review:

- `README.md` - still points most users to `telemetry_launcher.py`, while the registry marks it deprecated.
- `telemetry-viewer\README.md` - still points most users to `telemetry_launcher.py`, while the registry marks it deprecated.
- `docs\INDEX.md` - useful index, but it still belongs to the old broader instruction stack and should be reconciled with the new active source-of-truth rule.
- `docs\tool_registry.md` - useful command map, but should be reviewed after R1 creates one blessed recovery command.
- `docs\daily_live_architecture.md` - may remain useful architecture reference, but is too broad to be active recovery instruction.
- `docs\chatgpt_consultation_workflow.md` - workflow status unclear for the active recovery path.
- `docs\clickable_hull_pipeline.md` - architecture/reference status unclear for recovery milestones.
- `docs\telemetry_schema.md` - large schema reference; should not be active instruction.
- `telemetry-viewer\docs\live_stack_architecture.md` - current reference, but too broad to be active recovery instruction.
- `telemetry-viewer\docs\live_outputs.md` - large output reference; should not be active instruction.
- `docs\codex_handoff_current.md` - marked historical but still sounds current; needs archive treatment or stronger warning.
- `docs\cleanup_report.md` - historical cleanup reference.
- `docs\runtime_cleanup_report.md` - historical cleanup reference.
- `telemetry-viewer\docs\cleanup_inventory.md` - historical cleanup inventory with current-sounding command language.
- `telemetry-viewer\docs\pipeline_cleanup_inventory.md` - historical cleanup inventory.
- `telemetry-viewer\docs\pristine_audit_report.md` - historical audit with stale process assumptions.

## R1 read-only state baseline repair - 2026-06-14

Implemented Milestone R1 only.

Changed:

- Added `telemetry-viewer\context_service.py --state-baseline` as the canonical read-only recovery state parser/status command.
- Added `recovery_state_baseline.v1` compact status output.
- Reused the existing `LiveContextCache`, live state paths, timestamp parsing, latest-tick selection, and inventory normalization instead of creating a duplicate parser.
- Added field-name drift normalization for existing read-only state bridge names such as `gameState` / `game_state`, `latestTick` / `latestTickProcessed`, `worldX` / `world_x`, and `inventorySlotCount` / `slotCount`.
- Added `telemetry-viewer\tests\test_state_baseline.py` with missing-file, malformed-JSON, valid-minimal-JSON, and stale-JSON coverage.
- Updated `scripts\run_current_milestone.ps1` to run the R1 unittest subset and `python telemetry-viewer\context_service.py --latest-session --state-baseline`.
- Updated `PROJECT_STATE.md` with the canonical parser path, canonical status command, and limitations.

Blessed command result:

- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1` passed.
- Doctor passed.
- In-memory Python syntax compile checked 237 files.
- R1 unittest subset ran 4 tests and passed.
- The live state parser check returned `WARN` for the current latest session because `live_baseline_state.json`, `live_status.json`, `live_context_index.json`, and `live_candidates.jsonl` were missing under that session's `interaction_geometry\live` folder. This is an expected clean missing-state report, not action execution.

Current limitations:

- Missing state is reported, not repaired.
- Malformed JSON is reported, not rewritten.
- Stale timestamps are reported, not refreshed.
- `--state-baseline` does not prove loaded-scene readiness.
- `--state-baseline` does not start RuneLite, the daemon, the snapshot endpoint, or any external service.
- R1 emits no click, mouse, keyboard, menu, route, banking, task, or gameplay action command.

## Entrypoint quarantine update - 2026-06-14

Reduced recovery entrypoint confusion by documentation status only.

Changed:

- Added `scripts\README.md`.
- Recorded exactly one blessed recovery command: `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`.
- Listed support helpers: `scripts\doctor.ps1` and `python telemetry-viewer\context_service.py --latest-session --state-baseline`.
- Linked `scripts\README.md` from `PROJECT_STATE.md`.

No files were deleted. No files were renamed. No runtime logic was changed for this quarantine step.

Deprecated or unknown entrypoints documented as not blessed during recovery:

- `Start-LiveControlPanel.ps1` - obsolete/action-capable; hard-coded old path; no direct imports found.
- `Start-NormalLiveStack.ps1` - obsolete/action-capable; hard-coded old path and auto-start behavior; no direct imports found.
- `start_live_control_panel.bat` - obsolete/action-capable; hard-coded old path; no direct imports found.
- `start_normal_live_stack.bat` - obsolete/action-capable; hard-coded old path and auto-start behavior; no direct imports found.
- `gradlew.bat run` - action-capable/unknown for recovery; dev launch only, not loaded-scene proof.
- `telemetry-viewer\telemetry_launcher.py` - obsolete/action-capable; registry marks it deprecated; no direct imports found.
- `telemetry-viewer\live_control_panel.py` - action-capable; current outside recovery but launches subprocesses; tests import it.
- `telemetry-viewer\live_core_daemon.py` - read-only service/unknown for recovery; starting services is outside R1; tests and runtime helpers import it.
- `telemetry-viewer\run_daily_gauntlet.py` - read-only helper/unknown for recovery; depends on live daemon/session state; tests and QA helper import it.
- `telemetry-viewer\run_woodcut_bank_live_qa.py` - read-only helper/unknown for recovery; activity-specific and outside R1; tests import it.
- `telemetry-viewer\context_service.py` modes other than `--state-baseline` - mixed/unknown for recovery; broader query, handoff, external lookup, and recovery-capable modes remain unblessed.
- `telemetry-viewer\context_service.py --ensure-loaded-scene` - recovery-capable/action-capable; existing recovery path, not part of R1.
- `telemetry-viewer\execute_next_action.py` - action-capable; tests and traced-cycle helper import it.
- `telemetry-viewer\run_runelite_bootstrap.py` - action-capable; recovery core, traced-cycle helper, and tests import it.
- `telemetry-viewer\tools\run_traced_dev_cycle.py` - action-capable in run mode; tests import it.
- `telemetry-viewer\run_stabilization_suite.py` - read-only broad check; not the R1 blessed runner.
- `telemetry-viewer\inspect_live_packets.py` - obsolete retired compatibility shim; tests execute it as a subprocess.
- `telemetry-viewer\test_telemetry_paths.py` - obsolete/read-only compatibility wrapper; no direct imports found.
- `telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply` - deletion-capable maintenance mode; do not run during recovery.

## R2 compact context boundary - 2026-06-14

Implemented Milestone R2 only.

Changed:

- Added compact context boundary functions in `telemetry-viewer\context_service.py`.
- Added `python telemetry-viewer\context_service.py --latest-session --compact-context`.
- Added optional `--compact-context-request` for a small `context_request.v1` JSON request with `needs`, `responseMode`, and `maxAgeMs`.
- The R2 response schema is `context_response.v1`.
- The R2 response consumes the R1 `recovery_state_baseline.v1` payload and emits only read-only facts.
- Activity in the R2 response is an allowlisted summary, not a raw activity payload pass-through.
- Added `telemetry-viewer\tests\test_compact_context_boundary.py`.
- Marked `MILESTONES.md` active milestone as R2.
- Updated `scripts\run_current_milestone.ps1` so R2 checks run only when `MILESTONES.md` marks R2 active.
- Updated `PROJECT_STATE.md` and `scripts\README.md` with the compact context boundary.

R2 response allowed fields:

- schema
- ok
- errors
- warnings
- generatedAtUtc
- state
- player
- inventory
- activity
- liveness
- source

R2 response explicitly excludes:

- action commands
- click commands
- mouse commands
- keyboard commands
- menu commands
- movement commands
- target-to-interact-with fields
- anti-detection fields
- gameplay commands

R2 limitations:

- It does not choose tasks.
- It does not choose routes.
- It does not choose banking behavior.
- It does not prove loaded-scene readiness.
- It does not start RuneLite, the daemon, the snapshot endpoint, or any external service.

## Parallel read-only recovery audit - 2026-06-14

Four read-only subagents audited the recovery branch. No subagent edited files.
This section consolidates their findings only.

### Markdown audit

Authoritative docs:

- `AGENTS.md` - current Codex operating rules and recovery safety.
- `PROJECT_STATE.md` - current branch, worktree, architecture boundary, blessed command, and known entrypoints.
- `MILESTONES.md` - active milestone and next recovery milestones.
- `scripts\README.md` - recovery command inventory and command quarantine.
- `docs\archive\README.md` - archive/reference semantics.
- `RECOVERY_LOG.md` - recovery history/provenance only, not a current runbook.

Stale or confusing docs:

- `docs\INDEX.md` still describes an active instruction path that includes `CURRENT_GOAL.md` and `docs\INDEX.md`, while `AGENTS.md` now says to read only `PROJECT_STATE.md` and `MILESTONES.md` after `AGENTS.md`.
- `CURRENT_GOAL.md` appears stale for active R2 because it points at route/pathing regression work and possible behavior fixes.
- `README.md` and `telemetry-viewer\README.md` still promote `telemetry_launcher.py`, while `scripts\README.md` marks it obsolete/action-capable for recovery.
- `docs\codex_handoff_current.md` says it is historical reference only, but also tells new chats to read it after `AGENTS.md` and contains live execution examples.
- Very large context docs such as `docs\telemetry_schema.md`, `docs\codex_handoff_current.md`, `telemetry-viewer\docs\live_stack_architecture.md`, and `telemetry-viewer\docs\live_outputs.md` should remain reference-only, not read-first.
- `docs\analysis_examples.md` includes deletion-capable `--apply` examples and should not be treated as recovery guidance.

Contradictions:

- Active instruction path drift between `AGENTS.md`, `docs\INDEX.md`, and older `RECOVERY_LOG.md` entries.
- R2 read-only scope conflicts with stale `CURRENT_GOAL.md` route/pathing behavior language.
- One blessed command in `PROJECT_STATE.md` conflicts with README launcher guidance.
- Historical docs are bannered as stale but still contain current-sounding instructions.

Recommended instruction clarification:

- Make clear that `CURRENT_GOAL.md`, `docs\INDEX.md`, README files, tool registry docs, architecture docs, examples, handoffs, and old recovery-log entries are reference only during recovery unless restated in `AGENTS.md`, `PROJECT_STATE.md`, or `MILESTONES.md`.

### Entrypoint audit

Blessed command:

- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

Support commands:

- `powershell -ExecutionPolicy Bypass -File scripts/doctor.ps1`
- `python telemetry-viewer\context_service.py --latest-session --state-baseline`
- `python telemetry-viewer\context_service.py --latest-session --compact-context`

Known but not blessed:

- `.\gradlew.bat test`
- `.\gradlew.bat build`
- `python telemetry-viewer\run_stabilization_suite.py`
- Context query commands, readiness diagnostics, daily gauntlet, and live QA helpers listed in `PROJECT_STATE.md`.

Deprecated, unknown, or dangerous/confusing commands:

- `Start-LiveControlPanel.ps1`, `Start-NormalLiveStack.ps1`, `start_live_control_panel.bat`, and `start_normal_live_stack.bat` are obsolete/action-capable wrappers with old hard-coded paths.
- `telemetry-viewer\telemetry_launcher.py` is deprecated in the registry, but older READMEs still promote it.
- `telemetry-viewer\test_telemetry_paths.py` and `telemetry-viewer\inspect_live_packets.py` are obsolete compatibility surfaces.
- `telemetry-viewer\live_control_panel.py`, `live_core_daemon.py`, `run_daily_gauntlet.py`, `run_woodcut_bank_live_qa.py`, and broad `context_service.py` query modes are current daily/debug surfaces but unblessed for recovery.

Commands Codex should not run during recovery:

- `gradlew run`; it is a dev launch and not loaded-scene proof.
- `telemetry-viewer\context_service.py --ensure-loaded-scene`; recovery-capable/action-capable and outside R2.
- `telemetry-viewer\execute_next_action.py`, especially `--execute`, `--auto-recover-loaded-scene`, `--allow-software-input`, or `--unsafe-allow-pyautogui-live`.
- `telemetry-viewer\run_runelite_bootstrap.py`, especially execution, launch, loaded-scene recovery, or launcher automation modes.
- `telemetry-viewer\tools\run_traced_dev_cycle.py --run`.
- `telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply`.

### Test/check audit

Existing tests:

- Java uses Gradle with JUnit 4.12 and has Java tests under `src\test\java`.
- Python tests are standard-library `unittest` scripts under `telemetry-viewer\tests`.
- The blessed runner executes `telemetry-viewer\tests\test_state_baseline.py` for R1 and adds `telemetry-viewer\tests\test_compact_context_boundary.py` when R2 is active.
- R1 tests cover missing state, malformed JSON, valid minimal state, stale state, and absence of obvious input/action terms in the minimal fixture.
- R2 tests cover compact response shape, request filtering, R1 parser error propagation, stale max-age behavior, and dropping action-like fields from activity summaries.

Missing tests:

- No direct smoke test for `scripts\run_current_milestone.ps1` milestone detection and fail-clearly paths.
- No deterministic CLI smoke using a synthetic session directory; current runner uses `--latest-session`.
- No blessed smoke asserting R1/R2 output is not loaded-scene proof and cannot green-light gameplay.
- No Java smoke in the blessed runner; doctor checks Java and Gradle wrapper availability only.
- No blessed JSON-content assertion for CLI smoke outputs. Compact context can return `ok:false` while the CLI exits 0.

Safest check command:

- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

Runner risks:

- `--latest-session` makes the smoke checks environment-sensitive.
- Missing/stale/malformed state can be reported as `WARN` while the runner passes.
- `context_response.v1` can be `ok:false` while the compact-context CLI exits 0 because the shared print helper is status-oriented.
- R2 activation depends on matching `Active milestone:\s*R2\b`; if the line drifts, the runner silently falls back to R1-only.
- Gradle checks are less reproducible because `mavenLocal()` is first and RuneLite uses `latest.release`.

### Architecture boundary audit

Read-only components:

- `PROJECT_STATE.md` and `MILESTONES.md` define R1/R2 as read-only state/context work.
- The R1/R2 path in `telemetry-viewer\context_service.py` is read-only: it loads existing telemetry files, builds `recovery_state_baseline.v1`, and returns compact `context_response.v1` with allowlisted sections.
- `telemetry-viewer\live_context_query.py` contains read-only file/state reader and selector helpers.
- Boundary tests cover the compact context response and obvious forbidden action/input terms.

Action-capable components:

- `telemetry-viewer\execute_next_action.py` is the explicit action executor.
- `telemetry-viewer\run_runelite_bootstrap.py` is execution-capable when execution or launcher/recovery flags are used.
- Executor tests confirm action execution is possible when readiness permits, and should remain outside recovery milestones.

Mixed-responsibility components:

- `telemetry-viewer\context_service.py` contains the clean R1/R2 read-only path, but the same module also contains task needs, watch-request file writes, task summaries, handoff/query surfaces, and loaded-scene recovery delegation.
- `telemetry-viewer\live_context_query.py` is read-only with respect to input but mixes telemetry reads with task-specific woodcutting, target summaries, aim points, navigation readiness, and task readiness.
- `telemetry-viewer\live_core_daemon.py` is not the direct action executor, but it mixes daemon state, task policy, brain/decision endpoints, route/service/bank readiness, and control surfaces.

Recommended separation:

- Keep `context_service.py --state-baseline` and `--compact-context` as the canonical R1/R2 read-only surface for now, or later split them into a small read-only module with no recovery import, subprocess launching, task-summary needs, or action-readiness schemas.
- Move task summaries, Knowledge Fabric task-script readiness, brain decisions, route/service/bank readiness, and `/brain` behavior into an advisory decision-context layer separate from read-only telemetry context.
- Keep `execute_next_action.py` and `run_runelite_bootstrap.py` as the only action-capable CLIs, clearly outside R1/R2.
- Expand forbidden-field tests to cover unsupported request warning text and every R2 read-only entrypoint/schema.

### Consolidated follow-up risks

- R2 warning text can echo unsupported request values; a request containing click/menu/action-like text can make those words appear in `context_response.v1` warnings.
- `PROJECT_STATE.md` still has stale wording that calls the runner the R1 runner even though R2 is active.
- R1 may pass through raw `bank` or `activity` objects when present; this is read-only but less strict than the R2 allowlist.
- Existing docs still contain many old launcher, execution, cleanup, and route/task examples that are useful reference but dangerous as active instructions.

## Recovery runner and R2 sanitization fix - 2026-06-14

Fixed only the current recovery blockers.

Changed files:

- `telemetry-viewer\context_service.py`
- `telemetry-viewer\tests\test_compact_context_boundary.py`
- `telemetry-viewer\tests\test_recovery_response_verifier.py`
- `scripts\verify_recovery_response.py`
- `scripts\run_current_milestone.ps1`
- `scripts\README.md`
- `PROJECT_STATE.md`
- `RECOVERY_LOG.md`

Blocker A - blessed runner trust:

- Added `scripts\verify_recovery_response.py`, a narrow stdin JSON verifier for recovery milestone responses.
- Added deterministic fixture coverage in `telemetry-viewer\tests\test_recovery_response_verifier.py`.
- The verifier rejects invalid JSON, missing required fields, `status: "FAIL"`, `ok: false`, forbidden response field names, and forbidden compact-context response text.
- Updated `scripts\run_current_milestone.ps1` so the required gate is the deterministic test/fixture suite.
- Relabeled `--latest-session` output as optional diagnostic only. Latest-session warnings or `ok:false` are no longer the proof that R1/R2 passed.

Blocker B - R2 response sanitization:

- Replaced raw unsupported request echoes with safe warning/error codes.
- Unsupported `needs`, `task`, `profile`, `responseMode`, invalid schema, invalid JSON, and unknown request fields are reported as codes such as `unsupported_need`, `unsupported_task`, `unsupported_profile`, `unsupported_response_mode`, `invalid_schema`, `invalid_json_request`, and `unsupported_request_field_count`.
- Added tests proving unsupported request values containing action-like text do not echo into `context_response.v1`.
- Expanded recursive forbidden-field/text checks for `context_response.v1`.

Commands run:

- `python telemetry-viewer\tests\test_state_baseline.py`
- `python telemetry-viewer\tests\test_compact_context_boundary.py`
- `python telemetry-viewer\tests\test_recovery_response_verifier.py`
- forced `context_response.v1` `ok:false` verifier probe
- forced `recovery_state_baseline.v1` `status:"FAIL"` verifier probe
- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`
- compact-context unsupported-request sanitization probe
- `git diff --check`

Remaining risks:

- `context_service.py` remains mixed-responsibility outside the narrow R1/R2 path.
- R1 still has a raw `bank`/`activity` pass-through when those optional read-only objects are present; this was not changed in this blocker fix.
- Large historical docs and old launcher/action examples remain in the tree as reference material and should not be treated as active recovery instructions.

## R3 no-action diagnostic scaffold - 2026-06-14

Started R3 as a diagnostic-only boundary.

Changed files:

- `telemetry-viewer\recovery_diagnostics.py`
- `telemetry-viewer\tests\test_recovery_diagnostics.py`
- `scripts\run_current_milestone.ps1`
- `MILESTONES.md`
- `PROJECT_STATE.md`
- `RECOVERY_LOG.md`

What was added:

- Added `recovery_diagnostic.v1`, an in-memory diagnostic response produced from `context_response.v1`.
- Added a small diagnostic module that validates read-only context presence and shape.
- Added deterministic tests for missing context, malformed context, valid minimal context, context warnings, and forbidden field rejection.
- Updated the blessed runner so R3 runs only deterministic no-action tests when `MILESTONES.md` marks R3 active.

Why it is safe:

- The R3 module accepts an already-built compact context object.
- It does not read live files.
- It does not call subprocesses.
- It does not start RuneLite, the daemon, the snapshot endpoint, or any external service.
- It does not choose tasks, routes, targets, banking behavior, or activity behavior.
- It returns diagnostic fields only: `schema`, `ok`, `status`, `reasons`, `required_context`, `observed_context`, and `warnings`.
- It rejects forbidden context field names without echoing raw field names into the diagnostic response.

Commands run:

- `python telemetry-viewer\tests\test_recovery_diagnostics.py`
- `python telemetry-viewer\tests\test_state_baseline.py`
- `python telemetry-viewer\tests\test_compact_context_boundary.py`
- `python telemetry-viewer\tests\test_recovery_response_verifier.py`
- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

Remaining risks:

- `context_service.py` remains mixed-responsibility outside the R1/R2 path. R3 was kept in a separate small module instead of further expanding that file.

## R2.5 context-boundary hardening after R3 - 2026-06-14

Started the final cleanup pass from clean checkpoint `548c179`.

Responsibility classification:

- R1 state loading/file discovery remains in `telemetry-viewer\context_service.py` through `ContextState`.
- R1 state parsing and normalization now lives in `telemetry-viewer\state_baseline.py`.
- R2 compact context request/response handling now lives in `telemetry-viewer\context_boundary.py`.
- R2 response safety is covered by `telemetry-viewer\tests\test_compact_context_boundary.py` and `scripts\verify_recovery_response.py`.
- CLI/server/wrapper glue remains in `telemetry-viewer\context_service.py`.
- Older query, watch, server, recovery, and maintenance surfaces remain outside the R1/R2 recovery boundary.

What was extracted:

- Moved the pure `recovery_state_baseline.v1` payload builder and read-only normalization helpers into `state_baseline.py`.
- Moved the pure `context_request.v1`/`context_response.v1` compact boundary into `context_boundary.py`.
- Kept compatibility imports in `context_service.py` so existing tests and callers can still use `context_service.state_baseline_payload` and `context_service.compact_context_response`.
- Updated the blessed runner to recognize `R2.5` and run the same deterministic R1/R2/R3 safety gate.

Why this stayed small:

- No schema changed.
- No runner was duplicated.
- No parser was duplicated.
- No live file loading behavior moved.
- No task, route, banking, activity automation, anti-detection, or direct action execution was added.

Commands run:

- `python telemetry-viewer\tests\test_state_baseline.py`
- `python telemetry-viewer\tests\test_compact_context_boundary.py`
- `python telemetry-viewer\tests\test_recovery_response_verifier.py`
- `python telemetry-viewer\tests\test_recovery_diagnostics.py`
- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1`

Remaining risks:

- `context_service.py` remains broad because it still owns CLI/server/query/watch glue and older non-recovery surfaces.
- The remaining broadness is not blocking the read-only recovery boundary because R1/R2 payload logic is now isolated and covered by deterministic tests.
- A larger server split should wait for a non-recovery refactor milestone.

Recovery completion note:

- After the blessed command passes and this cleanup is reviewed, the read-only recovery baseline can be considered complete enough to checkpoint before any R4 work.

## R4 read-only live readiness fixtures - 2026-06-14

Started R4 from checkpoint `632ad0f91e8a613ef0608fee509adf127d62e709`.

Changed files:

- `telemetry-viewer\recovery_diagnostics.py`
- `telemetry-viewer\tests\test_r4_live_readiness_fixtures.py`
- `telemetry-viewer\tests\fixtures\r4_live_readiness\stale_logged_in.json`
- `telemetry-viewer\tests\fixtures\r4_live_readiness\login_screen.json`
- `telemetry-viewer\tests\fixtures\r4_live_readiness\logged_in_no_scene_evidence.json`
- `telemetry-viewer\tests\fixtures\r4_live_readiness\loaded_scene_evidence_present.json`
- `telemetry-viewer\tests\fixtures\r4_live_readiness\incomplete_telemetry.json`
- `scripts\run_current_milestone.ps1`
- `PROJECT_STATE.md`
- `MILESTONES.md`
- `RECOVERY_LOG.md`

What was added:

- Added deterministic R4 fixtures for missing state setup, malformed state setup, stale logged-in state, login-screen state, logged-in state without scene evidence, loaded-scene evidence, and incomplete telemetry.
- Added `evaluate_observation_readiness`, a read-only in-memory diagnostic helper that accepts already-loaded compact context data plus already-loaded fixture evidence.
- Added R4 tests for observation-readiness only, recursive `context_response.v1` safety, recursive `recovery_diagnostic.v1` safety, and fixture naming.
- Updated the blessed runner to recognize R4 and run the deterministic R1/R2/R2 verifier/R3/R4 test gate.
- For R4, the runner omits `--latest-session` diagnostics so the pass proof is fixture-only and deterministic.

Why it is safe:

- No live files are required for R4 tests.
- No subprocesses are launched by the R4 helper.
- No client control or gameplay behavior is implemented.
- No task, route, banking, activity, anti-detection, or direct action execution behavior is added.
- Loaded-scene readiness is documented and tested as observation-readiness only.
- `gradlew run` remains documented as a development launch, not loaded-scene proof.

Commands run:

- `powershell -ExecutionPolicy Bypass -File scripts/run_current_milestone.ps1` before R4 edits; result: PASS.
- `python telemetry-viewer\tests\test_r4_live_readiness_fixtures.py`; first run found a test assertion mismatch, then rerun passed after narrowing the assertion to the diagnostic contract.

Remaining risks:

- `context_service.py` remains broad outside the isolated R1/R2 payload boundary.
- R4 proves only deterministic fixture behavior and does not prove the current live client is loaded.
- R4 does not validate task selection, routing, banking, activity automation, or gameplay execution.

# Historical Reference Only

Historical reference only. Do not treat as current implementation guidance.

---

# Cleanup Inventory

Scope inspected:

- `telemetry-viewer/*.py`
- `telemetry-viewer/input_control/*.py`
- `telemetry-viewer/tests/*.py`
- `telemetry-viewer/docs/*.md`
- `telemetry-viewer/target_profiles.json`
- `telemetry-viewer/target_library.json`
- Live output conventions under `interaction_geometry/live`
- `AGENTS.md`

This inventory classifies the current live/action/candidate surface and recommends canonical entrypoints. It preserves existing diagnostics unless a script is clearly superseded.

## Canonical Systems

| System | Canonical module or command | Notes |
| --- | --- | --- |
| Session/path resolution | `live_session_core.py`, `telemetry_paths.py` | Use daemon session for action source; compare with newest live-output session. |
| Live file/cache loading | `live_file_core.py` | Use shared path/read helpers for explicit bounded debug/latest-state files; snapshot-no-files may intentionally omit candidate files. |
| Candidate classification/scoring | `live_target_processor.py`, `candidate_core.py`, `target_library.json`, `target_profiles.json` | Processor owns generation/scoring; core owns explanation/source checks. |
| Readiness/freshness validation | `live_readiness_core.py`, `diagnose_live_readiness.py` | Reusable PASS/WARN/FAIL readiness contract. |
| Context/daemon handling | `live_core_daemon.py`, `context_service.py`, `live_context_query.py` | Daemon status is the action source of truth. |
| Action proposal/explanation | `input_control/action_proposal.py`, `action_proposal_core.py`, `candidate_core.explain_candidate` | Proposer is read-only. |
| Action execution | `input_control/executor.py`, `execute_next_action.py` | Execution must gate on readiness PASS. |
| Client-tick interaction | `client_tick_core.py`, Java `ClientTickHotState`, `PluginSnapshotEndpoint` | Shared hover/menu freshness and clicked-menu classification. |
| Diagnostics/reporting | Canonical diagnostics below | Diagnostics import core modules; they do not define runtime truth. |
| Tests | `telemetry-viewer/tests/*.py`, `run_stabilization_suite.py` | Suite now reports behavior categories. |
| Docs/runbooks | `telemetry-viewer/docs/*.md`, `docs/codex_handoff_current.md`, `AGENTS.md` | Architecture and outputs are documented here. |

## Canonical Commands

| Purpose | Command |
| --- | --- |
| Live readiness | `python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting` |
| Woodcutting candidates | `python telemetry-viewer\diagnose_woodcutting_candidates.py --latest-session --profile woodcutting --top 20 --show-rejections` |
| Visual inspector/highlighter | `python telemetry-viewer\target_geometry_inspector.py --from-daemon --daemon-url http://127.0.0.1:8890 --live` |
| Active-session live processor, only when file-output inspection is required | `python telemetry-viewer\live_target_processor.py --from-daemon --daemon-url http://127.0.0.1:8890 --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark` |
| Action dry run | `python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --dry-run --explain-target --verify-coordinates` |
| Bounded execution | `python telemetry-viewer\execute_next_action.py --daemon-url http://127.0.0.1:8890 --backend pyautogui --movement-profile linear_debug --execute --verify-after-action --wait-for-ready 30` |

## Runtime Scripts

| Entrypoint | Purpose | Role/status | Main inputs | Main outputs | Source | Can execute input/clicks | Related tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `live_core_daemon.py` | Runs live context daemon, analyzers, overlay state, and control/status endpoints. | Runtime canonical | Plugin snapshot, compact packets, sessions, policy/profile args | HTTP status/context/control responses, optional overlay state | Daemon and live/session sources | No direct click execution | `test_live_core_daemon.py`, context/diagnostic tests |
| `live_target_processor.py` | Builds live candidates, activity, navigation, overlay debug state, and live files. Use `--from-daemon` for active live validation. | Runtime canonical | Raw ticks, compact packets, plugin snapshot, target library/profiles | Live files/cache, candidates, overlay debug JSON | Live files/cache | No | `test_live_target_processor.py` |
| `context_service.py` | Serves compact context answers from live files/session. | Runtime/service canonical | Session/live files, context requests | HTTP context responses | Live files | No | `test_context_service.py` |
| `live_context_query.py` | CLI/context query helper for live files. | Runtime/diagnostic canonical for context answers | Session/live files | Summary/task/context JSON or human output | Live files | No | `test_live_context_query.py` |
| `live_control_panel.py` | Mission Control dashboard. | Runtime UI | Daemon endpoints and commands | Local panel/dashboard | Daemon | Can launch commands, no gameplay input | `test_live_control_panel.py` |
| `control_live_daemon.py` | Sends daemon control commands. | Runtime control helper | Daemon URL/control payload | Control response | Daemon | No | Covered through daemon/control tests |
| `execute_next_action.py` | Builds dry-run/action report or executes one bounded action/loop. | Runtime executor canonical | Daemon status, readiness, backend options | Execution result JSON/human output | Daemon plus readiness/live files | Yes only with `--execute` | `test_input_control_executor.py`, `test_live_readiness.py` |
| `run_runelite_bootstrap.py` | Already-authenticated RuneLite startup/bootstrap helper. | Runtime helper with bounded input | RuneLite process/window, plugin endpoint, daemon args | Bootstrap/live QA report | Plugin endpoint/daemon | Yes for allowed startup buttons only | `test_runelite_bootstrap.py` |
| `run_woodcut_bank_live_qa.py` | One-command live QA runner for woodcut bank cycle. | Runtime QA | Daemon URL/plugin endpoint | QA report | Daemon/plugin endpoint | No direct gameplay click | `test_run_woodcut_bank_live_qa.py` |
| `run_daily_gauntlet.py` | Daily daemon/overlay/context health gauntlet. | Runtime QA | Daemon URL/process checks | PASS/WARN/FAIL report | Daemon/live overlay | No | `test_run_daily_gauntlet.py` |
| `telemetry_launcher.py` | Launcher/control utility for local telemetry workflows. | Runtime utility | Sessions/processes/config | Launcher UI/status | Files/processes | May launch processes, not gameplay input | Not in stabilization suite |

## Diagnostic Scripts

| Entrypoint | Purpose | Role/status | Main inputs | Main outputs | Source | Can execute input/clicks | Related tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `diagnose_live_readiness.py` | Checks daemon/session/live/highlighter/target/input readiness. | Diagnostic canonical | Daemon status, sessions, live outputs | `live_readiness.v1` | Both daemon and live files | No | `test_live_readiness.py`, `test_live_core_contracts.py` |
| `diagnose_woodcutting_candidates.py` | Explains candidate/highlighter/source health. | Diagnostic canonical | Daemon status, latest/live sessions | `woodcutting_candidate_diagnostic.v1` | Both daemon and live files | No | `test_woodcutting_candidate_diagnostic.py` |
| `target_geometry_inspector.py` | Browser visual inspector for target/highlighter/live geometry. In live daemon mode it can use `overlay_debug_state.json` directly when candidate files are intentionally absent. | Diagnostic canonical visual inspector | Session or daemon session, live files | Local browser/UI data | Live files; daemon for `--from-daemon` | No | `test_live_target_processor.py` inspector coverage |
| `diagnose_action_proposal.py` | Read-only action proposal diagnostic. | Diagnostic canonical for proposal JSON | Daemon status | `action_proposal_diagnostic.v1` | Daemon | No | `test_diagnose_action_proposal.py` |
| `diagnose_action_lifecycle.py` | Explains action lifecycle/expected result state. | Diagnostic canonical for lifecycle | Daemon status | Lifecycle diagnostic | Daemon | No | Action lifecycle tests |
| `diagnose_input_geometry.py` | Checks input/canvas geometry conversion. | Diagnostic canonical | Daemon status | Input geometry report | Daemon | No | `test_diagnose_input_geometry.py` |
| `diagnose_mouse_movement.py` | Explains movement profile planning. | Diagnostic helper | Movement args | Movement diagnostic | None/live status optional | No | `test_diagnose_mouse_movement.py` |
| `diagnose_service_context.py` | Explains bank/service target state. | Diagnostic canonical for service | Daemon status | Service report | Daemon | No | `test_diagnose_service_context.py` |
| `diagnose_pathing_context.py` | Explains pathing/serviceReady state. | Diagnostic canonical for pathing | Daemon status | Pathing report | Daemon | No | `test_diagnose_pathing_context.py` |
| `diagnose_task_transition.py` | Explains policy/task transition state. | Diagnostic canonical | Daemon status or synthetic scenarios | Transition report | Daemon/synthetic | No | `test_task_transitions.py` |
| `diagnose_task_policy.py` | Policy decision diagnostic. | Diagnostic helper | Policy args/synthetic state | Policy report | Synthetic/core | No | `test_diagnose_task_policy.py` |
| `diagnose_bank_ui_context.py` | Bank UI context diagnostic. | Diagnostic canonical | Daemon status | Bank UI report | Daemon | No | `test_bank_ui_diagnostic.py` |
| `diagnose_bank_operation_context.py` | Bank operation diagnostic. | Diagnostic canonical | Daemon status | Bank operation report | Daemon | No | `test_bank_operation_diagnostic.py` |
| `diagnose_close_bank_context.py` | Close-bank readiness diagnostic. | Diagnostic canonical | Daemon status | Close-bank report | Daemon | No | `test_close_bank_diagnostic.py` |
| `diagnose_return_to_resource_context.py` | Return-to-resource context diagnostic. | Diagnostic canonical | Daemon status | Return report | Daemon | No | `test_return_to_resource_diagnostic.py` |
| `diagnose_post_bank_reacquisition_context.py` | Post-bank reacquisition diagnostic. | Diagnostic canonical | Daemon status | Reacquisition report | Daemon | No | `test_post_bank_reacquisition_diagnostic.py` |
| `diagnose_resource_return_context.py` | Resource return destination diagnostic. | Diagnostic canonical | Daemon status | Resource return report | Daemon | No | `test_resource_return_diagnostic.py` |
| `diagnose_cycle_history.py` | Cycle history tail/transition diagnostic. | Diagnostic canonical | Daemon status/history | Cycle history report | Daemon | No | `test_cycle_history.py` |
| `diagnose_woodcut_bank_cycle.py` | Full woodcut bank cycle diagnostic. | Diagnostic canonical cycle view | Daemon status | Cycle report | Daemon | No | `test_woodcut_bank_cycle_diagnostic.py` |
| `diagnose_woodcut_bank_scenarios.py` | Synthetic scenario suite. | Diagnostic/test helper canonical | In-memory scenarios | Scenario report | Synthetic/core | No | `test_woodcut_bank_scenarios.py` |
| `diagnose_overlay_state.py` | Overlay/live candidate consistency diagnostic. | Diagnostic useful but not canonical readiness | Latest/session live files | Overlay report | Live files | No | `test_diagnose_overlay_state.py` |
| `diagnose_overlay_geometry.py` | Overlay geometry/hull diagnostic. | Diagnostic useful but overlaps visual inspector | Session live files | Geometry report | Live files | No | `test_diagnose_overlay_geometry.py` |
| `diagnose_target_coverage.py` | Deep raw/world/candidate coverage audit. | Diagnostic audit; not daily canonical | Raw ticks, derived geometry/candidates | Coverage report | Live/files/raw ticks | No | `test_diagnose_target_coverage.py` |
| `diagnose_plugin_snapshot.py` | Plugin snapshot endpoint diagnostic. | Diagnostic helper | Plugin endpoint and bounded snapshot payloads | Snapshot report | Plugin endpoint | No | Used by live target processor tests |
| `diagnose_brain_progress.py` | Brain/progress diagnostic. | Diagnostic helper | Session/daemon status | Progress report | Both | No | `test_diagnose_brain_progress.py` |
| `diagnose_inventory_slots.py` | Inventory slot/resource diagnostic. | Diagnostic helper | Session/live inventory | Inventory report | Live files/session | No | `test_diagnose_inventory_slots.py` |
| `diagnose_navigation_intent.py` | Navigation intent diagnostic. | Diagnostic helper | Daemon status | Navigation intent report | Daemon | No | Navigation tests |
| `diagnose_pathing_matrix.py` | Synthetic pathing matrix. | Diagnostic/test helper | Synthetic scenarios | Matrix report | Synthetic/core | No | `test_pathing_matrix.py` |
| `check_live_setup.py` | Session/plugin/process setup doctor. | Diagnostic helper | Sessions/plugin endpoint/processes | Setup report | Both | No | `test_check_live_setup.py` |
| `live_config_doctor.py` | Live output/config health doctor. | Diagnostic helper | Session/live files | Config report | Live files | No | `test_live_config_doctor.py` |
| `inspect_live_packets.py` | Retired live packet inspector shim. | Legacy cleanup pointer | None | Retirement message and replacement commands | No runtime files | No | `test_inspect_live_packets.py` |
| `inspect_target_geometry.py` | Static derived target geometry CLI inspector. | Deprecated candidate for live visual work; keep for static JSONL review | Derived world/UI geometry files | Compact rows/JSON | Files only | No | No direct test |
| `inspect_perception.py` | Perception bundle inspector. | Diagnostic helper | Perception outputs | Perception report | Files | No | No direct test |
| `inspect_tab_detection.py` | Tab detection inspector. | Diagnostic helper | Perception/tab outputs | Tab report | Files | No | No direct test |
| `training_dataset_inspector.py` | Training dataset inspector. | Diagnostic helper | Training dataset files | Dataset report | Files | No | No direct test |
| `dataset_status.py` | Dataset/session status summary. | Diagnostic helper | Session/dataset files | Status report | Files | No | No direct test |
| `validate_session.py` | Session validation. | Diagnostic helper | Session files | Validation report | Files | No | No direct test |
| `viewer.py` / `replay_viewer.py` | Local session/replay viewers. | Diagnostic/viewer | Session files | Viewer output/UI | Files | No | No direct test |
| `latest_state.py` | Latest state snapshot utility. | Diagnostic/export helper | Session files | Latest summary JSON | Files | No | No direct test |
| `label_ranges.py` | Label range helper. | Dataset helper | Labels/session ticks | Label report | Files | No | No direct test |
| `summarize_candidate_quality.py` | Candidate quality summary. | Diagnostic helper | Candidate files | Summary report | Files | No | No direct test |
| `suggest_target_overrides.py` | Suggests target library overrides. | Diagnostic helper | Derived target files | Suggestions | Files | No | No direct test |

## Build/Export/Batch Scripts

| Entrypoint | Purpose | Role/status | Main inputs | Main outputs | Source | Can execute input/clicks | Related tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `build_world_target_geometry.py` | Builds world target geometry outputs. | Batch canonical | Session raw ticks/perception | `interaction_geometry/world_*` | Files | No | Indirect target coverage tests |
| `build_ui_target_geometry.py` | Builds UI target geometry outputs. | Batch canonical | Session/perception/UI rules | `interaction_geometry/ui_*` | Files | No | Indirect tests |
| `select_target_candidates.py` | Builds candidate files from derived target geometry. | Batch canonical | Target geometry, library, profiles | `target_candidates*.json*` | Files | No | Candidate/live target tests |
| `build_perception_dataset.py` | Builds perception datasets. | Batch helper | Session ticks/frames | Perception dataset | Files | No | No direct test |
| `prepare_visual_perception.py` | Prepares visual perception crops/bundles. | Batch helper | Session/perception config | Crop outputs | Files | No | No direct test |
| `build_training_dataset.py` | Builds training dataset. | Batch helper | Session/perception/labels | Training dataset | Files | No | No direct test |
| `export_curated_training_dataset.py` | Exports curated training dataset. | Batch helper | Training dataset | Curated export | Files | No | No direct test |
| `export_session.py` | Exports session summary/data. | Batch helper | Session files | Export report/files | Files | No | No direct test |
| `export_target_handoff.py` | Exports target handoff bundle. | Batch helper | Target geometry/candidates | Handoff output | Files | No | No direct test |
| `build_scenario_dataset.py` | Builds scenario datasets. | Batch helper | Session/templates | Scenario dataset | Files | No | No direct test |
| `run_target_geometry_pipeline.py` | Runs target geometry build steps. | Batch orchestration | Session and build args | Derived geometry/candidates | Files | Launches subprocesses, no gameplay input | No direct test |
| `calibrate_screen_regions.py` | Screen-region calibration preview/tool. | Batch/visual tool | Session frames/perception | Calibration preview/server | Files | UI tool only | No direct test |
| `capture_bootstrap_template.py` | Captures bootstrap image templates. | Setup helper | Screenshot/window region | Template image | Desktop screenshot | Can focus/capture, not gameplay action | `test_capture_bootstrap_template.py` |
| `scenario_inspector.py` | Scenario/override inspector. | Batch helper | Scenario/target files | Report/optional override write | Files | No | No direct test |

## Helper/Core Modules

| Module | Purpose | Notes/tests |
| --- | --- | --- |
| `telemetry_paths.py` | Session discovery, file iteration, frame state, newest live session helpers. | `test_telemetry_paths.py` |
| `live_session_core.py` | Shared daemon/latest/live/highlighter session rules. | `test_live_core_contracts.py` |
| `live_file_core.py` | Shared latest-state/debug file paths and safe bounded JSON/JSONL loading for explicit diagnostics. | `test_live_core_contracts.py` |
| `candidate_core.py` | Shared candidate identity, freshness, matching, woodcutting summary, explanation. | Candidate/readiness/action tests |
| `live_readiness_core.py` | Shared readiness result and pre-action gate contract. | `test_live_readiness.py`, `test_live_core_contracts.py` |
| `action_proposal_core.py` | Stable import surface for action proposal/explanation. | `test_live_core_contracts.py` |
| `client_tick_core.py` | Shared `client_tick_hot.v1` parser, generic action-intent hover matching, and clicked-menu classifier. | `test_client_tick_core.py`, `test_input_control_executor.py` |
| `safe_aimpoint_core.py` | Shared safe visible/interactable aimpoint contract for clipping partial candidate geometry and rejecting off-viewport action points. | `test_safe_aimpoint_core.py`, `test_action_proposal.py` |
| `woodcutting_candidate_diagnostics.py` | Backward-compatible wrapper to `candidate_core.py`. | `test_woodcutting_candidate_diagnostic.py` |
| `live_readiness.py` | Backward-compatible wrapper to `live_readiness_core.py`. | `test_live_readiness.py` |
| `brain_core.py` | Main task/context decision core. | `test_brain_core.py`, rehearsal/tests |
| `task_policy.py`, `task_state.py`, `resource_progress.py`, `runtime_control.py`, `mission_presets.py`, `mission_snapshot.py` | Core task/runtime support. | Stabilization suite |
| `live_packet_reader.py` | Removed with the live packet archive. | Retired; maintenance report/prune is the only legacy packet-file path. |
| `navigation_reachability.py`, `intent_stabilizer.py`, `cycle_history.py` | Navigation/intent/history support. | Stabilization suite |
| `capabilities.py`, `tab_detection.py`, `tab_profile_names.py`, `bootstrap_window.py`, `bootstrap_vision.py` | Supporting utilities. | Stabilization suite where listed |
| `analyzers/*.py` | Domain analyzers for inventory, target, navigation, pathing, activity, overlay intent, service, bank UI/operation, return/reacquisition/close-bank/process inventory. | Analyzer tests |
| `input_control/*.py` | Proposal, lifecycle, geometry, executor, backends, movement diagnostics. | Input/action tests |

## Test Scripts

Tests are now grouped in `run_stabilization_suite.py` by behavior:

- Path/session resolution: `test_telemetry_paths.py`
- Live file loading/cache: current file helpers and `test_live_target_processor.py`
- Candidate classification/scoring: `test_target_analyzer.py`, `test_target_candidate_dedupe.py`, `test_woodcutting_candidate_diagnostic.py`, `test_live_core_contracts.py`
- Woodcutting profile/task state: `test_task_policy.py`, `test_task_state.py`, `test_task_transitions.py`, `test_resource_progress.py`
- Readiness gate: `test_live_readiness.py`
- Action proposal: `test_action_proposal.py`, `test_diagnose_action_proposal.py`
- Executor/action lifecycle: `test_action_lifecycle.py`, `test_input_control_executor.py`, `test_input_geometry.py`, `test_mouse_movement.py`
- Context service/query: `test_context_service.py`, `test_live_context_query.py`, `test_live_control_panel.py`, `test_mission_snapshot.py`
- Diagnostics smoke: `test_diagnose_*.py`, `*_diagnostic.py`, live QA/gauntlet/packet setup tests
- Cycle analyzers: service/pathing/navigation/bank/return analyzer tests
- Bootstrap/input startup: bootstrap template/window/vision tests
- Core/analyzer behavior: remaining analyzer/core behavior tests

## Generated/Live Output Files

See `live_outputs.md` for the contract for:

- `live_baseline_state.json`
- `live_context_index.json`
- `live_candidates.jsonl`
- `live_status.json`
- `live_activity_state.json`
- `live_navigation_summary.json`
- `live_event_timeline.jsonl`
- `overlay_debug_state.json`
- `last_action_trace.json`
- retired packet archive index/segments under `live_packets/` are legacy cleanup only

## Duplicate Or Overlapping Responsibilities

| Area | Overlap | Cleanup decision |
| --- | --- | --- |
| Latest-session and live-session choice | `telemetry_paths.py`, candidate diagnostic, readiness diagnostic, inspector, live processor | Shared through `live_session_core.py`; keep `telemetry_paths.py` as low-level discovery. Active live tools should use daemon session binding instead of blind newest-session selection. |
| Explicit debug/latest-state JSON/JSONL loading | Candidate diagnostic, readiness diagnostic, context query, overlay diagnostics, inspector | Shared new helpers in `live_file_core.py`; do not force every older audit script through it unless touched. This does not revive the retired `live_packets` archive. |
| Candidate identity/highlighter matching | Candidate diagnostic, readiness diagnostic, action explanation | Shared through `candidate_core.py`. |
| Candidate freshness | Action proposal and readiness/candidate diagnostics | Shared through `candidate_core.target_freshness_issue` and readiness contract. |
| Candidate explanation | Action proposal diagnostic and woodcutting candidate diagnostic | Shared through `candidate_core.explain_candidate`. |
| Visual/highlighter inspection | `target_geometry_inspector.py`, `inspect_target_geometry.py`, overlay diagnostics | Keep `target_geometry_inspector.py` canonical for live visual/highlighter; keep static/audit scripts as non-canonical helpers. |
| Action proposal vs action dry-run | `diagnose_action_proposal.py`, `execute_next_action.py --dry-run` | Keep diagnostic for proposal JSON; use `execute_next_action.py --dry-run --explain-target --verify-coordinates` as canonical action dry-run. |
| Readiness-related diagnostics | `diagnose_live_readiness.py`, candidate diagnostic, action executor gate | Keep one readiness contract in `live_readiness_core.py`; other scripts consume it or report compatible fields. |

## Deprecated Or Wrapper Candidates

| Script | Status | Recommendation |
| --- | --- | --- |
| `live_readiness.py` | Wrapper | Backward-compatible import only; prefer `live_readiness_core.py` in new code. |
| `woodcutting_candidate_diagnostics.py` | Wrapper | Backward-compatible import only; prefer `candidate_core.py` in new code. |
| `inspect_target_geometry.py` | Deprecated for live visual/highlighter inspection | Prefer `target_geometry_inspector.py --from-daemon --live`; keep this for static derived world/UI JSONL review. |
| `diagnose_overlay_state.py` | Non-canonical but useful | Keep as focused overlay/live-file audit; do not use as action readiness source. |
| `diagnose_overlay_geometry.py` | Non-canonical but useful | Keep as overlay geometry audit; visual inspection should start with `target_geometry_inspector.py`. |
| `diagnose_target_coverage.py` | Heavy audit | Keep for raw/world/candidate coverage investigations; not part of daily readiness. |
| `diagnose_action_proposal.py` | Useful diagnostic | Keep for proposal JSON smoke; canonical dry-run is `execute_next_action.py --dry-run --explain-target --verify-coordinates`. |

## Docs

| File | Purpose |
| --- | --- |
| `telemetry-viewer/README.md` | Existing viewer overview. |
| `telemetry-viewer/docs/live_stack_architecture.md` | Intended live stack and dependency rules. |
| `telemetry-viewer/docs/live_outputs.md` | Generated/live output file contract. |
| `telemetry-viewer/docs/cleanup_inventory.md` | This inventory and consolidation map. |
| `AGENTS.md` | Agent guardrails and current repo conventions. |
| `docs/codex_handoff_current.md` | Current project handoff/runbook outside `telemetry-viewer`. |

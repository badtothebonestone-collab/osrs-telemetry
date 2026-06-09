# Project Bootstrap Sweep

Date: 2026-06-09
Repo root: `C:\Users\badto\osrs-telemetry`
Final verdict: WARN

The bootstrap, packaging, wiring, and guardrail sweep completed without finding a missing mandatory core path. The verdict is WARN because the worktree was already broad and dirty before the sweep, the bot preflight correctly warns that the current Start Game command resolves to a dev Gradle launch, and the optional stabilization suite timed out after the focused checks had passed.

## Git Status Summary

Before:
- `git status --short` was run before edits.
- The tree was already dirty with tracked modifications in `AGENTS.md`, Java plugin bridge files, core telemetry modules, input/executor modules, readiness/recovery modules, and tests.
- Many repo-owned/generated paths were untracked, including `docs\knowledge`, `telemetry-viewer\knowledge_base`, route templates/guides, recordings, bot runs, lifecycle analyzers, UI/recorder/analyzer modules, and tests.

After:
- The tree remains dirty by design; no unrelated pre-existing changes were reverted.
- This pass intentionally added or updated bootstrap/guardrail files, command inventory, knowledge integrity checks, UI diagnostics wiring, bot preflight wiring, tests, and this report.

Files intentionally touched by this pass:
- `AGENTS.md`
- `docs\knowledge\ENTRYPOINTS.md`
- `docs\project_bootstrap_sweep.md`
- `telemetry-viewer\update_project_knowledge.py`
- `telemetry-viewer\command_registry.py`
- `telemetry-viewer\bot_eval_runner.py`
- `telemetry-viewer\telemetry_ui.py`
- `telemetry-viewer\tests\test_project_bootstrap.py`
- `telemetry-viewer\tests\test_project_knowledge.py`
- `telemetry-viewer\tests\test_bot_eval_runner.py`
- `telemetry-viewer\tests\test_liveness_recovery_core.py`
- `telemetry-viewer\tests\test_telemetry_ui.py`
- `telemetry-viewer\live_control_panel.py`
- `telemetry-viewer\telemetry_launcher.py`
- `telemetry-viewer\tools\run_traced_dev_cycle.py`
- `telemetry-viewer\knowledge_base\*.json`
- Generated knowledge docs under `docs\knowledge`

## Inventory Result

Required core modules:
- FOUND: `manual_recorder.py`, `analyze_manual_recording.py`, `telemetry_ui.py`, `context_service.py`, `mcp_server.py`, `task_script_api.py`, `knowledge_fabric.py`, `bot_eval_runner.py`, `execute_next_action.py`, `liveness_recovery_core.py`, `live_readiness_core.py`, `start_game_command.py`
- FOUND: `input_control\executor.py`, `input_control\action_proposal.py`, `input_control\click_planner.py`, `candidate_core.py`
- FOUND: `route_demonstration.py`, `route_monitor.py`, `route_template.py`, `woodcutting_lifecycle.py`, `woodcutting_loop_lifecycle.py`, `banking_lifecycle.py`, `interruption_lifecycle.py`, `combat_damage_summary.py`, `human_click_profile.py`, `update_project_knowledge.py`
- FOUND: `src\main\java\com\osrstelemetry\TelemetryPlugin.java`
- MISSING: none in the required module list.

User-facing commands:
- FOUND: Start UI simple mode, reset UI config, telemetry UI check, manual recorder basic command, analyzer broad command, update knowledge, validate template, route monitor live/follow, bot eval replay, bot eval live action, loaded-scene recovery, MCP list tools, bot eval preflight.
- WARN: command registry intentionally reports placeholders for commands that require a recording, template, or label argument.

Knowledge docs:
- FOUND: `PROJECT_STATE.md`, `ENTRYPOINTS.md`, `CAPABILITY_REGISTRY.md`, `API_DATA_PATHS.md`, `SCRIPT_API_MAP.md`, `OPEN_GAPS.md`, `DECISIONS.md`, `NEXT_TASKS.md`.
- MISSING: none.

Machine-readable indexes:
- FOUND: `telemetry-viewer\knowledge_base\recording_index.json`
- FOUND: `telemetry-viewer\knowledge_base\capability_registry.json`
- FOUND: `telemetry-viewer\knowledge_base\gap_index.json`
- FOUND: `telemetry-viewer\knowledge_base\script_api_map.json`
- MISSING: none.

Route assets:
- FOUND: `route_templates`
- FOUND: `route_guides`
- MISSING: none.

Lifecycle analyzers:
- FOUND: traversal, route, banking, woodcutting, woodcutting loop, interruption, combat damage, human click profile.
- MISSING: none.

Context/MCP/task API:
- FOUND: `context_service.py`, `mcp_server.py`, `task_script_api.py`, `knowledge_fabric.py`.
- MISSING: none.

Java plugin bridge:
- FOUND: `TelemetryPlugin.java`
- FOUND: bank UI / bank container delta surfaces in the Java bridge.
- MISSING: none found during this sweep.

Recovery/start-game functions:
- FOUND: `start_game_command.resolve_start_game_command`
- FOUND: `start_game_command.launch_start_game`
- FOUND: `start_game_command.classify_launch_mode`
- FOUND: `liveness_recovery_core` recovery orchestration
- FOUND: `context_service.py --ensure-loaded-scene`
- FOUND: `execute_next_action.py --auto-recover-loaded-scene`
- FOUND: `bot_eval_runner.py --auto-recover-loaded-scene`
- MISSING: none.

## Entrypoints Documented

Created `docs\knowledge\ENTRYPOINTS.md` with the required ownership table:

`Responsibility | Canonical module/function/command | Do not duplicate in | Notes`

It covers Start Game, loaded-scene recovery, live readiness, Record Everything, knowledge base ownership, bot eval, script-facing API, context API, click/action planning, routes, banking, woodcutting, combat/interruption, and human click profile ownership.

## AGENTS.md Hardening

Updated `AGENTS.md` with the mandatory startup sequence for broad telemetry/API/bot tasks:

1. Read `docs\knowledge\PROJECT_STATE.md`.
2. Read `docs\knowledge\ENTRYPOINTS.md`.
3. Read `docs\knowledge\CAPABILITY_REGISTRY.md`.
4. Read `docs\knowledge\API_DATA_PATHS.md`.
5. Read `docs\knowledge\SCRIPT_API_MAP.md`.
6. Read `docs\knowledge\OPEN_GAPS.md`.
7. Reuse canonical modules instead of creating alternate recovery/start-game/recorder/analyzer paths.
8. After changes, update knowledge docs/indexes.
9. Run focused tests/checks.

Also added explicit rules:
- Do not replace live bot action tasks with dry-run unless explicitly requested or a true external blocker prevents live execution.
- Use `liveness_recovery_core.py` / `context_service.py --ensure-loaded-scene` for loaded-scene recovery.
- Use `start_game_command.py` for Start Game.
- Classify missing capability layers as plugin, recorder, analyzer, context, MCP, task_script_api, executor, tests, or docs.

## Knowledge Integrity Result

Command:

`python telemetry-viewer\update_project_knowledge.py --check`

Result: PASS

Observed summary:
- Recordings indexed: 80
- Capabilities indexed: 25
- Open gaps indexed: 11
- Required docs present.
- Required machine-readable indexes present.
- Required capabilities indexed, including Record Everything, banking lifecycle, bank UI, bank container delta, woodcutting lifecycle, woodcutting loop lifecycle, traversal lifecycle, route template, route monitor, route demonstration, interruption lifecycle, combat damage summary, human click profile, human click planning, bot eval runner, and loaded-scene recovery.
- Open gaps and script API map are indexed.

Knowledge docs and indexes were regenerated with:

`python telemetry-viewer\update_project_knowledge.py --scan-recordings --write-docs --json`

Result: PASS

## Command Registry Result

Created `telemetry-viewer\command_registry.py`.

Supported commands:
- `python telemetry-viewer\command_registry.py --list`
- `python telemetry-viewer\command_registry.py --check`

Registry coverage:
- Start UI simple mode
- Reset UI config
- Start telemetry UI check
- Manual recorder basic command
- Analyzer broad command
- Update knowledge
- Validate template
- Route monitor live/follow
- Bot eval replay
- Bot eval live action
- Loaded-scene recovery
- MCP list tools
- Bot eval preflight

Command:

`python telemetry-viewer\command_registry.py --check --json`

Result: WARN

Reason:
- No command files are missing.
- The registry intentionally warns for placeholder commands that require caller-supplied values such as `<label>`, `<recording>`, and `<template>`.

## Duplicate/Stale Function Audit

Searched for duplicate/stale implementations of:
- Start Game command resolution
- Loaded-scene recovery
- Live readiness
- Route template resolution
- Record Everything profile
- Human click profile loading
- Banking deposit result parsing
- Route monitor status parsing

Results:
- Active Start Game path is canonicalized through `start_game_command.py`.
- `telemetry_ui.py` Start Game now uses `start_game_command.resolve_start_game_command` and `launch_start_game`.
- `bot_eval_runner.py` recovery uses `context_service.py --ensure-loaded-scene`.
- `liveness_recovery_core.py` uses the Start Game resolver for relaunch.
- `live_readiness.py` remains a compatibility wrapper to `live_readiness_core.py`.
- No risky deletions were made.

Deprecated/legacy comments added:
- `telemetry-viewer\live_control_panel.py`: marked as a legacy dev-panel launcher, not canonical Start Game.
- `telemetry-viewer\telemetry_launcher.py`: marked as a legacy dev launcher, not canonical Start Game.
- `telemetry-viewer\tools\run_traced_dev_cycle.py`: marked as a dev-cycle default only; canonical Start Game resolution lives in `start_game_command.py`.

Remaining intentional non-canonical helpers:
- `run_runelite_bootstrap.py --gradle-command` remains a bootstrap/dev helper used by recovery paths, not a replacement for canonical Start Game resolution.

## Recovery/Start-Game Wiring Result

Added and/or verified tests for:
- Bot eval recovery path uses `context_service.py --ensure-loaded-scene` when `--auto-recover-loaded-scene` is set.
- Recovery relaunch resolves Start Game through `start_game_command`.
- Telemetry UI Start Game resolves through `start_game_command`.
- Dev Gradle launches classify as `dev_gradle_run`.
- Live bot eval does not silently downgrade to dry-run.
- Missing loaded scene fails closed before gameplay actions.
- Dry-run flags are only used when explicitly requested.
- Manual loaded-scene wait is explicit when present.
- Recovery artifacts include launch mode, command source, and loaded-scene verification fields.

Result: PASS in focused tests.

## Bot Eval Preflight Result

Implemented:

`python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json`

Purpose:
- Validate wiring only.
- Does not run the bot.
- Does not send input.
- Does not replace real live execution.

Checks included:
- Knowledge base readable
- `task_script_api.py` readable/importable
- Route templates and guides present
- Start Game command classified
- Recovery path available
- Readiness path available
- Executor available
- Arduino optional/available status
- Human click profile available
- Output folder writable

Result: WARN

Reason:
- Mandatory wiring checks passed.
- Start Game resolved to launch mode `dev_gradle_run`, which may start RuneLite but does not prove an authenticated loaded game scene.
- `liveInputExecuted` was false.

Exact next live bot command once the client is loaded:

`python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json`

## UI Diagnostics

Updated `telemetry_ui.py` diagnostics commands without cluttering Simple Mode.

Diagnostics now exposes:
- Bootstrap Check
- Open Project Bootstrap Report
- Open Knowledge
- Run Command Registry Check
- Bot Eval Preflight command construction

Simple Mode remains the small Record Everything-oriented surface.

## Checks Run

Passed:
- `python -m py_compile telemetry-viewer\start_game_command.py`
- `python -m py_compile telemetry-viewer\liveness_recovery_core.py`
- `python -m py_compile telemetry-viewer\live_readiness_core.py`
- `python -m py_compile telemetry-viewer\bot_eval_runner.py`
- `python -m py_compile telemetry-viewer\manual_recorder.py`
- `python -m py_compile telemetry-viewer\analyze_manual_recording.py`
- `python -m py_compile telemetry-viewer\context_service.py`
- `python -m py_compile telemetry-viewer\mcp_server.py`
- `python -m py_compile telemetry-viewer\task_script_api.py`
- `python -m py_compile telemetry-viewer\knowledge_fabric.py`
- `python -m py_compile telemetry-viewer\update_project_knowledge.py`
- `python -m py_compile telemetry-viewer\command_registry.py`
- `python -m py_compile telemetry-viewer\live_control_panel.py`
- `python -m py_compile telemetry-viewer\telemetry_launcher.py`
- `python -m py_compile telemetry-viewer\tools\run_traced_dev_cycle.py`
- `python telemetry-viewer\tests\test_project_bootstrap.py` - 4 tests OK
- `python telemetry-viewer\tests\test_project_knowledge.py` - 7 tests OK
- `python telemetry-viewer\tests\test_bot_eval_runner.py` - 22 tests OK
- `python telemetry-viewer\tests\test_liveness_recovery_core.py` - 20 tests OK
- `python telemetry-viewer\tests\test_telemetry_ui.py` - 38 tests OK
- `python telemetry-viewer\telemetry_ui.py --check` - PASS
- `python telemetry-viewer\update_project_knowledge.py --check` - PASS
- `python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json` - WARN only, no mandatory wiring failures
- `python telemetry-viewer\command_registry.py --check --json` - WARN only, placeholder inventory warnings
- `python telemetry-viewer\update_project_knowledge.py --scan-recordings --write-docs --json` - PASS

Timed out:
- `python telemetry-viewer\run_stabilization_suite.py`

Exact reason:
- The stabilization suite exceeded the practical sweep timeout after about 184 seconds.
- A leftover Python process for that suite was stopped with process id 2900.
- No `run_stabilization_suite.py` Python process remained afterward.

## Current Known Blockers

- Live bot execution still requires a verified loaded scene before gameplay actions.
- Current Start Game resolution classifies as `dev_gradle_run`; this is acceptable for dev launch wiring but does not prove login or loaded-scene readiness.
- Optional full stabilization did not complete during this pass.
- The worktree remains broad and dirty with many pre-existing generated/untracked files; commit/package decisions should be deliberate and explicit.

## Remaining Cleanup Risks

- Some legacy dev launch helpers still exist by necessity. They are now marked as non-canonical, but future work should avoid promoting them into broad bot/recovery flows.
- Knowledge docs are generated from current recordings and indexes; after any capability or path change, rerun `update_project_knowledge.py --scan-recordings --write-docs --json` and `--check`.
- Command registry placeholder warnings are expected, but should not be ignored if a future command has a concrete path and still warns.

## Next Session Instructions

For the next Codex session, before broad telemetry/API/bot work:

1. Read `AGENTS.md`.
2. Read `docs\knowledge\PROJECT_STATE.md`.
3. Read `docs\knowledge\ENTRYPOINTS.md`.
4. Read `docs\knowledge\CAPABILITY_REGISTRY.md`.
5. Read `docs\knowledge\API_DATA_PATHS.md`.
6. Read `docs\knowledge\SCRIPT_API_MAP.md`.
7. Read `docs\knowledge\OPEN_GAPS.md`.
8. Run `python telemetry-viewer\update_project_knowledge.py --check`.
9. Run `python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json`.
10. Reuse canonical modules. Do not create alternate Start Game, loaded-scene recovery, recorder, analyzer, route/template, or dry-run live-action paths.

Once the client is authenticated and loaded-scene readiness is verified, the next live command is:

`python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json`

## 2026-06-09 Stabilization Checkpoint Addendum

Live bot iteration is paused. The project now has a dedicated checkpoint branch
for source-control stabilization and live-loop recovery planning:

`stabilization/live-loop-recovery-20260609`

Current stabilization docs:

- `docs\git_cleanup_report.md`
- `docs\local_artifact_inventory.md`
- `docs\live_bot_regression_audit.md`
- `docs\next_live_loop_recovery_plan.md`
- `docs\stabilization_checkpoint_report.md`

Canonical ownership remains:

- Start Game: `telemetry-viewer\start_game_command.py`
- Loaded-scene recovery: `telemetry-viewer\liveness_recovery_core.py` and
  `telemetry-viewer\context_service.py --ensure-loaded-scene`
- Live readiness: `telemetry-viewer\live_readiness_core.py`
- Input geometry: `telemetry-viewer\input_control\input_geometry.py`
- Bot orchestration: `telemetry-viewer\bot_eval_runner.py`
- Candidate/action proposal: `telemetry-viewer\candidate_core.py` and
  `telemetry-viewer\input_control\action_proposal.py`
- Execution safety: `telemetry-viewer\input_control\executor.py`
- Route guides/templates/monitoring: `route_demonstration.py`,
  `route_template.py`, and `route_monitor.py`
- Knowledge refresh: `telemetry-viewer\update_project_knowledge.py`

Do not resume live runs until `docs\next_live_loop_recovery_plan.md` gates pass.

# Stabilization Checkpoint Report

Date: 2026-06-09

## 1. Git Status Before / After

Before the checkpoint, the repo was on `master` with a broad dirty tree:
modified source/tests/docs plus many untracked docs, Python modules, route
assets, tests, and knowledge indexes. Raw local runtime artifacts under
`bot_runs/`, `recordings/`, and live logs were present locally but were not
staged.

After the checkpoint commit and push:

```text
git status --short --untracked-files=all
<clean>
```

## 2. Branch Created

```text
stabilization/live-loop-recovery-20260609
```

The branch tracks:

```text
origin/stabilization/live-loop-recovery-20260609
```

## 3. Commit Hash

Checkpoint commit:

```text
619e2b8179be511bfe2c7dc6bf0d89324345f787
```

Commit message:

```text
stabilize telemetry bot stack before live loop recovery
```

## 4. Push Result

Push succeeded.

Remote:

```text
https://github.com/badtothebonestone-collab/osrs-telemetry.git
```

Pull request URL offered by GitHub:

```text
https://github.com/badtothebonestone-collab/osrs-telemetry/pull/new/stabilization/live-loop-recovery-20260609
```

## 5. Files Committed

The checkpoint committed 199 files.

Included categories:

- Source code under `telemetry-viewer\*.py` and
  `telemetry-viewer\input_control\*.py`.
- Java bridge changes under `src\main\java\com\osrstelemetry`.
- Tests under `telemetry-viewer\tests`.
- Docs under `docs`.
- Knowledge docs under `docs\knowledge`.
- Knowledge JSON indexes under `telemetry-viewer\knowledge_base`.
- Stable route assets under `route_templates` and `route_guides`.
- UI launcher helper `run_telemetry_ui.bat`.

Key stabilization deliverables committed:

- `docs\git_cleanup_report.md`
- `docs\local_artifact_inventory.md`
- `docs\live_bot_regression_audit.md`
- `docs\next_live_loop_recovery_plan.md`
- `docs\project_bootstrap_sweep.md`
- `docs\knowledge\ENTRYPOINTS.md`
- `docs\bot_eval_live_input_geometry_readiness.md`

## 6. Files Intentionally Ignored

`.gitignore` now keeps generated/local runtime artifacts out of normal status
noise:

- `bot_runs/`
- `recordings/`
- `**/__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `*.tmp`
- `*.temp`
- `*.bak`

Existing runtime/log ignores still cover local `.osrs-telemetry` state and
`telemetry-viewer/logs`.

## 7. Local Artifacts Inventory

Inventory path:

```text
docs\local_artifact_inventory.md
```

Local-only artifact summary:

- `bot_runs/`: 127 folders, 3745 files, 546,677,503 bytes.
- `recordings/`: 84 folders, 2210 files, 472,242,941 bytes.
- `telemetry-viewer/logs/live_core_daemon_8890.out.log`: about 432 MB.

These were not committed.

## 8. Last Known Working Baseline Summary

Detailed audit:

```text
docs\live_bot_regression_audit.md
```

Baseline sequence:

1. Record Everything Simple recording PASS:
   `recordings\20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory`
   and
   `recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor`.
2. Route template PASS:
   `recordings\20260606_121630_bank_to_WC` and
   `route_templates\Bank_to_Woodcutting_area.route_template.json`.
3. Full woodcutting loop analyzer PASS:
   `recordings\20260607_171427_Wood_cutting_attacked`.
4. Bot replay eval PASS:
   `bot_runs\20260607_204642_woodcutting_loop_eval`.
5. First useful live Arduino-backed progress:
   `bot_runs\20260608_160916_live_woodcutting_loop`, with one attempted
   action, one actual click, one successful action, and safe stop on
   `route_waypoint_arrived_but_route_state_stale`.

The last known working baseline is therefore a replay/analyzer PASS plus a
bounded live action proof, not a completed live woodcutting loop.

## 9. Current Live Blocker Summary

No live bot loop was run during this stabilization task.

Current latest blocker from the existing reports is:

```text
input_geometry_stale
```

The latest non-live UI status check also showed stale file-session live sources
and `context_service_running=false`. The bot preflight wiring check itself
returned `WARN`, not `FAIL`, because Start Game is still classified as
`dev_gradle_run`, which may launch RuneLite but is not authenticated loaded
scene proof.

## 10. Module Ownership Audit Summary

Canonical owners recorded in `docs\knowledge\ENTRYPOINTS.md`:

- Start Game: `telemetry-viewer\start_game_command.py`.
- Loaded-scene recovery: `telemetry-viewer\liveness_recovery_core.py` and
  `context_service.py --ensure-loaded-scene`.
- Live readiness: `telemetry-viewer\live_readiness_core.py`.
- Input geometry: `telemetry-viewer\input_control\input_geometry.py`.
- Bot orchestration: `telemetry-viewer\bot_eval_runner.py`.
- Candidate/action planning: `telemetry-viewer\input_control\action_proposal.py`
  and `telemetry-viewer\candidate_core.py`.
- Route guide/template/monitoring:
  `route_demonstration.py`, `route_template.py`, and `route_monitor.py`.
- Script-facing state: `task_script_api.py` and `knowledge_fabric.py`.
- Context API: `context_service.py` and `mcp_server.py`.
- Record Everything/analyzer:
  `manual_recorder.py`, `analyze_manual_recording.py`, and
  `update_project_knowledge.py`.

## 11. Duplicate / Bypass Risks

No critical bypass was deleted in this checkpoint. Risks are documented instead:

- Legacy/dev launch helpers still exist in `live_control_panel.py`,
  `telemetry_launcher.py`, and `tools\run_traced_dev_cycle.py`. They should not
  replace `start_game_command.py`.
- `run_runelite_bootstrap.py` has bootstrap/recovery helper behavior, but
  loaded-scene recovery ownership remains `liveness_recovery_core.py`.
- Software input backends may exist for tests/debugging, but live gameplay input
  should remain guarded through `HumanInputController -> ArduinoHIDBackend`.
- Stale route/session traces should stay diagnostic-only unless current indexed
  evidence supports blocking.

## 12. Tests / Checks Run

Compile checks passed:

- `python -m py_compile telemetry-viewer\start_game_command.py`
- `python -m py_compile telemetry-viewer\liveness_recovery_core.py`
- `python -m py_compile telemetry-viewer\live_readiness_core.py`
- `python -m py_compile telemetry-viewer\input_control\input_geometry.py`
- `python -m py_compile telemetry-viewer\bot_eval_runner.py`
- `python -m py_compile telemetry-viewer\candidate_core.py`
- `python -m py_compile telemetry-viewer\input_control\action_proposal.py`
- `python -m py_compile telemetry-viewer\input_control\executor.py`
- `python -m py_compile telemetry-viewer\task_script_api.py`
- `python -m py_compile telemetry-viewer\knowledge_fabric.py`
- `python -m py_compile telemetry-viewer\context_service.py`

Focused tests passed:

- `python telemetry-viewer\tests\test_project_bootstrap.py` - 4 tests OK.
- `python telemetry-viewer\tests\test_project_knowledge.py` - 7 tests OK.
- `python telemetry-viewer\tests\test_bot_eval_runner.py` - 24 tests OK.
- `python telemetry-viewer\tests\test_liveness_recovery_core.py` - 20 tests OK.
- `python telemetry-viewer\tests\test_live_readiness.py` - 51 tests OK.
- `python telemetry-viewer\tests\test_action_proposal.py` - 86 tests OK.
- `python telemetry-viewer\tests\test_telemetry_ui.py` - 38 tests OK.

Other checks:

- `python telemetry-viewer\telemetry_ui.py --check` - PASS.
- `python telemetry-viewer\update_project_knowledge.py --check` - PASS:
  `recordings=80 capabilities=26 gaps=12`.
- `python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json`
  - WARN only, due to `dev_gradle_run` Start Game classification.

The full stabilization suite was not run. The focused checks passed quickly and
the task explicitly asked for source-control stabilization rather than broad
live/debug iteration.

## 13. Failures

No focused compile/test/check command failed.

Non-failing warning:

- Bot preflight warned that the configured Start Game command is
  `dev_gradle_run`, which is not authenticated loaded-scene proof.

Current live-loop blocker remains unresolved by this checkpoint:

- `input_geometry_stale` / stale live source context.

## 14. Next Live-Loop Recovery Plan

Plan path:

```text
docs\next_live_loop_recovery_plan.md
```

The plan says to proceed only in this order:

1. Confirm RuneLite/authenticated game state and telemetry endpoints.
2. Run preflight.
3. Run input geometry check.
4. Only after those pass, run the real live command once.
5. If action sends and fails, inspect candidate/action/postcondition trace
   instead of cycling runs.

## 15. Exact Next Command

Next non-live command for the next live-loop recovery session:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json
```

If preflight passes or only warns on known non-blocking launch classification,
the next gate is:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

The real live command is only allowed after those gates pass:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## 16. Safe To Continue Live-Loop Work?

Verdict: WARN.

The repo is now source-control safe to continue from: the branch is pushed, the
working tree is clean, generated artifacts are ignored, and the baseline audit
is written down.

It is not safe to resume live bot execution until:

- Telemetry endpoints are current and healthy.
- Loaded-scene proof is current.
- Input geometry returns PASS.

Once those gates pass, the first likely code area is route guide / action
proposal next-segment advancement around the earlier
`route_waypoint_arrived_but_route_state_stale` blocker.

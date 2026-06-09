# Live Loop Execution Fix Report

Date: 2026-06-09

## 1. Git Branch

```text
stabilization/live-loop-recovery-20260609
```

## 2. Git Status Before / After

Before work:

```text
git status --short
<clean>
```

After the fix commit and push, the only remaining change is this report
finalization update.

After edits and before the fix commit:

```text
M docs\codex_questions.md
M docs\knowledge\ENTRYPOINTS.md
M telemetry-viewer\bot_eval_runner.py
M telemetry-viewer\tests\test_bot_eval_runner.py
A docs\live_loop_execution_fix_report.md
```

## 3. Docs Read

- `AGENTS.md`
- `docs\knowledge\ENTRYPOINTS.md`
- `docs\knowledge\PROJECT_STATE.md`
- `docs\knowledge\CAPABILITY_REGISTRY.md`
- `docs\knowledge\API_DATA_PATHS.md`
- `docs\knowledge\SCRIPT_API_MAP.md`
- `docs\knowledge\OPEN_GAPS.md`
- `docs\knowledge\DECISIONS.md`
- `docs\next_live_loop_recovery_plan.md`
- `docs\stabilization_checkpoint_report.md`
- `docs\live_bot_regression_audit.md`
- `docs\project_bootstrap_sweep.md`

## 4. Preflight Result

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json
```

Result: `WARN`

Mandatory failures: none.

Reason for warning:

```text
Start Game is classified as dev_gradle_run.
```

This is the known warning that the Gradle/dev launch can start RuneLite but does
not prove authenticated loaded-scene state by itself.

## 5. Input Geometry Result

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Result: `FAIL`

Exact blockers:

- `loaded_scene_not_ready`
- `input_geometry_stale`
- `client_tick_hot_stale_age_ms_5284672`
- `context_health_unreachable`
- `context_status_unreachable`
- `snapshot_health_unreachable`

Important fields:

- `loadedSceneVerified=false`
- `gameState=LOGGED_IN`
- `latestTick=766`
- `worldModelObjectTotal=498`
- `inputGeometryPass=false`
- Geometry source: `file_session.baseline.inputGeometry`
- Geometry freshness: about `5285255 ms`
- Foreground window title: `Codex`
- RuneLite window matched: `false`
- Context endpoint `8890`: refused connection
- Snapshot endpoint `8893`: refused connection

Additional local checks:

- No listener was found on local ports `8890` or `8893`.
- No visible RuneLite/Java window title was found by the local process/window
  check.

Conclusion: this is an environment/current-source blocker. Route,
woodcutting, banking, and click-planning logic were not touched.

## 6. Live Command Run

The real live command was not run because input geometry failed:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

This follows the gate rule: no live bot actions when input geometry is stale or
telemetry endpoints are down.

## 7. Live Run Folder

None. The real live loop did not start.

## 8. Linked Recording Folder

None. Record Everything was not started for a live run because the input
geometry gate failed first.

## 9. Bot Actions Sent

```text
0
```

## 10. Live Input Executed

```text
no
```

## 11. Loop Completion

```text
no
```

The loop was not attempted because the geometry gate failed.

## 12. Exact Blocker If Failed

Current blocker:

```text
input_geometry_stale
```

Root cause from this run:

```text
Telemetry endpoints 8890 and 8893 are not running/refuse connection, the latest
live source files are stale, foreground is Codex, and no RuneLite window was
matched.
```

## 13. First Proven Blocker Patched

Patched blocker:

```text
--live without --execute-actions could silently enter live_dry_run behavior.
```

New behavior:

- `--live --execute-actions` is required for real live action.
- `--live` alone fails closed with:

```text
This is not real action execution. Use --live --execute-actions for real actions.
```

- `--live --execute-actions --dry-run-actions` fails closed as conflicting.
- Explicit no-input/smoke behavior remains available through clearly named
  dry-run/smoke flags.

Unpatched blocker:

```text
input_geometry_stale
```

Reason: the evidence points to an environment/current-source issue, not a code
bug in the geometry resolver.

## 14. Files Changed

- `telemetry-viewer\bot_eval_runner.py`
- `telemetry-viewer\tests\test_bot_eval_runner.py`
- `docs\knowledge\ENTRYPOINTS.md`
- `docs\codex_questions.md`
- `docs\live_loop_execution_fix_report.md`

## 15. Tests / Checks Run

Compile checks:

- `python -m py_compile telemetry-viewer\bot_eval_runner.py`
- `python -m py_compile telemetry-viewer\execute_next_action.py`
- `python -m py_compile telemetry-viewer\task_script_api.py`
- `python -m py_compile telemetry-viewer\knowledge_fabric.py`
- `python -m py_compile telemetry-viewer\context_service.py`
- `python -m py_compile telemetry-viewer\input_control\executor.py`
- `python -m py_compile telemetry-viewer\input_control\action_proposal.py`
- `python -m py_compile telemetry-viewer\candidate_core.py`
- `python -m py_compile telemetry-viewer\live_readiness_core.py`
- `python -m py_compile telemetry-viewer\input_control\input_geometry.py`

Focused tests/checks:

- `python telemetry-viewer\tests\test_bot_eval_runner.py` - 26 tests OK.
- `python telemetry-viewer\tests\test_task_script_api.py` - 31 tests OK.
- `python telemetry-viewer\tests\test_knowledge_fabric.py` - 44 tests OK.
- `python telemetry-viewer\tests\test_context_service.py` - 50 tests OK.
- `python telemetry-viewer\tests\test_action_proposal.py` - 86 tests OK.
- `python telemetry-viewer\tests\test_live_readiness.py` - 51 tests OK.
- `python telemetry-viewer\tests\test_project_knowledge.py` - 7 tests OK.
- `python telemetry-viewer\tests\test_telemetry_ui.py` - 38 tests OK.
- `python telemetry-viewer\telemetry_ui.py --check` - PASS.
- `python telemetry-viewer\update_project_knowledge.py --check` - PASS:
  `recordings=80 capabilities=26 gaps=12`.

Skipped:

- `run_stabilization_suite.py`

Reason: focused checks passed, the live path is blocked by an external
telemetry/geometry gate, and the previous stabilization checkpoint recorded a
timeout for the full suite.

## 16. Commit Hash

Fix commit:

```text
73b41ced1357b5c482d9c4209b7fee6b8d43b7c8
```

Commit message:

```text
fix live woodcutting loop blocker from stabilized branch
```

## 17. Push Result

Push succeeded to:

```text
origin/stabilization/live-loop-recovery-20260609
```

## 18. Exact Next Action

User/environment action:

1. Open or restore RuneLite.
2. Load into the game world.
3. Make RuneLite visible and focusable.
4. Start the telemetry stack so both endpoints respond:

```text
http://127.0.0.1:8890/health
http://127.0.0.1:8893/health
```

Then rerun:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Only after that passes, run the real live command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

# Live Loop Execution Fix Report

Date: 2026-06-09

## 1. Branch And Git Status

Branch:

```text
stabilization/live-loop-recovery-20260609
```

Status before the final commit pass:

```text
M telemetry-viewer/bot_eval_runner.py
M telemetry-viewer/input_control/action_lifecycle.py
M telemetry-viewer/input_control/action_proposal.py
M telemetry-viewer/input_control/executor.py
M telemetry-viewer/live_readiness_core.py
M telemetry-viewer/tests/test_action_lifecycle.py
M telemetry-viewer/tests/test_action_proposal.py
M telemetry-viewer/tests/test_bot_eval_runner.py
M telemetry-viewer/tests/test_input_control_executor.py
M telemetry-viewer/tests/test_live_readiness.py
M telemetry-viewer/knowledge_base/*.json
?? logs/live_core_daemon_restart_20260609_134221.err.txt
?? logs/live_core_daemon_restart_20260609_134221.out.txt
```

The generated daemon restart text logs were not committed. `.gitignore` now ignores `logs/*.txt`.

## 2. Docs Read

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
- `docs\project_bootstrap_sweep.md`

## 3. Telemetry Stack Repair

`Start-NormalLiveStack.ps1` was inspected, but its configured path points at stale local state:

```text
C:\Users\stone\osrs-telemetry\example-plugin
```

The live stack was repaired by restarting the canonical repo daemon directly:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

Endpoint result after repair:

```text
8890 /health: 200, status ok, latestTick 8284
8893 /health: 200 via readiness check
8893 /snapshot: 200 with POST, latestTick 8284
```

Current listening processes at the final check:

```text
8890: python PID 7080
8893: java/RuneLite PID 2508
```

## 4. RuneLite Window And Focus

RuneLite was found, attached, restored/focused, and verified as foreground:

```text
Process: java PID 2508
Window title: RuneLite - KCLBolus
HWND: 9307348
Window rect: x=137 y=30 width=1282 height=906
Foreground window: RuneLite - KCLBolus
Matched RuneLite window: true
Visible: true
Minimized: false
```

## 5. Loaded Scene Result

Loaded-scene recovery used the canonical context-service path:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
```

Result:

```text
status: recovered_loaded_scene
loadedSceneVerified: true
latestTick: 8284 at final geometry check
```

The recovery path handled disconnected/login prompts before the final live run.

## 6. Input Geometry Result

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Result:

```text
status: PASS
inputGeometryPass: true
source: daemon_status.inputGeometry
canvasRect: x=148 y=57 width=1229 height=868
clientRect: x=137 y=30 width=1282 height=906
dpiScale: 1.75 x 1.75
screenToClientAvailable: true
clientToScreenAvailable: true
foregroundWindowTitle: RuneLite - KCLBolus
blockerCode: input_geometry_pass
```

## 7. Real Live Command Result

Command run without dry-run or no-input flags:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Latest run:

```text
Run folder: C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop
Linked recording: C:\Users\badto\osrs-telemetry\recordings\20260609_135443_live_woodcutting_loop_20260609_135443
Status: FAIL
Executor reason: max_runtime_reached
Live input executed: yes
Dry run: false
Action results: 46
Actions executed: 1
Actual gameplay clicks: 1
Successful actions: 1
Deposit successes: 1
Service complete events: 1
Return routes started: 1
Return routes completed: 0
Route transition attempts: 1
Route transition first-try successes: 1
Final phase: return_to_resource
Final active intent: return_to_resource_area
Loop complete: no
```

The loop advanced past the previous environment blocker, verified geometry, sent Arduino-backed live input, completed deposit proof, and started the return route.

## 8. Fixes Made

Patched in the fix commit:

- `bot_eval_runner.py`: added bounded status fallback for loaded-scene/readiness when health or file-session data is stale, and made input geometry status use the longer diagnostic status path.
- `live_readiness_core.py`: allowed fresh live service-object and bank-UI action sources through known stale file-session/session-match false positives, including `bank_close_keyboard`.
- `input_control\executor.py`: added bounded waiting after a confirmed service-object click when bank UI proof may arrive late, and added context-wait reacquire handling when service route context exists.
- `input_control\action_proposal.py`: added deposit target fallback through bank inventory/resource widgets and classified keyboard bank close as a canonical bank UI action target.
- `input_control\action_lifecycle.py`: accepted fresh inventory resource progress and free-slot increase as deposit proof when bank-operation resource counts are stale.
- Tests were added or updated for the readiness fallback, geometry diagnostic timeout, executor wait/reacquire behavior, bank UI close readiness, deposit target fallback, and deposit verification.
- Knowledge JSON indexes were refreshed.
- `.gitignore` now excludes generated `logs/*.txt` files.

## 9. Remaining Blocker

The current blocker is no longer environment, input geometry, loaded scene, bank deposit, or live input.

Latest proven blocker:

```text
return_route_staircase_hover_menu
```

Evidence from:

```text
C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop\bot_action_trace.jsonl
C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop\bot_candidate_trace.jsonl
C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop\bot_postcondition_trace.jsonl
```

The first return-route Staircase transition succeeded and changed plane. The later plane-1 Staircase target repeatedly produced either:

```text
hover confirmation failed: hover_confirm_timeout; top menu=Walk here
right-click menu selection failed: menu did not open
```

The target involved `Staircase` object id `16672` at world tile `3204,3229,1`, expected `Climb-down`, with planned screen point near `535,347`.

## 10. Tests And Checks Run

Passed:

```powershell
python -m py_compile telemetry-viewer\bot_eval_runner.py telemetry-viewer\live_readiness_core.py telemetry-viewer\input_control\input_geometry.py telemetry-viewer\input_control\executor.py telemetry-viewer\context_service.py telemetry-viewer\start_game_command.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\action_lifecycle.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_live_readiness.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\tests\test_telemetry_ui.py
python telemetry-viewer\tests\test_action_lifecycle.py ActionLifecycleTest.test_expected_result_verified_for_deposit_when_resources_clear ActionLifecycleTest.test_deposit_verification_uses_fresh_inventory_progress_when_bank_operation_count_stale ActionLifecycleTest.test_deposit_verification_uses_free_slot_increase_when_resource_count_stale
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Known test limitation:

```text
python telemetry-viewer\tests\test_action_lifecycle.py
```

The full file timed out after 124 seconds. The focused deposit lifecycle tests covering the touched verifier paths passed.

## 11. Commit And Push

Fix commit:

```text
c45c89c fix live environment readiness and woodcutting loop execution
```

Push result:

```text
origin/stabilization/live-loop-recovery-20260609 updated successfully
```

## 12. Human Or Environment Questions

None. No manual RuneLite focus/login request is needed for the current state. The environment self-heal paths succeeded.

## 13. Exact Next Action

Patch the return-route Staircase hover/menu blocker from the latest live trace. Inspect first:

```text
telemetry-viewer\input_control\executor.py
telemetry-viewer\input_control\action_proposal.py
telemetry-viewer\candidate_core.py
telemetry-viewer\route_demonstration.py
telemetry-viewer\route_template.py
bot_runs\20260609_135357_live_woodcutting_loop\bot_action_trace.jsonl
```

After that focused patch, rerun:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## 14. Plane-1 Staircase Hover/Menu Targeting Diagnosis

Updated: 2026-06-09.

Source run:

```text
C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop
```

The expected return-route segment was the plane-1 Lumbridge castle Staircase:

```text
target: Staircase
object id: 16672
world: 3204,3229,1
expected action: Climb-down
planned screen point: about 535,347
candidate geometry source: canvasLocation / safeAimPoint sampled around canvas 241,168
```

The first return-route staircase transition succeeded. The later plane-1 target failed because hover/menu confirmation was too permissive and then too repetitive:

- A generic `Climb` hover at the target could expose `Climb-down` only as a lower menu row.
- If right-click menu open was not observed, the loop classified this as a generic right-click failure and kept retrying.
- In a follow-up run, the same stale plane-1 target was proposed after the player had already moved to plane 0; the visible object was a different Staircase (`56230`) with top action `Climb-up`, and the matcher accepted it because generic `Climb` matched directional `Climb-up` by substring and target text alone could satisfy a different object id.

Responsible gates/functions:

```text
client_tick_core._option_matches
client_tick_core._target_matches
input_control.executor._entry_matches_route_transition_dialogue_opener
input_control.executor._route_transition_plane_mismatch_issue
input_control.executor._record_target_hover_failure
```

Fix behavior:

- Generic `Climb` no longer confirms directional `Climb-up` or `Climb-down` by substring.
- If a proposal names an expected object id and the hover/menu sample also has an id, the id must match.
- Route transition dialogue opener matching treats generic `Climb` as exact, not as a substring of `Climb-up`.
- A route interaction target whose plane differs from the current player plane is blocked before hover/click as `route_transition_target_plane_mismatch`.
- Repeated route target menu-open failures now stop quickly as `repeated_route_target_hover_failure` and record attempted points plus observed menu rows.

## 15. Rerun Results After Staircase Guard Fix

Geometry check:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Result: PASS, `inputGeometryPass=true`, foreground RuneLite, canvas `148,57 1229x868`.

Real command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

First post-fix run:

```text
Run folder: C:\Users\badto\osrs-telemetry\bot_runs\20260609_143211_live_woodcutting_loop
Status: FAIL
Live input executed: yes
Actions executed: 2
Climb-down Staircase succeeded: yes
Wrong Climb-up regression observed before the final guard patch: yes
Final blocker: candidate_data_stale after stale return/resource context
```

Second post-fix run:

```text
Run folder: C:\Users\badto\osrs-telemetry\bot_runs\20260609_144521_live_woodcutting_loop
Linked recording: C:\Users\badto\osrs-telemetry\recordings\20260609_144618_live_woodcutting_loop_20260609_144618
Status: FAIL
Live input executed: no
Actions executed: 0
Final location: 3206,3229,1
Final phase: needs_more_context
Final blocker: no_executable_action
```

The second run failed closed before gameplay input because the previous bad run left the character on the wrong floor. The code now prevents accepting the wrong `Climb-up` object/action and prevents clicking a route target on the wrong plane. The remaining recovery task is to get the live state back to a valid route/resource context from `3206,3229,1`, then rerun the same real command.

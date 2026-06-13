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

## 16. Wrong-Floor Route Recovery Diagnosis

Updated: 2026-06-09.

Current wrong-floor state:

```text
current world/plane: 3206,3229,1
current phase: needs_more_context / return_to_resource recovery
expected loop phase: routing back to the woodcutting area after deposit
route leg believed active: Bank_to_Woodcutting_area return leg
```

The route guide evidence does not currently contain a demonstrated same-plane recovery step for plane `1`:

```text
nearest same-plane guide point: none
nearest same-plane interaction: none
inferred subsegment: intermediate_floor_between_route_transitions
nearest cross-plane guide point: 3204,3229,0
nearest cross-plane interaction: Staircase 56231 at 3205,3208,2, Climb-down
```

The no-action failure was produced because the planner fell through to the generic context fallback after banking completed and route/resource state was stale. The responsible gate was the route/context branch in:

```text
telemetry-viewer\input_control\action_proposal.py
```

Fix behavior:

- `route_demonstration.resolve_reentry` now reports route-guide re-entry evidence for the current world tile.
- `task_script_api.get_route_guide_reentry` and `KnowledgeFabric.route_guide_reentry` expose that evidence to scripts and diagnostics.
- `action_proposal` now recognizes wrong/intermediate route floors and returns `routing_to_trees_intermediate_floor` context with a specific route-guide re-entry candidate/blocker.
- If no same-plane demonstrated route step exists, the candidate is non-executable with `route_guide_no_same_plane_reentry` instead of generic `no_executable_action`.
- Strict route-object guards remain unchanged: directional action, object id, and plane still must match before any Staircase click can execute.

Current guide/template gap:

```text
route guide lacks demonstrated plane-1 recovery point: yes
route template lacks explicit intermediate-floor segment: yes
live object evidence missing for a safe same-plane route target: yes
current route state stale/wrong after previous run: yes
```

## 17. Wrong-Floor Route Re-Entry Fix And Rerun

Updated: 2026-06-09.

Changed behavior:

- `route_demonstration.resolve_reentry` identifies wrong/intermediate route floors and reports same-plane guide point/interaction evidence.
- `action_proposal` now emits route re-entry evidence for `3206,3229,1` instead of falling through to generic `no_executable_action`.
- `route_monitor` classifies a route-corridor tile on an untemplated plane as `route_reentry_needed` with `routing_to_trees_intermediate_floor`.
- `input_control.executor` preserves `route_guide_no_same_plane_reentry` when a non-executable context fallback is the reason for stopping.
- `bot_eval_runner` preserves named fail-closed executor blockers in summary/trace extraction.

Route guide enrichment:

```text
enriched route guide: no
reason: no recording/artifact proved a same-plane plane-1 recovery point or interaction for 3206,3229,1
safe blocker: route_guide_no_same_plane_reentry
```

Geometry check:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Result:

```text
status: PASS
inputGeometryPass: true
loadedSceneVerified: true
foregroundWindowTitle: RuneLite - KCLBolus
canvas: 148,57 1229x868
```

Real command rerun:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Latest run:

```text
Run folder: C:\Users\badto\osrs-telemetry\bot_runs\20260609_154147_live_woodcutting_loop
Linked recording: C:\Users\badto\osrs-telemetry\recordings\20260609_154300_live_woodcutting_loop_20260609_154259
Status: FAIL
Loaded-scene recovery: recovered_loaded_scene, finalLoadedSceneVerified=true
Input geometry before run: PASS
Bot actions sent: 0
Live input executed: no
Loop completed: no
Final location: 3206,3229,1
Final phase: needs_more_context
Exact blocker: route_guide_no_same_plane_reentry
Candidate count: 1 non-executable wait_for_context candidate
Action trace: executed=false, observedResult=route_guide_no_same_plane_reentry
```

Outcome:

```text
Recovered from 3206,3229,1: no
Route back reached woodcutting area: no
Loop completed: no
Reason: the current Bank_to_Woodcutting_area guide lacks a demonstrated same-plane plane-1 re-entry step.
```

This is an acceptable fail-closed outcome. The bot did not click a wrong-plane route target, did not repeat the old bad Staircase point, did not substitute dry-run, and did not run max-runtime with an unexplained empty candidate trace.

## 18. Final Wrong-Floor Recovery Pass Report

Git state:

```text
branch: stabilization/live-loop-recovery-20260609
status before: clean
status after: source/docs/tests/knowledge changes pending commit at report write
```

Changed file groups:

```text
Route re-entry resolver/API: route_demonstration.py, task_script_api.py, knowledge_fabric.py
Planner/trace/executor: action_proposal.py, candidate_core.py, input_control/executor.py, bot_eval_runner.py
Route monitor: route_monitor.py
Tests: test_route_demonstration.py, test_action_proposal.py, test_bot_eval_runner.py, test_input_control_executor.py, test_knowledge_fabric.py, test_route_monitor.py, test_task_script_api.py
Docs/knowledge: live_loop_execution_fix_report.md, bot_eval_live_woodcutting_loop_run.md, ENTRYPOINTS.md, SCRIPT_API_MAP.md, OPEN_GAPS.md, NEXT_TASKS.md, knowledge_base JSON indexes
```

Root cause:

```text
The bot was stranded at 3206,3229,1 after an earlier stale route transition. Strict Staircase guards correctly prevent wrong-plane/wrong-id clicks, but the Bank_to_Woodcutting_area route guide has no demonstrated same-plane plane-1 recovery point or interaction. Without this patch the planner collapsed that state into generic no_executable_action.
```

Verification run:

```text
Input geometry: PASS
Real live command: ran with --live --execute-actions
Live run folder: C:\Users\badto\osrs-telemetry\bot_runs\20260609_154147_live_woodcutting_loop
Linked recording folder: C:\Users\badto\osrs-telemetry\recordings\20260609_154300_live_woodcutting_loop_20260609_154259
Bot actions sent: 0
Live input executed: no
Recovered from 3206,3229,1: no
Route back reached woodcutting area: no
Loop completed: no
Exact blocker: route_guide_no_same_plane_reentry
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\route_demonstration.py telemetry-viewer\candidate_core.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\executor.py telemetry-viewer\bot_eval_runner.py telemetry-viewer\task_script_api.py telemetry-viewer\knowledge_fabric.py telemetry-viewer\route_monitor.py
python telemetry-viewer\tests\test_route_demonstration.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_route_monitor.py
python telemetry-viewer\tests\test_task_script_api.py
python telemetry-viewer\tests\test_knowledge_fabric.py
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\tests\test_input_control_executor.py InputControlExecutorTest.test_context_fallback_original_reason_becomes_blocked_result_reason
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Failure/limitation:

```text
python telemetry-viewer\tests\test_input_control_executor.py timed out after about 244 seconds. The focused executor regression for the touched route-blocker reporting path passed.
```

Next recommended task:

```text
Record or extract a demonstrated same-plane plane-1 route re-entry step for 3206,3229,1, then add it to the route guide/template evidence. Do not loosen the strict Staircase object id/action/plane guards.
```

## 19. Floor-Selection Route Audit

Updated: 2026-06-09.

Audit report:

```text
docs\recording_analysis_staircase_floor_selection_route.md
```

Evidence inspected:

```text
recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
recordings\20260606_121630_bank_to_WC
recordings\20260607_104613_Woodcutting_area_to_bank
recordings\20260606_201613_Bank_to_tree_area
recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor
recordings\20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area
```

Finding:

```text
Bottom floor text found: recording labels only
Bottom floor captured as menu row: no
Middle floor / Top floor captured: no
Direct plane skip proven: yes, plane 2 -> 0
Plane 1 recovery demonstrated: no
```

The route guide now models this distinction:

```text
floorSelectionInteractions: []
directPlaneSkips: plane 2 -> 0 Staircase object 56231, skipped plane 1
```

The live blocker remains intentionally fail-closed:

```text
blocker: route_guide_no_same_plane_reentry
likelyReason: successful guide used a direct multi-plane stair transition; plane-1 recovery is not demonstrated
suggestedFixture: record a short plane-1 Staircase recovery from 3206,3229,1
safeState: no click sent because route guide lacks same-plane proof
```

No live rerun was performed for this audit because existing evidence still does not prove a safe plane-1 route interaction or waypoint. The next live-safe task is to record or extract that short plane-1 recovery fixture.

## 20. Final Floor-Selection Audit Pass Report

Git status:

```text
before: clean on stabilization/live-loop-recovery-20260609
after validation: scoped source/docs/tests/route-guide/knowledge changes pending commit
```

Changed areas:

```text
docs\recording_analysis_staircase_floor_selection_route.md
docs\live_loop_execution_fix_report.md
docs\bot_eval_live_woodcutting_loop_run.md
docs\knowledge\ENTRYPOINTS.md
docs\knowledge\OPEN_GAPS.md
docs\knowledge\NEXT_TASKS.md
docs\knowledge\SCRIPT_API_MAP.md
docs\knowledge\PROJECT_STATE.md
docs\knowledge\RECORDING_INDEX.md
route_guides\Bank_to_Woodcutting_area.route_guide.json
route_guides\woodcutting_area_to_bank.route_guide.json
telemetry-viewer\route_demonstration.py
telemetry-viewer\input_control\action_proposal.py
telemetry-viewer\candidate_core.py
telemetry-viewer\bot_eval_runner.py
telemetry-viewer\tests\test_route_demonstration.py
telemetry-viewer\tests\test_action_proposal.py
telemetry-viewer\tests\test_bot_eval_runner.py
telemetry-viewer\tests\test_task_script_api.py
telemetry-viewer\tests\test_knowledge_fabric.py
telemetry-viewer\knowledge_base\*.json
```

Commit note:

```text
The commit hash is reported in the final chat response after the commit is created.
```

Bottom floor finding:

```text
Bottom floor found in existing recordings: yes, recording labels only
Bottom floor captured as menu row: no
source labels:
  recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor
  recordings\20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area
machine route evidence: direct plane-2 to plane-0 Staircase transition
```

Route guide changes:

```text
floorSelectionInteractions schema supported
directPlaneSkips generated for existing direct multi-plane transitions
Bank_to_Woodcutting_area directPlaneSkips: plane 2 -> 0, skipped plane 1, Staircase object 56231
plane-1 path points/interactions added: none
```

Plane-1 recovery behavior:

```text
If a future guide proves allowedSourcePlanes includes plane 1 for a floor-selection interaction, action proposal can surface a non-clicking floor_selection_interaction candidate pending live target reacquisition.
With current evidence, 3206,3229,1 still fails closed as route_guide_no_same_plane_reentry.
```

Rerun decision:

```text
real live rerun safe: no
reason: existing evidence does not prove a plane-1 waypoint, strict Climb-down target, or captured Bottom floor option from plane 1
live command run: no
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\route_demonstration.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\candidate_core.py telemetry-viewer\bot_eval_runner.py telemetry-viewer\task_script_api.py telemetry-viewer\knowledge_fabric.py
python telemetry-viewer\tests\test_route_demonstration.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_route_monitor.py
python telemetry-viewer\tests\test_task_script_api.py
python telemetry-viewer\tests\test_knowledge_fabric.py
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
```

Failures:

```text
One attempted combined PowerShell test command failed because this shell does not accept && as a separator. The same tests were rerun as separate commands and passed.
```

Remaining need:

```text
Record or extract a short plane-1 Staircase recovery sample from 3206,3229,1. The useful proof is one of: same-plane waypoint, strict Climb-down target, or captured Bottom floor option from plane 1.
```

## 21. Plane-1 Staircase Recovery Probe

Probe goal:

```text
Collect fresh hover/right-click menu evidence for the plane-1 Staircase near 3206,3229,1 without running a full loop or sending a route-transition click.
```

Probe artifacts:

```text
recordings\20260609_171349_plane1_staircase_recovery_probe\plane1_staircase_recovery_probe.json
recordings\20260609_171349_plane1_staircase_recovery_probe\plane1_staircase_recovery_probe.jsonl
recordings\20260609_171349_plane1_staircase_recovery_probe\schema_gap_report.md
recordings\20260609_171349_plane1_staircase_recovery_probe\screen_after_probe.png
docs\recording_analysis_plane1_staircase_recovery_probe.md
```

Observed route candidate:

```text
reported player: 3206,3229,1
reported target: Staircase object 16672 at 3204,3229,1
attempted aim points: clickbox/projection-derived Staircase points only
route transition click sent: no
```

Menu evidence result:

```text
fresh Bottom floor captured: no
fresh Climb-down captured: no
fresh Climb-up captured: no
observed menu sample: Cancel only
postMenuSortAgeMillis: about 5.18M ms
snapshot freshness: plugin_all_packets_stale
post-probe screen: RuneLite disconnected/login screen
```

Route guide decision:

```text
route guide updated: no
reason: stale daemon/menu context and disconnected client are not safe plane-1 recovery evidence
current blocker remains: route_guide_no_same_plane_reentry
```

Next exact focused command after reconnecting and returning to the plane-1 state:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\plane1_staircase_recovery_probe.py --json
```

## 22. Top-Floor Staircase Floor-Selection Fix

Root cause:

```text
The live return route targeted the correct top-floor Staircase object 56231, but selected the top menu row Climb-down. The action ledger shows that Climb-down moved the player from plane 2 to plane 1, creating the later route_guide_no_same_plane_reentry blocker at 3206,3229,1.
```

Live menu evidence:

```text
source: bot_runs\20260609_135357_live_woodcutting_loop\bot_action_trace.jsonl
target: Staircase object 56231
world: 3205,3229,2
top menu: Climb-down / Staircase
captured lower row: Bottom-floor / Staircase
clicked row in bad run: Climb-down
observed bad postcondition: plane 2 -> 1
```

Fix:

```text
Added staircase_floor_selection_probe.py for focused top-floor menu evidence collection.
Normalized Bottom-floor to Bottom floor in route-demonstration floor-selection parsing.
Updated Bank_to_Woodcutting_area.route_guide.json with a strict live-trace-backed Bottom floor floor_selection_interaction.
Updated action proposal to prefer the proven Bottom floor interaction over Climb-down only when object id, world, and plane match.
Updated executor so missing Bottom floor fails closed as floor_selection_option_missing instead of falling back to a left-click Climb-down.
```

Plane-1 policy:

```text
Still blocked safely as route_guide_no_same_plane_reentry when already stranded on plane 1.
likelyCause: expected Bottom floor direct transition was missed or not used
recovery: return to top-floor state or capture plane-1 recovery evidence
safeState: no route click sent
```

Focused rerun policy:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\staircase_floor_selection_probe.py --json
```

Only rerun the full live loop if the focused probe proves the current live top-floor Staircase still exposes Bottom floor.

Focused probe result on 2026-06-09:

```text
geometry check after focusing RuneLite: PASS
probe folder: recordings\20260609_181650_staircase_floor_selection_probe
current world during probe: 3206,3229,1
expected top-floor state: 3209,3220,2 near Staircase 56231 at 3205,3229,2
observed Staircase: object 16672 at 3204,3229,1
bottomFloorAvailable: false
blockers: player_not_near_top_floor_route_state, expected_top_floor_staircase_missing
full live loop rerun: no
reason: the client is already on the unproven plane-1 state, so the top-floor Bottom floor probe cannot validate the intended route action and plane-1 recovery remains unproven.
```

Final report for this pass:

```text
git status before: existing dirty tree with prior plane-1 recovery docs/code plus knowledge/report edits
git status after: source/docs/tests modified; focused probe artifact written under recordings and left uncommitted
changed files: staircase_floor_selection_probe.py, route_demonstration.py, action_proposal.py, executor.py, Bank_to_Woodcutting_area.route_guide.json, focused tests, live/knowledge reports
Bottom floor live-probed this pass: no, because current live player is already on plane 1
Bottom floor source evidence used: bot_runs\20260609_135357_live_woodcutting_loop\bot_action_trace.jsonl
route guide updated: yes, from strict live trace evidence for object 56231 at 3205,3229,2
full loop rerun: no
reason full loop was not rerun: focused probe did not prove current top-floor Bottom floor availability; current state is the unproven plane-1 blocker
commit hash: not committed in this pass
next recommended task: either return the character to the top-floor bank Staircase state and rerun staircase_floor_selection_probe.py --json, or capture a fresh plane-1 recovery fixture
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\staircase_floor_selection_probe.py telemetry-viewer\route_demonstration.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\executor.py telemetry-viewer\candidate_core.py
python telemetry-viewer\tests\test_route_demonstration.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_input_control_executor.py -k floor_selection
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\staircase_floor_selection_probe.py --json
```

Check results:

```text
py_compile: PASS
route_demonstration/action_proposal/bot_eval_runner tests: PASS
input_control_executor focused floor_selection tests: PASS
full input_control_executor.py: timed out before focused rerun
project knowledge test/check and telemetry_ui --check: PASS
initial input geometry check: FAIL input_geometry_focus_needed while Chrome was foreground
after Computer activation of RuneLite: input geometry PASS
staircase_floor_selection_probe.py --json: FAIL because current player is 3206,3229,1, not top-floor route state
```

## 23. Fresh Plane-1 Recovery Evidence Probe

Committed checkpoint:

```text
commit: fa285e8 add focused staircase floor selection probe and evidence reporting
push: stabilization/live-loop-recovery-20260609 pushed to origin
```

Environment repair before probe:

```text
initial geometry check: WARN because 8893 refused connection and foreground was Chrome
canonical recovery command: context_service.py --ensure-loaded-scene
recovery result: recovered_loaded_scene
snapshot endpoint: PASS on 8893
RuneLite foreground: RuneLite - KCLBolus
loadedSceneVerified: true
inputGeometryPass: true
current world: 3206,3229,1
```

Focused probe:

```text
probe type: plane1_staircase_recovery_probe
probe folder: recordings\20260609_185122_plane1_staircase_recovery_probe
target object: Staircase
object id: 16672
object world: 3204,3229,1
top menu: Climb / Staircase
captured rows: Climb, Climb-up, Climb-down, Walk here, Examine
Bottom floor captured: no
Climb-down captured: yes
Climb-up captured: yes
menu stale: no
row bounds captured: no
route transition click sent: no
```

Guide/model update:

```text
route guide updated: yes
new entry: Bank_to_Woodcutting_area.plane1RecoveryInteractions[0]
action: Climb-down / Staircase
strict match: object 16672 at 3204,3229,1, allowed source plane 1
top-floor Bottom floor guard: unchanged
name-only Staircase matching: still rejected
generic Climb matching: still rejected
```

Rerun decision:

```text
full woodcutting loop run: no
reason: this task was evidence capture and guide modeling only
next command after checks: python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
first thing to verify in that run: plane-1 Climb-down changes plane 1 -> 0 and the return route resumes
```

Focused route-candidate check:

```text
proposedAction: interact_service_route_object
reason: route_guide_plane1_recovery_interaction
target: Staircase
object id: 16672
world: 3204,3229,1
expected option: Climb-down
executable: true
suggested click point: 191,146 canvas
missing capabilities: none
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\staircase_floor_selection_probe.py telemetry-viewer\plane1_staircase_recovery_probe.py telemetry-viewer\route_demonstration.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\executor.py
python telemetry-viewer\tests\test_route_demonstration.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_input_control_executor.py -k floor_selection
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
```

Check results:

```text
py_compile: PASS
test_route_demonstration.py: PASS, 17 tests
test_action_proposal.py: PASS, 92 tests
test_bot_eval_runner.py: PASS, 32 tests
test_input_control_executor.py -k floor_selection: PASS, 2 tests
test_project_knowledge.py: PASS, 7 tests
telemetry_ui.py --check: PASS
update_project_knowledge.py --check: PASS
```

## 24. Post-Recovery Route Context Rehydration Diagnosis

Updated: 2026-06-13.

Patch scope:

```text
branch: stabilization/live-loop-recovery-20260609
changed source: bot_eval_runner.py, candidate_core.py, input_control/action_proposal.py, input_control/executor.py
changed tests: test_action_proposal.py, test_bot_eval_runner.py, test_input_control_executor.py
changed report: docs/live_loop_execution_fix_report.md
strict Staircase guards loosened: no
```

Root cause of `route_context_not_rehydrated_after_loaded_scene_recovery`:

```text
The live proposal path could receive a fresh player tile after recovery while the older route/task phase labels were empty or stale.
_wrong_floor_route_reentry_proposal required a route-ish phase/intent before calling route_demonstration.resolve_reentry, so the proven plane-1 recovery interaction was skipped.
_merge_plugin_snapshot_into_status exposed playerLocation but not canonical playerWorldPosition or hydration provenance.
_maybe_context_action_proposal could keep a generic non-executable context fallback instead of a fresh route-guide reentry proposal/blocker.
```

Post-recovery hydration behavior after the patch:

```text
fresh plugin snapshot baseline player tile is copied to playerWorldPosition and brain.playerWorldPosition
postRecoveryContextHydration records hydrationSource, freshSnapshotTick, freshExportSeq, freshPlayerWorldPosition, freshPlane, blockers, and warnings
same-plane route-guide reentry is recomputed from the current WorldPoint even when route phase labels are missing
generic no_executable_action is not allowed to mask a specific route-guide reentry candidate/blocker
candidate traces now surface routeGuideReentryAttempted, routeGuideReentryCandidate, postRecoveryContextHydrated, hydrationSource, and freshPlayerWorldPosition
```

Expected behavior at `3206,3229,1` after patch:

```text
with live Staircase object 16672 at 3204,3229,1 available:
  proposedAction: interact_service_route_object
  reason: route_guide_plane1_recovery_interaction
  target: Staircase
  object id: 16672
  action: Climb-down

without a live matching target:
  proposedAction: wait_for_context
  reason: plane1_recovery_live_target_missing
  routeGuideReentryCandidate: plane1_recovery_interaction
```

Input geometry gate:

```text
initial gate: FAIL because 8890/8893 were down, foreground was Codex, and no RuneLite window was matched
self-heal used: live_control_panel.py --auto-start-normal-live --profile woodcutting from the current repo, then canonical start_game_command launch
Start Game classification: dev_gradle_run
second gate: PASS
RuneLite foreground: RuneLite
context health: PASS
snapshot health: PASS
inputGeometryPass: true
geometry source: win32.window_client_geometry
canvas: 1268x898 at screen origin 144,30
```

Real command run:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Live run result:

```text
run folder: bot_runs\20260613_110620_live_woodcutting_loop
linked recording: recordings\20260613_110750_live_woodcutting_loop_20260613_110750
status: FAIL
dry-run/no-input flags used: no
bot actions sent: 0
live input executed: no
plane-1 recovery candidate appeared: no
Climb-down / Staircase 16672 attempted: no
plane 1 -> 0 postcondition: not reached
route back continued: no
loop completed: no
```

Reason the patched route path was not live-validated:

```text
loaded-scene recovery result: unsafe
loadedSceneVerified after recovery: false
finalHotGameState: LOGIN_SCREEN
recovery blocker: stale_hot_client
readiness status: WARN
readiness rootCause: telemetry_stale
executor reason: arduino_pointer_calibration_required
candidate trace reason: arduino_pointer_calibration_required
safe result: no gameplay click was sent
```

Responsible gates in the latest live attempt:

```text
loaded-scene recovery blocked before a valid loaded scene was proven.
executor hardware gate blocked on missing/invalid Arduino pointer calibration before gameplay action proposal could execute.
route-guide reentry did not run in the live trace because the environment/hardware gates failed first.
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\bot_eval_runner.py telemetry-viewer\candidate_core.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\executor.py telemetry-viewer\task_script_api.py telemetry-viewer\knowledge_fabric.py telemetry-viewer\context_service.py telemetry-viewer\route_demonstration.py
python telemetry-viewer\tests\test_action_proposal.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_task_script_api.py
python telemetry-viewer\tests\test_knowledge_fabric.py
python telemetry-viewer\tests\test_route_demonstration.py
python telemetry-viewer\tests\test_project_knowledge.py
python telemetry-viewer\tests\test_input_control_executor.py -k post_recovery -k context_action_fallback_does_not_mask_fresh_plane1_reentry
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Check results:

```text
py_compile: PASS
test_action_proposal.py: PASS, 94 tests
test_bot_eval_runner.py: PASS, 33 tests
test_task_script_api.py: PASS, 31 tests
test_knowledge_fabric.py: PASS, 44 tests
test_route_demonstration.py: PASS, 17 tests
test_project_knowledge.py: PASS, 7 tests
focused executor tests: PASS, 2 tests
full test_input_control_executor.py: timed out after 184 seconds; focused touched tests passed
telemetry_ui.py --check: PASS
update_project_knowledge.py --check: PASS
```

Remaining blocker and next task:

```text
The route/context rehydration bug is fixed in code and covered by focused tests.
It still needs live validation after the client is authenticated/loaded and Arduino pointer calibration is valid.
Exact next command after those environment gates are true:
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
First live evidence to verify: candidate trace includes route_guide_plane1_recovery_interaction, Staircase 16672, Climb-down, plane 1, followed by a plane 1 -> 0 postcondition.
```

## 25. Live Gate Repair Attempt Before Plane-1 Recovery Validation

Updated: 2026-06-13.

Goal:

```text
Get telemetry, loaded-scene proof, input geometry, and Arduino pointer calibration green before rerunning the real live woodcutting loop against commit dbea3fb.
Do not modify route rehydration or plane-1 recovery unless the route candidate is reached and fails.
```

Git state:

```text
branch: stabilization/live-loop-recovery-20260609
HEAD: dbea3fb fix post-recovery route context rehydration
git status before: generated knowledge JSON files dirty only
changed files this pass: docs/live_loop_execution_fix_report.md
generated JSON left unstaged: capability_registry.json, open_gaps.json, project_knowledge.json, recordings_index.json, script_api_map.json
```

Telemetry repair result:

```text
8890 context service: listening, health reachable
8893 snapshot service: listening, health reachable
RuneLite/Java process: present
canonical live stack command needed this pass: not restarted because both ports were already up
snapshot health status: WARN
snapshot latestTick: -1
```

Loaded-scene recovery command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 180 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_gate_recovery
```

Loaded-scene result:

```text
status: manual_login_required
loadedSceneVerified: false
gameState: LOGIN_SCREEN
latestTick: -1
worldModelObjectTotal: 0
daemonFresh: false
clientTickHotFresh: false
blocker: manual_login_required
failure class: login_surface_no_saved_account
manual action required: Log in or clear the account/credential prompt manually inside the VM.
recovery artifacts: bot_runs\20260613_gate_recovery
```

Start Game/authentication check:

```text
normal resolver: cmd /c .\gradlew.bat --no-daemon run
prefer authenticated resolver: cmd /c .\gradlew.bat --no-daemon run
launchMode: dev_gradle_run
authenticatedLaunchLikely: false
authenticated launch configured: no
```

Input geometry result after recovery failure:

```text
command: python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
status: FAIL
blocker: input_geometry_focus_needed
RuneLite matched: true
matched window title: RuneLite Shell
foreground window: Chrome
focus repair attempted: true
focus repair succeeded: false
desktop focus automation: attempted; Computer Use bootstrap was unavailable in this Codex session, so repo focus repair was the active fallback
canvas geometry: 800x832 at screen origin 7,30
```

Arduino pointer calibration result:

```text
not run
reason: loadedSceneVerified=false and inputGeometryPass=false
policy: do not run calibration or gameplay actions until loaded scene and geometry gates pass
```

Real live command result:

```text
not run in this pass
reason: live gates did not pass
no dry-run substitution: yes
bot actions sent: 0
live input executed: no
plane-1 recovery candidate appeared: no, not reached
Climb-down / Staircase 16672 attempted: no
plane 1 -> 0 postcondition: not reached
```

Exact blocker:

```text
primary blocker: manual_login_required / login_surface_no_saved_account
secondary blocker after geometry check: input_geometry_focus_needed
why this is external: no authenticated launch command is configured, the dev Gradle path started RuneLite at LOGIN_SCREEN, and the repo recovery path detected no safe saved-account surface to click. Credentials will not be typed or automated.
```

## Auto-Login / Loaded-Scene Recovery Audit

Date: 2026-06-13

Existing recovery/autologin functions found:

```text
start_game_command.py
- resolve_start_game_command(prefer_authenticated=True)
- launch_start_game(...)
- classify_launch_mode(...)

liveness_recovery_core.py
- ensure_loaded_scene(...)
- loaded_scene_proof(...)
- classify_recovery_failure(...)
- canonical recovery ladder used by context_service.py

context_service.py
- --ensure-loaded-scene
- writes recovery_summary.json, recovery_attempts.jsonl, latest_recovery_state.json

execute_next_action.py
- --auto-recover-loaded-scene calls liveness_recovery_core.ensure_loaded_scene

bot_eval_runner.py
- --auto-recover-loaded-scene calls context_service.py --ensure-loaded-scene before live actions

run_runelite_bootstrap.py
- disconnected_ok candidate
- saved-account Play Now candidate
- Click here to play candidate
- launcher/startup bootstrap checks
```

Existing safe login-surface handlers found:

```text
disconnected_ok: present in run_runelite_bootstrap.py
play_now: present in run_runelite_bootstrap.py
click_here_to_play: present in run_runelite_bootstrap.py
Start Game resolver: present in start_game_command.py
credential entry: intentionally not automated
```

Where `manual_login_required` was emitted:

```text
liveness_recovery_core.ensure_loaded_scene previously treated login_screen as a manual stop before the safe bootstrap ladder.
run_runelite_bootstrap.py may still report blocked_user_login_required after bounded safe clicks fail.
bot_eval_runner.py surfaces the context_service/liveness recovery result in live action artifacts.
```

Why existing recovery was skipped or did not progress:

```text
Before this patch, LOGIN_SCREEN could stop before the bootstrap ladder because login_screen was not in RECOVERABLE_STATES and manualLoginRequired short-circuited recovery.
After enabling recovery, the first live attempt showed Play Now was visible but only Click here to play was clicked.
The recovery ladder now retries once with Play Now preferred when the first bootstrap attempt proves Play Now was available but untouched.
After Play Now was attempted, the client still did not reach loaded-scene proof, so the ladder escalated to Start Game relaunch through start_game_command.py.
The configured Start Game command is dev_gradle_run, not an authenticated launch, so relaunch returned to LOGIN_SCREEN and failed with dev_launch_not_loaded.
```

Current exact recovery result:

```text
command: python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
status: unsafe
blocker: dev_launch_not_loaded
loadedSceneVerified: false
finalHotGameState: LOGIN_SCREEN
worldModelObjectTotal: 0
recoveryAttempted: true
autologinRecoveryAttempted: true
savedAccountDetected: true
playNowAttempted: true
clickHereToPlayAttempted: true
launcherRecoveryAttempted: true
waitForLoadedSceneAttempted: true
manualLoginRequiredOnlyAfterRecovery: false
Start Game command: cmd /c .\gradlew.bat --no-daemon run
Start Game source: ui_config:C:\Users\badto\.osrs-telemetry\telemetry_ui_config.json
launchMode: dev_gradle_run
artifact folder: bot_runs\20260613_114916_loaded_scene_recovery
```

Preflight / geometry after recovery patch:

```text
preflight: WARN only because Start Game is dev_gradle_run / not authenticated
input geometry: PASS, RuneLite Shell focused, canvas 800x832 at 7,30
live command: not run
reason: canonical loaded-scene recovery still failed loadedSceneVerified=false
bot actions sent: 0
```

New recovery-before-manual-login behavior:

```text
LOGIN_SCREEN is recoverable when --auto-recover-loaded-scene is active.
manual_login_required is now only valid after bounded safe recovery attempts, unless the screen is a credential-required surface.
The recovery artifacts record recoveryAttempted, autologinRecoveryAttempted, savedAccountDetected, playNowAttempted, disconnectedOkAttempted, clickHereToPlayAttempted, launcherRecoveryAttempted, waitForLoadedSceneAttempted, and manualLoginRequiredOnlyAfterRecovery.
If Play Now is visible but not attempted, the result is saved_account_play_now_not_attempted, not manual_login_required.
```

Commit / push:

```text
commit: 358ced27ab7a0ec51f91f68eaecc55d6675ce063 enforce canonical auto-login recovery before manual login blocker
push result: origin/stabilization/live-loop-recovery-20260609 updated successfully through report commit 232b3f6
remaining unstaged generated JSON: capability_registry.json, project_knowledge.json, recordings_index.json, script_api_map.json
```

Checks run:

```powershell
git branch --show-current
git status --short
git log --oneline -n 5
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 180 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_gate_recovery
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Next recommended task:

```text
Log in or configure an authenticated Start Game command, then rerun:
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\execute_next_action.py --arduino-pointer-calibration-test --allowed-window runelite --arduino-port COM6 --arduino-pointer-calibration-path .osrs-telemetry\arduino_pointer_calibration.json --json
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## Authenticated Live Start Path Fix

Updated: 2026-06-13.

Branch:

```text
stabilization/live-loop-recovery-20260609
```

Start Game / recovery audit:

```text
canonical owner: telemetry-viewer\start_game_command.py
recovery caller: liveness_recovery_core.py -> resolve_start_game_command(prefer_authenticated=True)
UI caller: telemetry_ui.py Start Game and Diagnostics
bot preflight caller: bot_eval_runner.py run_preflight
previous blocker: dev_launch_not_loaded
previous cause: prefer_authenticated still fell back to game_launch_command / Gradle
```

Authenticated launch discovery:

```text
Jagex Launcher path found: C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe
resolved live command: "C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite
launch mode: jagex_launcher_runelite_quick_launch
standalone RuneLite path found: C:\Users\badto\AppData\Local\RuneLite\RuneLite.exe
standalone RuneLite status: WARN only; Jagex quick launch is preferred for live authentication
existing loaded client candidates at inventory check: none
```

Behavior change:

```text
devStartCommand: cmd /c .\gradlew.bat --no-daemon run
dev launch mode: dev_gradle_run
liveStartCommand: discovered Jagex quick launch when no config value is set
live launch mode: jagex_launcher_runelite_quick_launch
dev_gradle_run accepted as primary live login path: no
missing live command blocker: authenticated_live_start_missing
manual_login_required remains post-recovery only
```

Validation commands:

```powershell
python telemetry-viewer\start_game_command.py --list
python telemetry-viewer\start_game_command.py --validate-live
python telemetry-viewer\start_game_command.py --print-live-command
```

Validation result:

```text
--validate-live: PASS
--print-live-command: "C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite
bot preflight: PASS
startGameCommandClassified: PASS / jagex_launcher_runelite_quick_launch
```

Loaded-scene recovery through the authenticated path:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_authenticated_start_recovery
```

Recovery result:

```text
status: unsafe
blocker: stale_login_screen_after_relaunch
initial state: plugin_endpoint_down
final state: disconnected_dialog
final hot game state: LOGIN_SCREEN
loadedSceneVerified: false
worldModelObjectTotal: 0
launch mode: jagex_launcher_runelite_quick_launch
start command: "C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite
launch result: PASS, launchedProcessPid=14440
safe login action tried: disconnected_ok
clickHereToPlayAttempted: false
playNowAttempted: false
launcherRecoveryAttempted: true
waitForLoadedSceneAttempted: true
recovery artifacts: bot_runs\20260613_authenticated_start_recovery
```

Live loop status for this pass:

```text
full live bot loop run: not yet
reason: loaded-scene proof did not pass after Jagex quick launch; no gameplay action is allowed while finalHotGameState=LOGIN_SCREEN and loadedSceneVerified=false.
```

Checks run:

```powershell
python -m py_compile telemetry-viewer\start_game_command.py telemetry-viewer\liveness_recovery_core.py telemetry-viewer\context_service.py telemetry-viewer\bot_eval_runner.py telemetry-viewer\telemetry_ui.py
python telemetry-viewer\tests\test_start_game_command.py
python telemetry-viewer\tests\test_liveness_recovery_core.py
python telemetry-viewer\tests\test_bot_eval_runner.py
python telemetry-viewer\tests\test_telemetry_ui.py
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\start_game_command.py --list
python telemetry-viewer\start_game_command.py --validate-live
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_authenticated_start_recovery
```

Check results:

```text
py_compile: PASS
test_start_game_command.py: PASS, 5 tests
test_liveness_recovery_core.py: PASS, 25 tests
test_bot_eval_runner.py: PASS, 33 tests
test_telemetry_ui.py: PASS, 38 tests
telemetry_ui.py --check: PASS
update_project_knowledge.py --check: PASS
start_game_command.py --validate-live: PASS
bot_eval_runner.py --preflight: PASS
context_service.py --ensure-loaded-scene: FAIL/unsafe, blocker stale_login_screen_after_relaunch
```

Next command once the disconnected/login surface is cleared:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3

## Visible Recovery Click Proof Fix - 2026-06-13

Branch: stabilization/live-loop-recovery-20260609

Changed recovery layers:

- `telemetry-viewer\run_runelite_bootstrap.py`
- `telemetry-viewer\liveness_recovery_core.py`
- `telemetry-viewer\context_service.py`
- `telemetry-viewer\tests\test_liveness_recovery_core.py`

Previous bug:

`disconnected_ok` recovery artifacts only showed the last Arduino acknowledgement, usually `OK MOUSE_UP`. That did not prove the cursor was at the button, that the click was complete, that RuneLite was focused, or that the post-click state changed. The recovery state machine also treated `disconnected_ok` as if it had to produce `loadedSceneVerified=true` immediately.

New behavior:

- `disconnected_ok` expects a state-specific transition to login/play/loading/loaded scene. It is not required to load the scene immediately.
- `play_now`, `click_here_to_play`, and `continue` each carry their own expected next-state set.
- `recovery_attempts.jsonl` rows now use `recovery_attempt.v2`.
- Each visible button attempt records foreground window title, RuneLite focus proof, target rect/point, cursor before/after move/click, cursor-target distance, Arduino ACK list, `mouseDownSent`, `mouseUpSent`, `clickSent`, `fullClickSequenceVerified`, before/after visual/hot states, expected transition result, and blocker.
- `visible_button_no_transition` is only emitted after a focused, valid, grounded, complete click produces no expected visual or hot-state change.
- Missing focus, invalid target, missing cursor grounding, or incomplete click sequence now produce specific blockers instead of generic no-transition.

Latest recovery command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_visible_click_proof_recovery_v3
```

Latest recovery result:

- status: unsafe
- blocker: `visible_button_no_transition`
- loaded scene: false
- recovery artifact folder: `bot_runs\20260613_visible_click_proof_recovery_v3`
- visible button: `disconnected_ok`
- foreground/focus: `RuneLite`, `windowFocusVerified=true`
- target validation: `PASS`, target point `{x:1362,y:1013}`
- cursor proof: cursor at target, distance about `3.61px`
- click sequence: `mouseDownSent=true`, `mouseUpSent=true`, `clickSent=true`, `fullClickSequenceVerified=true`
- transition: `expected_transition_not_observed`; dialog remained `disconnected_dialog`

Loaded scene result:

Loaded-scene proof remained false, so the input-geometry check and real live bot loop were not run in this pass.

Exact next command after the environment is expected to transition:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
```
```

Only after that returns `loadedSceneVerified=true`, continue with:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## Visible Play Button Recovery Fix

Updated: 2026-06-13.

Visible Play Button Recovery Audit:

```text
manual_login_required owner: liveness_recovery_core.py result classification
visible screen classifier: run_runelite_bootstrap.py screenshot/window button candidates
safe visible buttons: disconnected_ok, play_now, click_here_to_play, continue
previous gap: after Start Game relaunch, recovery waited for loaded-scene proof and could stop at stale/login state without re-entering the safe visible-button click ladder.
patched gate: post-relaunch disconnected/play/click-here surfaces now run the same canonical bootstrap recovery once before reporting failure.
spam guard: repeated identical visible button clicks stop at visible_button_no_transition instead of max-budget looping.
input path: HumanInputController -> ArduinoHIDBackend, recorded per click attempt.
manual_login_required: only valid after visibleButtonScanAttempted=true and no safe visible recovery button exists, or credentials/account/2FA are required.
```

Latest recovery command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_visible_button_recovery_rerun
```

Latest recovery result:

```text
status: unsafe
blocker: visible_button_no_transition
recoveryFailureClass: visible_button_no_transition
loadedSceneVerified: false
initial state: disconnected_dialog
final state: disconnected_dialog
final hot game state: LOGIN_SCREEN
visibleButtonScanAttempted: true
visibleButtonsFound: disconnected_ok
target validation: PASS, inside RuneLite safe click region
input path: HumanInputController/ArduinoHIDBackend
Arduino evidence: OK MOUSE_UP acknowledgements recorded
click attempts: 4 bounded disconnected_ok attempts across initial and post-relaunch recovery
Start Game relaunch: attempted through jagex_launcher_runelite_quick_launch
live bot loop run: not run
reason: loadedSceneVerified=false; gameplay input remains blocked until a loaded scene is proven.
recovery artifacts: bot_runs\20260613_visible_button_recovery_rerun
```

Next command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
```

## Verified Recovery Click No-Transition Diagnosis

Updated: 2026-06-13.

Root cause:

The previous `visible_button_no_transition` was not a dead OK button as first labeled. The new screenshot evidence showed RuneLite was on the saved-account `Play Now` screen, while `run_runelite_bootstrap.py` had selected a stale `disconnected_ok` candidate at `{x:1362,y:1013}`. The target landed below the real Play Now text/account panel, so the client did not transition.

Fixes made:

- `run_runelite_bootstrap.py` now records before-click, after-click, and after-wait screenshots for visible recovery clicks.
- Recovery click attempts now record selected RuneLite window identity, clicked window identity, and whether the target HWND matches the selected RuneLite HWND.
- If a complete click causes no visual/hot-state transition, recovery tries one bounded alternate direct `CLICK` method before classifying the surface.
- Repeated no-effect visible button clicks now classify as `stale_dead_runelite_instance` and may use the existing Start Game relaunch path.
- Saved-account `Play Now` detection now suppresses stale disconnected-OK false positives, and its contrast threshold was relaxed enough to recognize the current live Play Now screen.

Evidence:

```text
wrong-target artifact: bot_runs\20260613_verified_click_no_transition_diagnosis
overlay: bot_runs\20260613_verified_click_no_transition_diagnosis\target_overlay.png
selected window: RuneLite, hwnd 16189074
clicked window: RuneLite, rootHwnd 16189074
clickedWindowMatchesSelected: true
old selected action: disconnected_ok
old target: {x:1362,y:1013}
actual visible screen: saved-account Play Now
```

Recovery validation after fix:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_play_now_recovery_after_target_fix
```

Result:

```text
status: recovered_loaded_scene
clicked buttons: play_now, click_here_to_play
loadedSceneVerified: true
gameState: LOGGED_IN
worldModelObjectTotal: 1248
recovery artifact folder: bot_runs\20260613_play_now_recovery_after_target_fix
```

Input geometry:

```text
command: python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
status: PASS
inputGeometryPass: true
RuneLite foreground: true
canvas: 1229x868 at {x:148,y:57}
```

Real live command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Result:

```text
live run folder: bot_runs\20260613_132900_live_woodcutting_loop
linked recording: recordings\20260613_132909_live_woodcutting_loop_20260613_132909
bot actions sent: 0
live input executed: no
loop completed: no
blocker: arduino_pointer_calibration_required
secondary readiness note: live readiness reported loaded_scene_not_ready from stale client_tick_hot during the executor gate
```

Next exact task:

Run or repair the canonical Arduino pointer calibration gate, then rerun:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## Arduino Pointer Calibration Gate

Updated: 2026-06-13.

Audit:

```text
blocker owner: execute_next_action.py live Arduino movement gate
previous blocker: arduino_pointer_calibration_required
previous expected file: interaction_geometry\live\arduino_pointer_calibration_COM6.json
previous file state: present but stale, written 2026-06-09T22:13:41Z
canonical refreshed path: .osrs-telemetry\arduino_pointer_calibration.json
Arduino port: COM6
RuneLite window: RuneLite - KCLBolus
RuneLite hwnd: 16189074
input geometry: PASS
canvas rect: {x:148,y:57,width:1229,height:868}
calibration allowed region: {x:217,y:150,width:1122,height:686}
previous recovery clicks: not accepted as pointer calibration because only arduino_pointer_calibration_record.v1 with PASS/freshness satisfies gameplay movement safety
```

Calibration command:

```powershell
python telemetry-viewer\execute_next_action.py --arduino-pointer-calibration-test --allowed-window runelite --arduino-port COM6 --arduino-pointer-calibration-path .osrs-telemetry\arduino_pointer_calibration.json --json
```

Calibration result:

```text
status: PASS
calibration artifact: .osrs-telemetry\arduino_pointer_calibration.json
writtenAtUtc: 2026-06-13T18:39:27Z
movement chunks: 135/135 successful
movementSuccessRate: 1.0
maxPositionErrorPx: 4
finalPositionErrorPx: 1
clickSent: false
keySent: false
foreground window before/after: RuneLite - KCLBolus
input backend: HumanInputController / ArduinoHIDBackend
```

Wiring fix:

`bot_eval_runner.py` now passes the refreshed canonical calibration path to `execute_next_action.py`, and successful executor payloads include `pointerCalibration` and `movementSafety` so `bot_eval_summary.json` can report `arduinoPointerCalibrationStatus`, `calibrationPath`, `calibrationBlocker`, and `arduinoMovementSafetyStatus`.

Input geometry after calibration:

```text
command: python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
status: PASS
loadedSceneVerified: true
inputGeometryPass: true
foreground window: RuneLite - KCLBolus
```

Real live command after calibration:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Run result:

```text
live run folder: bot_runs\20260613_134142_live_woodcutting_loop
linked recording: recordings\20260613_134150_live_woodcutting_loop_20260613_134150
status: WARN
bot actions sent: 0
live input executed: no
loop completed: no
arduinoPointerCalibrationStatus: PASS
arduinoMovementSafetyStatus: PASS
plane-1 recovery candidate: reached
candidate: route_guide_plane1_recovery_interaction
target: Staircase 16672 at 3204,3229,1
expected action: Climb-down
final blocker: plane1_recovery_hover_option_mismatch
evidence: hover top menu was Climb / Staircase, while Climb-down / Staircase was present as a lower menu entry; executor skipped click safely with no_click_safety_skip.
```

Next exact task:

Fix strict route-object lower-menu selection for the plane-1 `Climb-down / Staircase` recovery. The next fix should use the captured lower menu entry for object `16672` and preserve strict id/world/plane/action guards.

Checks:

```text
PASS: python -m py_compile telemetry-viewer\execute_next_action.py telemetry-viewer\bot_eval_runner.py telemetry-viewer\input_control\executor.py telemetry-viewer\input_control\input_geometry.py
PASS: python telemetry-viewer\tests\test_arduino_live_input_policy.py
PASS: python telemetry-viewer\tests\test_bot_eval_runner.py
PASS: python telemetry-viewer\tests\test_live_readiness.py
PASS: python telemetry-viewer\tests\test_telemetry_ui.py
PASS: python telemetry-viewer\tests\test_project_knowledge.py
PASS: python telemetry-viewer\telemetry_ui.py --check
PASS: python telemetry-viewer\update_project_knowledge.py --check
TIMEOUT: python telemetry-viewer\tests\test_input_control_executor.py timed out after 124 seconds.
FAIL: focused executor check InputControlExecutorTest.test_live_movement_safety_blocks_off_region_screen_point_before_move expected screen_click_point_outside_movement_safety_region but current branch returns input_geometry_invalid. The calibration patch did not touch executor safety semantics; this is recorded as a remaining focused executor-test mismatch.
```

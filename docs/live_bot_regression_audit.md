# Live Bot Regression Audit

Date: 2026-06-09

## Verdict

The last strong non-live bot-loop baseline is the full-loop recording and replay
eval from 2026-06-07 / early 2026-06-08. The live path then progressed through
real Arduino-backed action execution, but current progress is blocked by a mix
of environment gates and route/action-proposal handoff issues.

No more live runs should be attempted until the recovery plan in
`docs\next_live_loop_recovery_plan.md` is followed.

## Milestones

| Milestone | Artifact | Report | Status | What worked | What failed | Likely code area |
| --- | --- | --- | --- | --- | --- | --- |
| Last known Record Everything Simple recording PASS | `recordings\20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` and `recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor` | `docs\recording_analysis_latest_record_everything_sweep.md` | PASS | Simple Mode/Record Everything preserved input, camera, menu, target, traversal, and lifecycle evidence. | Tree direct candidates and menu row geometry were partial. | `manual_recorder.py`, `analyze_manual_recording.py`, lifecycle analyzers. |
| Last known route template PASS | `recordings\20260606_121630_bank_to_WC`; `route_templates\Bank_to_Woodcutting_area.route_template.json` | `docs\recording_analysis_route_template_third_fresh_run.md` | PASS | Route lifecycle and template comparison matched 5/5 required segments with score 1.0. | Door/Open was removed as a hard requirement; one Cancel menu row is review evidence. | `route_template.py`, `traversal_lifecycle.py`, `route_monitor.py`. |
| Last known full woodcutting loop analyzer PASS | `recordings\20260607_171427_Wood_cutting_attacked` | `docs\recording_analysis_full_woodcutting_loop_latest.md` | PASS | Full loop detected: chop to full inventory, route to bank, deposit Logs x28, route back, resume after combat. | No single registered full-loop route template; some row geometry partial. | `woodcutting_loop_lifecycle.py`, `banking_lifecycle.py`, `interruption_lifecycle.py`, `combat_damage_summary.py`. |
| Last known bot replay eval PASS | `bot_runs\20260607_204642_woodcutting_loop_eval` | `docs\bot_eval_full_woodcutting_loop.md` | PASS | Eight phase decisions matched expected loop primitives, zero postcondition warnings. | Replay only; live geometry/readiness was not exercised. | `bot_eval_runner.py`, `task_script_api.py`, `knowledge_fabric.py`. |
| First live readiness baseline PASS | `bot_runs\20260608_081231_woodcutting_loop_live_smoke`; `bot_runs\20260608_081923_woodcutting_loop_live_dry_run` | `docs\bot_eval_full_woodcutting_loop.md`, `docs\bot_eval_live_daemon_readiness.md` | PASS | Context service, plugin snapshot, fresh telemetry, loaded scene, route templates, and task script API all passed. | Dry-run/no-input only. | `bot_eval_runner.py`, `live_readiness_core.py`. |
| First live action run that tried real input | `bot_runs\20260608_084813_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_run.md` | FAIL | Readiness passed and a linked recording was started. | Pointer calibration was for the fallback calibration window, so real movement was blocked before useful gameplay. | `execute_next_action.py`, `input_control\executor.py`, Arduino calibration safety. |
| First live action run with loaded scene and no executable candidate | `bot_runs\20260608_103728_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_run.md` | WARN | Loaded scene proof and readiness passed; Arduino backend connected/armed safely. | 0 candidates, 0 attempted actions, max runtime reached. | Candidate/action handoff: `candidate_core.py`, `action_proposal.py`, `bot_eval_runner.py`. |
| First live action sequence that sent Arduino-backed gameplay input | `bot_runs\20260608_154205_live_woodcutting_loop` through `bot_runs\20260608_160916_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_autonomous_run.md` | WARN | Real Arduino-backed route clicks were sent. `20260608_160916` executed 1 action, 1 click, 1 success, and stopped safely. | Route context kept proposing an arrived/current waypoint near `3203,3237`. | Route candidate/proposal advancement: `action_proposal.py`, `candidate_core.py`, route guide integration. |
| First live run stuck on current tile / route waypoint | `bot_runs\20260608_160159_live_woodcutting_loop` and `bot_runs\20260608_160916_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_autonomous_run.md` | WARN | Executor recognized progress and stopped on bounded stale-route state instead of clicking forever. | Service-route/pathing context did not advance from the castle approach waypoint. | `action_proposal.py`, `route_demonstration.py`, `route_monitor.py`, `live_core_daemon.py`. |
| Wrong route-object / ladder regression narrowed | `bot_runs\20260608_173455_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_run.md`, `docs\bot_eval_live_woodcutting_loop_autonomous_run.md` | WARN | Sent 7 Arduino-backed actions and exposed route oscillation. | Generic Ladder candidate was too permissive before route identity/corridor checks. | Route object candidate validation in `candidate_core.py` / `action_proposal.py`. |
| Later route-guide validation blocker | `bot_runs\20260608_182824_live_woodcutting_loop` and `bot_runs\20260608_203556_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_autonomous_run.md` | FAIL | Guide path avoided unrelated Ladder; executor blocked unsafe states. | `182824` stopped on pre-action readiness after one click; `203556` exposed a bad full-inventory Tree fallback and movement safety region failure. | Route guide/action fallback policy; executor safety. |
| First live run blocked by loaded-scene/login after guide changes | `bot_runs\20260608_204534_live_woodcutting_loop`; `bot_runs\20260608_213714_live_woodcutting_loop` | `docs\bot_eval_live_woodcutting_loop_run.md`, `docs\bot_eval_live_daemon_readiness.md` | FAIL | Recovery and manual-loaded-scene gates failed closed before route execution. | Final hot state was `LOGIN_SCREEN` / loaded scene unavailable. | `liveness_recovery_core.py`, `start_game_command.py`, environment/auth path. |
| First live run blocked by input geometry | `bot_runs\20260609_100724_woodcutting_loop_live_dry_run` and the 2026-06-09 input geometry check | `docs\bot_eval_live_input_geometry_readiness.md` | FAIL | Geometry resolver identified stale canvas geometry precisely and refused gameplay input. | 8890/8893 refused connections, no RuneLite window match, stale file-session geometry only. | `input_control\input_geometry.py`, `live_readiness_core.py`, environment/focus/daemon startup. |
| Current latest live blocker | `docs\bot_eval_live_input_geometry_readiness.md` | same | FAIL | Current blocker is explicit: `input_geometry_stale`. | Live loop not allowed until RuneLite/endpoints/current geometry are healthy. | Environment first; then route/action proposal if loaded and geometry pass. |

## Regression Diff Focus

The reliable baseline was:

- Full-loop analyzer PASS on `20260607_171427_Wood_cutting_attacked`.
- Bot replay eval PASS on `bot_runs\20260607_204642_woodcutting_loop_eval`.
- Live smoke/dry-run PASS on `20260608_081231` / `20260608_081923`.

The first useful live action evidence came later:

- `bot_runs\20260608_160916_live_woodcutting_loop`
- `executedActionCount=1`
- `actionsExecuted=1`
- `actualClicks=1`
- `successfulActions=1`
- `stopReason=route_waypoint_arrived_but_route_state_stale`
- final location `3203,3237,0`

Likely regression areas:

- Route candidate generation: current tile and stale daemon route context can outrank next-segment progress.
- Route guide integration: guide progress is promising but must be used as the first repair target once live gates pass.
- Readiness/input geometry: newer gates are correct, but current environment must satisfy them before route debugging.
- Start Game/dev launch: `dev_gradle_run` is not authenticated loaded-scene proof.
- Bot eval orchestration: should stay as the sole live-loop runner and stop before execution if readiness or geometry fails.
- Executor safety policy: correctly stops bad geometry/current-tile/repeated-route behavior; do not weaken it to chase progress.

## Last Known Working Behavior

The last known working bot-loop baseline is not a completed live loop. It is:

1. Full-loop recording/analyzer PASS:
   `recordings\20260607_171427_Wood_cutting_attacked`.
2. Bot replay eval PASS:
   `bot_runs\20260607_204642_woodcutting_loop_eval`.
3. First real live action progress:
   `bot_runs\20260608_160916_live_woodcutting_loop`, which sent one
   Arduino-backed click and then stopped safely on route waypoint state.

Recovery should anchor on that sequence instead of last-run symptoms.

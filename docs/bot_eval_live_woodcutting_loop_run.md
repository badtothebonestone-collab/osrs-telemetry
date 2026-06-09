# Live Woodcutting Bot Loop Run

Date: 2026-06-08

## Verdict

Final status: FAIL, blocked before bot actions.

The bot loop did not complete because RuneLite was on the disconnected/login surface. The recovery path clicked only recognized safe startup controls through the Arduino input path, but the client returned to the disconnected dialog and loaded-scene proof never recovered.

No live woodcutting/banking bot actions were sent after the readiness fix.

The latest command did not fall back to dry-run. It ran real live-action mode
with an automatic loaded-scene recovery prelude, then stopped because recovery
did not achieve loaded-scene proof.

## Commands Run

Pointer calibration:

```powershell
python telemetry-viewer\execute_next_action.py --backend arduino --arduino-port COM6 --arduino-pointer-calibration-test --allowed-window runelite --no-click --calibration-staging-max-distance-px 400 --focus-runelite --json
```

Live run attempt:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --arduino-port COM6 --json
```

Loaded-scene recovery:

```powershell
python telemetry-viewer\run_runelite_bootstrap.py --recover-loaded-scene --verify-loaded-scene --backend arduino --arduino-port COM6 --execute --save-debug-screenshot --timeout-seconds 180 --max-startup-clicks 8 --keep-existing-runelite --json
```

Readiness smoke after patch:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live-smoke --duration 5 --no-input --json
```

Final live-action gate after patch:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --arduino-port COM6 --json
```

Real live-action command with auto recovery:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 180 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

## Run Folders

| Run | Folder | Result |
| --- | --- | --- |
| Initial live action | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_084813_live_woodcutting_loop` | Failed before click because persisted pointer calibration was for the fallback calibration window, not RuneLite. |
| Live action after calibration | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_085824_live_woodcutting_loop` | Started while the client was actually disconnected because readiness trusted fresh wrapper files. Stopped with no useful bot actions. |
| Readiness smoke after fix | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_091822_woodcutting_loop_live_smoke` | Correctly failed readiness with no input commands. |
| Final live-action gate after fix | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_091842_live_woodcutting_loop` | Correctly failed readiness with no bot input. |
| Real live action with auto recovery | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_093432_live_woodcutting_loop` | Ran recovery first, wrote recovery artifacts, then failed readiness because the client was still disconnected. Bot actions sent: 0. |
| Repaired direct recovery | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_100633_loaded_scene_recovery` | Recovery now classifies the blocker as `disconnected_loop` and writes per-click recovery evidence. Bot loop was not started because recovery failed. |

Linked recording from the stopped disconnected run:

`C:\Users\badto\osrs-telemetry\recordings\20260608_085824_live_woodcutting_loop_20260608_085824`

This recording is a disconnected-state artifact, not a full-loop fixture.

## Readiness Result

Latest readiness status: FAIL.

| Field | Result |
| --- | --- |
| context service reachable | false after recovery, `/health` timed out |
| snapshot service reachable | true |
| telemetry wrapper fresh | true |
| game client loaded | false |
| loaded scene ready | false |
| latest tick | 514 |
| current area from stale context | woodcutting |
| route templates loaded | true |
| task_script_api readable | true |
| live eval can start | false |
| root cause | `context_health_unreachable_or_unresponsive`, with loaded-scene blockers still present |
| blockers | `client_tick_hot_game_state_login_screen`, `client_tick_hot_stale_age_ms_*` |

The key bug was that the old readiness gate treated a freshly generated wrapper file and stale baseline `LOGGED_IN` crumbs as enough to start. It now checks current hot client state and loaded-scene proof before allowing live action.

## Recovery Result

Recovery used the existing RuneLite bootstrap path and Arduino input. It safely clicked:

- `disconnected_ok`
- saved-account `play_now`

The clicks landed inside the RuneLite window and were acknowledged by the Arduino backend. The client still returned to login/disconnected surfaces and never produced loaded-scene proof.

Latest recovery artifact paths:

- `C:\Users\badto\osrs-telemetry\bot_runs\20260608_093432_live_woodcutting_loop\recovery_attempts.jsonl`
- `C:\Users\badto\osrs-telemetry\bot_runs\20260608_093432_live_woodcutting_loop\recovery_summary.json`
- `C:\Users\badto\osrs-telemetry\bot_runs\20260608_100633_loaded_scene_recovery\recovery_attempts.jsonl`
- `C:\Users\badto\osrs-telemetry\bot_runs\20260608_100633_loaded_scene_recovery\recovery_summary.json`
- `C:\Users\badto\osrs-telemetry\bot_runs\20260608_100633_loaded_scene_recovery\latest_recovery_state.json`

Latest recovery summary:

| Field | Result |
| --- | --- |
| status | `unsafe` |
| recovery failure class | `disconnected_loop` |
| loadedSceneVerified | false |
| blocker | `disconnected_loop` |
| raw blocker | `loaded_scene_not_verified` |
| final state | `disconnected_dialog` |
| clicked candidates | `disconnected_ok`, then saved-account `play_now` 8 times |
| world objects | 0 |
| client hot state | `LOGIN_SCREEN`, stale |

Final recovery state:

- `loadedSceneVerified`: false
- `gameState`: `LOGIN_SCREEN`
- `worldModelObjectTotal`: 0 during disconnected proof
- `screenClassification`: `login_screen_or_disconnected_dialog`
- startup stage: `blocked_user_login_required` / disconnected retry loop
- exact recovery blocker: `disconnected_loop`

## Phase Results

| Phase | Result | Notes |
| --- | --- | --- |
| Woodcutting | not reached | Bot stopped before action because loaded scene was not ready. |
| Inventory full | not reached | No live loop ran. |
| Route to bank | not reached | No route action ran. |
| Banking/deposit | not reached | No bank action ran. |
| Route to trees | not reached | No route action ran. |
| Resume cutting | not reached | No loop resume evidence. |
| Interruption/combat | not reached | The only interruption was client disconnected. |

## Fixes Made

- `bot_eval_runner.py` live readiness now blocks stale/login/disconnected hot client state even when the wrapper file timestamp is fresh.
- `bot_eval_runner.py` live action can accept an explicit `sessions_root` for isolated tests.
- `execute_next_action.py` now focuses RuneLite before RuneLite-window pointer calibration and before live action.
- `bot_eval_runner.py` live executor command now passes `--focus-runelite` and a bounded action-loop timeout.
- `bot_eval_runner.py` now supports `--auto-recover-loaded-scene` and writes `recovery_attempts.jsonl` plus `recovery_summary.json` into the live run folder before real action execution.
- `liveness_recovery_core.py` now escalates `disconnected_loop` to a bounded Start Game relaunch using the same command path as the Simple Mode UI.
- Recovery artifacts now include `relaunchRequired`, `relaunchAttempted`, `startGameCommandSource`, `relaunchResult`, and `loadedSceneAfterRelaunch`.
- Tests cover the disconnected/login readiness blocker and RuneLite calibration focus behavior.

## Click Planner Usage

The click planner produced advisory Tree / Chop down plans from stale context, but readiness now overrides that correctly. Planner PASS is not enough to allow a click when loaded-scene proof fails.

## Remaining Blocker

The real blocker is external client state: RuneLite remains disconnected after safe
recovery clicks. The bot should not run until the client reaches a verified
loaded scene.

The repaired recovery state machine classifies this as `disconnected_loop`, not a
woodcutting-loop decision problem. Current proof still says `LOGIN_SCREEN`, stale
hot client sample, and zero world objects.

Recovery now escalates that exact loop through the Start Game relaunch path. The
bot loop should still send zero woodcutting actions unless the relaunch produces
current loaded-scene proof.

Next target command after recovery/relaunch can prove a loaded scene:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 180 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

## 2026-06-08 Real Command After Loaded Scene Recovery

The loaded-scene blocker was repaired enough to start the real action command.
No dry-run fallback was used.

Recovery command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
```

Recovery result:

| Field | Result |
| --- | --- |
| recovery folder | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_103540_loaded_scene_recovery` |
| status | `loaded_scene_ready` |
| loaded scene achieved | yes |
| final hot game state | `LOGGED_IN` |
| world objects | 7729 |
| latest tick | 191 |

Real live-action command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

Final run folder:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_103728_live_woodcutting_loop`

Linked recording:

`C:\Users\badto\osrs-telemetry\recordings\20260608_103730_live_woodcutting_loop_20260608_103729`

Run result:

| Field | Result |
| --- | --- |
| final status | `WARN` in the original artifact, now classified by the runner as `FAIL` for future runs with this pattern |
| readiness | PASS |
| loaded scene | PASS |
| record everything | started and analyzed |
| duration | 1236.688 seconds |
| bot actions sent | 0 |
| live input executed by loop | false |
| Arduino path | connected, identified, armed, stopped, disarmed safely |
| loop complete | no |
| executor reason | `max_runtime_reached` |
| executor lifecycle reason | `no_executable_action` |

The bot did not click the game world. The Arduino backend was armed, but the
executor never received or produced an executable action candidate:

| Executor field | Result |
| --- | --- |
| `candidatesEvaluated` | 0 |
| `proposedActions` | 0 |
| `actionsAttempted` | 0 |
| `actionsExecuted` | 0 |
| lifecycle state | `blocked` |
| lifecycle last action | `wait_for_context` |
| lifecycle reason | `no_executable_action` |

Recording analysis from the linked recording:

| Analyzer | Result |
| --- | --- |
| woodcutting lifecycle | WARN, `tree_available`, no fresh Chop down click |
| inventory | Logs 10 -> 10, free slots 18 -> 18 |
| tree candidates | 35 unique tree targets observed |
| interruption lifecycle | WARN, combat/hostile NPC evidence, task not resumed |
| combat damage summary | WARN, primary opponent Mugger, 2 ambiguous hitsplats, HP 11 -> 11 |
| route/banking/deposit | not reached |

## Current Proven Blocker

The disconnected/login recovery blocker is no longer the active blocker for the
latest real run. The current blocker is:

`live_executor_no_executable_action`

The live scene was loaded and telemetry was fresh enough to pass readiness, but
the executor stayed in `wait_for_context` until max runtime. The next integration
task should fix the live context/action-candidate handoff or interruption
recovery decision so the executor can either attempt a valid action or stop
quickly with a phase-specific blocker.
## 2026-06-08 Autonomous Live Attempt Update

Loaded-scene recovery succeeded and real Arduino-backed live actions were sent. The latest autonomous run sequence reached the Lumbridge Castle/bank approach with a full log inventory but did not complete the full loop.

Most recent run folders:

- `bot_runs\20260608_160159_live_woodcutting_loop`
- `bot_runs\20260608_160916_live_woodcutting_loop`

Most recent linked recording:

- `recordings\20260608_160958_live_woodcutting_loop_20260608_160958`

Current blocker:

- `route_waypoint_arrived_but_route_state_stale`
- The bot reaches an approach waypoint around `3203,3237,0`, then route/pathing context continues to propose the current player tile.

Fixes made in this pass:

- Route stability now suppresses arrived repeated waypoints and bounds reobserve attempts.
- Non-click hover/volatile navigation skips can retry when `nextActionAllowed=true`.
- Proposal player-location parsing now recognizes live `playerWorldPosition` and related fields.
- Route waypoint selection can skip an arrived/current tile when forward path tiles exist.

Next validation:

- Run one more real live loop or replay fixture from `bot_runs\20260608_160916_live_woodcutting_loop` to verify the next proposal is a forward path tile or bank/stair interaction, not the current player tile.

## Castle Approach / Wrong Ladder / Camera Integration Failure

Updated: 2026-06-08 18:31 local.

The bad live route/camera/action handoff was reproduced and narrowed in two real action reruns. No dry-run was used as the final outcome.

| Field | Result |
| --- | --- |
| proof run | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_173455_live_woodcutting_loop` |
| patched validation run | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_182050_live_woodcutting_loop` |
| latest bounded run | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_182824_live_woodcutting_loop` |
| latest linked recording | `C:\Users\badto\osrs-telemetry\recordings\20260608_182920_live_woodcutting_loop_20260608_182919` |
| route phase | `route_to_bank` / `navigate_to_service` with full logs in bot context |
| expected template | `woodcutting_area_to_bank` |
| expected next route segment after approach | bank/castle Staircase or bank-service segment |

Failure timeline:

- The pre-patch live run selected a visible `Ladder` near the general-store/castle wall area as `interact_service_route_object`.
- That Ladder was self-certified by `plugin_snapshot_route_transition_visible` and was not proven to be on the `woodcutting_area_to_bank` template segment.
- The route template expects `Climb-up Staircase` for the bank route plane transition, with an allowed `Trapdoor/Climb-down` variant only when it satisfies the same route transition. A generic nearby Ladder is not enough.
- Camera readiness did not produce an executable camera action, so a wrong visible object was clicked instead of requiring the correct route target.

Fixes applied:

- Route-object candidates now carry route validation evidence: `routeCorridorMatch`, `routeProgressScore`, `routeCandidateValidation`, and `cameraReadiness`.
- Plugin-snapshot route-transition objects must match explicit route step identity or corridor evidence. Generic Ladder/Stepladder objects are rejected with reasons such as `route_object_not_on_expected_segment`, `wrong_building_or_wrong_area`, and `unrelated_route_object`.
- Immediate reverse plane-transition oscillation is blocked before input.
- Fresh live route waypoints from `local_frontier_waypoint` can pass readiness even if the plugin snapshot endpoint has a transient timeout; object clicks still fail closed when the snapshot is needed.
- When live position proves the route waypoint/destination is already reached, stale compact route context no longer reverses to an earlier tile. It blocks with `blocked_route_waypoint_arrived_needs_next_segment`.

Validation results:

| Run | Result |
| --- | --- |
| `20260608_182050_live_woodcutting_loop` | Sent 2 Arduino-backed actions. The general-store Ladder was not clicked again. The run stopped on `route_oscillation_detected` after stale pathing alternated between `3203,3238,0` and `3207,3238,0`. |
| `20260608_182824_live_woodcutting_loop` | Sent 1 Arduino-backed action to `3203,3238,0`. The bot did not click the Ladder and did not reverse to `3207`; it stopped on `blocked_route_waypoint_arrived_needs_next_segment` / pre-action readiness. |

Camera/action integration result:

- Camera readiness is now recorded in candidate explanations.
- The bot no longer clicks an unrelated visible route object just because the intended route/stair target is not available.
- No camera adjustment was executed in these reruns; the remaining blocker is route-state/session freshness and next-segment exposure, not camera input itself.

Current blocker:

`route_next_segment_missing_after_arrived_waypoint`

The bot reaches the Lumbridge Castle west approach around `3203,3238,0`, but the live compact route context still describes the same arrived waypoint as the active navigation target. The next fix should refresh/rebind route context after loaded-scene recovery/Record Everything starts, or expose the next `woodcutting_area_to_bank` Staircase/bank-service segment when the approach waypoint is reached.

## Demonstrated Route Guide Consumption

Updated: 2026-06-08 evening.

The bot action-proposal layer now has a demonstrated route guide fallback before stale route monitor/template state. The guide is built from successful traversal recordings and written to:

- `route_guides\woodcutting_area_to_bank.route_guide.json`
- `route_guides\Bank_to_Woodcutting_area.route_guide.json`

For `woodcutting_area_to_bank`, the guide currently extracts 5 path points, the demonstrated `Climb-down` Trapdoor interaction at `3209,3216,0`, one plane change, and camera evidence before the route interaction.

For `Bank_to_Woodcutting_area`, the guide currently extracts 17 path points, demonstrated Staircase interactions, four plane-change records, and camera evidence from route recordings.

At the previously stuck `3203,3238,0` approach tile, action proposal now:

- marks the guide point as reached,
- skips proposing the current tile,
- advances toward the next demonstrated path point (`3208,3212,0`) when route context names `woodcutting_area_to_bank`,
- prefers a demonstrated interaction step over a cross-plane walk target when the player is near the interaction,
- rejects generic nearby Ladder/Staircase fallback unless it matches the demonstrated route step.

If live geometry for the demonstrated route interaction is missing, the bot now reports `route_guide_interaction_needs_live_target` instead of clicking an unrelated object.

## Route Guide Validation / Full-Inventory Fallback Fix

Updated: 2026-06-08 late evening.

The demonstrated route guide is now the primary route fallback for the live proposal layer:

- `woodcutting_area_to_bank` guide: PASS, 5 path points, 1 demonstrated interaction, 1 plane change.
- `Bank_to_Woodcutting_area` guide: PASS, 17 path points, 3 non-cancel demonstrated interactions, 4 plane-change records.
- The guide is loaded from `route_guides\*.route_guide.json` and exposed through `get_route_demonstration_guide()` / `get_route_guide_progress()`.

Additional live-safety fix:

- When the inventory is full, executor context fallback now rejects any rebuilt `select_resource_target` proposal. This closes the bug seen in `bot_runs\20260608_203556_live_woodcutting_loop`, where a visible Tree was clicked after route context was rejected.

Latest rerun:

| Field | Result |
| --- | --- |
| command | `python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json` |
| run folder | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_204534_live_woodcutting_loop` |
| route actions sent | 0 |
| live input executed by bot loop | no |
| recovery result | FAIL before route execution |
| recovery blocker | `stale_login_screen_after_relaunch` |
| final hot game state | `LOGIN_SCREEN` |
| loaded scene verified | false |

Route validation status:

- The route guide code path has unit and focused executor coverage.
- Earlier real runs proved the guide can advance past stale route waypoints and avoid the wrong general-store Ladder.
- The latest route validation rerun could not reach action selection because the client returned to the login/disconnected surface during loaded-scene recovery.

Next live task:

- Restore loaded-scene telemetry, then rerun the same real action command.
- Success criterion remains: pass the castle approach using demonstrated guide progress, reach bank/deposit, or stop quickly on `route_guide_interaction_needs_live_target` / `target_not_visible_camera_adjust_needed`; do not repeat current tile and do not click unrelated Ladder/Staircase objects.

## Launch / Attach Correction

Updated: 2026-06-08 late evening.

The live runner now distinguishes a process launch from loaded-scene proof. The
current Start Game command:

```powershell
cmd /c .\gradlew.bat --no-daemon run
```

is classified as `dev_gradle_run`. It may launch a RuneLite/dev client, but it
is not considered an authenticated game start unless the client produces current
loaded-scene telemetry. If a dev launch reaches or remains at `LOGIN_SCREEN`,
the recovery blocker is `dev_launch_not_loaded`.

The live runner now prefers an existing loaded client through readiness proof,
then recovery, then an authenticated launcher command if configured. If no
authenticated path exists, the real live command can enter
`wait_for_manual_loaded_scene` and continue automatically after
`loadedSceneVerified=true`.

Recommended command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --wait-for-manual-loaded-scene --manual-loaded-scene-timeout-seconds 600 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

Latest command status:

| Field | Result |
| --- | --- |
| run folder | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_213714_live_woodcutting_loop` |
| command mode | real live action command, manual-loaded-scene wait enabled |
| recovery result | failed closed before gameplay |
| recovery blocker | `logged_in_without_scene` |
| final hot state | `LOGIN_SCREEN` |
| manual wait | active, polling for `loadedSceneVerified=true` |
| bot actions sent | 0 |
| route guide tested | no, scene is not loaded |

The previous readiness bug that allowed stale tick-only telemetry into executor
pre-action checks is fixed. Route guide validation should resume only after the
manual wait sees current loaded-scene proof.

## 2026-06-09 Update: Input Geometry Blocker

The next live-action gate is now input geometry, not woodcutting route logic.
The live runner and executor use `input_control\input_geometry.py` to verify
current RuneLite window/client/canvas bounds and screen/client/canvas transforms
before any gameplay click can reach the input backend.

Latest geometry command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Latest result:

| Field | Result |
| --- | --- |
| geometry status | FAIL |
| blocker | `input_geometry_stale` |
| inputGeometryPass | false |
| live loop run | no |
| bot actions sent | 0 |
| live gameplay input | not executed |
| foreground window | `Bot script - API vs Sidecar in OSRS - Google Chrome` |
| RuneLite window matched | false |
| context/snapshot endpoints | 8890/8893 refused connections |
| stale canvas source | `C:\Users\badto\.osrs-telemetry\sessions\2026-06-09_10-04-17\interaction_geometry\live\live_baseline_state.json` |

The stale file source still has a plausible canvas rect
(`x=146,y=110,width=1229,height=868`) and DPI scale (`1.75`), but it is not
current enough to permit live input. The runner did not fall back to dry-run and
did not start the woodcutting loop.

Exact next sequence:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Then, only if that returns `inputGeometryPass=true`:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## 2026-06-09 Update: Return Staircase Hover/Menu Guard

The plane-1 return-route Staircase blocker is now diagnosed and guarded in code.

Evidence source:

```text
C:\Users\badto\osrs-telemetry\bot_runs\20260609_135357_live_woodcutting_loop
```

Diagnosis:

- Expected target was `Staircase` object `16672` at `3204,3229,1`, expected action `Climb-down`.
- The planned point was around screen `535,347`, canvas `241,168`.
- The correct route object was live and on-screen, but `Climb-down` could be a lower menu row under generic `Climb`.
- When the menu-open fallback failed, retries stayed on the same point instead of failing quickly with route-target evidence.
- A follow-up run showed a worse variant: stale plane-1 route state was still proposed after the player was on plane 0, and a different Staircase (`56230`) with top action `Climb-up` was accepted too loosely.

Fix:

- Route hover matching now requires the expected directional action, not a substring match from generic `Climb`.
- When an expected object id is known, hover/menu samples with a different object id are rejected.
- Generic route dialogue opener matching treats `Climb` as exact.
- Route transition proposals with target plane different from current player plane now fail closed before hover/click.
- Repeated route target hover/menu failures now record attempted points and observed menu rows, then stop as `repeated_route_target_hover_failure`.

Latest reruns:

| Field | `20260609_143211_live_woodcutting_loop` | `20260609_144521_live_woodcutting_loop` |
| --- | --- | --- |
| command | real live action | real live action |
| dry-run/no-input | no | no |
| geometry | PASS | PASS before run |
| actions executed | 2 | 0 |
| Climb-down object 16672 | succeeded | not attempted |
| live input executed | yes | no gameplay input |
| final blocker | `candidate_data_stale` after stale route/resource context | `no_executable_action` from wrong-floor state |
| final location | `3206,3229,1` | `3206,3229,1` |

Current next task:

```text
Recover or route from wrong-floor state 3206,3229,1 back to a valid resource/return-route context, then rerun the same real command. Do not loosen route-object matching.
```

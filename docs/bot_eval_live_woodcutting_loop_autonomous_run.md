# Live Woodcutting Loop Autonomous Run

Updated: 2026-06-08

## Summary

Codex recovered RuneLite into a loaded scene and ran the real live woodcutting-loop command with action execution enabled. This was not a dry run. The bot sent Arduino-backed live clicks and Record Everything captured linked recordings for the attempts.

The loop did not complete within the five-attempt budget. The current proven blocker is route advancement near the Lumbridge Castle bank approach: the bot reaches a route waypoint, but the service-route/pathing context keeps proposing the arrived tile instead of advancing to the next bank route phase.

## Commands Used

Recovery/live command shape:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

## Live Attempts

| Attempt | Run folder | Linked recording | Result | Live actions |
| --- | --- | --- | --- | --- |
| 1 | `bot_runs\20260608_154205_live_woodcutting_loop` | `recordings\20260608_154217_live_woodcutting_loop_20260608_154216` | WARN, route hover safety skip | 1 |
| 2 | `bot_runs\20260608_154654_live_woodcutting_loop` | `recordings\20260608_154656_live_woodcutting_loop_20260608_154655` | WARN, hover confirm timeout | 1 |
| 3 | `bot_runs\20260608_155101_live_woodcutting_loop` | `recordings\20260608_155103_live_woodcutting_loop_20260608_155103` | WARN, repeated castle approach waypoint | 8 |
| 4 | `bot_runs\20260608_160159_live_woodcutting_loop` | `recordings\20260608_160239_live_woodcutting_loop_20260608_160239` | WARN, `route_waypoint_arrived_but_route_state_stale` | 2 |
| 5 | `bot_runs\20260608_160916_live_woodcutting_loop` | `recordings\20260608_160958_live_woodcutting_loop_20260608_160958` | WARN, `route_waypoint_arrived_but_route_state_stale` | 1 |

## Loaded Scene

Loaded scene recovery was achieved. Latest runs had:

- `loadedSceneVerified=true`
- hot game state `LOGGED_IN`
- world object count present
- Record Everything started
- Arduino-backed live clicks executed

The previous disconnected/login issue was real, but it was not the final blocker in these attempts.

## Phase Results

- Woodcutting inventory state: full inventory of logs was detected at run start.
- Route to bank: partially successful; the bot walked from the woodcutting area toward Lumbridge Castle/bank.
- Banking/deposit: not reached.
- Route back to trees: not reached.
- Resume chopping: not reached.
- Interruption/combat: not the blocker in these attempts.

## Candidate/Action Findings

- The earlier zero-candidate failure was fixed before these attempts; live candidates/actions now exist.
- The bot produced `navigate_to_service` candidates and sent real clicks through the live action path.
- The executor correctly refused unsafe/repeated route clicks instead of blindly clicking.
- The repeated blocker is now specific and bounded: route pathing still proposes an already-arrived waypoint around `3203,3237` / `3204,3237`.

## Fixes Made

- Re-armed Arduino live input before each action so safe skips do not leave the backend unarmed.
- Treated loaded-scene recovery as successful when hot loaded-scene proof exists even if daemon rebind reporting is noisy.
- Allowed retry for non-click navigation skips such as volatile hover and hover-confirm timeout when `nextActionAllowed=true`.
- Downgraded stale route-transition readiness when plugin snapshot route projection is otherwise executable.
- Added route stability handling so an arrived repeated waypoint becomes `route_waypoint_arrived_advance_required`, then a bounded `route_waypoint_arrived_but_route_state_stale` blocker.
- Updated route waypoint selection to skip the current/arrived player tile when pathing still has forward tiles.
- Updated proposal player-location parsing to recognize live `playerWorldPosition`, `playerWorldTile`, `currentPlayerTile`, and top-level player world coordinates.

## Current Blocker

The service-route/pathing context still does not advance cleanly from the Lumbridge Castle approach toward the bank/stair/bank interaction phase. In the latest run, the first click moved to `3203,3237`, then route context kept proposing `3203,3237` as the next waypoint. The executor stopped quickly after bounded reobserve attempts.

This is repo-fixable, but the five live-attempt budget was reached before validating the final player-location parsing patch with another live run.

## Checks

Passed:

- `python -m py_compile telemetry-viewer\bot_eval_runner.py telemetry-viewer\execute_next_action.py telemetry-viewer\context_service.py telemetry-viewer\liveness_recovery_core.py telemetry-viewer\task_script_api.py telemetry-viewer\knowledge_fabric.py telemetry-viewer\input_control\executor.py telemetry-viewer\input_control\action_proposal.py telemetry-viewer\input_control\click_planner.py`
- `python telemetry-viewer\tests\test_action_proposal.py ActionProposalTest.test_adaptive_service_route_skips_arrived_current_waypoint ActionProposalTest.test_adaptive_service_route_uses_live_player_world_position_to_skip_arrived_waypoint`
- `python telemetry-viewer\tests\test_input_control_executor.py RecoveryResultTest.test_retryable_navigation_safety_skip_does_not_end_loop InputControlExecutorTest.test_route_stability_reached_waypoint_advances_instead_of_clicking_again InputControlExecutorTest.test_route_stability_allows_repeat_after_navigation_progress`
- `python telemetry-viewer\tests\test_bot_eval_runner.py`
- `python telemetry-viewer\tests\test_arduino_live_input_policy.py`
- `python telemetry-viewer\tests\test_task_script_api.py`
- `python telemetry-viewer\tests\test_knowledge_fabric.py`
- `python telemetry-viewer\tests\test_context_service.py`
- `python telemetry-viewer\tests\test_human_click_planning.py`
- `python telemetry-viewer\tests\test_project_knowledge.py`
- `python telemetry-viewer\tests\test_telemetry_ui.py`
- `python telemetry-viewer\telemetry_ui.py --check`
- `python telemetry-viewer\update_project_knowledge.py --check`

Failed/timed out:

- `python telemetry-viewer\tests\test_input_control_executor.py` timed out after 604 seconds when run as the full file. Focused tests for the changed executor paths passed.

## Next Task

Run one more real live loop after the player-location parsing patch, or first add a small replay/unit fixture using the latest route context showing:

- player at `3203,3237`
- next waypoint also `3203,3237`
- predicted path contains `3202,3237`, `3201,3236`, `3200,3235`, ...

Success criterion: proposal selects the next forward path tile or an explicit bank/stair interaction candidate, not the current player tile.

## 2026-06-08 Evening Route/Candidate Follow-Up

Two additional real live-action reruns were performed after the wrong-ladder investigation.

| Run folder | Linked recording | Result | Live actions |
| --- | --- | --- | --- |
| `bot_runs\20260608_182050_live_woodcutting_loop` | `recordings\20260608_182141_live_woodcutting_loop_20260608_182140` | WARN, `route_oscillation_detected` after 3203/3207 waypoint alternation | 2 |
| `bot_runs\20260608_182824_live_woodcutting_loop` | `recordings\20260608_182920_live_woodcutting_loop_20260608_182919` | FAIL, bounded `blocked_route_waypoint_arrived_needs_next_segment` / pre-action readiness | 1 |

What improved:

- The unrelated general-store/castle-wall Ladder was not clicked again.
- Route-transition candidates now require explicit route segment/corridor identity.
- Fresh live route waypoints are allowed to pass readiness without treating a transient plugin-snapshot timeout as fatal.
- Stale arrived-waypoint route context no longer reverses to the old path start; it stops before input.

Current blocker:

- The live compact route context remains stale after reaching `3203,3238,0`. It does not advance to the next bank/castle Staircase or service segment.
- The latest stop is safe and fast, but the loop still cannot reach banking until route context/session freshness exposes the next segment.

Next task:

- Refresh/rebind daemon route context after loaded-scene recovery and Record Everything startup, then add route-template-aware next-segment inference for `woodcutting_area_to_bank` when the Lumbridge Castle west approach waypoint is reached.

## Demonstrated Route Guide Update

Updated: 2026-06-08 evening.

The live route proposal layer now consumes demonstrated route guides extracted from successful Record Everything traversal recordings before falling back to stale route monitor/template state.

Guide sources:

- `recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2`
- `recordings\20260606_121630_bank_to_WC`
- `recordings\20260607_104613_Woodcutting_area_to_bank`
- `recordings\20260606_201613_Bank_to_tree_area`

Written guides:

- `route_guides\Bank_to_Woodcutting_area.route_guide.json`
- `route_guides\woodcutting_area_to_bank.route_guide.json`

Extraction result:

- `woodcutting_area_to_bank`: PASS, 5 path points, 1 demonstrated interaction, 1 plane change. Includes the `Climb-down` Trapdoor step at `3209,3216,0` and a camera hint before the interaction.
- `Bank_to_Woodcutting_area`: PASS, 17 path points, 4 demonstrated interactions, 4 plane changes. Includes demonstrated Staircase interactions from the bank floor toward the tree area.

Current `3203,3238,0` behavior:

- A player already at `3203,3238,0` is no longer proposed that same tile as an executable route click.
- The guide resolver skips reached points and advances toward the next demonstrated `woodcutting_area_to_bank` point, currently `3208,3212,0`.
- When the next demonstrated step is a plane-transition interaction and no live target geometry is available, the action proposal creates an explicit `route_guide_interaction_needs_live_target` blocker instead of clicking a generic Ladder/Staircase.

Candidate trace additions:

- `routeGuideLoaded`
- `routeGuideName`
- `routeGuideProgress`
- `routeGuideSource`
- reached/skipped guide point details

Remaining validation:

- Rerun the real live loop and confirm the bot advances past `3203,3238,0` using the demonstrated guide or stops quickly on an explicit guide/interaction blocker.

## 2026-06-08 Demonstrated Route Guide Live Validation

Command used:

`python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json`

Code changes validated in this pass:

- Added `telemetry-viewer\route_demonstration.py`.
- Wrote `route_guides\woodcutting_area_to_bank.route_guide.json`.
- Wrote `route_guides\Bank_to_Woodcutting_area.route_guide.json`.
- Live action proposal now prefers demonstrated route-guide progress over stale route monitor waypoints.
- The executor now rejects a fresh `select_resource_target` fallback when inventory is full, so a visible Tree cannot win over the bank route phase.
- Navigation hover recovery can right-click/select a lower `Walk here` row when a Rat/Hans-style top hover blocks a route tile.

Live validation evidence:

| Run folder | Linked recording | Result |
| --- | --- | --- |
| `bot_runs\20260608_201220_live_woodcutting_loop` | `recordings\20260608_201259_live_woodcutting_loop_20260608_201259` | Route guide moved from `3201,3221` to `3201,3219`; then blocked on live route object readiness before the readiness source fix. |
| `bot_runs\20260608_202840_live_woodcutting_loop` | `recordings\20260608_202920_live_woodcutting_loop_20260608_202919` | Route guide selected next demonstrated point toward `3209,3216,0`; no general-store Ladder click occurred. |
| `bot_runs\20260608_203556_live_woodcutting_loop` | `recordings\20260608_203635_live_woodcutting_loop_20260608_203635` | Exposed a bad fallback: full inventory plus rejected route context still allowed a Tree click. This pass fixed that fallback. |
| `bot_runs\20260608_204534_live_woodcutting_loop` | none; recovery did not reach recording/action phase | Recovery failed before route execution: `stale_login_screen_after_relaunch`, final hot state `LOGIN_SCREEN`, `loadedSceneVerified=false`. |

Current `3203,3238,0` behavior:

- Guide progress skips the current/reached tile.
- `woodcutting_area_to_bank` advances toward the next recorded path point instead of reusing stale route state.
- If the next recorded step is a demonstrated interaction and live geometry is missing, the proposal returns `route_guide_interaction_needs_live_target`.
- If inventory is full, a fresh visible Tree proposal is rejected by executor fallback instead of being clicked.

Current blocker:

- The latest rerun did not validate the patched route path because loaded-scene recovery returned to `LOGIN_SCREEN` after relaunch. No bot route actions were sent in `20260608_204534_live_woodcutting_loop`.
- The next live attempt should first restore current loaded-scene telemetry, then rerun the same real action command to validate route-guide progress past the castle approach.

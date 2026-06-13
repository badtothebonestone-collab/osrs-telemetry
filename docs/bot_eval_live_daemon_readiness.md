# Live Daemon Readiness Smoke

Date: 2026-06-07

## Initial Result

Verdict: FAIL, live eval should not start yet.

The bot decision layer was not the blocker. The live readiness path failed before any live action could be considered because the daemon and snapshot HTTP endpoints did not answer within the bounded timeout, and the latest on-disk telemetry session was stale.

## Command

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live-smoke --duration 10 --no-input --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260607_214451_woodcutting_loop_live_smoke`

Artifacts:

- `bot_live_readiness.json`
- `bot_observation_trace.jsonl`
- `bot_action_trace.jsonl`
- `bot_eval_summary.json`

## Endpoint Checks

| Check | Endpoint | Timeout | Result |
| --- | --- | --- | --- |
| daemon status | `http://127.0.0.1:8890/status` | 0.75s | FAIL, timed out |
| daemon health | `http://127.0.0.1:8890/health` | 0.75s | WARN, timed out |
| snapshot health | `http://127.0.0.1:8893/health` | 0.75s | WARN, timed out |

## Readiness

| Field | Result |
| --- | --- |
| context service reachable | false |
| telemetry fresh | false |
| latest tick from disk | 383 |
| latest export sequence | not available |
| current area from disk | woodcutting |
| route templates loaded | true |
| task_script_api state readable | true |
| input backend required | false, no-input smoke mode |
| live eval can start | false |

Latest live session inspected:

`C:\Users\badto\.osrs-telemetry\sessions\2026-06-07_20-10-04\interaction_geometry\live`

The latest live files were usable for a stale disk snapshot, including a Tree candidate and route templates. They were not fresh enough to allow a live eval.

## Root Cause

`daemon.status` was the wrong readiness gate.

The first smoke used `/status`, which builds a heavier diagnostic payload. With a 0.75s readiness timeout, `/status` timed out and the client disconnected while the service was still trying to respond. Direct `/health` checks are fast enough for readiness and avoid that self-inflicted timeout.

The snapshot endpoint on `8893` was also unavailable during the first smoke because the RuneLite/plugin launch flow was not running.

## Behavior Added

The evaluator now writes a bounded `bot_live_readiness.v1` object with:

- PASS/WARN/FAIL status
- daemon/context reachability
- snapshot reachability
- telemetry freshness and latest tick
- current area when available
- route template availability
- task script readability
- input-backend requirement status
- warnings, errors, and root cause

Live smoke is no-input and dry-run by default. It writes observations and readiness artifacts even when live startup is blocked.

## Follow-up

The context readiness check now uses `/health` as the bounded endpoint and skips `/status` during live readiness. `/status` remains diagnostic-only.

The live stack was then started:

- Context service on `8890`
- RuneLite/plugin snapshot endpoint on `8893`
- Live target processor on the newest session

PASS smoke:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live-smoke --duration 10 --no-input --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081231_woodcutting_loop_live_smoke`

Initial PASS bounded dry-run:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --duration 5 --dry-run-actions --max-actions 20 --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081421_woodcutting_loop_live_dry_run`

Final readiness:

| Field | Result |
| --- | --- |
| context service reachable | true |
| snapshot service reachable | true |
| telemetry fresh | true |
| route templates loaded | true |
| task_script_api state readable | true |
| live eval can start | true |
| input commands sent | 0 |

The current click plan remains advisory/WARN because no live target/readiness/hover evidence was supplied to the planner during the no-input smoke. That is expected and safe.

The loaded-scene recovery check then advanced the client from the saved-account
Play Now screen into a loaded scene. After the loaded scene was verified, a final
bounded dry-run was run:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --duration 10 --dry-run-actions --max-actions 20 --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_081923_woodcutting_loop_live_dry_run`

Final readiness:

| Field | Result |
| --- | --- |
| readiness status | PASS |
| context service reachable | true |
| snapshot service reachable | true |
| telemetry fresh | true |
| game client loaded | true |
| loaded scene ready | true |
| latest tick | 118 |
| route templates loaded | true |
| task_script_api state readable | true |
| live eval can start | true |
| input commands sent | 0 |

The final click plan was advisory PASS for `Tree / Chop down` with target
geometry available. It still did not execute a click.

## Next Command

The service readiness blocker is cleared. The next validation step is still dry-run unless explicitly enabling controlled input:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --duration 30 --dry-run-actions --max-actions 20 --json
```

Do not run live input until readiness is PASS and the dry-run trace is sane.

## 2026-06-08 Update: Disconnected Client Blocker

The later real-action run exposed a stricter readiness issue. The context and
snapshot endpoints were reachable, and the top-level live status file was being
refreshed, but RuneLite itself was visibly disconnected.

Current proof:

| Field | Result |
| --- | --- |
| RuneLite screen | disconnected dialog / login screen |
| loaded scene verified | false |
| hot client game state | `LOGIN_SCREEN` |
| hot client sample | stale |
| world objects in disconnected proof | 0 |
| live eval can start | false |

The loaded-scene recovery path was run through Arduino input. It clicked the
recognized `disconnected_ok` and saved-account `play_now` controls, but the
client returned to the disconnected/login surface and never produced loaded-scene
proof.

Readiness now fails closed on this exact condition with:

```text
rootCause=loaded_scene_not_ready
loadedSceneBlockers=client_tick_hot_game_state_login_screen, client_tick_hot_stale_age_ms_*
```

This prevents the bot from starting from stale `LOGGED_IN` baseline crumbs while
the real client is disconnected.

## 2026-06-08 Auto-Recovery Live Action Attempt

The real live-action runner now supports an automatic loaded-scene recovery
prelude:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --liveness-max-total-seconds 180 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

Latest output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_093432_live_woodcutting_loop`

Result:

| Field | Result |
| --- | --- |
| recovery status | `unsafe` |
| loaded scene achieved | false |
| recovery blocker | `loaded_scene_not_verified` |
| final screen state | `disconnected_dialog` |
| safe controls clicked | `disconnected_ok`, saved-account `play_now` |
| bot actions sent | 0 |
| live input executed by bot loop | false |

This is now documented as a client loaded-scene blocker, not a bot decision
failure. The next target remains the real live-action command above after the
client can stay logged in; do not downgrade this task to dry-run unless that is
explicitly requested.

## Loaded Scene Recovery Failure Analysis

Latest repaired recovery command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 180.0 --liveness-max-attempts-per-state 3
```

Artifact folder:

`C:\Users\badto\osrs-telemetry\bot_runs\20260608_100633_loaded_scene_recovery`

| Step | Detected state before | Recovery action | Target / evidence | Expected next state | Observed result |
| --- | --- | --- | --- | --- | --- |
| 1 | `disconnected_dialog` | `disconnected_ok` | `{x:1432,y:591}`, inside RuneLite window, validation `PASS` | login/saved account | safe click acknowledged, recovery advanced to saved-account Play Now surface |
| 2 | saved-account surface | `play_now` | `{x:1432,y:483}`, inside RuneLite window, validation `PASS` | loading or click-here-to-play | safe click acknowledged, but no loaded-scene proof appeared |
| 3 | repeated Play Now surface | repeated bounded `play_now` | same safe target, Arduino path | loading / `LOGGED_IN` / scene proof | max startup clicks reached; final current state returned to `disconnected_dialog` |

Final proof:

| Field | Result |
| --- | --- |
| recovery status | `unsafe` |
| recovery failure class | `disconnected_loop` |
| raw blocker | `loaded_scene_not_verified` |
| loaded scene verified | false |
| final state | `disconnected_dialog` |
| hot client game state | `LOGIN_SCREEN` |
| hot client freshness | stale |
| world objects | 0 |
| loading observed | no current proof |
| `LOGGED_IN` observed | no current proof |
| scene/world/player evidence | absent |
| bot loop executed | no |

Exact blocker:

`disconnected_loop`: the existing recovery path safely clicked the disconnected OK
button and the saved-account Play Now button through Arduino, but RuneLite stayed
on or returned to the disconnected/login surface. The bot loop correctly remains
blocked until current hot client state is logged in and `loadedSceneVerified` is
true.

## 2026-06-08 Update: Relaunch Escalation

Recovery no longer keeps repeating the disconnected/login button loop. When
bounded in-client recovery returns `disconnected_loop`, the recovery controller
now marks `relaunch_required`, resolves the same Start Game command used by the
Simple Mode UI, launches it once, and waits for current hot telemetry to prove a
loaded scene.

Start Game command source:

```text
discovered_gradle_wrapper / UI game_launch_command
```

Default command:

```powershell
cmd /c .\gradlew.bat --no-daemon run
```

New recovery artifact fields:

| Field | Meaning |
| --- | --- |
| `disconnectedLoopDetected` | In-client safe clicks returned to disconnected/login. |
| `relaunchRequired` | Recovery escalated beyond button clicks. |
| `relaunchAttempted` | Start Game relaunch was actually started. |
| `startGameCommandSource` | Where the relaunch command came from. |
| `relaunchResult` | Process launch result and PID if available. |
| `loadedSceneAfterRelaunch` | Current hot telemetry proved a loaded scene after relaunch. |
| `finalHotGameState` | Final current hot client game state. |
| `finalLoadedSceneVerified` | Final loaded-scene proof gate. |

Recovery still fails closed if relaunch cannot prove:

- hot client state is no longer `LOGIN_SCREEN`
- `loadedSceneVerified=true`
- current world/player/object data is available
- tick/export data is fresh

## 2026-06-08 Update: Recovery And Health Fallback Result

The bounded recovery command was rerun with the relaunch-capable recovery path:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3
```

First result:

| Field | Result |
| --- | --- |
| recovery outcome | loaded scene reached |
| final hot game state | `LOGGED_IN` |
| loaded scene verified | true |
| world objects | 7728+ |
| remaining blocker | `daemon_rebind_failed` |
| root cause | `/status` timed out even though `/health` answered |

The recovery path did its job: it got the client to a loaded scene. The remaining
bug was the readiness probe using the heavyweight `/status` endpoint after
recovery. The readiness path now uses `/health` as the bounded endpoint and keeps
`/status` diagnostic-only.

Second result after the health fallback:

| Field | Result |
| --- | --- |
| recovery folder | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_103540_loaded_scene_recovery` |
| recovery status | `loaded_scene_ready` |
| final hot game state | `LOGGED_IN` |
| loaded scene verified | true |
| world objects | 7729 |
| latest tick | 191 |
| bot loop allowed to start | yes, if live readiness also passes |

This closes the original disconnected/login click-loop blocker for this run. The
client was no longer stuck on the disconnected surface, and the real-action bot
command was allowed to proceed after the separate telemetry freshness check was
fixed to read endpoint `liveFreshness.liveFileAgeMillis`.

## 2026-06-08 Update: Launch Mode And Manual Loaded-Scene Wait

The Start Game command currently resolves to:

```powershell
cmd /c .\gradlew.bat --no-daemon run
```

That command is now classified as `dev_gradle_run`. It is useful for plugin/dev
launches, but it is not treated as an authenticated live-game recovery path. If
it starts a client that remains on `LOGIN_SCREEN` or cannot prove
`loadedSceneVerified=true`, recovery reports `dev_launch_not_loaded` instead of
looping relaunches or pretending gameplay can start.

Supported launch handling:

- `attach_existing_loaded_client`: preferred when fresh loaded-scene telemetry already exists.
- `launcher_authenticated`: used when an authenticated launcher command is configured.
- `dev_gradle_run`: allowed once, but must still prove a loaded scene.
- `wait_for_manual_loaded_scene`: polls live telemetry until the user-restored client proves a loaded scene.

Use this real-action command when an authenticated launch path is not configured:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --duration 1200 --max-actions 300 --record-everything --analyze-after --require-readiness-pass --auto-recover-loaded-scene --wait-for-manual-loaded-scene --manual-loaded-scene-timeout-seconds 600 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --arduino-port COM6 --json
```

The manual wait does not dry-run and does not send gameplay input. It only waits
for current hot telemetry to show a real loaded scene, then resumes the same live
action path.

## 2026-06-13 Update: Authenticated Live Start Path

`start_game_command.py` now separates development launch from live launch:

- `devStartCommand`: `cmd /c .\gradlew.bat --no-daemon run`
- `liveStartCommand`: discovered Jagex Launcher quick launch when no live command is configured
- resolved live command: `"C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite`
- live launch mode: `jagex_launcher_runelite_quick_launch`

Recovery and bot preflight must prefer an already-loaded client or the live
Jagex quick-launch path. `dev_gradle_run` remains available for plugin/dev
testing, but it is no longer an acceptable authenticated live bot login path.
If no live path is available, the blocker is `authenticated_live_start_missing`;
if a dev Gradle relaunch is explicitly used and still does not load a scene, the
blocker remains `dev_launch_not_loaded`.

Live validation after the readiness correction:

| Field | Result |
| --- | --- |
| run folder | `C:\Users\badto\osrs-telemetry\bot_runs\20260608_213714_live_woodcutting_loop` |
| recovery status | `unsafe` |
| recovery blocker | `logged_in_without_scene` |
| final hot state | `LOGIN_SCREEN` |
| loaded scene verified | false |
| manual wait active | yes |
| latest manual wait reason | `loaded_scene_not_ready: current scene proof unavailable` |
| live gameplay actions | not sent |

The earlier false positive where readiness accepted stale tick-only telemetry was
fixed: live readiness now requires game-state/player/object scene proof and no
longer treats a tick counter by itself as a loaded scene.

## 2026-06-09 Update: Input Geometry Gate

The live runner now has an explicit input geometry check:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

The check uses `input_control\input_geometry.py` as the canonical resolver. It
can read fresh plugin/file geometry, fall back to current Win32 RuneLite
window/client geometry, attempt a bounded focus/restore repair, and report exact
blockers such as `input_geometry_focus_needed`, `input_geometry_canvas_missing`,
`input_geometry_transform_missing`, or `input_geometry_stale`.

Latest result: FAIL before gameplay input. The context and snapshot endpoints on
8890/8893 refused connections, no current RuneLite window was matched, and the
only usable canvas/client geometry came from a stale file-session baseline.

The active blocker is:

```text
input_geometry_stale
```

Do not start the live action loop until `--check-input-geometry --json` returns
`inputGeometryPass=true`.

## 2026-06-13 Update: Visible Button Recovery Before Manual Login

Loaded-scene recovery now treats visible safe login/play/disconnect controls as
mandatory recovery evidence before any manual-login blocker is reported.

Current behavior:

- `liveness_recovery_core.py` owns the loaded-scene state machine.
- `run_runelite_bootstrap.py` owns visible button detection and guarded clicks.
- Safe buttons are `disconnected_ok`, `play_now`, `click_here_to_play`, and `continue`.
- A Start Game relaunch that lands on a visible safe surface now re-enters the
  canonical visible-button ladder before failing.
- Repeated identical visible-button clicks stop quickly with
  `visible_button_no_transition`.
- Recovery artifacts include visible button scan, target validation, input path,
  and Arduino acknowledgement fields.

Latest command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_visible_button_recovery_rerun
```

Latest result:

| Field | Result |
| --- | --- |
| recovery status | `unsafe` |
| recovery blocker | `visible_button_no_transition` |
| visible buttons found | `disconnected_ok` |
| target validation | PASS, inside RuneLite safe click region |
| input path | HumanInputController / ArduinoHIDBackend |
| Arduino evidence | `OK MOUSE_UP` acknowledgements |
| loaded scene verified | false |
| final screen state | `disconnected_dialog` / `LOGIN_SCREEN` |
| live gameplay actions | not sent |

This is no longer a pre-recovery `manual_login_required` stop. The remaining
blocker is that the visible disconnected OK button can be clicked safely, but
the client does not transition into a loaded scene.

## 2026-06-13 Update: Visible Click Effect Proof

The recovery artifacts now prove the click sequence itself instead of relying on
the last Arduino acknowledgement. `recovery_attempts.jsonl` rows use
`recovery_attempt.v2` and include focused window, target rect/point, cursor
before/after, cursor distance to target, mouse move/down/up evidence, Arduino
ACKs, before/after visual state, before/after hot state, and the expected
transition result.

`disconnected_ok` now expects the disconnected dialog to disappear and a
login/play/loading/loaded state to appear. It does not require immediate
`loadedSceneVerified=true`.

Latest command:

```powershell
python telemetry-viewer\context_service.py --ensure-loaded-scene --daemon-url http://127.0.0.1:8890 --snapshot-url http://127.0.0.1:8893 --arduino-port COM6 --liveness-max-total-seconds 240 --liveness-max-attempts-per-state 3 --recovery-artifact-dir bot_runs\20260613_visible_click_proof_recovery_v3
```

Latest result:

| Field | Result |
| --- | --- |
| recovery status | `unsafe` |
| recovery blocker | `visible_button_no_transition` |
| visible button | `disconnected_ok` |
| foreground/focus | `RuneLite`, `windowFocusVerified=true` |
| target validation | `PASS`, target `{x:1362,y:1013}` |
| cursor proof | cursor at target, about `3.61px` from center |
| click sequence | `MOUSE_DOWN` and `MOUSE_UP` ACKs present |
| full click sequence | `true` |
| expected transition | `expected_transition_not_observed` |
| loaded scene verified | false |

The remaining blocker is now a genuine post-click no-transition: the
disconnected dialog stayed visible after bounded, focused, grounded clicks.

## 2026-06-13 Update: Wrong Target Fixed, Loaded Scene Recovered

Follow-up screenshot evidence showed the previous `visible_button_no_transition`
was caused by a wrong recovery target, not a dead OK button. The visible screen
was saved-account `Play Now`, but the stale disconnected detector selected a
lower-panel `disconnected_ok` candidate.

Recovery behavior now:

- visible recovery clicks save before/after/after-wait screenshots;
- attempts record selected RuneLite HWND, clicked HWND, and match result;
- repeated no-effect clicks try one direct `CLICK` alternate before stale/dead
  classification;
- saved-account `Play Now` suppresses stale disconnected false positives.

Validation:

| Field | Result |
| --- | --- |
| wrong-target artifact | `bot_runs\20260613_verified_click_no_transition_diagnosis` |
| corrected recovery artifact | `bot_runs\20260613_play_now_recovery_after_target_fix` |
| corrected buttons clicked | `play_now`, `click_here_to_play` |
| loaded scene verified | true |
| game state | `LOGGED_IN` |
| world objects | 1248 |
| input geometry check | PASS |
| real live run | `bot_runs\20260613_132900_live_woodcutting_loop` |
| live gameplay input | not executed |
| current blocker | `arduino_pointer_calibration_required` |

The next blocker is the Arduino pointer calibration gate, not login recovery or
the woodcutting route logic.

## 2026-06-13 Update: Arduino Calibration Passed, Route Hover Gate Reached

The canonical no-click Arduino pointer calibration passed on COM6 and wrote
`.osrs-telemetry\arduino_pointer_calibration.json`. `bot_eval_runner.py` now passes
that calibration path to `execute_next_action.py`, and summaries record the calibration
and movement-safety status.

Latest real live run:

| Field | Result |
| --- | --- |
| run | `bot_runs\20260613_134142_live_woodcutting_loop` |
| recording | `recordings\20260613_134150_live_woodcutting_loop_20260613_134150` |
| loaded scene | true |
| input geometry | PASS |
| Arduino pointer calibration | PASS |
| Arduino movement safety | PASS |
| live gameplay input | not executed |
| next blocker | `plane1_recovery_hover_option_mismatch` |

The run reached the strict plane-1 recovery candidate for `Staircase` object
`16672` at `3204,3229,1`. The executor moved/hovered safely, saw top menu
`Climb`, and refused to click because expected `Climb-down` was only a lower
menu entry. The next fix belongs to strict route-object menu selection, not
startup, geometry, or calibration.

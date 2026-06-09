# Live Input Geometry Readiness

Updated: 2026-06-09.

## Verdict

Status: FAIL, blocked before gameplay input.

The input geometry path is now explicit and fail-closed. The latest check did not
run the live woodcutting loop because the current machine state did not provide a
fresh RuneLite canvas/window geometry source.

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

## Loaded Scene Proof

| Field | Result |
| --- | --- |
| loadedSceneVerified | false |
| gameState from latest file source | `LOGGED_IN` |
| latestTick from latest file source | 766 |
| worldModelObjectTotal from latest file source | 498 |
| loaded-scene blockers | `client_tick_hot_game_state_login_screen`, `client_tick_hot_stale_age_ms_1698509` |

The file-session baseline still contains old loaded-scene-looking data, but the
live endpoints are down and the hot proof is stale. Readiness correctly treats
that as not currently loaded.

## Window And Canvas Geometry

| Field | Result |
| --- | --- |
| RuneLite foreground/focus state | no matched RuneLite window |
| foreground window title | `Bot script - API vs Sidecar in OSRS - Google Chrome` |
| RuneLite window matched | false |
| window title | unavailable from live Win32 lookup |
| hwnd | unavailable |
| window rect | unavailable |
| client rect | stale file source: x=135, y=83, width=1282, height=906 |
| canvas rect | stale file source: x=146, y=110, width=1229, height=868 |
| canvas width/height | stale file source: 1229 x 868 |
| viewport rect | stale file source: x=0, y=0, width=512, height=334 |
| geometry freshness | stale, 1699092 ms at the latest check |

The stale geometry came from:

`C:\Users\badto\.osrs-telemetry\sessions\2026-06-09_10-04-17\interaction_geometry\live\live_baseline_state.json`

## Coordinate Conversion

| Field | Result |
| --- | --- |
| screen-to-client/canvas conversion | available in stale file source only |
| client/canvas-to-screen conversion | available in stale file source only |
| screenToCanvasTransform | subtract canvas origin x=146, y=110 |
| canvasToScreenTransform | add canvas origin x=146, y=110 |
| DPI/scale | stale file source: x=1.75, y=1.75 |
| live Win32 conversion | unavailable because no RuneLite window was matched |

The transforms are structurally usable, but they are not current enough to allow
gameplay input.

## Input Backend

| Field | Result |
| --- | --- |
| executor geometry safety gate | implemented |
| planned point bounds check | blocks outside-canvas points before backend click |
| input backend used in this check | none, geometry check sends no gameplay input |
| Arduino/pointer calibration | not revalidated because geometry did not pass |

## Endpoint State

| Endpoint | Result |
| --- | --- |
| `http://127.0.0.1:8890/health` | refused connection |
| `http://127.0.0.1:8890/status` | refused connection |
| `http://127.0.0.1:8893/health` | refused connection |

Process and port inspection found no matching RuneLite/Java/Python telemetry
process and no listener on the expected telemetry ports at the time of the
check.

## Exact Blocker

Current blocker:

```text
input_geometry_stale
```

The older readiness result surfaced as `input_geometry_unavailable` because the
live readiness path was not consulting a canonical geometry resolver with
file-session fallback, bounded focus repair fields, and exact blocker codes.
That wiring bug is fixed. The current active blocker is stale source/current
environment state, not woodcutting logic or click planning.

## Fix Layer

| Layer | Status |
| --- | --- |
| readiness gate bug | fixed |
| canonical input geometry resolver | added in `input_control\input_geometry.py` |
| focus/window repair | added as bounded `repair_runelite_focus` path |
| coordinate conversion | validated through resolver fields |
| input backend safety | executor blocks unsafe points before click |
| stale source | still the current blocker |
| plugin/export | no new export required for this failure |

## Next Live Gate

Do not run the live woodcutting loop until this command returns
`inputGeometryPass=true`:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

After geometry passes, run the real live command without dry-run flags:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

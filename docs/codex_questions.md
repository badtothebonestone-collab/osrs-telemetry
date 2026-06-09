# Codex Questions / Blockers

Updated: 2026-06-09

## Current Blocker: Live Input Geometry Source Is Stale

Do not run the live bot loop or patch route/woodcutting/banking logic until this
gate passes.

### Exact blocker

`input_geometry_stale`

The current geometry check failed before live execution. This is an
environment/current-source blocker, not a route or task-logic blocker.

Latest command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Evidence from the failed check:

- Status: `FAIL`
- `inputGeometryPass=false`
- Loaded-scene proof: `loadedSceneVerified=false`
- Game state field present but stale: `gameState=LOGGED_IN`
- Source tick: `766`
- Stale loaded-scene blocker: `client_tick_hot_stale_age_ms_5284672`
- Geometry source: `file_session.baseline.inputGeometry`
- Geometry freshness: about `5285255 ms`
- Foreground window title: `Codex`
- RuneLite window matched: `false`
- Context endpoint `8890`: refused connection
- Snapshot endpoint `8893`: refused connection
- Local port check found no listener on `8890` or `8893`
- Local process/window check found no visible RuneLite/Java window title

### User/environment action needed

1. Open or restore RuneLite and load into the game world.
2. Make the RuneLite game window visible, not minimized, and focusable.
3. Start the telemetry stack so these endpoints respond:

```text
http://127.0.0.1:8890/health
http://127.0.0.1:8893/health
```

4. Rerun:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Only after input geometry passes should Codex run:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

## Repeated Blocker: Lumbridge Castle Bank Route Advancement

The live bot now recovers RuneLite, starts Record Everything, and sends real Arduino-backed actions. The current repeated blocker is not login and not zero candidates.

### Exact blocker

`route_waypoint_arrived_but_route_state_stale`

The bot reaches the Lumbridge Castle/bank approach route waypoint, but route/pathing context continues to propose the current player tile as the next `navigate_to_service` target.

Latest evidence:

- `bot_runs\20260608_160159_live_woodcutting_loop`
- `bot_runs\20260608_160916_live_woodcutting_loop`
- linked recording: `recordings\20260608_160958_live_woodcutting_loop_20260608_160958`
- latest repeated tile: `3203,3237,0`
- target route destination/path target: `3205,3232,0`
- predicted path still contains forward tiles after the current tile.

### What was tried

- Recovered loaded scene and confirmed `LOGGED_IN`.
- Ran real live bot loop with `--execute-actions`.
- Re-armed Arduino backend before each action.
- Allowed retry for non-click hover/volatile safety skips.
- Added route stability guard for arrived repeated waypoints.
- Added bounded reobserve before declaring route context stale.
- Added proposal logic to skip current/arrived waypoint when player tile is known.
- Added proposal parsing for live player fields such as `playerWorldPosition`.

### What is needed next

Run one more live validation after the latest player-location parsing patch, or build a replay fixture from `20260608_160916_live_woodcutting_loop` and confirm that the proposal layer now chooses the forward path tile rather than the current tile.

If it still repeats, inspect whether the live route/pathing source should advance from the walk segment to the staircase/bank interaction segment at the castle approach.

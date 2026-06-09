# Codex Questions / Blockers

Updated: 2026-06-08

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

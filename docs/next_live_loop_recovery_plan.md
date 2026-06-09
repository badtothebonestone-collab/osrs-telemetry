# Next Live Loop Recovery Plan

Date: 2026-06-09

This plan is intentionally short. Do not run the live loop until each preceding
gate passes.

## 1. Environment Preconditions

- RuneLite is already logged in and visible, or an authenticated Start Game path
  is configured.
- The dev Gradle launch path alone is not loaded-scene proof.
- Context service on `8890` is healthy.
- Plugin snapshot endpoint on `8893` is healthy.
- Loaded-scene proof is current: `loadedSceneVerified=true`, game state
  `LOGGED_IN`, fresh client tick, and world/player/object evidence present.
- Input geometry passes: current RuneLite window/client/canvas bounds and
  screen/client/canvas transforms are available.
- Arduino remains the live input path; do not switch to pyautogui/pydirectinput.

## 2. Preflight

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --preflight --json
```

Expected: PASS or only non-blocking warnings. If route templates/guides,
knowledge, executor, recovery, or Start Game classification are missing, fix
wiring before any live run.

## 3. Input Geometry Gate

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --check-input-geometry --json
```

Expected: `inputGeometryPass=true`.

If this fails:

- Do not run the bot.
- If no RuneLite window is matched, restore/focus RuneLite first.
- If 8890/8893 are down, restart telemetry services first.
- If geometry is stale, fix source freshness first.
- If transforms are missing, inspect `input_control\input_geometry.py` and the
  plugin/file geometry source.

## 4. Real Live Command

Only after the preflight and input geometry gate pass:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --live --execute-actions --auto-recover-loaded-scene --record-everything --analyze-after --json
```

Do not add `--dry-run-actions`, `--no-input`, or `--live-smoke` as a substitute
for this command when the task is real live validation.

## 5. Stop Criteria

- If loaded-scene proof fails, stop and fix launch/auth/recovery.
- If input geometry fails, stop and fix geometry/focus/telemetry source.
- If the action sends and fails, inspect the candidate/action/postcondition
  trace from that one run.
- Do not continue cycling live runs after the first useful failure artifact.
- Do not weaken executor safety gates to make progress.

## 6. First Likely Code Fix If Loaded + Geometry Pass

Inspect route guide / action proposal next-segment advancement first.

The last useful route blocker was:

```text
route_waypoint_arrived_but_route_state_stale
```

Known target state:

- Player around `3203,3237,0` or `3203,3238,0`.
- Inventory full.
- Route should advance toward the next `woodcutting_area_to_bank` demonstrated
  guide point or an explicit bank/stair/service interaction.
- It must not propose the current tile again.
- It must not fall back to Tree selection while inventory is full.

## 7. Files To Inspect First

1. `telemetry-viewer\input_control\action_proposal.py`
2. `telemetry-viewer\candidate_core.py`
3. `telemetry-viewer\route_demonstration.py`
4. `telemetry-viewer\route_monitor.py`
5. `telemetry-viewer\task_script_api.py`
6. `telemetry-viewer\knowledge_fabric.py`
7. `telemetry-viewer\input_control\executor.py`
8. `telemetry-viewer\bot_eval_runner.py`
9. `telemetry-viewer\live_readiness_core.py`
10. `telemetry-viewer\input_control\input_geometry.py`

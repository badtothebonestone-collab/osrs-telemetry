# Route History Monitor

Generated: 2026-06-06

## Scope

This pass extended `telemetry-viewer\route_monitor.py` from a snapshot route
readiness checker into a persistent route session/history monitor.

Template used:

```text
route_templates\Bank_to_Woodcutting_area.route_template.json
```

Template revision: `2`

Required route segments:

1. `area_start`
2. `walk_segment`
3. `stair_transition`
4. `walk_segment`
5. `area_arrival`

## Schemas

The persistent monitor writes:

- `route_session_state.v1`
- `route_session_event.v1`
- `route_progress_timeline.v1`
- `route_history_summary.v1`

Files:

- `route_session_state.json`
- `route_session_events.jsonl`
- `route_progress_timeline.jsonl`
- `route_history_summary.json`

Default live folder:

```text
%USERPROFILE%\.osrs-telemetry\route_monitor\<routeName>\<sessionId>\
```

Recording-mode history writes to the recording folder unless an output
directory or explicit output paths are supplied.

## Persistent State Machine

Implemented states:

- `unknown`
- `not_started`
- `ready_at_start`
- `in_progress`
- `segment_complete`
- `arrived`
- `off_route`
- `stale`
- `blocked`

Current behavior:

- `bank_area` completes `area_start` and reports `ready_at_start`.
- leaving the start cluster reports `in_progress` and completes the first walk
  segment.
- plane change in route context completes `stair_transition`.
- movement after stair transition completes the second walk segment.
- `woodcutting_area` completes all required segments and reports `arrived`.
- stale telemetry records stale periods and preserves previous route progress.
- off-route requires repeated fresh conflicting samples.

This is still a monitor/history layer only. It does not execute route actions.

## Recording Fixture: v2

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
```

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2" --write-history --json
```

Result:

- status: `PASS`
- route state: `arrived`
- current area: `woodcutting_area`
- completed / remaining: `5 / 0`
- off route: `false`
- stale periods: `0`
- plane changes: `1`
- events: `10`
- warnings: none

Artifacts written:

- `route_session_state.json`
- `route_session_events.jsonl`
- `route_progress_timeline.jsonl`
- `route_history_summary.json`

## Recording Fixture: Third Run

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC
```

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --write-history --json
```

Result:

- status: `PASS`
- route state: `arrived`
- current area: `woodcutting_area`
- completed / remaining: `5 / 0`
- off route: `false`
- stale periods: `0`
- plane changes: `1`
- events: `10`
- warnings: none

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-history --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-history
```

Analyzer result:

- status: `PASS`
- route state: `arrived`
- completed / remaining: `5 / 0`
- off route: `false`
- route history summary path:
  `C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC\route_history_summary.json`

## Live Follow Smoke

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --follow --duration 1 --poll-ms 250 --out-dir "%TEMP%\osrs_route_monitor_follow_smoke" --json
```

Result:

- status: `WARN`
- route state: `stale`
- current area: `woodcutting_area`
- completed / remaining: `0 / 5`
- off route: `false`
- stale periods: `1`
- events: `6`
- warning: `telemetry stale`
- source age during smoke: about `4318645.9 ms`

Interpretation:

The follow loop wrote a route session, but correctly refused to claim current
route progress because live telemetry was stale. This matches the intended
safety behavior.

## UI And Context

UI additions:

- `Start Route Monitor`
- `Stop Route Monitor`
- `Open Route Monitor Folder`
- artifact entries for route session state/events/summary

Context additions:

- `route_history`
- `route_session_state`
- `route_progress_timeline`
- `route_completed_segments`
- `route_remaining_segments`

If a route history state file exists, context returns compact session state.
If not, it can fall back to snapshot route readiness when a route template is
provided.

## Limitations

- Live route history uses simple area/plane/world heuristics, not pathfinding.
- `segment_complete` is represented through events and completed segments; the
  live state usually advances directly to `in_progress` or `arrived`.
- Repeated fresh off-route samples are required before declaring `off_route`.
- Live proof was stale in this pass, so the fresh live transition path is
  covered by synthetic tests rather than a current RuneLite run.

## Verdict

Persistent route history is implemented and verified for recording replay. Live
follow mode is operational and safely reports stale when telemetry is old.

# Live Route Monitor vs Offline Analyzer

Date: 2026-06-06

## Verdict

WARN: the live route monitor session does not agree with the offline analyzer, but the mismatch is explained by an invalid live monitor template load rather than by a failed route.

The live session observed a plausible Bank_to_Woodcutting_area world path, including a bank start, a 2 -> 0 plane change, and a final woodcutting-area position. However, the live route monitor session had `routeName: null`, `templateRevision: null`, zero expected/completed route segments, and 408 repeated `off_route` events. The offline analyzer for the newest matching recording passed the route template comparison with template revision 2.

This live monitor session should not be trusted as proof that persistent route monitoring is ready for this template. It is proof that the live sampler saw fresh route movement, and also proof that the live follow launch/configuration did not load the intended route template.

## Artifacts Compared

Live route monitor session:

`C:\Users\badto\.osrs-telemetry\route_monitor\route\route_20260606_184047`

Newest matching route recording:

`C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC`

Template requested:

`route_templates\Bank_to_Woodcutting_area.route_template.json`

Important timestamp limitation:

- Live monitor session: `2026-06-06T18:40:47Z` -> `2026-06-06T18:42:46Z`
- Recording: `2026-06-06T17:16:30Z` -> approximately `2026-06-06T17:17:11Z`

These are the newest live monitor and newest matching route recording artifacts, but they are not the same captured route attempt.

## Analyzer Command

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-monitor --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-history --print-route-monitor --print-route-history --print-traversal-lifecycle --print-route-segments --print-route-template-comparison
```

The analyzer completed successfully.

## Live Monitor Result

- Final route state: `off_route`
- Status: `FAIL`
- Route name: `null`
- Template revision: `null`
- Template path recorded: `Bank_to_Woodcutting_area.route_template.json`
- Current/end area: `woodcutting_area`
- Current world: `3195,3242,0`
- Freshness: `fresh`, source age about `642.6 ms`
- Completed segments: `0`
- Remaining segments: `0`
- Segment-completed events: `0`
- Plane changes: `1`
- Stale periods: `0`
- Off-route events: `408`
- Total events: `437`

Live world path evidence:

1. `2026-06-06T18:40:47Z`, tick `236`: `3208,3220,2`, area changed `None -> bank_area`
2. `2026-06-06T18:41:12Z`, tick `278`: `3205,3209,2`, area changed `bank_area -> plane_2`
3. `2026-06-06T18:41:14Z`, tick `280`: `3206,3208,0`, plane changed `2 -> 0`
4. `2026-06-06T18:41:30Z`, tick `307`: `3195,3242,0`, final observed route position

The live path itself resembles the route, but the live monitor did not convert any of it into route segment completions.

## Offline Analyzer Result

- Traversal verdict: `PASS`
- Route name: `Bank_to_Woodcutting_area`
- Template comparison status: `PASS`
- Status reason: `PASS_BASE_TEMPLATE`
- Template revision: `2`
- Comparison score: `1.0`
- Required segments matched: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Out-of-order segments: `0`
- Weak segments: `0`
- Failed postconditions: `0`
- Start/end area: `bank_area -> woodcutting_area`
- Recording route monitor state: `arrived`
- Recording route history completed/remaining: `5 / 0`
- Recording off-route: `false`
- Recording stale periods: `0`
- Recording plane changes: `1`

Offline route segments:

1. `area_start`: Start `bank_area`, success
2. `walk_segment`: Walk from `3209,3220,2` to `3205,3209,2`, success
3. `menu_selection`: Cancel, treated as incidental/review evidence
4. `stair_transition`: Climb-down Staircase, plane `2 -> 0`, success
5. `walk_segment`: Walk from `3206,3208,0` to `3195,3244,0`, success
6. `area_arrival`: Arrive `woodcutting_area`, success

## Comparison

Segment agreement:

- Live monitor completed `0` required segments.
- Offline analyzer completed `5` required segments.
- Segment agreement is FAIL for the actual live monitor output.

Area agreement:

- Live monitor first saw `bank_area` and ended in `woodcutting_area`.
- Offline analyzer started in `bank_area` and ended in `woodcutting_area`.
- Area evidence agrees at the route-shape level.

Plane transition agreement:

- Live monitor observed one plane change, `2 -> 0`, at `3206,3208,0`.
- Offline analyzer found one route plane transition, `2 -> 0`, for Climb-down Staircase at `3206,3208,0`.
- Plane evidence agrees at the route-shape level.

Timing agreement:

- Meaningful timing alignment is not possible because the newest live monitor session and newest recording are not the same attempt.
- The live monitor also never emitted `arrived`, so there is no live arrival time to compare against the offline analyzer's arrival.

Stale/off-route:

- Stale telemetry did not affect this live session; freshness was `fresh`.
- The live session produced 408 `off_route` events.
- Those off-route events appear to be caused by missing template semantics, not by the player moving to a wrong destination.

## Root Cause of Mismatch

The live session did not load the intended route template. Evidence:

- Output folder is under `route\route_20260606_184047` instead of `Bank_to_Woodcutting_area\...`.
- `route_session_state.json` has `routeName: null`.
- `route_session_state.json` has `templateRevision: null`.
- `remainingSegments` is empty from session start.
- The recorded template path is only `Bank_to_Woodcutting_area.route_template.json`, not the repo-relative or absolute path to `route_templates\Bank_to_Woodcutting_area.route_template.json`.

Likely code/config path:

- The live follow command probably passed a basename template path.
- `route_monitor.py` attempted to load that path relative to its process working directory.
- Missing template load produced an empty template payload rather than a hard failure.
- With no start/end/segment template semantics, the route state machine could not complete segments and repeatedly marked the fresh samples `off_route`.

## Decision

WARN for route attempt evidence, FAIL for live-monitor trust readiness.

The live samples and offline analyzer agree that the path shape was plausible: bank start, stair plane change, and woodcutting-area endpoint. But the actual live route monitor session did not agree with the offline analyzer on route completion because the monitor did not load template revision 2.

Do not trust the live route monitor for `Bank_to_Woodcutting_area` until the live launch path is corrected and a new session shows:

- `routeName: Bank_to_Woodcutting_area`
- `templateRevision: 2`
- completed segments `5 / 5`
- route state `arrived`
- offRoute `false`
- no repeated off-route events from the start tile

## Recommended Next Task

Fix or validate live monitor template path handling:

1. Make the UI and CLI pass the full template path:
   `C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json`
2. Make `route_monitor.py` fail loudly or warn clearly when a template file cannot be loaded.
3. Rerun a live follow session and confirm the output folder is:
   `%USERPROFILE%\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\<sessionId>\`
4. Then repeat this live-vs-offline comparison using artifacts from the same route attempt.

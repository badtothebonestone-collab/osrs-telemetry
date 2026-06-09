# Live Route Monitor Latest Session Comparison

Date: 2026-06-06

## Verdict

WARN: the live monitor and offline analyzer agree that the route completed
successfully, but the live monitor marked the final route segments complete too
early.

This is a major improvement over the previous broken monitor session:

- the live monitor loaded `Bank_to_Woodcutting_area`
- template revision was `2`
- required segment count was `5`
- output folder used `Bank_to_Woodcutting_area`
- live monitor completed `5 / 5`
- offline analyzer completed `5 / 5`
- both saw one plane change
- both ended in `woodcutting_area`
- no off-route events
- no stale periods

The remaining weakness is live segment timing. The live monitor completed
segments 3, 4, and 5 at the first lower-floor point after the staircase, while
the offline analyzer kept a later walk segment and arrival segment.

## Artifacts

Live monitor session:

```text
C:\Users\badto\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\route_20260606_204103
```

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_154108_manual_route-bank_to_woodcutting_area
```

Template:

```text
C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json
```

The UI route session manifest exists, but it points to:

```text
C:\Users\badto\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\route_20260606_154102
```

The actual monitor folder for this run is the UTC-named folder
`route_20260606_204103`. The timestamps line up with the recording and monitor
events, so this report compares the actual artifacts rather than trusting the
manifest folder path.

## Analyzer Command

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_154108_manual_route-bank_to_woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-monitor --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-history --print-route-monitor --print-route-history --print-traversal-lifecycle --print-route-segments --print-route-template-comparison
```

The analyzer completed successfully.

## Live Monitor Result

- Session id: `route_20260606_204103`
- Status: `PASS`
- Final route state: `arrived`
- Route: `Bank_to_Woodcutting_area`
- Template revision: `2`
- Required segments: `5`
- Completed / remaining: `5 / 0`
- Current area: `woodcutting_area`
- Current world: `3197,3244,0`
- Plane changes: `1`
- Stale periods: `0`
- Off-route: `false`
- Off-route events: `0`
- Event count: `131`

Live segment completion order:

1. `area_start`, `Start: bank_area`, tick `171`, world `3208,3220,2`
2. `walk_segment`, `Walk`, tick `193`, world `3205,3209,2`
3. `stair_transition`, `Climb-down Staircase`, tick `195`, world `3206,3208,0`
4. `walk_segment`, `Walk`, tick `195`, world `3206,3208,0`
5. `area_arrival`, `Arrive: woodcutting_area`, tick `195`, world `3206,3208,0`

Live state changes:

1. `unknown -> ready_at_start` at tick `171`
2. `ready_at_start -> in_progress` at tick `193`
3. `in_progress -> arrived` at tick `195`

Live area changes:

1. `None -> bank_area`, world `3208,3220,2`
2. `bank_area -> plane_2`, world `3205,3209,2`
3. `plane_2 -> woodcutting_area`, world `3206,3208,0`

## Offline Analyzer Result

- Traversal lifecycle: `PASS`
- Template comparison: `PASS_BASE_TEMPLATE`
- Score: `1.0`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Out-of-order: `0`
- Weak segments: `0`
- Failed postconditions: `0`
- Start/end area: `bank_area -> woodcutting_area`
- Recording route monitor: `PASS`, `arrived`, `5 / 0`, offRoute `false`
- Recording route history: `PASS`, `arrived`, `5 / 0`, offRoute `false`

Offline route segments:

1. `area_start`: Start `bank_area`, world `3208,3220,2`, success
2. `walk_segment`: Walk from `3208,3220,2` to `3205,3209,2`, success
3. `stair_transition`: Climb-down Staircase, movement-only partial review
4. `stair_transition`: Climb-down Staircase, plane `2 -> 0`, success
5. `walk_segment`: Walk from `3206,3208,0` to `3197,3244,0`, success
6. optional `door_transition`: Cancel Door, allowed/review evidence
7. `area_arrival`: Arrive `woodcutting_area`, world `3197,3244,0`, success

Template comparison matched the required template segments against recording
segments `1`, `2`, `4`, `5`, and `7`.

The partial stair movement candidate and optional Cancel Door evidence did not
hurt the route comparison.

## Input And Mapping Quality

- Input trace: `PASS`
- Input events: `187`
- Real input events: `185`
- OS clicks: `4`
- Eligible game-action clicks: `2`
- Menu selections: `2`
- Menu selections with row geometry: `2`
- Menu selections missing row geometry: `0`
- Target quality: `PASS`
- Strong matches: `2`
- Medium/weak/unmatched: `0 / 0 / 0`
- Arduino live click commands: `0`
- Duplicate click likely: `0`
- Post-action Arduino movement/click commands: `0 / 0`
- Feedback loop suspected: `false`
- Panic stops: `0`

Arduino/live mirror was not requested for this route run, which is correct for
route monitoring.

## Comparison

Agreement:

- Template loaded correctly in live and offline paths.
- Route name and revision match.
- Both completed all five required segments.
- Both reached `woodcutting_area`.
- Both observed the `2 -> 0` plane change.
- Neither reported off-route.
- Neither reported stale telemetry during the route.
- Offline input/menu/target evidence is strong.

Timing mismatch:

- Live monitor marked `stair_transition`, second `walk_segment`, and
  `area_arrival` complete at tick `195`, world `3206,3208,0`.
- Offline analyzer placed the successful second walk from `3206,3208,0` to
  `3197,3244,0` and final arrival at `3197,3244,0`.

Reason:

The live area heuristic labels the first lower-floor point near the staircase
as `woodcutting_area`. Because reaching the template end area completes the
route in the live state machine, the monitor completed the remaining required
segments at that moment.

This is not a route failure. It is a live-progress timing weakness.

## Decision

The route monitor is now trustworthy for broad `Bank_to_Woodcutting_area`
completion:

- correct template loaded
- correct route recognized
- no off-route spam
- route completion agrees with offline analyzer

It is not yet trustworthy for precise segment timing after the staircase,
because the end-area label is too broad for live progress tracking.

## Recommended Fix

Tune live route history progress so `area_arrival` requires end-area evidence
near the template/reference endpoint, not merely any point labeled
`woodcutting_area`.

Targeted change:

- keep `woodcutting_area` as a route/end label
- require second walk progress before completing `area_arrival`
- only complete final arrival when current world is near the template end
  cluster or after the second walk segment has moved enough tiles

Also fix the UI route session manifest so `routeMonitorSessionFolder` records
the actual monitor-created folder or passes the planned `--session-id`
consistently.

## Follow-up Fix Applied

The route monitor now has an arrival gate for persistent live history.
`woodcutting_area` near the lower staircase is treated as an arrival candidate,
not final arrival.

The replayed live points now behave as follows:

- `3206,3208,0`: `in_progress`, `arrivalGateStatus=waiting`,
  `distanceToEndCluster=37.108`, completed segments `3 / 5`
- `3197,3244,0`: `arrived`, `arrivalGateStatus=passed`,
  `distanceToEndCluster=0.0`, completed segments `5 / 5`

The route template now includes an `endCluster` at `3197,3244,0` with an
eight-tile tolerance. The persistent monitor uses that cluster, post-stair
movement distance, and second-walk progress before it completes
`area_arrival`.

The UI route-session launch also now passes its planned `routeSessionId` to
`route_monitor.py` with `--session-id`. The manifest records both
`plannedRouteMonitorFolder` and `actualRouteMonitorFolder`, and the UI falls
back to the actual folder if older sessions still contain a mismatch.

## Revision 3 Arrival Tightening

The next live run showed one more timing issue: the monitor no longer arrived
at the staircase point, but it still completed `area_arrival` at
`3202,3218,0` from distance-only second-walk progress.

Template revision `3` fixes that. `distanceAfterLastTransition` now completes
or supports the second walk, but cannot complete final `area_arrival`.

Replayed key points after the revision 3 change:

- `3206,3208,0`: `in_progress`, `arrivalGateStatus=waiting`,
  `arrivalGateRejectedReason=waiting_for_end_cluster`, completed segments
  `3 / 5`
- `3202,3218,0`: `in_progress`, `distanceOnlyProgressRejected=true`,
  `arrivalGateRejectedReason=distance_only_progress_not_arrival`, completed
  segments `4 / 5`
- `3194,3242,0`: `arrived`, `arrivalGateStatus=passed`,
  `arrivalGatePassedReason=near_end_cluster`, completed segments `5 / 5`

Repeated `arrival_gate_passed` and `arrived` events are now suppressed after
the route is already arrived.

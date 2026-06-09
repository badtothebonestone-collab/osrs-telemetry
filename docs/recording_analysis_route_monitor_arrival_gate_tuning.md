# Route Monitor Arrival Gate Tuning

Date: 2026-06-06

## Verdict

PASS for the targeted fix after revision 3.

The live route monitor no longer completes `area_arrival` at the first
lower-floor point after the staircase, and it no longer completes final arrival
from distance-only post-stair progress. It now requires endpoint-specific
evidence, normally proximity to the template end cluster.

The UI route-session folder mismatch is also fixed by passing the planned
`routeSessionId` to `route_monitor.py` with `--session-id`.

## Root Cause

The live route history state machine treated `currentArea == woodcutting_area`
as final arrival. The area label is broad near the lower staircase, so the
session completed segments 3, 4, and 5 at:

```text
3206,3208,0
```

The offline analyzer correctly kept the route in progress until the later
arrival cluster:

```text
3197,3244,0
```

The UI mismatch came from planning one monitor folder but launching the monitor
without `--session-id`, allowing the monitor to create its own UTC-style folder.

## Changes

Changed files in this pass:

- `telemetry-viewer\route_monitor.py`
- `telemetry-viewer\telemetry_ui.py`
- `telemetry-viewer\context_service.py`
- `telemetry-viewer\analyze_manual_recording.py`
- `telemetry-viewer\tests\test_route_monitor.py`
- `telemetry-viewer\tests\test_telemetry_ui.py`
- `route_templates\Bank_to_Woodcutting_area.route_template.json`
- `docs\route_monitor.md`
- `docs\telemetry_ui.md`
- `docs\recording_analysis_live_route_monitor_latest_session.md`
- `docs\recording_analysis_route_monitor_arrival_gate_tuning.md`

Revision 3 changes:

- `distanceAfterLastTransition` remains second-walk progress evidence.
- distance-only progress cannot complete `area_arrival`.
- `arrivalGateRejectedReason=distance_only_progress_not_arrival` identifies
  early progress that is not final arrival.
- `arrivalGatePassedReason=near_end_cluster` identifies accepted arrival.
- repeated arrival events are suppressed after the route is already arrived.

## Arrival Gate Behavior

The persistent live monitor now records arrival-gate fields:

- `arrivalGateStatus`
- `arrivalCandidateWorld`
- `arrivalCandidateReason`
- `distanceToEndCluster`
- `distanceAfterLastTransition`
- `freshEndAreaSampleCount`
- `prematureArrivalPrevented`

It emits route-history events:

- `arrival_candidate`
- `arrival_gate_waiting`
- `arrival_gate_passed`
- `second_walk_started`
- `second_walk_completed`

## End Cluster

The route template now keeps the broad end area and uses an exact end cluster:

```json
{
  "worldX": 3197,
  "worldY": 3244,
  "plane": 0
}
```

Tolerance: `8` tiles.

Template revision: `3`.

## Replay Proof

Synthetic live replay using the known route points:

| Sample | World | State | Gate | Completed |
| --- | --- | --- | --- | --- |
| start | `3208,3220,2` | `ready_at_start` | `not_started` | `1 / 5` |
| stair point | `3206,3208,0` | `in_progress` | `waiting` | `3 / 5` |
| early distance point | `3202,3218,0` | `in_progress` | `waiting` | `4 / 5` |
| arrival | `3194,3242,0` | `arrived` | `passed` | `5 / 5` |

At the old false-arrival point:

- `distanceToEndCluster`: `37.108`
- `distanceAfterLastTransition`: `0.0`
- `prematureArrivalPrevented`: `true`

At the distance-only progress point:

- `distanceToEndCluster`: `26.476`
- `distanceAfterLastTransition`: `10.77`
- `distanceOnlyProgressRejected`: `true`
- `arrivalGateRejectedReason`: `distance_only_progress_not_arrival`

At the real arrival point:

- `distanceToEndCluster`: `3.606`
- `distanceAfterLastTransition`: `36.056`
- `arrivalCandidateReason`: `near_end_cluster`
- `arrivalGatePassedReason`: `near_end_cluster`

After a repeated arrived sample, no new `arrived` or `arrival_gate_passed`
event was emitted and `duplicateArrivalEventsSuppressed` incremented.

## Recording-Mode Result

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC
```

Recording-mode route monitor:

- Status: `PASS`
- Route state: `arrived`
- Route: `Bank_to_Woodcutting_area`
- Template revision: `2`
- Required segments: `5`
- Completed / remaining: `5 / 0`
- Current area: `woodcutting_area`
- Current world: `3195,3244,0`
- Plane changes: `1`
- Off-route: `false`

Analyzer/template comparison:

- Status: `PASS`
- Status reason: `PASS_BASE_TEMPLATE`
- Score: `1.0`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Extra required segments: `0`
- Failed postconditions: `0`
- Allowed review evidence: `Cancel`

## Live Follow Smoke

Command:

```powershell
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --follow --duration 1 --poll-ms 250 --json
```

Result:

- Status: `WARN`
- Route state: `stale`
- Route: `Bank_to_Woodcutting_area`
- Template revision: `2`
- Required segments: `5`
- Output folder:
  `C:\Users\badto\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\route_20260606_210704`
- Warning: `telemetry stale: sourceAgeMs=1447378.4`

This is expected: the monitor loaded the template correctly and refused to call
stale live telemetry current.

## UI Folder Fix

`Start Route Session` now builds a route session plan with one `routeSessionId`
and passes it to the monitor command:

```text
--session-id <routeSessionId>
```

The manifest records:

- `plannedRouteMonitorFolder`
- `actualRouteMonitorFolder`
- `routeSessionId`
- `routeMonitorStarted`
- `routeMonitorStartupStatus`

If an older session still has a mismatch, `Stop Route Session` searches for the
actual route-name/session-id folder and updates the manifest.

## UI/Context Display

The compact route monitor display now includes:

- route state
- current area
- current segment
- next expected segment
- completed/remaining counts
- arrival gate status
- distance-to-end and post-transition distance when available
- actual route monitor folder

The context service compact route-session response now exposes the same
arrival-gate fields and actual monitor folder.

## Checks Run

```powershell
python -m py_compile telemetry-viewer\route_template.py telemetry-viewer\traversal_lifecycle.py telemetry-viewer\analyze_manual_recording.py telemetry-viewer\route_monitor.py telemetry-viewer\telemetry_ui.py telemetry-viewer\context_service.py
python telemetry-viewer\tests\test_route_template.py
python telemetry-viewer\tests\test_traversal_lifecycle.py
python telemetry-viewer\tests\test_route_monitor.py
python telemetry-viewer\tests\test_telemetry_ui.py
python telemetry-viewer\telemetry_ui.py --check
```

All passed.

Additional validation:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --write-history --json
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-monitor --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-history --print-route-monitor --print-route-history --print-traversal-lifecycle --print-route-segments --print-route-template-comparison
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --follow --duration 1 --poll-ms 250 --json
```

## Next UI Steps

1. Start Telemetry.
2. Select `Bank_to_Woodcutting_area`.
3. Confirm the template shows revision `3`.
4. Click `Check Route Readiness`.
5. Click `Start Route Session`.
6. Run the route.
7. Click `Stop Route Session`.
8. Review route state, arrival gate, segment counts, and latest report.

## Next Task

Run one fresh live route with current telemetry active and confirm the monitor
now records `arrival_gate_waiting` near `3206,3208,0`, then
`arrival_gate_passed` near the actual woodcutting-area endpoint.

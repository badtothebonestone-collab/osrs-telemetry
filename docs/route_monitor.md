# Route Monitor

`route_monitor.py` evaluates route readiness and progress against a route
template. It can read live telemetry snapshots or already-analyzed recording
artifacts.

The output artifact is:

```text
route_monitor_status.json
```

Its schema is `route_monitor_status.v1`.

## How It Fits

- `traversal_lifecycle.json` explains what happened in a recording.
- `route_template_comparison.json` compares a completed route to a template.
- `route_monitor_status.json` answers the operational question: where are we
  now, what route segment is next, and is the route ready, progressing, stale,
  blocked, or off-route?

The monitor uses `routeSegments` and route-template comparison semantics as the
primary model. Raw steps and review evidence are debugging context only.

## Route States

- `unknown`: telemetry is usable, but the monitor cannot confidently place the
  player on the route.
- `not_started`: reserved for persistent route history; not used heavily yet.
- `ready_at_start`: current area matches the template start area.
- `in_progress`: current world position appears inside the simple route
  corridor.
- `segment_complete`: reserved for future persistent live history.
- `arrived`: current or recorded end area matches the template end area.
- `off_route`: current area/world or recording endpoint conflicts with the
  template.
- `stale`: live telemetry is older than the freshness threshold.
- `blocked`: required route progress is missing and the endpoint was not
  reached.

Persistent history mode uses the same state names, but keeps previous state,
recent path, segment completions, stale periods, and route events across
samples.

## Live Readiness

Live mode loads the route template, reads current telemetry, and reports:

- route template and revision
- current area
- freshness and latest tick/export sequence
- start/end area match
- completed and remaining required segments
- next expected segment
- off-route reasons
- missing capabilities

For `Bank_to_Woodcutting_area`:

- `bank_area` means `ready_at_start`; the next segment is the first walk
  segment.
- `woodcutting_area` means `arrived`.
- a fresh point inside the simple start-to-end corridor means `in_progress`.
- a fresh point outside that corridor means `off_route`.
- stale telemetry always reports `stale` instead of pretending the live state is
  current.

The current live implementation is intentionally heuristic. It does not run
pathfinding; it uses area labels, nearby object hints, template endpoints, and a
simple corridor check.

## Recording Mode

Recording mode reads:

- `traversal_lifecycle.json`
- `route_template_comparison.json` when present
- the supplied route template

It reports the final route state, completed/remaining required segments,
comparison status reason, missing/extra/allowed-extra segments, and whether the
route endpoint was reached.

A recording with missing direct segment evidence but a matched endpoint reports
`WARN` and `arrived`, not `FAIL`. A recording with the wrong endpoint reports
`FAIL` and `off_route`.

## CLI

Validate route templates before monitoring:

```powershell
python telemetry-viewer\route_monitor.py --validate-template Bank_to_Woodcutting_area
python telemetry-viewer\route_monitor.py --validate-template Bank_to_Woodcutting_area.route_template.json
python telemetry-viewer\route_monitor.py --validate-template route_templates\Bank_to_Woodcutting_area.route_template.json
```

List known templates:

```powershell
python telemetry-viewer\route_monitor.py --list-route-templates
```

Check live readiness from the newest session:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --json
```

Monitor a recording:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "<recording_folder>" --json
```

Analyzer integration:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-monitor --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-monitor
```

## Context API

Request compact route readiness:

```json
{
  "schema": "context_request.v1",
  "needs": ["baseline", "route_monitor"],
  "routeTemplate": "route_templates/Bank_to_Woodcutting_area.route_template.json",
  "responseMode": "compact"
}
```

Compact responses include route name, template revision, route state, current
area, next expected segment, completed/remaining counts, off-route status,
freshness, and warnings.

## UI

In `OSRS Telemetry Control`, use the `Route / Traversal Recording` preset.

1. Set the Route template path.
2. Click `Check Route Readiness` to evaluate live telemetry.
3. Click `Monitor Latest Recording` after analyzing a route.
4. Click `Open Route Monitor` to inspect `route_monitor_status.json`.

The compact status shows route state, current area, next expected segment,
completed/remaining counts, and off-route warnings.

## Template Resolution And Launch Guardrails

The monitor resolves template inputs through the shared
`route_template_resolution.v1` resolver. Accepted inputs include:

- an absolute path such as
  `C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json`
- `route_templates\Bank_to_Woodcutting_area.route_template.json`
- `Bank_to_Woodcutting_area.route_template.json`
- `Bank_to_Woodcutting_area`

Follow/live mode now fails before starting if the template cannot be resolved,
or if the loaded template is missing `routeName`, `templateRevision`, or
required segments. This prevents a bad launch from creating a generic
`route\route_*` session with no route model loaded.

When a valid template is loaded, session state includes:

- the original template input
- the resolved absolute template path
- route name
- template revision
- required segment count
- the resolver status and candidates tried

If these fields are missing, treat the run as a launch/configuration failure,
not as proof that the player went off-route.

## Reading The Status

Key fields:

- `routeState`: live/recording state such as `ready_at_start`, `arrived`,
  `stale`, or `off_route`.
- `currentArea`: inferred area label.
- `nextExpectedSegment`: the next required route segment when the route is not
  complete.
- `completedSegments` / `remainingSegments`: template-required progress.
- `freshness`: live source age, latest tick, latest export sequence, and fresh
  or stale status.
- `offRouteReasons`: why the route was considered off-route.
- `warnings`: human-readable caveats.
- `missingCapabilities`: telemetry fields needed for stronger certainty.

Use `route_monitor_status.json` for readiness/progress. Use
`route_template_comparison.json` when you need the exact segment match table.

## Persistent History Mode

Follow mode turns the monitor into a route session tracker. It is still
read-only; it never clicks, moves, or executes a route.

The session artifacts are:

```text
route_session_state.json
route_session_events.jsonl
route_progress_timeline.jsonl
route_history_summary.json
```

Default live output folder:

```text
%USERPROFILE%\.osrs-telemetry\route_monitor\<routeName>\<sessionId>\
```

Recording-mode history writes the same files into the recording folder unless
`--out-dir` or explicit output paths are supplied.

### Session State

`route_session_state.json` stores:

- session id, route name, template path, and template revision
- route state and previous non-stale state
- current area and world point
- latest tick/export sequence
- freshness, stale period count, and longest stale duration
- current/next segment
- completed and remaining segments
- recent world path
- plane changes
- off-route reasons and confidence
- warnings and evidence

### Events

`route_session_events.jsonl` records significant changes:

- `session_start`
- `snapshot`
- `state_change`
- `segment_completed`
- `area_changed`
- `plane_changed`
- `off_route`
- `stale`
- `fresh`
- `arrived`
- `session_stop`

`route_progress_timeline.jsonl` is a compact timeline view derived from those
events.

### Segment Progress

For template revision 3 of `Bank_to_Woodcutting_area`, the live history
heuristics are:

- `area_start` completes when current area is `bank_area`.
- first `walk_segment` completes when the player moves away from the start
  cluster.
- `stair_transition` completes when the plane changes in route context.
- second `walk_segment` completes after the stair transition when movement
  continues toward the end area.
- `area_arrival` completes only when the arrival gate sees endpoint-specific
  evidence, normally proximity to the template end cluster.

The implementation deliberately avoids pathfinding. It uses route template
segments, area labels, world/plane deltas, and freshness.

### Arrival Gate

`woodcutting_area` is a useful area label, but it is broad near the lower
staircase. Persistent live history therefore treats the first end-area sample
as an arrival candidate, not proof of arrival.

For `Bank_to_Woodcutting_area`, `area_arrival` now requires:

- the stair/plane transition is complete
- the second walk has started
- the current area is compatible with the route end area
- the current world point is near the template end cluster
- live telemetry is fresh

`distanceAfterLastTransition` is still important, but it is progress evidence
for the second walk. It cannot complete final `area_arrival` by itself.

If a template does not have an end cluster, the monitor may fall back to
sustained end-area evidence and emits a warning:

```text
template lacks precise end cluster; arrival inferred from sustained end-area evidence
```

The session state records:

- `arrivalGateStatus`: `waiting`, `passed`, `arrived`, or `not_applicable`
- `arrivalCandidateWorld`
- `distanceToEndCluster`
- `endClusterToleranceTiles`
- `nearEndCluster`
- `nearEndClusterSampleCount`
- `distanceAfterLastTransition`
- `arrivalGateRequiresEndCluster`
- `distanceOnlyProgressRejected`
- `arrivalGateRejectedReason`
- `arrivalGatePassedReason`
- `freshEndAreaSampleCount`
- `arrivalGateWarnings`
- `prematureArrivalPrevented`
- `duplicateArrivalEventsSuppressed`

Events include `arrival_candidate`, `arrival_gate_waiting`,
`arrival_candidate_area_label_only`, `arrival_gate_waiting_for_end_cluster`,
`arrival_gate_rejected_distance_only`,
`arrival_gate_passed_near_end_cluster`, `arrival_gate_passed`,
`second_walk_started`, and `second_walk_completed`.

This prevents the lower-floor staircase point from finishing the route just
because the area label is already `woodcutting_area`.

### Stale And Off-Route Handling

Stale telemetry does not erase progress. The monitor records a stale event,
keeps the previous non-stale state, and restores route progress when telemetry
becomes fresh again.

Off-route is conservative. One fresh conflicting sample is not enough. Repeated
fresh samples outside the route corridor or inconsistent with template evidence
mark the session `off_route` and record reasons.

## Follow CLI

Start a live route monitor:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --follow --poll-ms 250 --print-state
```

Use a stable UI/session id when another tool needs to know the exact output
folder:

```powershell
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --follow --session-id route_YYYYMMDD_HHMMSS --out-dir "%USERPROFILE%\.osrs-telemetry\route_monitor"
```

The output folder is then:

```text
%USERPROFILE%\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\<sessionId>\
```

Run for a fixed duration:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --follow --duration 60 --out-dir "%USERPROFILE%\.osrs-telemetry\route_monitor"
```

Replay history from a recording:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "<recording_folder>" --write-history --json
```

Analyzer integration:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-history --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-history
```

## Persistent Context API

If a route history session exists, request it with:

```json
{
  "schema": "context_request.v1",
  "needs": ["route_history"],
  "responseMode": "compact"
}
```

To point at a specific state file:

```json
{
  "schema": "context_request.v1",
  "needs": ["route_session_state"],
  "routeSessionStatePath": "C:/path/to/route_session_state.json",
  "responseMode": "compact"
}
```

Related needs include `route_progress_timeline`, `route_completed_segments`,
and `route_remaining_segments`.

## Persistent UI Workflow

1. Select `Route / Traversal Recording`.
2. Confirm the route dropdown shows `Bank_to_Woodcutting_area` and the template
   status says loaded.
3. Click `Check Route Readiness`.
4. Click `Start Route Session` to launch monitor plus recording with the
   resolved template path.
5. Click `Stop Route Session` after the route.
6. Review the compact route verdict and open the monitor/report artifacts if
   needed.

# Route Templates

Route templates turn a successful `traversal_lifecycle.json` into a reusable
baseline for future route recordings. They use `routeSegments` as the primary
input because those segments already fold raw clicks, menu rows, movement, and
plane changes into route-progress steps.

Raw steps and review evidence are still kept for debugging, but they are not
the default comparison surface.

## Artifacts

Extraction writes:

```text
route_templates\<routeName>.route_template.json
```

Comparison writes:

```text
route_template_comparison.json
```

The schemas are:

- `route_template.v1`
- `route_template_segment.v1`
- `route_template_comparison.v1`
- `route_template_variant.v1`
- `route_template_resolution.v1`

## Template Resolution

All route-template callers use the same resolver. It accepts:

- an absolute template path
- a repo-relative path under `route_templates`
- a template filename
- a route name such as `Bank_to_Woodcutting_area`

Resolution searches:

1. the supplied absolute path, when one is provided
2. repo root plus the supplied relative path
3. repo root plus `route_templates` plus the supplied filename
4. `route_templates\<routeName>.route_template.json`
5. a configured default template, when a caller intentionally supplies one

The resolver returns `route_template_resolution.v1` with input, resolved path,
route name, template revision, required segment count, warnings, and candidates
tried. Route monitor follow mode treats unresolved templates, missing route
names, missing revisions, or zero required segments as launch failures.

This matters because a basename-only path can be valid when resolved through
`route_templates`, but it should never silently create a live monitor session
with no template model loaded.

## One-Way And Reverse Routes

Route templates are directional. `Bank_to_Woodcutting_area` starts at
`bank_area` and ends at `woodcutting_area`; the reverse route is a separate
template named `woodcutting_area_to_bank`.

Record Everything analysis should use `--auto-route-template`. Auto-selection
first matches the detected `traversal_lifecycle.routeName`, then falls back to
the detected start/end area pair. A reverse route should therefore compare to
`route_templates\woodcutting_area_to_bank.route_template.json`, not the forward
template.

If no matching template exists, the analyzer reports an untemplated route and
suggests a template name instead of failing the recording. If a strict manual
comparison uses the wrong one-way template, the comparison includes
`routeTemplateDirectionMismatch=true` so the failure is understood as a
template-direction problem, not bad route data.

For `woodcutting_area_to_bank`, the Deposit Box / Deposit click is endpoint or
task evidence. It is preserved in review notes and is not required traversal
progress unless a future banking/deposit-specific template declares it required.

## Template Contents

A template records:

- route name
- start area and optional world tolerance
- end area and optional world tolerance
- ordered required route segments
- optional/context segments
- expected action/target, when present
- expected postcondition, such as movement, plane change, or area arrival
- quality requirements, such as minimum target quality
- timing and position tolerances

Successful route-progress segments become required by default only when they
match the route semantics. Context segments such as `bank_context` or
`task_context` become optional. Review evidence is kept as notes and does not
become a required segment.

Route-specific semantics can demote a successful observed segment when the
segment is not actually required by the route. For
`Bank_to_Woodcutting_area`, Door/Open is optional navigation evidence because
the route can reach `woodcutting_area` without opening a template-visible door.

For `Bank_to_Woodcutting_area` revision 3, `area_arrival` also requires
proximity to the template `endCluster`. The broad `woodcutting_area` label is
useful for route context, but it is not enough by itself to finish the route.
Likewise, `distanceAfterLastTransition` proves post-stair route progress; it
does not prove final arrival unless the player is near the endpoint cluster.

## Extraction

Use the analyzer:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --extract-route-template --route-template-out route_templates
```

Or use the standalone helper:

```powershell
python telemetry-viewer\route_template.py extract --recording "<recording_folder>" --out route_templates
```

Extract templates from PASS routes, or from WARN routes only when the route
summary is usable and the warnings are understood.

## Comparison

Compare a later recording to a template:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-template-comparison
```

The comparison reports:

- overall status
- route name match
- start and end area match
- matched required segments
- missing segments
- extra segments
- out-of-order segments
- weak or partial segments
- failed postconditions
- target quality below requirement
- score
- warnings

Review evidence does not fail comparison by itself. Extra route segments are
reported because they may mean the route changed or the recording included
additional gameplay.

## Status Reasons

The comparison keeps the broad `PASS`, `WARN`, and `FAIL` status, and also
adds a more precise `statusReason`:

- `PASS_BASE_TEMPLATE`: the recording matched the template directly.
- `PASS_REGISTERED_VARIANT`: the recording matched by using a registered
  route variant.
- `WARN_VALID_UNREGISTERED_VARIANT`: the route reached the right endpoint and
  had strong movement/postcondition evidence, but used an unregistered
  navigation substitution.
- `WARN_EXTRA_REVIEW_EVIDENCE`: the route matched, but extra harmless or
  review-only evidence was present.
- `WARN_PARTIAL_BUT_ENDPOINT_REACHED`: required progress was not fully direct,
  but the endpoint and postconditions still support the route.
- `FAIL_MISSING_REQUIRED_SEGMENT`: a required segment was not satisfied.
- `FAIL_WRONG_ENDPOINT`: the recording ended in the wrong area.
- `FAIL_OUT_OF_ORDER_REQUIRED_SEGMENT`: strict transitions occurred out of
  order.
- `FAIL_FAILED_POSTCONDITION`: a required segment had a failed postcondition.

## Matching Rules

Segment matching prefers:

1. segment type
2. action option and target
3. postcondition type
4. plane delta or movement distance
5. target quality requirement

Ladder, stair, and plane-transition segments use strict order. Walk segments
are flexible because small movement chunks can vary between recordings.
Door/Open is only strict when the specific route template declares it required.

The end area is decisive. A recording that does not reach the template end area
fails even if some steps match.

## Route Variants

Variants keep the base template strict while allowing known gameplay
substitutions. A variant records:

- variant name
- source recording
- base segment being satisfied
- allowed alternative segment
- postcondition requirement
- target quality requirement
- endpoint-sharing requirement

For example, a route that truly requires a door can expect:

```text
door_transition: Open Door
```

A registered variant can allow:

```text
walk_segment: Walk here Large door
```

That alternative satisfies the Door/Open transition only when that route still
requires Door/Open, movement is strong, target quality is at least medium, the
route still reaches its destination, and no failed door/open postcondition
appears.

The current `Bank_to_Woodcutting_area` template no longer uses the
`walk_here_large_door` variant as a pass condition. User review confirmed the
route does not require opening a door, so both direct `Open Door` and
`Walk here Large door` are optional navigation/support evidence for this
route. A missing Door/Open segment must not by itself produce WARN or FAIL.

Register a variant from a recording that already compares as a valid
unregistered variant:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --extract-route-variant --route-variant-name "walk_here_large_door" --variant-description "Walk here Large door movement-support segment satisfies Door/Open when route progress and endpoint evidence are strong." --add-route-variant-to-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-variant
```

After registration, the same recording should compare as
`PASS_REGISTERED_VARIANT` and include `matchedVariantName`.

## Navigation-Support Substitutions

Navigation-support clicks include `Walk here`, minimap/navigation clicks when
classified, and movement-support menu selections. They can support or satisfy
a route segment only when route progress is proven by movement, endpoint, and
postcondition evidence.

Harmless minimap or navigation clicks should attach to a walk/movement segment
or remain review evidence. They should not become required template segments
and should not fail a route unless they cause the wrong endpoint, missed
required progress, out-of-order transitions, or failed postconditions.

Extra post-arrival actions are allowed as review evidence when the route has
already reached the expected endpoint.

## Example

For `Bank_to_Woodcutting_area`, the corrected required template contains:

1. `area_start`: bank area
2. `walk_segment`
3. `stair_transition`: Climb-down Staircase
4. `walk_segment`
5. `area_arrival`: woodcutting area

Optional/support evidence may include:

- `door_transition`: Open Door
- `walk_segment`: Walk here Large door
- `menu_selection`: Cancel or other incidental menu evidence

A future route can still pass with review evidence, as long as the required
route segments and destination match.

## UI

In the Telemetry Control UI, use the `Route / Traversal Recording` preset.
After analysis:

1. Click `Extract Template From Latest`.
2. Set or confirm the template path.
3. Click `Compare Latest To Template`.
4. Open `route_template_comparison.json` for segment-level details.
5. If the comparison says `WARN_VALID_UNREGISTERED_VARIANT`, use `Register
   Latest As Variant` to add the accepted substitution to the selected
   template.

## Route Monitor

Route templates also feed the route monitor. The monitor does not replace
template comparison. Instead, it answers the current readiness/progress
question:

- am I at the route start?
- which required segment is next?
- did the completed recording arrive at the template end?
- is live telemetry fresh, stale, off-route, or missing key fields?

Use:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --json
```

or analyzer `--route-monitor --route-monitor-template <template>` for
recordings. See `docs\route_monitor.md`.

For continuous live monitoring, add `--follow`. That writes
`route_session_state.json`, `route_session_events.jsonl`,
`route_progress_timeline.jsonl`, and `route_history_summary.json` while keeping
the same template semantics. History mode is useful for readiness/progress over
time; template comparison remains the completed-recording segment match table.

## Woodcutting Loop Use

The woodcutting loop lifecycle uses route template comparison and route monitor
outputs as evidence for high-level task phases. A successful
`woodcutting_area_to_bank` route points the loop to `banking_deposit`; a
successful `Bank_to_Woodcutting_area` route points it to `resume_cutting`.

This does not change route template semantics. Route templates still compare
required `routeSegments`, not raw clicks.

# Route Template Third Fresh Run

## Recording

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC
```

This folder did not match the exact `manual_route...` label pattern, but it was
newer than the matching v3 recording and its manifest label is `bank to WC`.
Its traversal lifecycle identifies the route as `Bank_to_Woodcutting_area`, so
it is the newest relevant Bank-to-Woodcutting route recording found.

Template used:

```text
C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json
```

## Analyzer Command

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-traversal-lifecycle --print-route-segments --print-route-template-comparison
```

## Verdict

- Route lifecycle verdict: `PASS`
- Route name: `Bank_to_Woodcutting_area`
- Template comparison status: `PASS`
- Status reason: `PASS_BASE_TEMPLATE`
- Matched variant name: `null`
- Comparison score: `1.0`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Allowed extras: `1`
- Out-of-order segments: `0`
- Weak segments: `0`
- Failed postconditions: `0`
- Start area matched: `true`
- End area matched: `true`
- Review evidence count: `0`
- Navigation-support substitutions: `0`
- Review evidence segments: `1`

The route itself succeeded: it started in `bank_area`, ended in
`woodcutting_area`, detected the staircase plane transition, and had no failed
postconditions. After the semantics audit, the comparison now agrees with that
route verdict.

## Route Segments

1. `area_start`: Start: `bank_area`, world `3209,3220,2`, success
2. `walk_segment`: Walk, `3209,3220,2` -> `3205,3209,2`, movement success
3. `menu_selection`: Cancel, `3206,3215,2` -> `3205,3209,2`, movement success
4. `stair_transition`: Climb-down Staircase, `3205,3209,2` -> `3206,3208,0`,
   plane-change success
5. `walk_segment`: Walk, `3206,3208,0` -> `3195,3244,0`, movement success
6. `area_arrival`: Arrive: `woodcutting_area`, success

## Template Match Details

Matched required route progress:

- Segment 1: `area_start`, bank area
- Segment 2: `walk_segment`
- Segment 3: `stair_transition`, Climb-down Staircase
- Segment 4: `walk_segment`
- Segment 5: `area_arrival`, woodcutting area

Missing:

- none

Allowed review evidence:

- Recording segment 3: `menu_selection`, `Cancel`, with movement success

Door/Open is no longer required for this route, so this run does not need a
direct `Open Door` segment or a `Walk here Large door` variant.

## Input And Safety Evidence

- Input events: `195`
- Real input events: `193`
- Raw OS clicks: `5`
- Eligible game-action clicks: `1`
- Minimap clicks detected: `0`
- Menu selections: `1`
- Menu selections with row geometry: `1`
- Target-relative clicks: `1`
- Strong target matches: `1`
- Duplicate click likely count: `0`
- Live Arduino click commands: `0`
- Panic stops: `0`
- Feedback loop suspected: `false`
- Input path classification: `os_polling_only`

No live Arduino duplicate-click or mirror feedback issue appears in this
recording.

## Interpretation

This recording proved the old Door/Open requirement was too strict. It reached
the destination without a template-visible door action, while the analyzer
surfaced a `Cancel` menu row with movement as review evidence. Because the
endpoint and staircase transition were correct, Door/Open absence is no longer
a template warning.

## Stability Assessment

The route lifecycle and corrected template are stable enough for route-level
readiness checks:

- start area correct
- end area correct
- route name correct
- staircase/plane transition detected
- no wrong endpoint
- no failed postconditions
- no missing Door/Open warning

## Recommended Next Task

Use the corrected template as the route readiness baseline. The next useful
task is route readiness/monitoring: record a few more route runs, compare them
against revision 2, and flag only true route problems such as wrong endpoint,
missing staircase/plane transition, failed postcondition, or out-of-order
required progress.

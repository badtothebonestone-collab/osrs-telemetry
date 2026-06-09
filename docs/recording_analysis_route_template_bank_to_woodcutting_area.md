# Route Template: Bank To Woodcutting Area

Generated from:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
```

Template path:

```text
route_templates\Bank_to_Woodcutting_area.route_template.json
```

## Extraction Result

- Template schema: `route_template.v1`
- Route name: `Bank_to_Woodcutting_area`
- Template revision: `2`
- Start area: `bank_area`
- End area: `woodcutting_area`
- Required segments: `5`
- Optional/context segments: `1`
- Review evidence promoted to required segments: `0`

Required template segments:

1. `area_start`: Start: bank_area
2. `walk_segment`: Walk, postcondition `movement`
3. `stair_transition`: Climb-down Staircase, postcondition `plane_change`, plane delta `-2`
4. `walk_segment`: Walk, postcondition `movement`
5. `area_arrival`: Arrive: woodcutting_area

Optional/support template segments:

- `door_transition`: Open Door, retained as navigation/support evidence only

Audit result: Door/Open appeared in the original v2 recording and was
incorrectly promoted to a required segment by the first extraction pass. User
review confirmed the route does not require opening a door, so revision 2 of
the template demotes Door/Open.

## Self Comparison

Compared the source recording back to the extracted template.

- Status: `PASS`
- Score: `1.0`
- Matched / required segments: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Optional segment matches: `1` (`Open Door`)
- Out-of-order segments: `0`
- Weak segments: `0`
- Failed postconditions: `0`
- Review evidence: `2`, not treated as template failures

Verdict: the extracted template exactly matches the fresh baseline recording.

## Older Fixture Comparison

Compared:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area
```

Result:

- Status: `WARN`
- Score: `1.0`
- Route name matched: `true`
- Start area matched: `true`
- End area matched: `true`
- Matched / required segments: `5 / 5`
- Extra segments: `2`
- Weak/partial segments: `2`
- Review evidence: `1`, not treated as a template failure

Why WARN:

- The older recording contains extra stair-transition segments.
- Its Staircase segment matched the template action but had plane delta `-1`
  where the fresh template expects `-2`.
- Door/Open is no longer evaluated as required route progress for this route.

This is acceptable behavior for comparison: the older recording reached the
same route endpoints, but the path evidence differs enough to require review.

## Commands Run

Extraction:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --extract-route-template --route-template-out route_templates --print-traversal-lifecycle --print-route-segments
```

Self comparison:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-template-comparison
```

Older fixture comparison:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-template-comparison
```

## Limitations

- Plane delta is inferred from the successful baseline. A different but valid
  route variant can become WARN if it uses a different floor sequence.
- Extra route-progress segments become review signals, even when the final
  destination is correct.
- Door/Open and Large door evidence are optional navigation/support evidence
  for this route, not readiness gates.

# Route Template Second Fresh Run

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_105427_manual_route-bank_to_woodcutting_area_v3
```

Template used:

```text
C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json
```

## Summary

- Traversal route verdict: `PASS`
- Route name: `Bank_to_Woodcutting_area`
- Start/end area: `bank_area` -> `woodcutting_area`
- Duplicate live Arduino click issue: not present
- Feedback loop / panic stop: not present
- Explicit minimap clicks detected: `0`
- Likely navigation-support click: event `97`, `Walk here` / `Large door`

The route is valid against the corrected revision-2 template. The earlier
variant registration result is now superseded: user review confirmed Door/Open
is not required for this route, so `Walk here Large door` is navigation-support
evidence rather than a required-segment substitution.

## Corrected Template Comparison

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_105427_manual_route-bank_to_woodcutting_area_v3" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-traversal-lifecycle --print-route-segments --print-route-template-comparison
```

Current comparison after the semantics audit:

- Status: `PASS`
- Status reason: `PASS_BASE_TEMPLATE`
- Score: `1.0`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Extra segments: `0`
- Allowed extra segments: `3`
- Navigation-support substitutions: `0`
- Navigation-support evidence: `1`
- Review evidence segments: `2`
- Out-of-order segments: `0`
- Weak segments: `0`
- Failed postconditions: `0`

The route reached `woodcutting_area` and matched all required route progress.
The Large door evidence is now recorded as support/review evidence:

```text
walk_segment: Walk here Large door
```

Evidence:

- Target quality: `strong`
- Movement after click: about `10.296` tiles
- Postcondition: `movement`
- Endpoint reached: `woodcutting_area`
- No failed door/open postcondition

## Deprecated Variant

The previous command registered a `walk_here_large_door` variant:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_105427_manual_route-bank_to_woodcutting_area_v3" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --extract-route-variant --route-variant-name "walk_here_large_door" --variant-description "Walk here Large door movement-support segment satisfies Door/Open when route progress and endpoint evidence are strong." --add-route-variant-to-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-variant
```

That variant is now deprecated in the template:

- Name: `walk_here_large_door`
- Deprecated reason: base Door/Open is no longer required
- Alternative: `walk_segment`, `Walk here` / `Large door`

The two partial post-arrival route-adjacent segments were allowed as review
evidence because the endpoint had already been reached:

- `Climb-up Staircase`
- `Climb-up Ladder`

## Minimap / Navigation Handling

The user noted one minimap/navigation click, but the artifacts did not contain
an explicit `minimap_click`. The relevant route-progress evidence is event
`97`, classified as a menu selection:

- Option/target: `Walk here` / `Large door`
- Row geometry: proven
- Target quality: `strong`
- Movement afterward: `3207,3213,0` -> `3198,3218,0`

This click is now treated as navigation-support evidence, not as a required raw
click segment, not as a variant substitution, and not as a route failure.

## Verdict

The second fresh route is stable against the corrected base template. No
registered variant is needed for Large door because Door/Open is not required
route progress for `Bank_to_Woodcutting_area`.

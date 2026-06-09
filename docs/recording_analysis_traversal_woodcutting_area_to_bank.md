# Traversal Analysis: Woodcutting Area To Bank

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260602_234307_manual_action-woodcuting_area_to_bank
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260602_234307_manual_action-woodcuting_area_to_bank" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --print-route-segments
```

## Verdict

PASS. The grouped lifecycle reconstructed the route from woodcutting-area
context back to bank-area context using movement and plane-transition evidence.

## Grouped Summary

- Route: `woodcutting_area_to_bank`
- Raw / grouped / route segments: `4 / 4 / 6`
- Successful route segments: `6`
- Partial route segments: `0`
- Review evidence: `0`
- Start/end: `woodcutting_area -> bank_area`
- Plane changes: `2`

## Route Segments

1. Start: `woodcutting_area`
2. Walk from trees toward the route transition
3. Climb-up Staircase, plane `0 -> 1`
4. Climb-up Staircase, plane `1 -> 2`
5. Walk into bank-area context
6. Arrive: `bank_area`

## Limitations

This fixture is useful for route-level movement and plane-transition lifecycle
validation, but not for modern OS-input target-click analysis. It has too
little real input evidence for menu-row or target-click inspection. New route
recordings should keep polling input, menu interactions, target quality, and
map-only Arduino mapping enabled.

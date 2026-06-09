# Traversal Analysis: Bank To Woodcutting Area

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --print-route-segments
```

## Verdict

PASS. The grouped lifecycle reconstructed a bank-to-woodcutting traversal with
movement, stair transitions, Door/Open target evidence, and arrival in the
woodcutting area.

## Grouped Summary

- Route: `Bank_to_Woodcutting_area`
- Raw / grouped / route segments: `7 / 6 / 8`
- Successful route segments: `7`
- Partial route segments: `1`
- Review evidence: `1`
- Partial raw steps resolved: `1`
- Start/end: `bank_area -> woodcutting_area`
- Plane changes: `2`

## Route Segments

1. Start: `bank_area`
2. Walk on plane `2`
3. Climb-down Staircase, plane `2 -> 1`
4. Climb-down Staircase, plane `1 -> 0`
5. Open Door, strong target quality, movement/plane evidence
6. Walk toward the trees
7. Climb-up Staircase, partial because position changed but no plane change
8. Arrive: `woodcutting_area`

## Review Evidence

- `raw_step_006`: Climb-up Ladder, medium target quality, no matching
  plane/position postcondition in the window.

## Limitations

This older fixture predates the strongest menu-row preservation work, so some
menu-row geometry is missing. The route still passes because movement,
plane-change, and target-quality evidence are sufficient for traversal.

# Fresh Traversal Route Analysis

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --print-traversal-lifecycle --print-route-segments
```

## Verdict

PASS. The fresh recording is now clean enough to use as the bank-to-woodcutting
route baseline.

The important tuning result is that traversal now separates route-progress
segments from review-only click evidence. The route still preserves the noisy
click rows, but they no longer weaken the main route summary.

## Before And After

- Before grouping: raw steps `6`, successful `4`, partial `2`, review `0`.
- After grouping: raw steps `6`, grouped route steps `4`, route segments `6`,
  successful route segments `6`, partial route segments `0`, review evidence
  `2`.
- Route verdict remained `PASS`.
- Route remained `Bank_to_Woodcutting_area`.
- Start/end remained `bank_area -> woodcutting_area`.

## Route Summary

- Status: `PASS`
- Phase: `arrived`
- Confidence: `0.81`
- Duration: `37.11` seconds
- Tick range: `166 -> 222`
- Start: `bank_area`, world `3208,3220,2`, inventory full `false`
- End: `woodcutting_area`, world `3193,3243,0`, inventory full `false`
- Distance estimate: `52.025` tiles
- Plane changes: `1`
- Raw / grouped / route segments: `6 / 4 / 6`
- Supporting / review evidence: `4 / 2`

## Route Segments

| Segment | Type | Label | Result | Evidence |
| --- | --- | --- | --- | --- |
| 1 | area_start | Start: bank_area | success | start world `3208,3220,2` |
| 2 | walk_segment | Walk | success | player moved `3208,3220,2 -> 3205,3209,2` |
| 3 | stair_transition | Climb-down Staircase | success | plane changed `2 -> 0` |
| 4 | walk_segment | Walk | success | player moved `3206,3208,0 -> 3193,3243,0` |
| 5 | door_transition | Open Door | success | strong target quality plus movement after Door/Open |
| 6 | area_arrival | Arrive: woodcutting_area | success | end world `3193,3243,0` |

## Review-Only Evidence

| Raw step | Evidence | Reason |
| --- | --- | --- |
| raw_step_002 | Bank table menu selection | bank context did not produce a route-progress postcondition |
| raw_step_005 | Climb-up Staircase target click | target evidence lacked a matching movement, plane, or widget postcondition |

These rows are still useful for debugging target quality and menu evidence, but
they are not route-progress segments for this completed route.

## Input And Mapping Quality

- Input trace: PASS, `219` events, `217` real events.
- Raw OS clicks: `8`
- Eligible game-action clicks: `3`
- Target quality: PASS, strong `3`, medium `0`, weak `0`, unmatched `0`
- Menu interactions: WARN, menu opens `2`, selections `3`, row geometry `0`,
  linked targets `3`
- Coordinate alignment: WARN, transform `client_inverse_dpi_1_75`
- Input path: PASS, `os_polling_only`
- Arduino live commands: `0`
- VM mapping: PASS, conversion trace only
- Duplicate clicks: `0`
- Post-action movement/click commands: `0 / 0`
- Feedback loop suspected: `false`
- Panic stops: `0`

## Evaluation

- Expected start area: yes.
- Expected end area: yes.
- Door/Open detection: yes, strong target quality and movement support.
- Plane transition alignment: yes, Climb-down Staircase is a successful
  `stair_transition`.
- Partial raw target-click rows: resolved into review evidence.
- Success without evidence: no severe case.
- Area labels: correct.
- Postcondition windows: good for this route after grouping.
- Target quality requirements: good for supporting route actions without
  promoting every strong retained click into a route segment.

## Remaining Review Evidence

The recording still has two review-only rows. That is acceptable for traversal:

- Bank table is bank-context evidence, not route progress.
- Climb-up Staircase lacks the postcondition needed to prove it advanced this
  route.

## Baseline Decision

The fresh route summary is clean enough as a baseline. Future traversal work can
use route segments first, then inspect `reviewEvidence` and `rawSteps` only when
debugging.

Template semantics audit note: although this recording observed a successful
`Open Door` route segment, user review later confirmed Door/Open is not
required for `Bank_to_Woodcutting_area`. The route template keeps Door/Open as
optional navigation/support evidence and gates readiness on start area, walking,
staircase/plane transition, and arrival in `woodcutting_area`.

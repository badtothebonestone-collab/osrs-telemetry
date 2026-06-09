# Route Template Semantics Audit

## Scope

Template audited:

```text
C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json
```

Recordings compared:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
C:\Users\badto\osrs-telemetry\recordings\20260606_105427_manual_route-bank_to_woodcutting_area_v3
C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC
```

## Root Cause

Door/Open became required because the first template extraction treated every
successful route-progress `routeSegment` as required unless it was context such
as `bank_context` or `task_context`.

The v2 baseline recording happened to include:

```text
door_transition: Open Door
```

That was real observed evidence, but it was not route semantics. The extractor
promoted it from "this happened in one successful run" to "future runs must do
this." The third fresh run proved that was too strict: it reached
`woodcutting_area` with the correct staircase/plane transition and no failed
postconditions, but without a captured Door/Open or Large door segment.

User review resolved the ambiguity: `Bank_to_Woodcutting_area` does not require
opening a door.

## Corrected Template Semantics

Template revision: `2`

Required route progress is now:

1. `area_start`: `bank_area`
2. `walk_segment`
3. `stair_transition`: `Climb-down Staircase`
4. `walk_segment`
5. `area_arrival`: `woodcutting_area`

Optional/support evidence:

- `door_transition`: `Open Door`
- `walk_segment`: `Walk here Large door`
- incidental menu evidence such as `Cancel`

The old registered `walk_here_large_door` variant is deprecated because it only
existed to satisfy the now-removed Door/Open requirement.

## Comparison Results After Correction

| Recording | Status | Reason | Matched | Missing | Allowed/review evidence |
| --- | --- | --- | --- | --- | --- |
| v2 baseline | `PASS` | `PASS_BASE_TEMPLATE` | `5 / 5` | `0` | optional `Open Door` matched |
| v3 Large door run | `PASS` | `PASS_BASE_TEMPLATE` | `5 / 5` | `0` | `Walk here Large door`, `Climb-up Staircase`, `Climb-up Ladder` allowed |
| third no-door run | `PASS` | `PASS_BASE_TEMPLATE` | `5 / 5` | `0` | `Cancel` review evidence allowed |

Door/Open is not listed as missing in any corrected comparison.

## Analyzer Behavior

The analyzer now surfaces:

- template revision
- template notes
- optional/context segment count
- navigation-support evidence count
- review evidence segment count
- route semantics notes for `Bank_to_Woodcutting_area`

For this route, comparison wording explicitly says:

- Door/Open is not required.
- Walk here / Large door is navigation support.
- Cancel is review evidence.

## Validity

The third fresh recording is now a valid route-template PASS, not a WARN. It
matched the required route progress and reached the expected endpoint. The
missing Door/Open segment is no longer a defect.

The corrected template is appropriate as a route readiness baseline. Future
WARN/FAIL results should come from true route problems: wrong endpoint, missing
staircase/plane transition, out-of-order required progress, failed
postconditions, or weak evidence that affects a required segment.

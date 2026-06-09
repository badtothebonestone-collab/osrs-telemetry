# Route Monitor: Bank to Woodcutting Area

Generated: 2026-06-06

## Scope

Implemented and verified `route_monitor_status.v1` for the corrected
`Bank_to_Woodcutting_area` route template revision 2:

```text
route_templates\Bank_to_Woodcutting_area.route_template.json
```

The monitor was tested in three modes:

- recording monitor against the v2 baseline fixture
- recording monitor against the third fresh route fixture
- live readiness against the newest telemetry session

## Template Semantics

Template revision: `2`

Required route progress:

1. `area_start`: `Start: bank_area`
2. `walk_segment`: `Walk`
3. `stair_transition`: `Climb-down Staircase`
4. `walk_segment`: `Walk`
5. `area_arrival`: `Arrive: woodcutting_area`

Door/Open, `Walk here Large door`, and Cancel remain optional navigation or
review evidence and are not required for this route.

## v2 Fixture

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2
```

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "C:\Users\badto\osrs-telemetry\recordings\20260606_094608_manual_route-bank_to_woodcutting_area_v2" --json
```

Result:

- monitor status: `PASS`
- route state: `arrived`
- current area: `woodcutting_area`
- start area matched: `true`
- end area matched: `true`
- completed segments: `5`
- remaining segments: `0`
- off route: `false`
- comparison status: `PASS`
- comparison reason: `PASS_BASE_TEMPLATE`
- score: `1.0`
- warnings: none

## Third Fresh Fixture

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC
```

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --recording "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --json
```

Result:

- monitor status: `PASS`
- route state: `arrived`
- current area: `woodcutting_area`
- start area matched: `true`
- end area matched: `true`
- completed segments: `5`
- remaining segments: `0`
- off route: `false`
- comparison status: `PASS`
- comparison reason: `PASS_BASE_TEMPLATE`
- score: `1.0`
- warnings: none

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_121630_bank_to_WC" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --compare-route-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --route-monitor --route-monitor-template "route_templates\Bank_to_Woodcutting_area.route_template.json" --print-route-monitor
```

Analyzer result matched the standalone monitor:

- monitor status: `PASS`
- route state: `arrived`
- comparison reason: `PASS_BASE_TEMPLATE`
- completed/remaining: `5 / 0`
- route monitor artifact: `route_monitor_status.json`

## Live Readiness

Command:

```powershell
python telemetry-viewer\route_monitor.py --template route_templates\Bank_to_Woodcutting_area.route_template.json --latest-session --live --json
```

Latest live source:

```text
C:\Users\badto\.osrs-telemetry\sessions\2026-06-06_12-15-26
```

Result:

- monitor status: `WARN`
- route state: `stale`
- current area: `woodcutting_area`
- start area matched: `false`
- end area matched: `true`
- latest tick: `266`
- latest export sequence: `2661`
- source age: about `3011407.7 ms`
- freshness: `stale`
- warning: `telemetry stale`

Interpretation:

The live monitor correctly inferred the last known area from the current live
files, but refused to treat it as active route readiness because the source
files were stale. This is the desired behavior for safety: route readiness must
not be claimed from old telemetry.

## State Machine Behavior Verified

- `ready_at_start`: produced by synthetic live bank-area snapshots.
- `arrived`: produced by fresh/synthetic woodcutting-area snapshots and both
  complete route recordings.
- `stale`: produced by stale live source files.
- `off_route`: produced by fresh far-away synthetic snapshots or wrong endpoint
  comparisons.
- `blocked`: reserved for incomplete route evidence where the endpoint is not
  reached.

For recordings, an endpoint reached with incomplete direct segment evidence
becomes `WARN` + `arrived`, not a hard route failure. Wrong endpoints remain
`FAIL` + `off_route`.

## Current Limitations

- Live mode is snapshot-based. It does not yet keep persistent segment history
  across a running route.
- Live in-progress detection uses area labels, template endpoints, and a simple
  corridor heuristic; it does not perform pathfinding.
- `segment_complete` is reserved for future persistent monitoring.
- Stronger live confidence would come from current destination/path fields and
  fresh route-object/postcondition evidence.

## Verdict

Route monitor implementation is ready for route readiness/monitoring usage.

The completed v2 and third fresh recordings both report:

```text
PASS, arrived, 5/5 completed, offRoute=false
```

The current live snapshot reports:

```text
WARN, stale
```

That stale warning is correct because live telemetry has not updated recently.

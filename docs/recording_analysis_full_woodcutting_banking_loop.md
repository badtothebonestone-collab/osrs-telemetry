# Full Woodcutting / Banking Loop Review

Generated: 2026-06-07

## Recording

- Folder: `C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked`
- Note: the folder label still says `Wood_cutting_attacked`, but the recording contains the full loop: cutting, travel to bank, banking/deposit, travel back to trees, and resumed cutting.

## Verdict

- Full loop verdict: `PASS`
- Loop state: `complete`
- Confidence: `0.95`
- The recording is useful as a full woodcutting/banking loop fixture.
- The phrase “matched as one named route” would be misleading for this run. The analyzer now reports it as a task loop with route legs.

## Detected Loop Phases

- Woodcutting: `PASS`
  - Normal logs gained during cycles: `28`
  - Fresh `Chop down` clicks: `8`
  - Animation 879 snapshots: `13`
  - Inventory filled during the loop from 28 starting free slots.
- Route to Bank: `PASS`, `woodcutting_area_to_bank`
  - Area transition: `woodcutting_area -> bank_area`
  - Transition step: `15`
  - Evidence: `Open Door`
- Banking: `PASS`
  - Bank open directly observed: `true`
  - Bank UI snapshots preserved: `42`
  - Bank container available: `true`
  - Bank container delta available: `true`
- Deposit: `PASS`
  - Loop layer confirms `Logs x28` from bank container delta plus Deposit-All action context.
  - Banking lifecycle direct deposit field remains conservative (`deposit.detected=false`), but its evidence records `Deposit-All Logs` and `Logs 153->181`.
- Route to Trees: `PASS`, `Bank_to_Woodcutting_area`
  - Area transition: `bank_area -> woodcutting_area`
  - Transition step: `19`
  - Evidence target: `Bank table`
- Resume Cutting: `PASS`
  - Combat interruption was detected as `mugger_attack`.
  - Task resumed after interruption and after the return leg.

## Route Interpretation

- `traversal_lifecycle.json` route name: `route_unknown`
- Traversal status: `PASS`
- Traversal start/end area: `woodcutting_area -> woodcutting_area`
- Route template comparison file: not present.
- Route monitor/history files: not present.

The traversal lifecycle contains one continuous traversal artifact for the whole loop, not one registered named route. Inside that continuous artifact, area transitions prove two route legs:

- `woodcutting_area_to_bank`: `woodcutting_area -> bank_area`
- `Bank_to_Woodcutting_area`: `bank_area -> woodcutting_area`

This means the loop should be displayed as a task loop with multiple phases. It should not be described as a single route template match.

## Files Updated By This Pass

- `woodcutting_loop_lifecycle.json`
  - Now includes explicit `routeLegs`.
  - Keeps `direction=multi_leg_loop`.
  - Names route legs as `Route to Bank` and `Route to Trees`.
- `schema_gap_report.md`
  - The analyzer section now includes loop phase and route leg wording.
- `summary.json`
  - Refreshed by the analyzer run.

## Simple Mode Summary Should Show

```text
Detected: Woodcutting Loop
Phases:
- Woodcutting: PASS
- Route to Bank: PASS, woodcutting_area_to_bank
- Banking: PASS
- Route to Trees: PASS, Bank_to_Woodcutting_area
- Resume Cutting: PASS
```

## Caveats

- No `route_template_comparison.json` was produced for this full-loop recording, so route evidence comes from traversal area transitions rather than registered template comparison.
- One traversal step has partial or unknown postcondition evidence.
- Some menu selections lack row geometry.
- Weak target quality matches are present, but route/banking/woodcutting postconditions still support the loop.
- Deposit-All still carries banking menu context while being region-classified as `minimap_click`; the loop remains valid because bank UI, bank container delta, and action context agree.

## Checks

- `python -m py_compile telemetry-viewer\woodcutting_loop_lifecycle.py telemetry-viewer\analyze_manual_recording.py telemetry-viewer\telemetry_ui.py` - PASS
- `python telemetry-viewer\tests\test_woodcutting_loop_lifecycle.py` - PASS, 13 tests
- `python telemetry-viewer\tests\test_telemetry_ui.py` - PASS, 36 tests
- `python telemetry-viewer\telemetry_ui.py --check` - PASS


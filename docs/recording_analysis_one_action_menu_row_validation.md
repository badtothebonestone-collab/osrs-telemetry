# One-Action Menu Row Validation

## Recording

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260606_085115_manual_action-menu_row_validation_one_action`

Analyzer rerun:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260606_085115_manual_action-menu_row_validation_one_action" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --print-menu-row-diagnostics --print-input-path-integrity --print-coordinate-alignment --print-menu-interactions --print-target-match-quality
```

## Verdict

`WARN`

The no-duplicate input/Arduino mapping safety layer is behaving correctly, but
this is not a clean one-action menu-row validation. The recording contains
`3` menu selections, not `1`, and the first selection still lacks row bounds.

Do not use this recording as the final gate for moving to route/traversal
lifecycle work. The pipeline is safe enough from a duplicate-click perspective,
but the menu-row capture goal needs one more cleaner one-action sample that
starts before the right click and stops after the first selected row.

## Summary

| Field | Result |
|---|---:|
| Duration | `34.656s` |
| Tick range | `65 -> 119` |
| Tick count | `33` |
| Telemetry freshness | good; `stale_sources={}`, `parse_failure_count=0` |
| Parse failures | `0` |
| Input events | `115` |
| Real input events | `113` |
| Mouse moves | `96` |
| Raw OS clicks | `5` |
| Eligible game-action clicks | `3` |
| Right-click menu opens | `0` |
| Menu selections | `3` |
| Menu selections with row geometry | `2` |
| Menu selections missing row geometry | `1` |
| Target quality | `PASS`; `3` strong |
| Coordinate alignment | `PASS`; normalized menu-row hits `2` |

## Menu Selections

| Event | Option / Target | Row Geometry | Source | Snapshot | Transform | Linked Target | Quality |
|---:|---|---|---|---|---|---|---|
| `12` | `Climb-down Staircase` | no | `fallback_target_link` | `menu_snapshot_0008` | `client_inverse_dpi_1_75` candidate, no row bounds | `Staircase`, id `56231` | `strong`, `1.0` |
| `39` | `Climb Staircase` | yes | `option_target_match` | `menu_snapshot_0023` | `client_inverse_scale_target_row_anchor` | `Staircase`, id `16672` | `strong`, `1.0` |
| `65` | `Climb-up Staircase` | yes | `option_target_match` | `menu_snapshot_0026` | `client_inverse_scale_target_row_anchor` | `Staircase`, id `56230` | `strong`, `1.0` |

The first selection had matching menu entries near the click, but all selected
candidate snapshots lacked menu bounds. Its missing-row reasons were:

- `menu_row_bounds_missing`
- `selection_inferred_from_game_target_without_row_geometry`

## Input And Arduino Mapping

| Field | Result |
|---|---:|
| clickPolicyUsed | `map_only` |
| totalArduinoLiveClickCommands | `0` |
| mapOnlyClickCount | `1` |
| duplicateClickLikelyCount | `0` |
| duplicateClickCandidateCount | `0` |
| postActionMovementCommandCount | `0` |
| postActionClickCommandCount | `0` |
| mirrorAutoPaused | `false` |
| panic stop count | `0` |
| feedbackLoopSuspected | `false` |
| possibleDoubleInput | `false` |

`arduino_action_commands.jsonl` contains two probe `MOVE` commands and one
dropped `CLICK` mapping record. The dropped click is expected for `map_only`;
it proves conversion/mapping without sending a live Arduino click.

## PASS Criteria Check

| Criterion | Result |
|---|---|
| menu selections: `1` | FAIL: `3` |
| row geometry proven: `1` | WARN: `2` proven, `1` missing |
| target quality strong or medium | PASS: `3` strong |
| clickPolicyUsed `map_only` | PASS |
| total Arduino live click commands `0` | PASS |
| mapOnlyClickCount `> 0` | PASS: `1` |
| duplicateClickLikelyCount `0` | PASS |
| postActionMovementCommandCount `0` | PASS |
| postActionClickCommandCount `0` | PASS |
| feedbackLoopSuspected `false` | PASS |
| panic stop count `0` | PASS |

## Biggest Remaining Gap

This was not a one-action validation. The OS trace captured no
`right_click_menu_open` events and recorded three left-click menu selections.
The first selected action still had no row bounds, so the menu-row proof is
partial even though target quality was strong.

## Move-On Decision

Not yet. The input capture, map-only click policy, duplicate-click prevention,
and post-action mirror safety are stable enough. The menu-row proof is not clean
enough to declare the whole menu/input/Arduino-mapping pipeline ready for
route/traversal lifecycle work from this recording alone.

Recommended next step: record one short single-action sample:

1. Start recording before right-clicking.
2. Right-click once on the target.
3. Select exactly one menu row.
4. Pause for about one second.
5. Stop recording.
6. Analyze latest.

Expected next PASS:

- one right-click menu open
- one menu selection
- selected row geometry present
- target quality strong or medium
- `clickPolicyUsed=map_only`
- no live Arduino click commands
- no duplicate click candidates
- no post-action mirror commands
- no panic stop
- no feedback loop

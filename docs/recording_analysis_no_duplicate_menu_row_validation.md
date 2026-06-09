# No-Duplicate Menu Row Validation Analysis

## Recording Inspected

`C:\Users\badto\osrs-telemetry\recordings\20260605_204307_manual_action-menu_row_validation_live_mirror_controlled`

No exact folder named `manual_action-menu_row_validation_no_duplicate_click` was present, so this report uses the newest Live Mirror Menu Row Validation recording created after the duplicate-click fix.

## Analyzer Command

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260605_204307_manual_action-menu_row_validation_live_mirror_controlled" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --print-input-path-integrity --print-coordinate-alignment --print-menu-interactions --print-target-match-quality
```

The analyzer completed successfully and regenerated the derived summaries.

## Verdict

`WARN`: the duplicate-click fix worked, but the recording is only a partial menu-row validation.

The click path is clean:

- `clickPolicyUsed`: `map_only`
- `totalArduinoLiveClickCommands`: `0`
- `mapOnlyClickCount`: `1`
- `duplicateClickLikelyCount`: `0`
- `possibleDoubleInput`: `false`

The menu/target layer is mixed:

- Target match quality: `PASS`, with `2` strong matches.
- Coordinate alignment: `PASS`.
- Menu interaction: `WARN`, because `1` of `2` menu selections had row geometry and `1` was inferred without row bounds.

## Timeline And Telemetry

| Field | Value |
|---|---:|
| Duration | `23.25s` |
| Tick range | `42 -> 77` (`23` snapshots) |
| Telemetry freshness | `PASS`: no stale sources reported, parse failures `0` |
| Input backend | `polling / polling` |
| Input events | `86` total, `84` real |
| Mouse moves | `69` |
| Keyboard events | `6` |
| Raw OS clicks | `3` |
| Eligible game-action clicks | `2` |
| Target-relative clicks | `2` |

## Menu, Coordinate, And Target Evidence

| Event | Classification | Selected Row | Row Geometry | Linked Target | Target Quality | Notes |
|---:|---|---|---|---|---|---|
| `4` | `menu_selection_click` | row `0`, `Climb Staircase` | missing | `Staircase / Climb`, id `16672`, world `3204,3207,1` | strong `1.0` | Strong logical target, but row bounds were missing. |
| `55` | `menu_selection_click` | row `3`, `Collect Bank booth` | proven | `Bank booth / Collect`, id `27270`, world `3210,3216,2` | strong `1.0` | Row bounds were present and the click was inside the row. |

Coordinate alignment selected `client_inverse_dpi_1_75` overall, with detected DPI scale `1.7413`. The Bank booth selection also had a direct `screen_minus_client_origin` row hit.

## Click Ownership

| Field | Value |
|---|---:|
| `clickPolicyUsed` | `map_only` |
| `totalArduinoLiveClickCommands` | `0` |
| `mapOnlyClickCount` | `1` |
| `duplicateClickCandidateCount` | `0` |
| `duplicateClickLikelyCount` | `0` |
| `liveClickWithoutSuppressionCount` | `0` |
| Click owners | `conversion_trace_click_only: 1`, `os_click_only: 2` |

Event `4` was mapped to Arduino click format as `map_1780710190689383900`, but it was not sent as a live Arduino `CLICK`. That is the intended behavior for safe menu-row validation.

## Arduino And Mirror Integrity

| Field | Value |
|---|---:|
| Arduino port/protocol | `COM6 / arduino_hid.v1` |
| Probe classification | `arduino_probe_verified_noisy` |
| Movement commands | `2` probe MOVE commands |
| Non-probe Arduino commands | `0` |
| Click commands | `0` |
| Post-action movement commands | `0` |
| Post-action click commands | `0` |
| Panic stops | `0` |
| Feedback loop suspected | `false` |
| Final mirror/input-path verdict | `PASS` for persistent-arm timing; input path `WARN` because this was probe-only plus map-only, not live command mirroring |

This is expected for `map_only`: the Arduino command path is probed, manual clicks are converted to mapping records, and no live Arduino click is emitted during the gameplay action.

## PASS Criteria Check

| Criterion | Result |
|---|---|
| `clickPolicyUsed: map_only` | PASS |
| `totalArduinoLiveClickCommands: 0` | PASS |
| `mapOnlyClickCount > 0` | PASS |
| `duplicateClickLikelyCount: 0` | PASS |
| Menu interaction PASS | WARN: row geometry missing for one selection |
| Row geometry proven | PARTIAL: proven for Bank booth, missing for Staircase |
| Target quality strong or medium | PASS: `2` strong |
| `postActionMovementCommandCount: 0` | PASS |
| `feedbackLoopSuspected: false` | PASS |
| `panicStopCount: 0` | PASS |

## Biggest Remaining Gap

Menu row geometry is still not preserved for every menu selection. The first action, `Climb Staircase`, linked cleanly to the game target and scored strong, but it lacked row bounds. The later `Collect Bank booth` menu selection did prove row bounds and inside-row hit testing.

That means the duplicate-click fix is validated, but the menu-row telemetry layer still needs one more preservation pass if the goal is "every right-click menu selection has row geometry."

## Validity

This recording is valid evidence that `map_only` prevents duplicate live Arduino clicks while preserving input classification, target-quality scoring, coordinate alignment, Arduino probe/mapping evidence, and VM mouse-to-Arduino conversion artifacts.

It is not a full PASS for menu-row geometry coverage because only `1` of `2` selections had row bounds.

## Next Recommended Task

Make menu row bounds preservation consistent for the first menu selection after a right-click. The most useful next recording should be a short single-action validation:

1. Right-click one object.
2. Select exactly one menu row.
3. Stop immediately.

Expected proof:

- one right-click menu open
- one menu selection
- row bounds present
- inside row bounds true
- linked target present
- strong target quality
- `clickPolicyUsed: map_only`
- `duplicateClickLikelyCount: 0`

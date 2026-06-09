# Menu Row Validation V2

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260603_031002_manual_action-menu_row_validation_V2`

Analyzer command run:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_031002_manual_action-menu_row_validation_V2" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --print-menu-interactions --print-target-match-quality
```

## Verdict

WARN: V2 proves that fresh menu telemetry is now being preserved, including open-menu bounds, entries, and normalized row bounds in the menu snapshot. It does not yet prove the selected menu row by row-bound hit testing.

The selected action is linked strongly to the game target, but the selected row geometry is still missing from the resolved selection.

## Recording Summary

| Field | Value |
| --- | --- |
| Duration | 25.328 seconds |
| Tick range | 236 -> 274 |
| Snapshots | 29 |
| Parse failures | 0 |
| Stale sources | 0 |
| Input backend requested / used | polling / polling |
| Input events | 88 total, 86 real |
| Mouse moves | 76 |
| OS clicks | 2 |
| Keyboard events | 4 |
| Camera segments | 1 |
| Arduino status | WARN, bridge connected, 3 events, 1 error, no action commands |

## Menu Interaction Result

| Field | Value |
| --- | --- |
| Right-click menu opens from OS input | 0 |
| Menu selections | 1 |
| Menu rows resolved | 1 |
| Menu selections linked to targets | 1 |
| Menu selections with row geometry | 0 |
| Menu selections missing row geometry | 1 |
| Target-relative clicks | 1 |
| Strong target matches | 1 |
| Medium / weak / unmatched | 0 / 0 / 0 |

The resolved menu selection was:

| Field | Value |
| --- | --- |
| Event seq | 37 |
| Classification | menu_selection_click |
| Selected option | Climb |
| Selected target | Staircase |
| Linked target ref | `1:3204:3207:52:47:GAME_OBJECT:16672:17482012596:1536` |
| Raw / effective id | 16672 / 16672 |
| Target world point | 3204, 3207, plane 1 |
| Target quality | strong, score 1.0 |
| Selection confidence | 0.65 |
| Row bounds | missing on resolved selection |
| Inside row bounds | unknown |

## What Improved

The live telemetry stack was working during this recording. The summary now shows `open_menu_bounds`, `open_menu_entries`, `open_menu_state`, and `hover_entries` as present. Source freshness was clean, with no stale sources and no parse failures.

The normalized menu snapshot attached to the selection preserved fresh menu geometry:

| Row | Option | Target | Bounds |
| --- | --- | --- | --- |
| 0 | Examine | Staircase | x=369, y=206, w=103, h=8.67 |
| 1 | Walk here | | x=369, y=214.67, w=103, h=8.67 |
| 2 | Climb-down | Staircase | x=369, y=223.33, w=103, h=8.67 |
| 3 | Climb-up | Staircase | x=369, y=232, w=103, h=8.67 |
| 4 | Climb | Staircase | x=369, y=240.67, w=103, h=8.67 |

This is the important improvement over the older validation fixture: the menu bounds and rows are no longer lost.

## What Still Failed

The selected OS click was recorded at client position approximately x=570, y=349, while the menu bounds were x=369..472 and y=206..258. Because those coordinates do not overlap, the resolver could not prove that the click landed inside the `Climb Staircase` row.

The analyzer therefore linked the selection through target/action fallback evidence instead of row geometry. That is why the target match is strong but the menu interaction summary still reports:

- `menuSelectionsWithRowGeometryCount`: 0
- `menuSelectionsMissingRowGeometryCount`: 1
- `menu_row_bounds_missing`
- `selection_inferred_from_game_target_without_row_geometry`

There is also no OS-level right-click open event in the input classification summary:

- `rightClickMenuOpenCount`: 0
- `menuSelectionClickCount`: 1

The telemetry did observe the open menu, but the input trace did not preserve the right-click as a classified menu-open click.

## Interpretation

This is not a Java bridge failure first. V2 shows the plugin/telemetry side can provide menu bounds and entries when the stack is enabled.

The remaining problem is in Python-side coordinate alignment and selection resolution:

1. OS input click coordinates and RuneLite menu/canvas bounds appear to be in different coordinate spaces.
2. The resolver should attach the matching `Climb Staircase` row by option/target as fallback evidence, while still marking `insideRowBounds=false` until coordinate alignment is fixed.
3. The input classifier should preserve or infer the right-click menu-open event from OS input plus `MenuOpened` telemetry.

## Remaining Menu Gaps

- Convert OS screen/client click points into the same coordinate space as RuneLite canvas/menu bounds.
- Preserve the right-click menu-open action as a classified input action when telemetry confirms `MenuOpened`.
- Resolve menu rows by both row hit-test and option/target matching, clearly separating those evidence types.
- Keep schema gap wording clear: `open_menu_row_geometry` is now available in normalized analyzer output, but not as a direct live source field.

## Next Recommended Task

Fix menu/input coordinate alignment and menu-selection row resolution.

Target outcome for the next validation:

- `rightClickMenuOpenCount >= 1`
- `menuSelectionsWithRowGeometryCount >= 1`
- selected row for `Climb Staircase` has row bounds
- `insideRowBounds=true`
- row center distance is reported
- target quality remains strong without relying only on target/action fallback

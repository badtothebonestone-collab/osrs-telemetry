# Menu Row Validation Recording

Verdict: **WARN: selection is linked, but row geometry is still missing or partial**

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_025316_manual_action-menu_row_validation
```

Analyzer command rerun:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_025316_manual_action-menu_row_validation" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --print-menu-interactions --print-target-match-quality
```

## Recording Summary

| Field | Value |
|---|---|
| Duration | `17.984s` |
| Tick range | `145 -> 145` |
| Snapshot count | `1` |
| Input backend requested/used | `polling / polling` |
| Input capture status | `captured_real_input` |
| Input events | `99` total, `97` real |
| Mouse moves | `77` |
| Raw OS clicks | `3` |
| Keyboard events | `11` |
| Right-click menu opens | `1` |
| Menu selections | `1` |
| Eligible game-action clicks | `1` |
| Target-relative clicks | `1` |

## Menu Telemetry Check

| Check | Result | Evidence |
|---|---|---|
| Menu bounds present | **Yes, but stale/non-matching** | Snapshot hover sample had `menuBounds: {x:250,y:146,width:150,height:112}`. |
| Entries present | **Yes, but stale/non-matching** | Snapshot entries contained only `Cancel`, not `Climb-up Ladder`. |
| Rows resolved | `1` | Menu interaction summary resolved one selection from linked target evidence. |
| Selected row index | `0` | `menuSelection.selectedRowIndex = 0`. |
| Selected option | `Climb-up` | Inferred from linked target/action. |
| Selected target | `Ladder` | Inferred from linked target/action. |
| Row bounds present for selected row | **No** | `menuSelection.rowBounds = null`. |
| Click inside row bounds | **Unknown** | No selected-row bounds were available. |
| Row center distance | **Unavailable** | `rowCenterDistancePx = null`. |
| Linked game target | **Yes** | Ladder, action `Climb-up`, id `16683`, ref `0:3211:3242:51:74:GAME_OBJECT:16683:17493534003:1024`. |
| Target match quality | **Strong** | `quality=strong`, `score=1.0`. |

## What Worked

The preservation patch did keep menu fields in future normalized snapshots:

- `menuBounds` survived into `high_value_fields.hover`.
- `entries` survived into `high_value_fields.hover`.
- The menu interaction model created `menu_snapshot.v1` and `menu_row.v1` data for the captured menu sample.
- The OS click sequence was correctly classified as:
  - Event `4`: `right_click_menu_open`
  - Event `12`: `menu_selection_click`
- Event `12` linked to the logical game target `Ladder / Climb-up`.
- Target match quality correctly treated the menu-row click separately from object aim geometry.

## What Did Not Work

The row geometry did **not** prove the actual selected menu row.

The only source snapshot was at recording elapsed `3.406s`, and all live source files were stale by about `7,975s`. The hover/menu sample inside that snapshot had a telemetry timestamp around `2026-06-03T05:40:21Z`, while the recording happened around `2026-06-03T07:53:18Z`.

The captured menu sample described a `Cancel` row:

```json
{
  "menuOpen": false,
  "menuBounds": {"x":250,"y":146,"width":150,"height":112},
  "entryCount": 1,
  "topOption": "Cancel",
  "entries": [{"option":"Cancel","target":"","type":"CANCEL"}]
}
```

The selected action was `Climb-up Ladder`, so the analyzer had to infer the selected row from target/action evidence rather than from row bounds. That is why the summary reports:

- `menuSelectionsWithRowGeometryCount = 0`
- `menuSelectionsMissingRowGeometryCount = 1`
- `menu_row_bounds_missing`
- `selection_inferred_from_game_target_without_row_geometry`

## Remaining Menu Telemetry Gaps

- The recorder did not observe a fresh `MenuOpened` / open-menu `PostMenuSort` snapshot for the actual right-click menu.
- The live telemetry source set was stale during the recording.
- Only one source snapshot was captured, so there was no fresh before/open/selection/after menu timeline.
- The selected row bounds for `Climb-up Ladder` are still missing.

## Bridge/API Verdict

Another Java/plugin bridge change is **not the first fix**. Existing plugin code can export `menuBounds` and entries, and this recording proves those fields can survive normalization.

The next fix should focus on freshness/capture timing:

1. Ensure the live telemetry stack is updating before recording.
2. Run recovery/session check before the validation recording.
3. Capture fresh source snapshots around input clicks, especially immediately after a right click opens a menu.
4. Re-record the same validation with a live/updating session.

## Final Verdict

**WARN**: The system preserved menu bounds and entries in the snapshot shape, and it linked the selected game target correctly, but it did not capture row geometry for the actual selected `Climb-up Ladder` menu row. The validation is therefore partial, not complete.

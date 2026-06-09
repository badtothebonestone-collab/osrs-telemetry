# Menu Interaction Analysis: Bank To Woodcutting Area

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --print-menu-interactions --print-target-match-quality
```

## Summary

| Metric | Value |
|---|---:|
| Raw OS clicks | 7 |
| Eligible game-action clicks | 3 |
| Target-relative clicks | 3 |
| Right-click menu opens | 1 |
| Menu selections | 1 |
| Menu rows resolved | 1 |
| Menu selections with row geometry | 0 |
| Menu selections linked to targets | 1 |
| Missing row geometry selections | 1 |
| Strong target matches | 2 |
| Medium target matches | 1 |
| Weak target matches | 0 |
| Unmatched target matches | 0 |

## Row Geometry Availability

The Java/plugin side already has a menu-bounds export path:
`TelemetryPlugin.hoverMenuPayload()` writes `menuBounds` and `entries`.
Explicit per-row bounds are computable in Python from those fields and visual
row order, so no Java bridge change was made in this pass.

This specific fixture does not contain usable `menuBounds`/`entries` in the
manual recording events because the older high-value-field normalizer truncated
the hot-menu sample before those keys. The Python normalizer now preserves those
menu keys for future recordings.

## Event 128: Door/Open

Before this pass, Event 128 was correctly classified as
`menu_selection_click`, but target quality warned that the click was far from
the Door aim point. That was confusing because the OS click coordinate belonged
to the menu row, not the door.

After this pass:

- OS click type: `menu_selection_click`
- Selected menu row: `Open Door`
- Menu-row geometry: missing in this older fixture
- Linked game target: `Door/Open`, object id `1535`, ref
  `1:3207:3227:47:59:WALL_OBJECT:1535:1609719215:4`
- Target quality: `strong`, score `1.0`
- Explanation: menu row option/target, target identity/action, fresh telemetry,
  menu confirmation, and post-click result all support Door/Open. Object
  clickbox proximity is not required for a menu-row click.

Current warnings are now clearer:

- `menu_row_bounds_missing`
- `selection_inferred_from_game_target_without_row_geometry`
- `menu_row_geometry_missing`
- `object_clickbox_proximity_not_required_for_menu_selection`

## Target Quality After Separation

| Event | Classification | Target/action | Quality | Score | Notes |
|---:|---|---|---|---:|---|
| 128 | `menu_selection_click` | Door / Open | strong | 1.0 | Logical target/action confirmed; row bounds missing in this fixture. |
| 170 | `object_action_click` | Ladder / Climb-up | medium | 0.84 | Identity/action present, but geometry and post-click plane evidence weaker. |
| 322 | `object_action_click` | Staircase / Climb-up | strong | 1.0 | Clickbox/aim geometry and target/action evidence strong. |

## Outputs Generated

- `menu_interactions.jsonl`
- `menu_interaction_summary.json`
- `input_action_classifications.jsonl`
- `input_action_summary.json`
- `target_match_quality.jsonl`
- `target_match_summary.json`
- `joined_input_telemetry.jsonl`
- `camera_behavior_summary.json`
- `vm_mouse_arduino_mapping.json`
- `summary.json`
- `schema_gap_report.md`

## Remaining Menu Gaps

- This fixture cannot prove menu row bounds because `menuBounds` and full
  `entries` were not preserved in its normalized snapshot fields.
- Future recordings should preserve `menuBounds`, `entries`, and display order
  in high-value fields.
- A fresh right-click recording should confirm that row bounds are computed and
  `insideRowBounds` becomes true for menu selections.

## Next Recommended Recording

Record a short route with one deliberate right-click object interaction:

1. Hover the target.
2. Right click once.
3. Pause briefly with the menu open.
4. Left click the intended row.
5. Pause after the game reacts.

Use input capture, join input, camera behavior, and summary analysis enabled.
The expected result is one `right_click_menu_open`, one `menu_selection_click`,
row geometry present, and a linked strong target match.

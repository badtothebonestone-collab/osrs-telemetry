# Target Match Quality Analysis: Bank To Woodcutting Area

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --print-target-match-quality
```

## Counts

| Metric | Count |
|---|---:|
| Raw OS clicks | 7 |
| Eligible game-action clicks | 3 |
| Target-relative clicks after classifier | 3 |
| Strong target matches | 2 |
| Medium target matches | 1 |
| Weak target matches | 0 |
| Unmatched target clicks | 0 |

Target-relative count before/after classifier: `7 -> 3`.

Quality-tier result after classifier:

```json
{
  "strong": 2,
  "medium": 1,
  "weak": 0,
  "unmatched": 0
}
```

## Per-Click Quality

| Event | Classification | Target | Action | Quality | Score | Strongest Evidence | Warnings |
|---:|---|---|---|---|---:|---|---|
| 128 | `menu_selection_click` | Door | Open | strong | 1.0 | Linked to prior right-click/menu flow, target/action confirmed, fresh telemetry, post-click outcome matched. | Click coordinate is the menu row, not the object aim point; distance from object aim point was 268.663 px. |
| 170 | `object_action_click` | Ladder | Climb-up | medium | 0.84 | Target name/id/ref/action present, menu action available, fresh tick evidence, later position changed. | Aim-point distance was 115.109 px, target was not on screen, no plane change was observed in the post-click window. |
| 322 | `object_action_click` | Staircase | Climb-up | strong | 1.0 | Later target observation put the click inside the staircase clickbox, distance from aim point was 9.22 px, identity/action/ref were present, and position changed after click. | No plane change was observed in the post-click window. |

## Warnings

- `click_outside_clickbox`
- `large_distance_from_aim_px=268.663`
- `large_distance_from_aim_px=115.109`
- `menu_selection_click_coordinates_do_not_represent_object_aim_point`
- `target_not_on_screen`
- `climb_target_without_plane_change_in_window`

These warnings are useful rather than alarming. They distinguish action evidence
from precise clickbox evidence. Door/Open remains strong as a menu-selection
action, but its click offset should not train door aim geometry. Ladder/Climb-up
is useful route evidence but only medium target quality.

## Mapping Impact

`vm_mouse_arduino_mapping.json` now includes target quality counts:

- Strong target mappings: 2.
- Medium target mappings: 1.
- Weak target mappings: 0.
- Unmatched target mappings: 0.

Only strong/medium target clicks are emitted as default target-relative mappings.
Weak or unmatched rows are retained separately with warnings.

## Remaining Opportunities

The main remaining improvement is better menu row geometry and post-click route
outcome detection. A menu-selection click should ideally attach the exact menu
entry bounds/option/target rather than relying on nearby object geometry. For
climb actions, the post-click window should distinguish "clicked route step but
walked toward target" from "successfully changed plane."

## Next Recommended Recording

Record a short route with explicit markers:

- `before right click door/stairs`
- `after menu selection`
- `before ladder/stair click`
- `after plane/position change`

Pause briefly before and after each click. That will make freshness, menu row,
and post-click outcome evidence easier to validate.

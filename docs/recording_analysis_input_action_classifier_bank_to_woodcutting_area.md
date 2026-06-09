# Input Action Classifier Analysis: Bank To Woodcutting Area

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --print-input-action-summary
```

## Recording Summary

- Duration: 48.25 seconds.
- Tick range: 57 -> 128 across 16 snapshots.
- Description: post-log bank deposit, then route from bank booth/top-floor stairs toward the woodcutting area with normal camera movement.
- Input backend: polling requested and polling used.
- Input capture: PASS, 400 total input events, 398 real events.
- Raw OS clicks: 7.
- Mouse moves: 328.
- Keyboard events: 30.
- Camera behavior: PASS, 1 middle-mouse camera segment.
- Arduino: WARN, `arduino_bridge_connected`, 3 events, 0 action/movement/click commands, 1 error.
- VM mapping: PASS with classification applied.

## Raw Counts Before Classification

Before click classification, target matching treated the 7 raw OS click records as target-click candidates. That made the route analysis noisy because two middle-button camera drag releases and the telemetry UI control click were mixed into target-relative analysis.

## Classified Counts

| Metric | Count |
|---|---:|
| Raw OS clicks | 7 |
| Classified click rows | 7 |
| Eligible game-action clicks | 3 |
| Target-relative clicks | 3 |
| Excluded clicks | 4 |
| Camera drag releases excluded | 2 |
| Right-click menu setup clicks | 1 |
| Menu selection clicks | 1 |
| UI/control clicks excluded | 1 |
| Minimap clicks | 0 |
| Ambiguous clicks | 0 |

Classification counts:

```json
{
  "camera_drag_release": 2,
  "menu_selection_click": 1,
  "object_action_click": 2,
  "right_click_menu_open": 1,
  "ui_control_click": 1
}
```

## Target-Relative Count Before And After

- Before: 7 raw OS clicks could be interpreted as target-relative click candidates.
- After: 3 clicks remain target-relative eligible.

The remaining target-relative clicks are plausible game actions:

| Event | Time | Classification | Target | Action | Notes |
|---:|---:|---|---|---|---|
| 128 | 6.469s | `menu_selection_click` | Door | Open | Left click after right-click/menu context. |
| 170 | 12.266s | `object_action_click` | Ladder | Climb-up | Matched object target evidence. |
| 322 | 35.031s | `object_action_click` | Staircase | Climb-up | Matched route target evidence. |

## Excluded Examples

Camera drag releases are now excluded:

- Event 200, middle click at 16.906s: `camera_drag_release`, reasons `drag_distance_px=115.278`, `middle_mouse_drag`.
- Event 244, middle click at 23.797s: `camera_drag_release`, reasons `drag_distance_px=156.668`, `middle_mouse_drag`.

The telemetry UI click is now excluded:

- Event 373, left click at 42.031s: `ui_control_click`, reasons `foreground_window_is_telemetry_ui`, `drag_distance_px=174.909`.

The right-click setup click is visible but excluded from target-relative matching:

- Event 119, right click at 5.344s: `right_click_menu_open`.

## Camera And Mapping Impact

`camera_behavior_summary.json` now applies eligible-click filtering. The camera summary reports 2 camera drag releases excluded from target-click analysis and does not attach the middle-button releases as fake target clicks.

`vm_mouse_arduino_mapping.json` now maps:

- 3 game-action clicks.
- 3 target-relative clicks.
- 2 camera drag click/release records as camera-drag evidence.
- 4 excluded clicks.
- 0 ambiguous clicks.

## Verdict

The classifier fixes the noisy target-click problem for this recording. Middle-button camera drag releases are no longer counted as target-relative game actions, and the telemetry UI click is classified as `ui_control_click` instead of a game click. The resulting synchronized dataset is usable for route analysis:

- Game telemetry shows bank/stair/tree route context.
- OS input trace captures movement, clicks, drags, and keyboard events.
- Camera behavior captures the long middle-mouse camera adjustment.
- Target-relative click analysis is limited to plausible game-action clicks.
- VM mouse mapping carries both game-action click mappings and excluded click reasons.

## Remaining Gap

The biggest remaining gap is target geometry confidence. The retained Door and Staircase target-relative examples have large aim-point offsets, so they are useful route-action evidence but not yet precise enough to treat as calibrated clickbox training data without review.

Recommended next implementation task:

Add target-match quality tiers to `input_trace_joiner.py` and `input_action_classifier.py`: `strong`, `medium`, `weak`, and `unmatched`, based on click-to-aim distance, clickbox containment when available, menu action confirmation, and route-object confidence. Keep weak matches eligible for route evidence, but report them separately from high-confidence clickbox/aim training samples.

Recommended next recording:

Record a short bank-stairs-tree route with one deliberate click per transition, leaving the mouse idle for a moment before each click and adding markers before `door`, `ladder`, `staircase`, and `tree destination`. This will make target geometry confidence easier to validate.

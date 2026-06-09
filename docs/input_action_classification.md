# Input Action Classification

Manual input traces record every OS click, but not every click is a game action.
`input_action_classifier.py` labels click-like events before target matching so
camera drags, UI clicks, right-click setup, and menu selections are not mixed
with object/NPC/world actions.

## Outputs

The analyzer and joiner write:

```text
input_action_classifications.jsonl
input_action_summary.json
```

Each classification row uses `input_action_classification.v1` and includes the
OS event sequence, button, region, drag context, menu context, target context,
confidence, reasons, and whether the click is target-relative eligible.

## Raw Clicks vs Game-Action Clicks

- Raw OS clicks are direct `click` events from `input_events.jsonl`.
- Eligible game-action clicks are clicks that look useful for game action
  analysis: object/NPC actions, world-walk clicks, or linked menu selections.
- Target-relative clicks are the smaller subset that should be compared to
  target aim/clickbox geometry.

Target matching only runs for rows where `targetRelativeEligible` is true.
Target match quality then scores those retained clicks as `strong`, `medium`,
`weak`, or `unmatched`; see `docs/target_match_quality.md`.

## Labels

The classifier may emit:

- `object_action_click`
- `npc_action_click`
- `game_action_click`
- `world_walk_click`
- `right_click_menu_open`
- `menu_selection_click`
- `camera_drag_click`
- `camera_drag_release`
- `minimap_click`
- `ui_control_click`
- `inventory_click`
- `sidebar_click`
- `chatbox_click`
- `window_chrome_click`
- `external_click`
- `ambiguous_click`

## Camera Drags

Middle mouse down/move/up sequences over the drag threshold are classified as
camera drag evidence. The release click is written as `camera_drag_release` and
is excluded from target-relative object analysis.

## Right Click And Menus

A right click in the viewport is `right_click_menu_open`. It is useful context,
but it is not a completed game action. A following left click with menu/target
evidence is `menu_selection_click` and may be target-relative eligible.

## UI And Regions

Clicks outside RuneLite are `external_click` unless the foreground window is the
desktop telemetry UI, where they are `ui_control_click`. Minimap, inventory,
sidebar, chatbox, and window chrome clicks stay visible as their own labels and
are excluded from object target matching.

## Reading The Summary

`input_action_summary.json` reports raw OS clicks, eligible game-action clicks,
target-relative clicks, excluded clicks, classification counts, exclusion
reasons, and examples. A healthy route recording usually has fewer
target-relative clicks than raw OS clicks because camera and UI activity has
been removed.

After classification, inspect `target_match_summary.json` to see which retained
target-relative clicks are reliable target/action matches and which are only
weak route evidence.

## Menu Selection Details

`menu_selection_click` rows now carry a `menuSelection` block when evidence is
available. It includes the selected row index, option, target, row bounds,
inside-row result, row-center distance, and linked game target. This lets later
analysis say "clicked the Open Door row" separately from "the row represented
the Door/Open target."

If row bounds are missing, the classifier keeps the linked target/action visible
and adds a warning instead of pretending the menu-row click was an object
clickbox click.
## Coordinate And Input Path Fields

Classified click records can include `normalizedMenuPoint`, `coordinateTransformUsed`, `inputPathClassification`, and `mirrorVerificationStatus`. Target-relative matching should use normalized menu points for menu selections and should keep camera/UI/minimap clicks excluded before target quality scoring.

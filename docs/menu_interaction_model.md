# Menu Interaction Model

`menu_interaction_model.py` separates right-click menu behavior into two
coordinate concepts:

- the OS click point used to open or select a menu row
- the underlying game target/action represented by that row

This matters because a left click on `Open Door` is correctly located on the
menu row, not on the door clickbox.

## Schemas

- `menu_snapshot.v1`: open-menu state, menu bounds, rows in visual order, and
  source timing.
- `menu_row.v1`: row index, option, target, type/id params, row bounds/center,
  and linked target hints.
- `menu_selection.v1`: OS click point, selected row, row-bound hit test, linked
  game target, confidence, and warnings.

## Interaction Types

- Object click: a left click in the viewport that directly matches an
  object/NPC/world target.
- Right-click menu open: a right click that opens or probably opens a RuneLite
  context menu.
- Menu row selection: a later left click inside, or inferred to select, a menu
  row.
- Linked game target: the object/NPC/widget/world action represented by the
  selected row.

## Coordinates

- Mouse click point: captured from OS input in screen/client/canvas space.
- Menu row bounds: the rectangle of the selected menu row.
- Menu row center: the center of that row, used for row-selection confidence.
- Object clickbox/aim point: the game-world target geometry, used for direct
  object clicks but not required for menu-selection clicks.

## Target Quality

For `menu_selection_click`, target quality now scores:

- `menuSelectionQuality`: row bounds, row center distance, selected option, and
  selected target.
- `gameTargetQuality`: target/action identity, hover/menu evidence, freshness,
  and post-click outcome.

If row geometry is missing but the selected option/target and post-click result
are strong, the final target match can still be strong with a clear warning.

## Inspecting Output

- `menu_interactions.jsonl`: one row per right-click open or menu selection.
- `menu_interaction_summary.json`: compact counts and examples.
- `target_match_quality.jsonl`: target quality rows with menu-row and game-target
  quality separated.

## Fixture Example

In `20260603_003927_manual_action-Bank_to_Woodcutting_area`, Event 128 is a
`menu_selection_click` for `Open Door`. The OS click is on the menu row. The
linked target is `Door/Open`. Object clickbox proximity is not required for that
selection.

## Validation

Record a short right-click action with input capture enabled and include menu
entries in telemetry. A good recording should show:

- one `right_click_menu_open`
- one `menu_selection_click`
- a selected row with option/target
- row bounds when the hot-menu snapshot includes `menuBounds`
- a linked game target

## Snapshot Pairing

Menu selection analysis keeps a small buffer of recent menu snapshots instead
of using only the nearest or latest snapshot. A right click starts a menu
session when the OS trace captured it; if the recording begins with a menu
already open, the first left-click selection is treated as an implicit session.

For each menu selection, the analyzer searches snapshots around the click and
scores candidates by:

- whether menu bounds are present
- whether entries include the selected option/target
- whether computed row bounds contain the normalized click point
- whether the snapshot is close in time to the right click and selection
- whether stale fallback entries such as `Cancel` do not match the target

The selected record stores `selectedSnapshotId`, `selectedSnapshotReason`,
`selectedSnapshotScore`, `candidateSnapshotCount`, and a brief candidate list.
`rowGeometrySource` explains how the row was resolved:

- `direct_row_hit`: the normalized click landed inside a row.
- `option_target_match`: option/target evidence chose the row, often after a
  coordinate transform or target-row anchor fallback.
- `fallback_target_link`: no row geometry was proven, but the logical target
  was linked.
- `missing_snapshot` or `stale_snapshot`: no usable menu snapshot was available.

This fixes the common first-selection case where early `MenuOpened` snapshots
contain entries but zero-size bounds, and a later `PostMenuSort` snapshot has
the real row geometry.
## Coordinate Alignment

Menu selections now consume `coordinate_alignment_summary.json` and normalized click points when available. A menu-selection click keeps both the raw OS point and the normalized menu point so reports can distinguish the clicked menu row from the underlying game object target.

For the V2 row validation fixture, raw `client x=570, y=349` missed the menu bounds, while normalized menu coordinates selected the `Climb Staircase` row.

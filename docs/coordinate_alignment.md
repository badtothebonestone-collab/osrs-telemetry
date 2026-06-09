# Coordinate Alignment

Coordinate alignment exists because OS input, Windows client coordinates, RuneLite canvas coordinates, and RuneLite menu bounds can use different pixel spaces. In the V2 menu-row validation recording, the raw OS click was `client x=570, y=349`, while the RuneLite menu bounds were `x=369..472, y=206..258`. The raw point missed the menu, but inverse DPI-style transforms placed it inside the menu row.

## Coordinate Spaces

- `screen`: physical desktop cursor coordinates.
- `window`: coordinates relative to the outer foreground window.
- `client`: coordinates after Windows `ScreenToClient`.
- `canvas` / `runelite_canvas`: RuneLite game canvas coordinates when available.
- `menu`: RuneLite menu and row bounds.
- `unknown`: missing or untrusted coordinate source.

## DPI And Logical Pixels

Windows may report cursor movement in physical pixels while RuneLite menu geometry is effectively logical/canvas pixels. The analyzer now tries candidate transforms, including raw client points, inverse DPI scales, screen/client-origin transforms, and a target-row fallback when target/action evidence is strong.

V2 example:

- Raw client point: `570,349`
- Menu bounds: `x=369..472`, `y=206..258`
- Inverse scale `1.4`: `407.143,249.286`
- Result: inside the `Climb Staircase` row bounds.

## Outputs

Inspect:

- `coordinate_alignment_summary.json`
- `menu_interactions.jsonl`
- `target_match_quality.jsonl`

Important fields:

- `rawMenuRowHitCount`
- `normalizedMenuRowHitCount`
- `chosenTransform`
- `normalizedPoint`
- `selectedRow`
- `coordinateTransformReasons`
- `inputPathClassification`
- `mirrorVerificationStatus`

## Expected Result

For a good menu-row validation recording:

- raw point may miss the menu bounds
- normalized point should hit the menu bounds
- selected row should have bounds
- `insideRowBounds` should be `true`
- target quality should remain strong

## Menu Snapshot Pairing

Coordinate alignment is applied after the menu interaction layer chooses a
candidate snapshot. The pairing layer keeps multiple recent menu snapshots and
prefers a snapshot whose entries match the selected option/target and whose row
bounds contain the normalized click point.

This matters for first selections after right click: early `MenuOpened`
snapshots can have entries but zero-size bounds, while a later `PostMenuSort`
snapshot carries the usable row geometry. The analyzer can now select the later
bounded snapshot, preserve the raw click point, and record the transform that
placed the click into the selected row.

Use `--menu-row-diagnostics` to inspect `selectedSnapshotId`,
`candidateSnapshotCount`, `rowGeometrySource`, and candidate transform evidence.

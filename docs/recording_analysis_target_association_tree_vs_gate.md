# Target Association: Tree vs Gate

Date: 2026-06-08

## Recording

`C:\Users\badto\osrs-telemetry\recordings\20260607_190145_Cutting_a_tree_or_two_with_camera_movement`

## Click Under Review

- Event sequence: `49`
- Click id: `click_02_49`
- Actual click point: `{x: 521, y: 212}`
- Classifier label: `object_action_click`
- Hover/menu option: `Chop down`
- Hover/menu target: `<col=ffff>Tree`

## Postcondition Evidence

- `woodcutting_lifecycle.json`: PASS
- Phase: `log_gained`
- Animation 879 observed
- Fresh Chop down click count: `1`
- Normal logs gained: `3`
- The recording is useful as a successful woodcutting action sample.

## Geometry Candidates Considered

The stale/bad association path had two Gate-shaped inputs:

- nearest geometry candidate: `Gate / Close`
- classifier target context: `Gate / Close`
- candidate ref: `0:3185:3268:41:76:WALL_OBJECT:12988:13619045929:8`
- candidate kind: `route`
- candidate world: `{worldX: 3185, worldY: 3268, plane: 0}`
- old aim point: `{x: 342, y: 4}`
- old actual-to-aim distance: about `274.418 px`

After the geometry recovery fix, usable Tree aim candidates are present in preserved `nearby_objects`. The selected Tree clickbox is still unavailable.

## Root Cause

This was a Python target-association bug.

Before the fix:

- hover/menu identity was only allowed to override geometry for `menu_selection_click`
- this event was an `object_action_click`
- `_target_from_classification` and nearest-target geometry carried `Gate / Close`
- geometry proximity was allowed to become the matched target even though hover/menu and postcondition evidence said `Chop down / Tree`

That made an unrelated route/gate object look like the target of a successful woodcutting click.

## Fix Layer

Implemented in the analyzer target-association layer:

- `target_match_quality.py` now resolves intended target identity before geometry matching.
- hover/menu identity can apply to object-action clicks, not only menu-row selections.
- identity/action mismatch applies a severe rejection to unrelated candidates.
- rejected geometry is preserved in `targetAssociation.rejectedCandidates` instead of becoming `matchedTarget`.
- `target_match_summary.json` now includes target-association conflict examples.
- `schema_gap_report.md` now prints Target Association Diagnostics.

No Java/plugin change was made in this pass. The recorder had already preserved Tree candidate aim geometry; the remaining geometry gap is selected-Tree clickbox or tile-polygon availability.

## Result After Rerun

`target_match_quality.jsonl` now reports:

- matched target: `Tree`
- matched action: `Chop down`
- quality: `strong`
- association method: `hover_menu_identity`
- intended target class: `woodcutting_target`
- selected Tree ref: `0:3200:3246:56:54:GAME_OBJECT:1278:1340218168:0`
- Tree aim point: `{x: 489.0, y: 234.0}`
- actual-to-aim distance: `38.833 px`
- clickbox available: `false`
- rejected candidates: `Gate / Close` from nearest geometry and classifier target context
- warnings: `target_identity_conflicting_geometry_rejected`

## Interpretation

The Gate geometry is now correctly rejected. The Tree identity is correct. The recording contains a usable Tree aim point for the successful click, but not a selected-Tree clickbox.

For click planning, this means:

- compare profile points against actual clicks only when target geometry belongs to the intended target
- allow hover/menu/postcondition proof to establish successful action identity
- do not train human click profile from rejected unrelated geometry
- keep live profile-based clicking disabled until Tree clickbox/tile-polygon containment can be validated against fresh recordings

# Click Plan vs Actual Woodcutting

Date: 2026-06-08

## Recording

`C:\Users\badto\osrs-telemetry\recordings\20260607_190145_Cutting_a_tree_or_two_with_camera_movement`

This short woodcutting recording remains useful for validating successful human input. It also exposed a target-association bug: hover/menu and postcondition evidence proved `Chop down / Tree`, while nearest geometry had previously attached the click to `Gate / Close`.

## Interactions Compared

Successful Tree / Chop down interactions compared: 1

Actual click:

- Event: `click_02_49`, `eventSeq=49`
- Action evidence: hover/menu context showed `Chop down` / `Tree`
- Actual click point: `{x: 521, y: 212}` in client/canvas-style coordinates
- Button: left
- Drag distance: `0.0 px`
- Input path: `os_polling_only`

Postcondition proof:

- `woodcutting_lifecycle.json` status: PASS
- Phase: `log_gained`
- Animation 879 observed
- Fresh Chop down click count: `1`
- Normal logs gained: `3`
- Woodcutting cycles linked to the recording: `3`

## Target Quality After Geometry Recovery

`target_match_quality.jsonl` now resolves the successful click as:

- Target: `Tree`
- Action: `Chop down`
- Quality: `strong`
- Association method: `hover_menu_identity`
- Intended target class: `woodcutting_target`
- Click point: `{x: 521, y: 212}`
- Tree aim point: `{x: 489.0, y: 234.0}`
- Actual-to-aim distance: `38.833 px`
- Clickbox available: `false`
- Inside clickbox: `unknown`
- Rejected candidate: `Gate / Close`
- Rejection reasons: `target_name_conflict`, `target_action_conflict`, `route_or_gate_geometry_conflicts_with_woodcutting_action`

This is the correct safe behavior. The click is not trained as a Gate click anymore, and Gate geometry is not used as the Tree aim point.

## Planner Comparison

The planner can now reason from the resolved semantic target instead of the wrong nearby geometry.

Tree / Chop down context:

- Planner status: PASS when supplied the recovered Tree aim point and hover/menu evidence
- Target: `Tree`
- Action: `Chop down`
- Target quality: strong from resolved target match
- Center/aim point: `{x: 489, y: 234}`
- Profile-informed point: available
- Remaining caveat: no selected-Tree clickbox, so containment cannot be judged

This is now useful for profile-point-vs-actual comparison at the aim-point level. It is still not enough for clickbox containment tuning.

## Actual Landing Summary

- The human click succeeded with a recovered intended Tree aim point.
- The actual landing point is `38.833 px` from the recovered Tree aim.
- The aim-distance bucket is `le80`.
- The actual landing point is useful as a successful imperfect Tree click because the selected Tree clickbox is unavailable.
- Human click profile now records this as an imperfect successful `Tree / Chop down` click, not as `Gate / Close`.

## Planner Verdict

Verdict: PASS for aim-point planning, WARN for clickbox containment.

The original blocker was target association, not profile offset size. The association bug is fixed and Tree aim geometry is now recovered from preserved nearby object candidates. The remaining blocker is selected-Tree clickbox or tile-polygon availability.

## Recommended Tuning

Do not enable live profile-based clicking yet.

Next useful task:

1. Preserve/export selected-Tree clickbox bounds or canvas tile polygon.
2. Record another short Tree / Chop down sample.
3. Re-run this comparison and judge both profile-informed aim point and inside-clickbox containment.

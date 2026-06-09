# Tree Geometry Data Path

Date: 2026-06-08

## Recording

`C:\Users\badto\osrs-telemetry\recordings\20260607_190145_Cutting_a_tree_or_two_with_camera_movement`

## Event Under Review

- Click id: `click_02_49`
- Event sequence: `49`
- Actual click point: `{x: 521, y: 212}`
- Hover/menu evidence: `Chop down` / `<col=ffff>Tree`
- Analyzer matched target after fix: `Tree` / `Chop down`
- Postcondition evidence: `woodcutting_lifecycle.json` PASS, animation/log-gain evidence, `+3` normal logs

## Data Path Finding

Tree geometry was already preserved in the recording. It was not missing from the RuneLite/plugin bridge or manual recorder.

The preserved snapshots contained many `nearby_objects` and `route_objects`, including Tree candidates with:

- Tree names: yes
- Tree object ids: yes, including `1276` and `1278`
- World points: yes
- Local/scene points: yes
- On-screen flags: yes
- Aim points: yes
- Clickboxes: no for the selected Tree
- Canvas tile polygons: no
- Effective actions: mostly missing on the Tree candidates

The key analyzer gap was that `target_match_quality.py` previously required action agreement for same-name candidate recovery. The preserved Tree candidates had names and aim points, but no `effectiveActions`, so the analyzer did not use them for `Chop down / Tree`.

## Selected Tree Geometry After Fix

After rerun, `target_match_quality.jsonl` reports:

- Matched target: `Tree`
- Action: `Chop down`
- Quality: `strong`
- Score: `1.0`
- Association method: `hover_menu_identity`
- Selected Tree ref: `0:3200:3246:56:54:GAME_OBJECT:1278:1340218168:0`
- Tree world point: `{worldX: 3200, worldY: 3246, plane: 0}`
- Tree aim point: `{x: 489.0, y: 234.0}`
- Actual-to-aim distance: `38.833 px`
- Target on screen: `true`
- Clickbox available: `false`
- Inside clickbox: `null`
- Canvas tile polygon: not available

## Rejected Geometry

The unrelated Gate remains rejected:

- Rejected candidate: `Gate` / `Close`
- Gate ref: `0:3185:3268:41:76:WALL_OBJECT:12988:13619045929:8`
- Rejection reasons:
  - `target_name_conflict`
  - `target_action_conflict`
  - `route_or_gate_geometry_conflicts_with_woodcutting_action`

## Candidate Ranking

When multiple Tree candidates exist:

- Hover/ref-linked candidates are preferred when their geometry is plausible.
- If a hover/ref-linked candidate has implausibly distant geometry, the analyzer keeps it as an alternative and selects the best plausible identity-matching Tree candidate.
- Geometry proximity is used only after identity/action compatibility is established.
- Missing candidate actions are allowed for woodcutting targets when the candidate name is Tree/Oak and hover/menu evidence supplies `Chop down`.

## Capability Status

Present:

- `objectCandidate.geometry`
- `objectCandidate.aimPoint`
- `objectCandidate.canvasLocation`
- `objectCandidate.clickbox` generically
- `woodcutting.treeGeometry`
- `woodcutting.treeAimPoint`
- `hoverMenu.targetRef`
- `menuEntry.targetRef`

Still missing for the selected Tree:

- `woodcutting.treeClickbox`
- `objectCandidate.canvasTilePoly`
- Tree clickbox containment proof

## Fix Layer

No Java/plugin change was needed for this fixture. The plugin/live bridge and recorder already preserved enough Tree aim geometry.

Implemented fix layer:

- Analyzer target geometry recovery and ranking in `target_match_quality.py`
- Planner support for nested recovered `geometry.aimPoint`
- Schema/capability names for object/Tree geometry
- Analyzer schema-gap rescan so new capability names can apply to old recordings
- Knowledge base status update

## Interpretation

The system can now compare the successful human Tree click against the intended Tree aim point. It still cannot say whether the click landed inside the Tree clickbox, because clickbox/hull geometry for the selected Tree is not available in this recording.

Next useful validation is a fresh short Tree sample where the bridge exports Tree clickbox bounds or a canvas tile polygon, so profile tuning can compare both aim distance and clickbox containment.

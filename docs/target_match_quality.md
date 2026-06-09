# Target Match Quality

Input action classification decides which raw OS clicks are eligible game
actions. Target match quality is the next layer: it scores each retained
target-relative click as `strong`, `medium`, `weak`, or `unmatched`.

## Why It Exists

Some clicks are real game actions but still poor clickbox training samples. A
menu-selection click may strongly prove "Open Door" while its screen coordinate
is the menu row, not the door's aim point. A route-object click may have strong
identity/action evidence but weak geometry. Quality tiers keep those cases
usable without pretending every retained click is equally precise.

## Tiers

- `strong`: reliable target/action match. This may come from clickbox/aim
  geometry, strong menu evidence, or post-click outcome confirmation.
- `medium`: useful route/action evidence, but geometry or outcome evidence is
  incomplete.
- `weak`: plausible target, but important evidence is stale, missing, or
  contradictory.
- `unmatched`: no plausible target or score below threshold.

## Scoring Inputs

The scorer writes `target_match_quality.v1` rows using:

- Click geometry: click point, aim point distance, clickbox containment, target
  on-screen status.
- Target identity: name, raw/effective id, kind, stable ref, route-object
  confidence.
- Action evidence: target actions, menu action availability, classifier label,
  prior right-click/menu selection linkage, hover/menu text, telemetry click
  history.
- Freshness: nearest telemetry age, tick delta, export sequence delta.
- Post-click result: plane change, position change, animation, inventory change,
  widget open, menu close, and expected outcome match.

## Examples

- Door/Open via menu selection can be `strong` when the click is linked to a
  right-click/menu sequence and the target/action are confirmed, even if the
  click coordinate is far from the door aim point.
- Ladder/Climb-up can be `medium` when identity/action are present but target
  geometry is weak and no plane change is observed.
- Staircase/Climb-up can be `strong` when a later fresh target observation puts
  the click inside the staircase clickbox.
- Camera drag releases and UI/control clicks are excluded by input action
  classification before quality scoring.

## Outputs

Analyzer/joiner outputs:

```text
target_match_quality.jsonl
target_match_summary.json
```

`target_match_summary.json` includes raw quality counts, examples, and warnings.
`target_match_quality.jsonl` contains the evidence, reasons, warnings, geometry,
freshness, and post-click result for each scored target-relative click.

## Menu Row Separation

For `menu_selection_click`, the scorer writes two nested blocks:

- `menuSelectionQuality`: row-bound hit testing, row-center distance, selected
  option/target, and row geometry warnings.
- `gameTargetQuality`: the underlying object/NPC/widget/world target evidence.

Door/Open through a menu row can remain `strong` without object clickbox
proximity because the OS click belongs to the row. Missing row bounds are now
reported as a menu geometry gap rather than as a misleading object clickbox
failure.
## Menu Row Geometry

For `menu_selection_click`, target quality scores menu-row geometry separately from the underlying object or NPC target. A strong menu selection can be confirmed by row bounds, selected option/target, hover/menu evidence, and post-click result; object clickbox proximity is not required for menu-row clicks.

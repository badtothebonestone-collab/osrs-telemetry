# Menu Row Geometry Consistency

## Recording

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260605_204307_manual_action-menu_row_validation_live_mirror_controlled`

Rerun command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260605_204307_manual_action-menu_row_validation_live_mirror_controlled" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --print-menu-row-diagnostics --print-input-path-integrity --print-coordinate-alignment --print-menu-interactions --print-target-match-quality
```

## Verdict

`PASS`: menu row geometry is now preserved and associated for both retained menu selections.

- Duration: `23.25s`
- Tick range: `42 -> 77`
- Parse failures: `0`
- Menu selections: `2`
- Selections with row geometry: `2`
- Missing row geometry: `0`
- Linked targets: `2`
- Target quality: `PASS`, `2` strong matches
- Coordinate alignment: `PASS`
- Raw menu-row hits: `1`
- Normalized menu-row hits: `2`

## Per-Selection Timeline

### Event 4: Climb Staircase

- OS click: event `4`, left click, elapsed `3.484s`
- Captured right click: none in OS trace; analyzer used implicit menu session `menu_session_implicit_001`
- Selected option/target: `Climb Staircase`
- Linked game target: object `Staircase`, id `16672`
- Target quality: `strong`, score `1.0`
- Selected snapshot: `menu_snapshot_0005`
- Snapshot time: elapsed `5.172s`, tick `47`, export sequence `470`
- Snapshot source: `PostMenuSort`
- Menu bounds present: yes
- Entries present: yes, `6`
- Entries included selected option/target: yes
- Row bounds present: yes
- Click inside row bounds: yes, after `client_inverse_scale_target_row_anchor`
- Row geometry source: `option_target_match`
- Candidate snapshots considered: `6`

Candidate snapshots near the selection:

| Snapshot | Elapsed | Source | Bounds | Entries | Match | Row Bounds | Hit | Score |
|---|---:|---|---|---:|---|---|---|---:|
| `menu_snapshot_0005` | `5.172s` | `PostMenuSort` | yes | `6` | yes | yes | yes | `10.481` |
| `menu_snapshot_0003` | `3.578s` | `MenuOpened` | no | `6` | yes | no | n/a | `4.841` |
| `menu_snapshot_0002` | `2.797s` | `MenuOpened` | no | `6` | yes | no | n/a | `4.781` |
| `menu_snapshot_0004` | `4.375s` | `MenuOpened` | no | `6` | yes | no | n/a | `4.761` |
| `menu_snapshot_0001` | `2.015s` | `MenuOpened` | no | `6` | yes | no | n/a | `4.703` |
| `menu_snapshot_0006` | `6.015s` | `PostMenuSort` | yes | `2` | no | yes | yes, but stale `Cancel` row | `3.397` |

Why it used to fail:

The first selection happened at the start of the recording and the OS trace did
not include the right-click that opened the menu. The nearest menu snapshots had
the correct Staircase entries but zero-size `menuBounds`, so older pairing could
link the target but not prove row bounds. A later `PostMenuSort` snapshot had
the real menu bounds and matching Staircase entry, but it was not retained and
scored as a candidate for that first selection.

Why it works now:

The analyzer keeps a menu snapshot buffer, treats the first left-click selection
as an implicit session when the right click is missing, scores snapshots in the
retention window, and rejects the later stale `Cancel` snapshot because it does
not match `Climb Staircase`.

### Event 55: Collect Bank Booth

- OS right click: event `48`, elapsed `10.406s`
- OS selection click: event `55`, elapsed `14.625s`
- Menu session: `menu_session_002`
- Selected option/target: `Collect Bank booth`
- Linked game target: object `Bank booth`
- Target quality: `strong`, score `1.0`
- Selected snapshot: `menu_snapshot_0015`
- Snapshot time: elapsed `14.609s`, tick `63`, export sequence `630`
- Snapshot source: `MenuOpened`
- Menu bounds present: yes
- Entries present: yes, `14`
- Row bounds present: yes
- Click inside row bounds: yes, using `screen_minus_client_origin`
- Row geometry source: `direct_row_hit`
- Candidate snapshots considered: `5`

Why it already worked:

The right-click event was captured, the menu remained visible through the
selection, and nearby snapshots preserved full bounds and rows. The click point
hit row index `3` directly.

## Root Cause Classification

The original WARN was Python-side association, not a Java/plugin export gap.

- Missing raw menu snapshot: no
- Stale menu snapshot: partly, because a later `Cancel` snapshot could appear near the selection
- Menu snapshot overwritten: yes, effectively, because previous selection logic emphasized latest/nearest snapshot state
- Snapshot not retained long enough: yes
- Selection associated with wrong snapshot: yes
- Row index/order mismatch: no evidence
- Coordinate transform not applied: no, transform candidates worked
- Analyzer normalization loss: yes, in the sense that multiple candidates were not preserved for the selection
- Burst timing gap: not the main blocker for this recording, but burst-until-selection is now enabled for future captures
- Bridge/export change needed: no

## Fix Summary

- `menu_interaction_model.py` now builds a durable menu snapshot buffer.
- Selection pairing searches snapshots after the preceding right click, before
  the left click, and within a retention window around the selection.
- Candidate snapshots are scored by bounds, option/target match, row hit,
  freshness, and stale-target penalties.
- First selections without a captured right click get an implicit menu session.
- Diagnostics preserve candidate snapshots, selected snapshot id, selected
  snapshot reason, score, and `rowGeometrySource`.
- Menu row validation presets now enable menu capture burst until selection with
  a short tail.

## Remaining Gaps

- Event `4` still has no captured right-click event because the recording began
  with the menu already open. The row is now recovered, but the cleanest future
  validation should start before the right click.
- The `target_row_anchor_transform_used` warning is expected for Event `4`: it
  means the row was recovered by target-row anchored coordinate alignment rather
  than a raw point hit.

## Next Recording

Run a one-action validation that starts before the right click, selects exactly
one row, pauses briefly, then stops. Expected PASS criteria:

- one right-click menu open
- one menu selection
- selected row has bounds
- rowGeometrySource is `direct_row_hit` or `option_target_match`
- target quality is `strong` or `medium`
- no duplicate live Arduino clicks
- no post-action Arduino movement/click commands

# Latest Record Everything Recording Sweep

Generated: 2026-06-07

## Scope

Reviewed the two newest Record Everything / Simple Mode recordings:

1. `C:\Users\badto\osrs-telemetry\recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor`
2. `C:\Users\badto\osrs-telemetry\recordings\20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory`

Both recordings preserve useful input, camera, menu, target-quality, coordinate-alignment, traversal, and lifecycle artifacts.

## Result Summary

### Bank To Woodcutting Area

- Detected activity: Route / Traversal
- Traversal: PASS
- Route: `Bank_to_Woodcutting_area`
- Template comparison: PASS_BASE_TEMPLATE
- Required route segments matched: 5 / 5
- Route monitor: PASS, arrived
- Plane changes: 1
- Distance estimate: about 53.327 tiles
- Useful as fixture: yes

Route segments:

1. Start: bank_area
2. Walk segment
3. Climb-down Staircase, plane change success
4. Walk segment
5. Arrive: woodcutting_area

Useful evidence:

- Start/end areas are correct: bank_area -> woodcutting_area.
- Template revision 3 matched with score 1.0.
- Route monitor and offline analyzer agree on arrival.
- Input capture was real OS polling input.
- No live Arduino click commands were issued.

Weak spots:

- Input preflight failed before recording, but recording still captured real input.
- One source parse attempt hit a permission error on `live_status.json`; the recording artifacts still contain enough route evidence.
- One menu selection lacked row geometry, but route postconditions were strong enough.
- Staircase click was outside the recovered clickbox/aim point, but still succeeded.

Human click/camera notes:

- 5 OS clicks, 161 mouse moves, 8 keyboard events.
- Target-relative clicks: 2.
- Both target-relative clicks were imperfect by geometry: clickbox inside/outside/unknown/unavailable = 0 / 2 / 0 / 0.
- Aim distance median/max: 143.122 / 170.141 px.
- The route still succeeded, which is a useful reminder that target quality must use postconditions and menu context, not perfect clickbox centering alone.
- Camera behavior found 2 camera segments. The first large camera turn changed yaw by about +1008 and pitch by +117 before the Staircase click.

### Woodcutting Area No Logs To Full Inventory

- Detected activity: Woodcutting
- Woodcutting lifecycle: PASS
- Phase: inventory_full
- Confidence: 0.95
- Duration: about 155.969 seconds
- Useful as fixture: yes

Useful evidence:

- Free slots changed 16 -> 0.
- Normal Logs changed 0 -> 11.
- Oak logs also appeared, so the final full inventory was mixed logs rather than all normal Logs.
- Woodcutting animation 879 was observed in 22 snapshots.
- Fresh Chop down click evidence: 2.
- Input/menu tree hover evidence: 12 records.
- Inventory full was detected.

Weak spots:

- Direct tree candidates were not present in `treeCandidates`; tree evidence came from input/menu hover context instead.
- Some route/traversal evidence is noisy and should stay review-only for this task.
- Menu row geometry remains partial for several menu selections.
- Arduino is unavailable, which is fine for Record Everything but still appears as a warning.

Human click/camera notes:

- 10 OS clicks, 337 mouse moves, 24 keyboard events.
- One clear middle-mouse camera drag was captured: screen path 1397,347 -> 1209,330, delta -188,-17.
- Camera behavior found 6 camera segments and 5 camera-before-click cases.
- The task included imperfect human click behavior:
  - One useful Chop-down click was classified as `ambiguous_click` because of a 12.083 px drag.
  - The lifecycle now counts this correctly when the preserved menu hover says `Chop down` / `Tree`.
  - Target-relative clickbox inside/outside/unknown/unavailable = 0 / 2 / 0 / 2.
  - Aim distance median/max = 249.466 / 343.839 px.
  - Menu-row bounds were missing for 3 of 4 target-relative menu selections.

This supports a human-clicker lesson: the click does not need to land on the ideal aim point. A robust model should allow small drag during click, use hover/menu intent, and then validate success from animation, inventory, movement, or other postconditions.

## Fixes Made

### Woodcutting lifecycle input/menu evidence

`telemetry-viewer\woodcutting_lifecycle.py` now consumes `input_action_classifications.jsonl` evidence when available.

New behavior:

- A left click with `menuContext.hoverOption = Chop down` and `menuContext.hoverTarget = Tree` counts as woodcutting click evidence.
- Small human micro-drag clicks can still count when the preserved hover/menu context is clear.
- Input/menu tree hover evidence contributes to tree target evidence when direct tree candidates are absent.
- The analyzer recomputes woodcutting lifecycle with the current input classifications.

Impact on the woodcutting recording:

- Before: WARN, no fresh Chop down click, no tree target evidence.
- After: PASS, 2 fresh Chop down clicks, 12 input/menu tree hover evidence records.

### Click landing summary

`telemetry-viewer\target_match_quality.py` now writes `clickLandingSummary` into `target_match_summary.json`.

It includes:

- clickbox inside/outside/unknown/unavailable counts
- aim distance median/max/average/min
- aim distance buckets
- menu-row inside/missing-bounds counts
- examples of imperfect but useful clicks

The markdown report now prints this compactly under Target Match Quality.

## Remaining Gaps

- Tree candidate source should be reviewed later if we want direct tree object proof on every woodcutting recording.
- Menu row geometry is still partial in some right-click/menu selection cases.
- Raw Input device attribution is not implemented for polling backend.
- Arduino unavailable is expected and not a blocker for Record Everything.
- Human clicker tuning should use these recordings as tolerance evidence, especially micro-drag and click/postcondition success, rather than aiming for perfect clickbox centers.

## Commands Run

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor" --summary --schema-gap --woodcutting-lifecycle --banking-lifecycle --traversal-lifecycle --group-traversal-steps --auto-route-template --input-trace --join-input --human-input-summary --classify-input-actions --target-match-quality --menu-interactions --menu-row-diagnostics --coordinate-alignment --input-path-integrity --arduino-mirror-verification --camera-behavior --route-monitor --route-history --update-knowledge
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory" --summary --schema-gap --woodcutting-lifecycle --banking-lifecycle --traversal-lifecycle --group-traversal-steps --auto-route-template --input-trace --join-input --human-input-summary --classify-input-actions --target-match-quality --menu-interactions --menu-row-diagnostics --coordinate-alignment --input-path-integrity --arduino-mirror-verification --camera-behavior --update-knowledge
python telemetry-viewer\update_project_knowledge.py --scan-recordings --write-docs --json
```

## Checks Run

```powershell
python -m py_compile telemetry-viewer\woodcutting_lifecycle.py
python -m py_compile telemetry-viewer\target_match_quality.py
python -m py_compile telemetry-viewer\analyze_manual_recording.py
python telemetry-viewer\tests\test_woodcutting_lifecycle.py
python telemetry-viewer\tests\test_target_match_quality.py
```


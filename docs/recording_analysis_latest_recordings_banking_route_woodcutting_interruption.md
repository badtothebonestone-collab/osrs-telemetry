# Latest Record Everything Sweep: Banking, Route, Interrupted Woodcutting

Generated: 2026-06-07

## Recordings inspected

- `recordings/20260607_143719_Open_Bank_Deposit_logs_CLose_bank`
- `recordings/20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area`
- `recordings/20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs`

Analyzer profile used:

```text
--summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --auto-route-template --woodcutting-lifecycle --banking-lifecycle --human-click-profile --update-knowledge
```

## Verdict

The recordings are useful. They prove three important behaviors:

- Banking/deposit proof is strong and direct.
- Bank to woodcutting traversal still matches the route template cleanly.
- Woodcutting remains recognizable even with a human interruption, movement away, camera changes, and resumed chopping.

The interrupted woodcutting recording is not a strict full-inventory proof: telemetry ends at `27` normal logs and `1` free inventory slot. It is still a strong interrupted-task/resume fixture.

## Banking/deposit recording

Recording: `20260607_143719_Open_Bank_Deposit_logs_CLose_bank`

- Banking lifecycle: `PASS`
- Phase: `complete`
- Confidence: `0.95`
- Direct bank UI: present
- Bank container: available
- Bank container delta: available
- Deposit confirmation: `bank_container_delta_confirmed`
- Deposited:
  - `Logs x11`
  - `Oak logs x5`
- Inventory evidence: free slots `0 -> 16`

Useful evidence:

- The bank UI live-cache payload was preserved.
- Bank open and bank container evidence were direct.
- Deposit proof used inventory delta plus bank delta plus menu/action context.

Gap:

- Target-match rows were not present for this short banking recording, so click landing/profile data is weak here. Banking itself is still strongly proven.

## Bank to woodcutting route

Recording: `20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area`

- Traversal lifecycle: `PASS`
- Route template comparison: `PASS_BASE_TEMPLATE`
- Matched required segments: `5 / 5`
- Missing segments: `0`
- Failed postconditions: `0`
- Start/end: `bank_area -> woodcutting_area`
- Plane changes: `1`

Useful evidence:

- Route segments were clean:
  - Start bank area
  - Walk
  - Stair transition
  - Walk
  - Arrive woodcutting area
- Door/Open remained optional/review evidence, as intended.
- Two target-relative clicks were both strong.

Gaps:

- Some menu row geometry was still inferred from target context rather than direct row bounds.
- Arduino was unavailable, but this is not a route-recording failure because OS polling captured the human input.

## Interrupted woodcutting recording

Recording: `20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs`

- Woodcutting lifecycle: `PASS`
- Phase: `chopping`
- Confidence: `0.95`
- Normal logs: `0 -> 27`
- Free slots: `28 -> 1`
- Inventory full: `False`
- Fresh Chop down clicks: `11`
- Woodcutting animation `879` snapshots: `28`
- Input/menu tree hover evidence records: `50`

Useful evidence:

- The recording proves chopping continued after interruption.
- It captured real mouse movement: `1300` input events, `45` OS clicks, `1005` mouse moves, `30` keyboard events.
- It captured rich menu behavior: `20` menu selections and `2` right-click opens.
- It captured camera behavior: `10` camera segments, `3` middle-mouse drags, `9` camera-before-click segments.
- It captured many imperfect but successful clicks, which is valuable for human click modeling.

Important caveat:

- The mugger/attack/level-up story is not directly represented by named combat or level-up fields in the preserved artifacts. Searches found no `Mugger`, combat, level-up, hitpoints, or chat-message evidence promoted into the recording.
- The human response is visible indirectly as movement, camera, path, menu/action changes, and resumed woodcutting.

This means the current stack can say:

- The task was interrupted by movement/actions and later resumed.
- Woodcutting still succeeded.

It cannot yet say:

- The interruption was caused by a Mugger.
- The player was attacked.
- Attack level increased.
- The player ran to safety because of combat.

## Fix made during this sweep

Target-match quality was incorrectly trusting fallback linked route objects for some woodcutting menu selections when direct row geometry was missing. Example: hover/menu context said `Chop down / Tree`, but fallback target context linked the click to `Gate / Open` or `Staircase / Climb-up`.

The target-quality layer now prefers non-generic hover menu targets, such as `Chop down / Tree`, when they conflict with a weak fallback linked target.

Also fixed a postcondition matcher bug where `Chop down` was treated like a climb/down action because it contained the word `down`. `Chop down` now correctly uses animation/inventory postconditions.

After the fix, the interrupted woodcutting target summary includes:

- `Tree / Chop down`: `8` strong
- `Yew tree / Chop down`: `2` strong
- `Tree / Chop down`: `1` medium
- Remaining route-object/stair clicks reflect actual movement/interruption behavior.

## Updated human click/camera profile

Aggregate profile path:

- `telemetry-viewer/knowledge_base/human_click_profile.json`
- `docs/human_click_profile.md`

Profile now includes 7 useful recordings.

Summary:

- Status: `PASS`
- Raw clicks: `75`
- Target-relative clicks: `33`
- Strong / medium / weak: `31 / 2 / 0`
- Menu selections: `32`
- Right-click menu opens: `5`
- Camera segments: `20`
- Middle-mouse drags: `4`
- Camera-before-click: `15`
- Imperfect successful clicks: `32`
- Duplicate click likely: `0`

Click landing:

- Inside clickbox: `1`
- Outside recovered geometry: `11`
- Geometry unavailable: `21`
- Menu row inside: `12`
- Menu row missing bounds: `20`
- Median aim distance: `194.531 px`

Interpretation:

- Human clicks are often not center-perfect.
- Menu/hover/postcondition evidence is more reliable than recovered clickbox membership alone.
- Geometry missing/outside should not be treated as a failed click when animation, inventory, movement, widget, or route postconditions prove success.

## What is lacking

- Direct combat/interruption data: hostile NPC name, target-of-player, player health/hit splats, combat state, chat/game messages, and level-up events are not available in the offline report.
- Menu row geometry is still missing for many rows, though target/menu/hover context often recovers the action.
- The interrupted woodcutting recording stopped at `27` logs and `1` free slot, so it is not a strict full-inventory fixture.
- Arduino is unavailable in these recordings, but Record Everything still captured usable OS-polling input.

## Recommended next task

Add a small combat/interruption telemetry path:

- Plugin/live bridge: expose nearby hostile NPCs, player interacting target, combat state, hitsplats/health if available, chat/game messages, and level-up events.
- Recorder: preserve those packets in Record Everything.
- Analyzer: add a compact `task_interruption_summary.json`.
- Woodcutting lifecycle: report `interrupted_by_combat`, `retreated`, `resumed_chopping`, and whether task completion still succeeded.

Recommended next recording after that:

1. Start Record Everything.
2. Begin chopping with empty inventory.
3. Let a hostile NPC interrupt if it naturally happens.
4. Move away to safety.
5. Resume chopping until inventory is truly full.
6. Stop after one more telemetry tick after full inventory.


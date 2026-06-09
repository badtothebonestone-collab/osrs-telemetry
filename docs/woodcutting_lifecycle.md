# Woodcutting Lifecycle

`telemetry-viewer\woodcutting_lifecycle.py` turns raw manual recording and live
context telemetry into a compact read-only lifecycle summary for normal
woodcutting:

```text
fresh Chop down click -> animation active -> log added -> target depleted or retarget -> repeat -> inventory full
```

The lifecycle exists because raw telemetry is rich but noisy. It preserves the
important evidence without making the normal context response dump full raw
recordings.

## Signals Used

- `lastMenuOptionClicked` and menu entries for `Chop down` / `Tree`.
- Inventory `resourceCounts.normal_logs.count`, normal log item id `1511`, and
  `freeSlots`.
- Local player animation `879`.
- `woodcuttingState` values such as `woodcutting_possible`, `target_depleted`,
  and `inventory_full`.
- Resource object census tree candidates with name, id, action, world point,
  distance, on-screen/actionable state, and aim geometry.
- Event timeline target-depletion and inventory events when present.

## Fresh Click Filtering

`lastMenuOptionClicked` is sticky: it repeats the same click until a newer click
arrives. The lifecycle layer deduplicates clicks with a stable key made from
option, target, type, tick, timestamp, scene/canvas parameters, and mouse
position.

Click records are classified as:

- `fresh_chop_click`: a non-duplicate `Chop down` click on a tree with usable
  timestamp or tick evidence.
- `repeated_old_click`: the same click seen again in later snapshots.
- `pre_recording_click`: a click timestamped before recording start, such as an
  old `Drop Logs` click.
- `unrelated_click`: a fresh click that is not part of woodcutting.
- `ambiguous_click`: a possible click without enough freshness evidence.

## Inventory And Logs

The primary progress signal is normal logs, not generic inventory count.
`resourceCounts.normal_logs.count` is preferred. If that is unavailable, the
module counts item id `1511` in known inventory slots. It also reports
`freeSlotsStart`, `freeSlotsEnd`, and `inventoryFull`.

## Animation

Animation id `879` is treated as woodcutting animation evidence. The lifecycle
records active ticks, first/last seen tick, and active snapshot count.

## Target Depleted And Retargeting

`woodcuttingState: target_depleted` and event timeline `target_depleted` events
are recorded as depletion evidence. Cycles mark target depletion when it appears
near a log gain.

## Run On The Tree Recording

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260602_223444_manual_action-Tree_cutting" --summary --schema-gap --woodcutting-lifecycle
```

This updates:

```text
summary.json
schema_gap_report.md
woodcutting_lifecycle.json
```

For input-enabled recordings, add:

```powershell
--join-input --camera-behavior --vm-mouse-mapping
```

The woodcutting lifecycle remains telemetry-driven, while the input trace
explains the human mouse path, hover duration, camera movement, and
target-relative click offsets that led into each fresh Chop down click.

## Context Request

```powershell
python telemetry-viewer\context_service.py --latest-session --oneshot-request "{\"schema\":\"context_request.v1\",\"task\":\"woodcutting\",\"needs\":[\"baseline\",\"inventory\",\"activity\",\"best:tree\",\"woodcutting_lifecycle\"],\"maxCandidates\":1,\"responseMode\":\"compact\"}"
```

The compact response includes `woodcuttingLifecycle` with phase, confidence,
fresh click count, normal logs gained, current/free slots, inventory-full state,
animation state, target-depleted state, last tree target, and warnings.

## Phases

- `unknown`: no useful woodcutting signals.
- `idle`: context exists but no current lifecycle activity is visible.
- `tree_available`: a usable tree target is visible.
- `chop_clicked`: a fresh tree chop click was just observed.
- `chopping`: animation `879` is active.
- `log_gained`: logs increased in the observed window.
- `target_depleted`: tree depletion/retarget evidence is visible.
- `retargeting`: reserved for future richer route/target transition evidence.
- `inventory_full`: inventory is full or `freeSlots` is `0`.
## Interruptions

Woodcutting lifecycle summaries can include a compact `interruption` block when `interruption_lifecycle.json` is available. Old recordings can prove task stop/resume from gaps in Chop down, animation, and inventory evidence, but direct cause needs `combat_state` or chat/stat evidence.

Useful interruption fields:
- `interruption.interruptionDetected`
- `interruption.interruptionType`
- `interruption.primaryCause`
- `interruption.taskResumed`
- `interruption.combatObserved`
- `interruption.missingCapabilities`

When `combat_state` includes hitsplat amount, actor death, and HP fields, the
analyzer also writes `combat_damage_summary.json`. That summary records damage
taken/dealt, the primary opponent, HP before/after, and whether woodcutting
resumed after combat. The Mugger fixture proves this path while keeping
woodcutting lifecycle `PASS`.

## Loop Lifecycle

`woodcutting_loop_lifecycle.json` builds on this file. It combines woodcutting,
banking, route, interruption, and combat summaries to answer the task-level
question: what phase is the woodcutting loop in, and what should happen next?

For example, a woodcutting lifecycle ending with `inventoryFull=true` becomes
loop next phase `route_to_bank`.

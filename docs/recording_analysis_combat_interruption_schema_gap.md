# Combat / Interruption Schema Gap

Recording: `C:\Users\badto\osrs-telemetry\recordings\20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs`

## Result
- Direct combat shown: `no`
- NPC targeting player shown: `no`
- Player targeting NPC shown: `no`
- Hitsplats shown: `no`
- HP/health change shown: `no`
- Chat/game messages shown: `no`
- StatChanged / XP / level-up shown: `no`
- Task interruption shown: `present`
- Task resume shown: `present`

## Evidence Present
- Woodcutting lifecycle: `PASS`
- Normal logs: `0 -> 27`
- Woodcutting animation `879` observed.
- Fresh Chop down evidence resumed after a gap.
- Interruption analyzer found a stop/resume window from about `130.406s` to `222.031s`.

## Evidence Inferred Only
- The task was interrupted because woodcutting evidence stopped and later resumed.
- The user-reported mugger/attack-level event cannot be directly proven from this old recording.

## Requires Plugin / Export
- `combat_state`
- `combat.actorsInteractingWithPlayer`
- `combat.recentHitsplats`
- `combat.recentChatMessages`
- `combat.recentStatChanges`
- `combat.playerHealth`

## Computable In Python
- Task stop/resume gaps from lifecycle evidence.
- Interruption duration.
- Unknown-cause WARN classification when direct combat/message/stat evidence is absent.

## Script Exposure
- `get_combat_state(source)`
- `is_in_combat(source)`
- `get_interruption_lifecycle(source)`
- `was_task_interrupted(source)`
- `get_interruption_cause(source)`
- `get_recent_hitsplats(source)`
- `get_recent_stat_changes(source)`
- `get_recent_game_messages(source)`

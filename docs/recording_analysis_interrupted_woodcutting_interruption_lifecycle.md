# Interrupted Woodcutting Interruption Lifecycle

Recording: `C:\Users\badto\osrs-telemetry\recordings\20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs`

## Analyzer Result
- Interruption lifecycle: `WARN`
- Interruption detected: `true`
- Interruption type: `unknown`
- Primary cause: `unknown`
- Confidence: `0.45`
- Task before: `woodcutting`
- Task resumed: `true`
- Duration: `91625 ms`
- Direct combat observed: `false`
- combat_state snapshots: `0`
- Hitsplats seen: `0`
- Stat changes: `0`
- Chat/game messages: `0`

## Useful Evidence
- Woodcutting lifecycle remains `PASS`.
- Logs gained: `0 -> 27`.
- Fresh Chop down and animation evidence resumed after the interruption gap.
- The recording is useful as an old inferred-interruption fixture.

## Missing Evidence
- `combat_state`
- `combat.actorsInteractingWithPlayer`
- `combat.recentHitsplats`
- `combat.recentChatMessages`
- `combat.recentStatChanges`
- `combat.playerHealth`

## Interpretation
This recording proves a task interruption and resume, but not the cause. Future Record Everything recordings should preserve `combat_state.v1` so a mugger/NPC attack, hitsplat, level-up, chat message, or HP change can be classified directly.

## Follow-Up Direct Fixture

`C:\Users\badto\osrs-telemetry\recordings\20260607_154606_Wood_cutting_attacked`
now closes the direct-combat gap:

- Interruption lifecycle: `PASS`
- Cause: `mugger_attack`
- Hitsplats: `37`
- Damage taken/dealt: `5 / 9`
- HP: `10 -> 7`, lowest `6`
- Actor death: Mugger
- Task resumed: `true`

The older recording remains useful as a historical unknown-cause fixture, while
the newer Mugger recording proves direct combat interruption and damage summary.

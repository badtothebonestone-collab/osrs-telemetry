# Combat Damage Summary

`combat_damage_summary.py` turns preserved `combat_state.v1` snapshots into compact script-readable combat damage evidence.

## Data Path

- RuneLite plugin exports `combat_state.v1` through the plugin snapshot/cache path.
- Record Everything preserves `combat_state` snapshots into `events.jsonl`.
- `interruption_lifecycle.py` detects combat interruption and task resume.
- `combat_damage_summary.py` computes damage totals, HP change, primary opponent, actor death, and combat window.
- Analyzer writes `combat_damage_summary.json` and embeds compact fields into `summary.json`.
- Context, MCP, task script API, and knowledge base expose compact damage fields.

## What It Answers

- Damage taken by the local player.
- Damage dealt to the opponent, when hitsplat actor/interaction evidence supports it.
- Primary opponent name/id/confidence.
- Total player/opponent/ambiguous hitsplats.
- HP before/after/lowest observed HP.
- Combat start/end tick.
- Actor death.
- Whether the original task resumed after combat.

## Attribution Rules

- Local-player hitsplats count as damage taken.
- NPC hitsplats count as damage dealt when the local player was interacting with that NPC or that NPC is the primary opponent.
- HP decrease supports damage-taken evidence.
- Actor death supports combat outcome evidence.
- Multiple actors or missing amounts produce warnings instead of hidden assumptions.

## Script Helpers

- `get_combat_damage_summary(source)`
- `get_damage_taken(source)`
- `get_damage_dealt(source)`
- `get_primary_opponent(source)`
- `did_take_damage(source)`
- `did_deal_damage(source)`
- `get_recent_combat_window(source)`

## Fixture Proof

`recordings\20260607_154606_Wood_cutting_attacked` proves the current path:

- Interruption: combat / `mugger_attack`
- Primary opponent: Mugger
- Hitsplats: 37
- Damage taken: 5
- Damage dealt: 9
- HP: 10 -> 7, lowest 6
- Actor death: true
- Task resumed: true


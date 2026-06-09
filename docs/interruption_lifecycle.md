# Interruption Lifecycle

`interruption_lifecycle.py` summarizes task interruptions from Record Everything recordings and live combat telemetry.

## Data Path
- RuneLite plugin exports compact `combat_state.v1`.
- Record Everything preserves `combat_state` through the plugin snapshot endpoint.
- Analyzer writes `interruption_lifecycle.json`.
- Context/MCP/task script layers expose compact combat and interruption answers.

## Direct Evidence
- NPC/player interaction.
- Hitsplats.
- Player health changes.
- Chat/game messages.
- Stat or level changes.

## Inferred Evidence
- A task lifecycle, such as woodcutting, stops unexpectedly and later resumes.
- If no direct combat/message/stat evidence exists, the interruption remains `WARN` with `primaryCause=unknown`.

## Status Meaning
- `PASS`: direct cause evidence is present, or combat evidence is direct enough.
- `WARN`: interruption/resume is inferred but cause is unknown, or direct capability is missing.
- `FAIL`: no usable task or combat evidence is available.

## Script Helpers
- `get_combat_state(source)`
- `is_in_combat(source)`
- `get_interruption_lifecycle(source)`
- `was_task_interrupted(source)`
- `get_interruption_cause(source)`
- `get_combat_damage_summary(source)`
- `get_damage_taken(source)`
- `get_damage_dealt(source)`
- `get_primary_opponent(source)`
- `get_recent_hitsplats(source)`
- `get_recent_stat_changes(source)`
- `get_recent_game_messages(source)`

## Combat Damage

When `combat_state.recentHitsplats` includes actor and amount data,
`combat_damage_summary.py` computes compact damage taken/dealt, HP before/after,
primary opponent, actor death, and task-resume evidence. The interruption
lifecycle embeds the compact damage summary under `combatDamageSummary` and
mirrors key fields in `combat`.

# Combat Damage Schema Gap

Fixture: `recordings\20260607_154606_Wood_cutting_attacked`

## Findings

| Question | Status | Notes |
| --- | --- | --- |
| Hitsplats preserved? | present | `combat_state.recentHitsplats` was preserved in Record Everything events. |
| Hitsplat actor/target available? | present | Each hitsplat includes the actor receiving the hitsplat. Interacting actor is available for direct attribution context. |
| Hitsplat amount available? | present | `amount` is present and usable for totals. |
| Player damage taken separable? | present | Local-player hitsplats separate damage taken from NPC hitsplats. |
| Damage dealt separable? | present | NPC Mugger hitsplats plus player-target/interacting evidence support damage dealt. |
| Mugger source identifiable? | present | Mugger appears in actors targeting player, player target, hitsplats, and actor death. |
| HP before/after available? | present | `playerHealth.boostedHitpoints` shows 10 -> 7, lowest 6. |
| Actor death associated? | present | One Mugger actor death is inside the combat window. |
| Combat start/end bounded? | present_but_weak | Start/end ticks are known; elapsed start time is unavailable for the first hitsplat. |
| Task resume proven? | present | Woodcutting evidence continues after combat. |

## Result

No plugin change is required for this fixture. Damage summary is computable in Python from existing `combat_state.v1` fields.

Remaining caveat: multi-actor damage attribution is not yet proven by a fixture.


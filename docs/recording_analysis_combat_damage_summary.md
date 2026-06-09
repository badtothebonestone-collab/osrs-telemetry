# Combat Damage Summary Analysis

Fixture: `recordings\20260607_154606_Wood_cutting_attacked`

## Verdict

- Combat damage summary: PASS
- Combat observed: true
- Primary opponent: Mugger
- Confidence: 0.95

## Damage

- Total hitsplats: 37
- Local-player hitsplats: 23
- Opponent hitsplats: 14
- Ambiguous hitsplats: 0
- Damage taken: 5
- Damage dealt: 9

## Health

- HP before: 10
- HP after: 7
- HP delta: -3
- Lowest observed HP: 6
- HP changed: true

## Combat Window

- Start tick: 57
- End tick: 184
- End inferred: true

The start time in milliseconds is unknown because the first hitsplat does not carry elapsed time, but tick/time evidence is sufficient for lifecycle ordering.

## Outcome

- Actor death: Mugger
- Task resumed: true
- Woodcutting lifecycle remained PASS
- Interruption lifecycle remained PASS with `primaryCause=mugger_attack`

## Data Usefulness

This recording is a strong fixture for combat interruption during woodcutting. It proves direct combat state, damage amounts, HP change, actor death, and post-combat task resume.


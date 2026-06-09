# Full Woodcutting Loop Recording Analysis

Generated: `2026-06-07`

## Recording

`C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked`

The label still says `Wood cutting attacked`, but the data is the first full
woodcutting loop fixture:

1. Start at the woodcutting area.
2. Chop from empty inventory to full inventory.
3. Route to the bank.
4. Open/use bank UI and deposit logs.
5. Route back to the woodcutting area.
6. Resume/continue after a combat interruption.

## Verdict

- Woodcutting loop lifecycle: `PASS`
- Loop state: `complete`
- Confidence: `0.95`
- Next expected phase: `continue_current_phase`
- Missing capabilities: none for the loop summary

## Useful Evidence

- Woodcutting cycles: `21`
- Cycle-level logs gained: `28`
- Net inventory logs gained: `0`, expected for a completed loop because logs were deposited before the recording ended
- Inventory full during loop: yes, inferred from `28` logs gained into `28` starting free slots
- Fresh Chop down clicks: `8`
- Animation `879` snapshots: `13`
- Banking lifecycle: `PASS`
- Bank UI preserved: yes
- Bank container available: yes
- Bank container delta available: yes
- Deposited item: `Logs x28`
- Route direction: `multi_leg_loop`
- Route legs detected: `woodcutting_area_to_bank`, `bank_to_woodcutting_area`
- Start/end area: `woodcutting_area` -> `woodcutting_area`
- Interruption: combat, `mugger_attack`
- Task resumed after interruption: yes

## Fix Made

The original loop summary under-called the recording as `banking` because it
only trusted the top-level woodcutting inventory delta. In a complete loop that
delta is naturally `0 -> 0` after deposit. The loop analyzer now:

- uses cycle-level log gain when net inventory returns to empty,
- treats cycle log gain equal to starting free slots as inventory-full evidence,
- treats bank-container log increases plus Deposit-All action context as deposit proof,
- recognizes both route directions inside one long traversal recording.

## Caveats

- The route is detected as a `multi_leg_loop`, not a registered single full-loop route template.
- One traversal step has partial/unknown postcondition evidence, but the overall traversal still passes.
- Some menu selections lack row geometry; target/postcondition evidence still supports the action.
- Deposit-All was still region-classified as `minimap_click`, but menu context and bank-container delta made the banking result trustworthy.
- The combat damage summary still warns about some ambiguous hitsplats, which is already tracked as a lower-priority combat attribution gap.

## Data Usefulness

This is a good fixture. It proves the full task-loop state layer can summarize
the whole practical loop from existing Record Everything data, including
interruption and recovery. It should be used as the current fixture for
`woodcutting_loop_lifecycle` and script-facing task phase decisions.

## Analyzer Command

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --auto-route-template --banking-lifecycle --interruption-lifecycle --combat-damage-summary --human-click-profile --woodcutting-loop-lifecycle --update-knowledge
```

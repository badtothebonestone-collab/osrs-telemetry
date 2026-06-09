# Banking Lifecycle Fixture Result

Recording:

```text
C:\Users\badto\osrs-telemetry\recordings\20260607_104744_Opening_Bank_and_Deposit_all_logs
```

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260607_104744_Opening_Bank_and_Deposit_all_logs" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --auto-route-template --banking-lifecycle --print-banking-lifecycle
```

## Lifecycle Summary

- Status: `WARN`
- Phase: `complete`
- Confidence: `0.65`
- Bank-like interface: `bank`
- Direct bank open: `false`
- Direct deposit box open: `false`
- Bank widget/root seen: `false`
- Bank container available: `false`
- `bank_ui` present in recording: `false`
- `bank_ui` source configured in historical recording: `false`
- Deposit detected: `true`
- Withdraw detected: `false`
- Deposited items: `Logs x6`
- Inventory free slots: `10 -> 16`
- Normal logs itemId `1511`: `6 -> 0`

## Evidence

- Bank booth, Banker, Bank Deposit Box, and Bank table were visible as nearby/service targets.
- Input menu context showed `Deposit-All` with target `Logs`.
- Inventory showed normal logs itemId `1511` dropping from `6` to `0`.
- Inventory free slots increased from `10` to `16`.

## Warnings

- Bank open/closed state was not directly observed in the recording.
- Bank widget/root visibility was not directly observed.
- Bank container contents were not directly observed.
- `bank_ui` live-cache payload was not present in this historical recording.
- Deposit was inferred from inventory/menu evidence because bank container telemetry was missing.
- The Deposit-All click carried banking menu context while the input classifier labeled the region as `minimap_click`.

## Verdict

This is a useful banking data-collection recording. It proves the deposit result
from inventory/menu evidence and clearly identifies the missing direct bank
telemetry that should be captured next.

Future Record Everything bank recordings should preserve `bank_ui` from the
plugin snapshot endpoint when the live bridge provides it. That should move the
direct bank-open/widget evidence from inferred `WARN` toward direct `PASS`
evidence.

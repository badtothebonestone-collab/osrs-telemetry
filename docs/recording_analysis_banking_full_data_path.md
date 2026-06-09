# Banking Full Data Path Verification

Recording:
`C:\Users\badto\osrs-telemetry\recordings\20260607_120446_Bank_opening_deposit`

Analyzer command:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260607_120446_Bank_opening_deposit" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --auto-route-template --banking-lifecycle --print-banking-lifecycle
```

## Verdict

- Banking lifecycle: `PASS`
- Phase: `complete`
- Confidence: `0.95`
- Direct bank UI: yes
- Bank container available: yes
- Bank container delta available: yes
- Deposit confirmation level: `bank_container_delta_confirmed`
- Deposited item: Logs x16
- Inventory Logs: `16 -> 0`
- Free slots: `0 -> 16`
- Bank container Logs: `126 -> 142`
- Missing banking capabilities: none

## Stack Result

- RuneLite bridge: `bank_ui` live-cache packet exists and now exports `bankContainerDelta` for future runs.
- Live source discovery: `bank_ui` is discoverable through the plugin snapshot endpoint.
- Recorder: Record Everything preserved eight fresh `bank_ui` snapshots.
- Analyzer: banking lifecycle recovered direct bank state, inventory delta, and bank container delta.
- Schema/capability reports: banking fields and `banking.bankContainer.delta` are named explicitly.
- Context API: compact `banking`, `bank_state`, `inventory_delta`, and `deposit_result` needs are available.
- MCP: compact banking state/lifecycle/inventory/deposit tools are available.
- Task script API: scripts can call `did_deposit_item(1511)` and related helpers without raw JSON parsing.
- UI: Simple Mode analysis log includes banking status, deposited item, direct bank evidence, container, and bank delta.

## Script-Facing Example

```json
{
  "bankOpen": true,
  "depositBoxOpen": false,
  "activeBankLikeInterface": "bank",
  "depositComplete": true,
  "depositedItems": [
    {
      "id": 1511,
      "name": "Logs",
      "quantity": 16,
      "confirmationLevel": "bank_container_delta_confirmed"
    }
  ],
  "inventoryFreeSlotsAfter": 16,
  "confidence": 0.95,
  "missingCapabilities": []
}
```

## Remaining Review Note

One `Deposit-All` banking-context click was region-classified as `minimap_click`. The lifecycle handled it through menu context and direct bank evidence. This is not blocking for banking proof, but it remains a classifier cleanup candidate.

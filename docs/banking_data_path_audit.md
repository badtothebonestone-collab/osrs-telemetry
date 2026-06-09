# Banking Data Path Audit

Recording audited:
`C:\Users\badto\osrs-telemetry\recordings\20260607_120446_Bank_opening_deposit`

## Result

The banking path is now script-readable across the stack. The PASS recording proves:

- `bank_ui` was requested from the RuneLite plugin snapshot endpoint.
- `bank_ui` was preserved in `events.jsonl`.
- Direct bank state was observed.
- Bank container contents were observed.
- Inventory changed from Logs x16 to Logs x0.
- Bank container Logs changed from 126 to 142.
- Banking lifecycle reports `depositConfirmationLevel=bank_container_delta_confirmed`.

## Data Path

| Field | Plugin/live source | Recorder preserved? | Analyzer uses? | Context API exposes? | MCP exposes? | Task script exposes? | Status | Notes |
|---|---|---:|---:|---:|---:|---:|---|---|
| `bank_ui` present | `live_bank_ui_packet.v1` via `POST /snapshot` need `bank_ui` | yes | yes | yes, `bank_ui` / `banking` | yes | yes | implemented | Record Everything requests and preserves it when present. |
| `bankOpen` | `bank_ui.bankOpen` | yes | yes | yes, `bank_state` | yes, `get_banking_state` | yes, `is_bank_open()` | implemented | Direct open proof. |
| `depositBoxOpen` | `bank_ui.depositBoxOpen` when bridge provides it | yes | yes | yes | yes | yes, `is_deposit_box_open()` | implemented | Current fixture used bank, not deposit box. |
| `activeBankLikeInterface` | plugin field if present; analyzer derives from direct state | yes | yes | yes | yes | yes | implemented | `bank` in fixture. |
| `bankWidgetRoot` | `bank_ui.bankRootWidget` | yes | yes | yes | yes | yes | implemented | Direct widget/root evidence. |
| `depositBoxWidgetRoot` | `bank_ui.depositBoxWidgetRoot` if present | yes | yes | yes | yes | yes | implemented | Tracked by schema even when absent. |
| `bankContainerAvailable` | `bank_ui.bankContainerVisible` + `bankSummary.known` | yes | yes | yes | yes | yes | implemented | True in fixture. |
| bank container item count | `bank_ui.bankSummary.itemCount` | yes | yes | compact yes | compact yes | compact yes | implemented | Fixture bank count 201 -> 217. |
| bank container items/counts | `bank_ui.bankSummary.totalQuantityByItemId` | yes | yes | compact yes | compact yes | compact yes | implemented | Logs 126 -> 142. |
| `bankContainer.delta` | future `bank_ui.bankContainerDelta`; analyzer also diffs recorded bank snapshots | yes | yes | yes | yes | yes | implemented | No longer missing for the PASS fixture. |
| inventory snapshot | live baseline/activity + `bank_ui.inventorySummary` | yes | yes | yes | via context | yes | implemented | Inventory Logs 16 -> 0. |
| inventory delta | analyzer from before/after inventory; live has recent deltas when available | yes | yes | yes, `inventory_delta` | yes, `get_inventory_delta` | yes, `get_inventory_delta()` | implemented | Free slots 0 -> 16. |
| deposited items | analyzer lifecycle | yes | yes | yes, `deposit_result` | yes, `get_deposit_result` | yes, `get_deposit_result()` / `did_deposit_item()` | implemented | Logs x16. |
| withdrawn items | analyzer lifecycle | yes | yes | yes | yes | yes | implemented | None in fixture. |
| lifecycle status | `banking_lifecycle.json` | yes | yes | yes | yes | yes | implemented | PASS. |
| lifecycle phase | `banking_lifecycle.json` | yes | yes | yes | yes | yes | implemented | complete. |
| lifecycle confidence | `banking_lifecycle.json` | yes | yes | yes | yes | yes | implemented | 0.95. |
| missing capabilities | lifecycle/schema/capability reports | yes | yes | yes | yes | yes | implemented | Empty for the new PASS fixture. |
| warnings | lifecycle/schema/capability reports | yes | yes | yes | yes | yes | implemented | Region classifier warning remains review-only. |

## Plugin / Bridge

`TelemetryPlugin.bankUiPayload()` emits `bank_ui_context_payload.v1` into the live cache as `live_bank_ui_packet.v1`. It includes bank-open state, widget visibility, bank/inventory widgets, inventory summary, and bank summary.

This pass adds `bankContainerDelta` to the same `bank_ui` payload. The field is computed by comparing cached bank container summaries between ticks:

```json
{
  "schema": "bank_container_delta.v1",
  "available": true,
  "tick": 0,
  "source": "gameTickBankSnapshot",
  "changedItems": [
    {
      "itemId": 1511,
      "beforeQuantity": 126,
      "afterQuantity": 142,
      "delta": 16,
      "source": "snapshot_diff"
    }
  ],
  "warnings": []
}
```

The old `bank_ui` shape is preserved.

## Script-Facing API

Task scripts can now use:

- `get_bank_state(source)`
- `get_banking_lifecycle(source)`
- `is_bank_open(source)`
- `is_deposit_box_open(source)`
- `get_active_bank_like_interface(source)`
- `get_inventory_delta(source)`
- `get_deposit_result(source)`
- `get_deposited_items(source)`
- `did_deposit_item(source, item_id)`
- `get_banking_missing_capabilities(source)`

These accept a lifecycle dict, summary dict, recording folder, or `banking_lifecycle.json`.

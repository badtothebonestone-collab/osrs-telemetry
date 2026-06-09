# Banking Schema Gap Analysis

Recording inspected:

```text
C:\Users\badto\osrs-telemetry\recordings\20260607_104744_Opening_Bank_and_Deposit_all_logs
```

## Result

- Bank/deposit-box target detected: yes. Nearby/service evidence included Bank booth, Banker, Bank Deposit Box, and Bank table.
- Selected action detected: yes. Input menu context showed `Deposit-All` on `Logs`.
- Bank open detected directly: no.
- Bank close detected directly: no.
- Bank widget/root detected directly: no.
- Bank container detected directly: no.
- Bank item slots detected directly: no.
- Inventory before/after detected: yes.
- Normal logs itemId `1511` before/after: `6 -> 0`.
- Deposited log count: `6`.
- Bank contents changing detected: no.
- Free slot changes detected: `10 -> 16`.
- Deposit button/widget/menu row detected: menu context yes, direct widget no.

## Categories

### present

- bank/deposit-box target evidence
- deposit action/menu context
- inventory before/after
- normal logs itemId `1511` before/after
- free slot delta

### present_but_weak

- bank-open state inferred from deposit action plus inventory delta
- Deposit-All action recovered from menu context even though the input classifier region was `minimap_click`

### computable_in_python

- deposited normal logs count from inventory delta
- inventory changed item counts
- free slot delta
- banking activity label from lifecycle evidence

### requires_bridge_export

- `bankOpen` / `depositBoxOpen` in recorded source snapshots
- bank widget/root visibility in recorded source snapshots
- bank container item slots
- bank item count deltas after deposit/withdraw
- item container change details for bank/inventory actions

### needs_manual_review

- Whether the direct bank UI packet should be captured through file-based recorder sources, snapshot endpoint polling, or both.
- Whether bank widget item bounds are needed now or can wait until bank slot interaction training.

### analyzer_gap

- Deposit-All menu-context clicks should remain banking evidence even when a coarse screen-region classifier says `minimap_click`.

## Bridge Inspection

The Java bridge already has a `bank_ui` live cache packet path. It exports
`bankOpen`, `bankPinOpen`, bank root/container/inventory visibility, deposit
inventory button visibility, widget snapshots, inventory slot widgets, and a
bank summary.

Preservation update:

- `summary.json.bank_ui_source.configured`: `false`
- `summary.json.bank_ui_source.observed`: `false`
- `banking_lifecycle.bank.bankUiPresent`: `false`
- missing capability: `banking.bank_ui`

This confirms the old fixture is missing the already-existing live-cache packet
from the offline recording. Record Everything now requests `bank_ui` from the
plugin snapshot endpoint and preserves the parsed payload in future source
snapshots when the live bridge provides it.

This recording did not preserve that packet in the manual recording source set.
The immediate gap is therefore not “no bank bridge exists”; it is “Record
Everything did not capture direct bank UI/container evidence into this offline
recording.” Full bank item-slot delta details are still a bridge/export gap if
the current bank summary is not enough.

## Verdict

The recording is useful for banking analysis and proves `Logs x6` were
deposited, but it is not sufficient proof of direct bank state. Treat it as
`WARN` until bank-open/widget/container fields are recorded directly.

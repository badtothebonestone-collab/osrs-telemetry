# Banking Lifecycle

`telemetry-viewer\banking_lifecycle.py` analyzes bank, deposit-box, deposit, and
withdraw recordings from the same Record Everything artifacts used by route and
woodcutting analysis.

The output file is:

```text
banking_lifecycle.json
```

Schema:

```text
banking_lifecycle.v1
```

## What It Captures

- bank or deposit-box target evidence
- direct `bankOpen` / `depositBoxOpen` state when present
- bank widget/root visibility when present
- bank container availability and item deltas when present
- inventory before/after
- free slot changes
- deposit and withdraw actions
- deposited or withdrawn item ids, names, and counts
- missing capabilities that need bridge/export work

## Direct Vs Inferred Evidence

Direct banking evidence is strongest:

- preserved `bank_ui` live-cache payload in `events.jsonl`
- `bankOpen` or `depositBoxOpen` true
- bank/deposit root widget visible
- bank container contents available
- bank container item count changes after a deposit or withdraw

Inferred banking evidence is still useful, but should usually be `WARN`:

- bank booth/deposit box target is nearby
- menu context says `Deposit-All`, `Withdraw-*`, or `Bank`
- inventory item counts change after the action
- free slots change in the expected direction

Example: if normal logs itemId `1511` goes from `6 -> 0` and free slots go from
`10 -> 16` after a `Deposit-All Logs` action, the lifecycle reports deposited
`Logs x6`. If bank container telemetry is missing, it also reports the missing
bank capabilities instead of pretending the bank delta was proven.

## Status

- `PASS`: direct bank state and container evidence support the action.
- `WARN`: the banking action is useful but one or more direct bank fields are
  missing.
- `FAIL`: no banking lifecycle signals were found.

## Common Missing Capabilities

- `banking.bankOpen_or_depositBoxOpen`
- `banking.bankWidgetRoot`
- `banking.bankContainer.items`
- `banking.bankContainer.delta`

These are bridge or recorder-source gaps, not gameplay failures.

## Deposit Confirmation Levels

Banking lifecycle reports `depositConfirmationLevel` so callers can tell how
strong the proof is:

- `inventory_only`: item counts/free slots changed, but no direct bank state was
  preserved.
- `bank_open_plus_inventory`: the bank or deposit box was directly observed and
  inventory changed as expected.
- `bank_container_delta_confirmed`: the bank container changed by the deposited
  item quantity.
- `combined`: multiple direct signals agree.

When `bankContainerDeltaAvailable` is true, deposited items include
`confirmationLevel` and the lifecycle can distinguish a real bank-side increase
from an inventory-only inference.

## `bank_ui` Preservation

The RuneLite bridge already exposes a `bank_ui` live-cache packet through the
plugin snapshot endpoint. Record Everything recordings now ask that endpoint for
`bank_ui` on source snapshots and preserve the parsed payload directly on the
`bank_ui` source entry when it is present.

That payload can include:

- `bankOpen` / `depositBoxOpen`
- bank and deposit-box root/widget visibility
- active bank-like interface
- bank inventory and container widgets
- bank item summaries and inventory summaries
- bank container changed-item deltas when the bridge can compare snapshots
- packet freshness, tick, and source metadata

If `bank_ui` is missing, banking lifecycle still uses menu, target, and
inventory deltas. The result should remain `WARN` because the action was inferred
rather than directly proven by bank state.

## Script-Facing Banking API

Task scripts should use the compact API instead of reading raw JSON artifacts:

```python
import task_script_api as api

result = api.get_deposit_result(recording_folder)
api.is_bank_open(recording_folder)
api.did_deposit_item(recording_folder, 1511)
api.get_banking_missing_capabilities(recording_folder)
```

The returned deposit result includes deposited items, confidence,
`depositConfirmationLevel`, `bankContainerDeltaAvailable`, missing capabilities,
and warnings.

## Analyze A Banking Recording

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --input-trace --join-input --classify-input-actions --target-match-quality --menu-interactions --banking-lifecycle --print-banking-lifecycle
```

Record Everything runs this automatically during normal UI analysis.

## Recording A Direct Bank Sample

1. Open OSRS Telemetry Recorder.
2. Start Game.
3. Start Telemetry.
4. Start Recording.
5. Open the bank or deposit box.
6. Deposit logs.
7. Close the bank or deposit box.
8. Stop Recording and let Analyze Latest finish.

Expected direct-evidence PASS criteria:

- `bank_ui` present in the recording
- `bankOpenSeen` or `depositBoxOpenSeen` true
- active bank-like interface is bank or deposit box
- inventory before/after captured
- deposited logs detected
- bank container available when the live bridge provides it

## Loop Lifecycle

When `banking_lifecycle.json` proves a deposit, the woodcutting loop lifecycle
sets `loopState=deposit_complete` and `nextExpectedPhase=route_to_woodcutting_area`.
Scripts should use `task_script_api.get_woodcutting_loop_lifecycle()` or
`get_next_expected_phase()` instead of reading banking JSON directly when they
need the next task phase.

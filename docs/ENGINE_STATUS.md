# Engine Status

## Current milestone

**Phase 0 complete — proven woodcut/bank/return baseline frozen.**

Checkpoint subject: `baseline: freeze proven woodcut bank return cycle`

Regression command:

```powershell
.\run.cmd replay
```

## Proven now

- RuneLite publishes the single snapshot consumed as one immutable
  `Observation`.
- `WoodcutBankTask` explicitly models the one supported ordinary-tree cycle.
- Exact Tree `1276`, fixed outbound/return route steps, both upward stairs,
  exact bank booth, deposit-all-logs, verified Escape close, and terminal
  `COMPLETE` are represented in the committed golden replay.
- Live component traces physically reached all of those milestones on
  2026-07-10 and ended at `COMPLETE` with acknowledged `STOP_ALL`/`DISARM`.
- All live gameplay input remains Arduino-only; no software fallback exists.

## Evidence boundary

The live proof is stitched across bounded continuation runs made while the
route was being corrected. The five-event terminal trace proves only the last
waypoint and terminal state. It is not an uninterrupted raw replay, and the
trace summaries omit full observations, menus, geometry, and command receipts.
The golden fixture records this caveat and hashes the key ignored artifacts.

## Validation

- Final Python suite: 116 passed.
- Forced fresh Java suite: 22 passed, 0 failed, 0 errors, 0 skipped.
- Golden replay: 28 chop actions, 19-step outbound route, bank
  open/deposit/close, 15-step return route, one completed cycle.
- `git diff --check`: passed.

## Current blockers and next work

- Source facts can still be assembled from different/stale ticks while the HTTP
  response looks new. Phase 2 must make source evidence coherent before broad
  task flexibility.
- Runtime/task contracts are still woodcut-specific; Phase 3 will extract only
  the minimal explicit task seam.
- Arduino ownership and authoritative final firmware `STATUS` are not yet one
  enforced boundary; Phase 5 owns that hardening.
- The next checkpoint is Phase 1: update the governing product and architecture
  contract without changing runtime behavior.

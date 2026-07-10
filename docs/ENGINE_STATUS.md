# Engine Status

## Current milestone

**Phase 1 complete — modular OSRS engine governing contract established.**

Current checkpoint subject: `docs: establish modular engine governing contract`

Frozen baseline: commit `beb9cbb`, tag
`baseline-proven-woodcut-bank-return-2026-07-10`.

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

## Governing direction

- The product is a small OSRS-specific engine, not a general agent framework.
- The proven Lumbridge cycle is the regression baseline; future flexibility
  comes from validated profiles and immutable task/site definitions feeding
  explicit task-specific FSMs.
- Profiles and definitions can never weaken engine invariants.
- RuneLite API facts remain authoritative. Vision may supplement or veto but
  cannot replace semantic API truth. No model dependency is active.
- One future `InputCoordinator` owns every Arduino session; one future
  `EngineFrame` owns diagnostic truth. Neither is falsely claimed as implemented
  yet.
- A future LLM may read offline evidence but has no runtime control authority.
- Static definitions, active FSM state, run history, and demonstration evidence
  remain separate; unsafe ephemeral state is never restored after restart.

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
- Phase 1 documentation gate repeated the replay, all 116 Python tests, and a
  forced fresh 22-test Java run successfully.
- `git diff --check`: passed.

## Current blockers and next work

- Source facts can still be assembled from different/stale ticks while the HTTP
  response looks new. **Phase 2 is next** and must make source evidence coherent
  before broader task seams.
- Runtime/task contracts are still woodcut-specific; Phase 3 will extract only
  the minimal explicit task seam.
- Arduino ownership and authoritative final firmware `STATUS` are not yet one
  enforced boundary; Phase 5 owns that hardening.
- Phase 1 changed documentation only; runtime behavior remains the frozen Phase
  0 baseline until Phase 2 begins.

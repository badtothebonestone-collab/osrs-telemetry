# Engine Status

## Current milestone

**Phase 3 complete — minimal task seam and typed outcome path established.**

Current checkpoint subject: `engine: extract minimal task contract and typed outcomes`

Frozen baseline: commit `beb9cbb`, tag
`baseline-proven-woodcut-bank-return-2026-07-10`.

Regression command:

```powershell
.\run.cmd replay
```

## Proven now

- RuneLite publishes the single snapshot consumed as one immutable
  `Observation`.
- The plugin publishes one immutable `sensor_frame.v1` atomically. Core facts
  cannot be assembled across publications, and unavailable captures replace
  old facts explicitly.
- Snapshot response v2 distinguishes source capture time from HTTP assembly
  time. World/tile geometry and menu evidence have separately enforced source
  identities.
- Runtime consumes only the structural `Task` contract: bounded observation
  request, opaque decision, typed verification application, and immutable
  status snapshot. It has no woodcut import, phase comparison, or progress
  access.
- `WoodcutBankTask` explicitly models the one supported ordinary-tree cycle.
- The shared model has no log ID or woodcut phase. Task-specific item,
  interface, plane, and dialogue requirements are immutable action/spec
  constraints, while SafetyGate invariants remain non-overridable.
- Verification passes carry one typed `Outcome`; diagnostic reason strings do
  not select state transitions. A non-woodcut waypoint fake passes through the
  real runtime, SafetyGate, and Verifier without engine changes.
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
- Phase 2 gate: golden replay 2 passed; 123 Python tests passed; forced fresh
  Java suite 39 passed across 6 suites with zero failures/errors/skips.
- Phase 3 gate: golden replay 2 passed; 136 Python tests passed; forced fresh
  Java suite 39 passed across 6 suites with zero failures/errors/skips.
- The bounded Phase 2 live observation served response v2/frame v1 at the
  RuneLite login screen. Only baseline was available; inventory, activity,
  bank UI, and dialogue were explicitly unavailable. `observe` returned
  `loadedScene=false`, and the launched client/port were closed afterward.
- `git diff --check`: passed.

## Current blockers and next work

- The Phase 2 live check did not reach a loaded scene, so fresh loaded-game
  observation and safe default-cycle proof remain final-regression work.
- New-tick world-model refreshes have a 250 ms provider wait. Persistent loaded
  scene latency/timeout behavior is unmeasured and must be inspected in final
  live evidence rather than inferred from the login-screen check.
- Lumbridge IDs, areas, route steps, deadlines, and the one-cycle goal still
  live beside the task FSM. **Phase 4 is next** and will move only those facts
  into one immutable built-in definition with one validated default profile.
- Arduino ownership and authoritative final firmware `STATUS` are not yet one
  enforced boundary; Phase 5 owns that hardening.
- The atomic sensor and minimal task contracts are implemented; definition,
  input, diagnostics, recorder, and facade migrations remain phase-scoped.

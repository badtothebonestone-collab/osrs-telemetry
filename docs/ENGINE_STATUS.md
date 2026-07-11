# Engine Status

## Current milestone

**Phase 6 complete — one immutable diagnostic truth and passive overlay established.**

Current checkpoint subject: `diagnostics: publish EngineFrame and passive overlay`

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
- The canonical request now consumes one neutral scene-object census. Exact
  selected-definition facts assign task meaning downstream; RuneLite candidate
  hints are not authorization.
- Runtime consumes only the structural `Task` contract: bounded observation
  request, opaque decision, typed verification application, and immutable
  status snapshot. It has no woodcut import, phase comparison, or progress
  access.
- `WoodcutBankTask` explicitly models the one supported ordinary-tree cycle.
- The FSM is bound to exactly one immutable `LUMBRIDGE_WEST_TREES_V1`
  definition and one validated one-cycle default profile. All Lumbridge IDs,
  coordinates, route facts, deadlines, predicates, and provenance live there.
- Endpoint/Arduino/polling and runtime limits live separately in immutable,
  finite, engine-capped `RuntimeConfig`.
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
- Gameplay and saved-session login now submit immutable approved intents to one
  `InputCoordinator`; neither can open an Arduino session or call raw input.
- `CoordinatedActionInterface` preserves exact post-move hover/menu/widget
  checks, context-row revalidation, and the verified bank-close Escape path.
- The deterministic pointer policy produces only bounded relative motion inside
  the verified canvas, with velocity/acceleration caps and target-aware braking.
- Every connected transaction records a non-truncated command/ACK ledger and
  attempts `STOP_ALL`, `DISARM`, and wire `STATUS`. Success requires the final
  firmware report to prove disarmed with zero held keys/buttons and no missing,
  failed, or unresolved command evidence.
- The Arduino transport and raw operations are private. Recursive static
  boundary tests reject another production importer/caller and reject software
  input modules.
- Task snapshots now expose the bound definition/profile and bounded route or
  cycle progress without runtime reading mutable FSM internals.
- Decisions carry the exact selected/eligible/rejected evidence produced by the
  task's real selection path, including stable rejection codes and source
  geometry provenance.
- Safety evaluations record the ordered checks actually used. Execution results
  retain those checks across bounded retries without diagnostic re-evaluation.
- `TaskRuntime` publishes one monotonic latest `engine_frame.v1` at observation,
  decision, execution, verification, and terminal boundaries. Terminal frames
  retain the last receipt, typed outcome, derived cleanup state, and blocker.
- The optional Windows overlay renders only EngineFrame: selected green,
  alternatives amber, optional rejected red, plus compact status text. It
  suppresses stale geometry and verifies click-through/no-activate window
  styles before display. Overlay failure cannot alter engine control.

## Governing direction

- The product is a small OSRS-specific engine, not a general agent framework.
- The proven Lumbridge cycle is the regression baseline; future flexibility
  comes from validated profiles and immutable task/site definitions feeding
  explicit task-specific FSMs.
- Profiles and definitions can never weaken engine invariants.
- RuneLite API facts remain authoritative. Vision may supplement or veto but
  cannot replace semantic API truth. No model dependency is active.
- One implemented `InputCoordinator` owns every Arduino session, and one
  implemented `EngineFrame` owns read-only diagnostic truth.
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
- Phase 4 gate: golden replay 2 passed; 169 Python tests passed; forced fresh
  Java suite 71 passed across 8 suites with zero failures/errors/skips.
- Phase 5 gate: golden replay 2 passed; 220 Python tests passed; forced fresh
  Java suite 71 passed across 8 suites with zero failures/errors/skips.
- Phase 6 gate: golden replay 2 passed; 241 Python tests passed; forced fresh
  Java suite 71 passed across 8 suites with zero failures/errors/skips.
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
- There is intentionally no external profile loader, second definition, or
  generic navigation/transition framework.
- The sole input boundary is implemented and fake-transport tested. The final
  bounded live regression must still retain a real successful receipt and safe
  firmware STATUS proof after live input.
- The passive overlay is structurally and render-policy tested; final regression
  must still compare its visible geometry/text with a fresh loaded EngineFrame.
- Sensor, task, definition, profile, runtime-configuration, input, and
  diagnostic contracts are implemented. **Phase 7 is next**; recorder and
  facade work remain phase-scoped.

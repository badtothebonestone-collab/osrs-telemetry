# Engine Stabilization Plan

## Mission

Turn the proven Lumbridge west ordinary-tree -> Lumbridge Castle bank -> return
slice into a small OSRS-specific engine. Preserve one sensor truth, one task
FSM, one safety gate, one Arduino action path, typed verification, bounded
cleanup, and a thin runtime. Do not grow a generic planner, task language,
behavior tree, knowledge system, learned policy, anti-detection system, or GUI
runtime logic.

## Decisions

- **D001 — Evidence classification:** the 2026-07-10 physical cycle is stitched
  component proof across bounded continuation runs, not one uninterrupted
  process. It is valid route/interaction evidence but must never be presented
  as a raw end-to-end Observation replay.
- **D002 — Regression artifact:** preserve the live evidence hashes and final
  semantic contract in `tests/fixtures/golden_lumbridge_cycle.json`. The golden
  replay is deterministic, sanitized, hardware-free, and exercises the real
  `WoodcutBankTask` plus `Verifier`.
- **D003 — Fail-closed route evidence:** absent or temporarily non-actionable
  projection evidence waits; an explicitly contradictory labeled projection
  blocks.
- **D004 — Milestone discipline:** one phase, one reviewed diff, one checkpoint
  commit. Do not push without explicit user direction.
- **D005 — Product governance:** `docs/PRODUCT_VISION.md` defines the product;
  `docs/ARCHITECTURE.md` separates current implementation from target contracts;
  the rescue contract remains only the frozen regression baseline.
- **D006 — Extension boundary:** flexibility comes from one validated profile
  and one immutable built-in task/site definition feeding an explicit task FSM.
  Neither may weaken engine invariants.
- **D007 — Authority boundary:** RuneLite owns semantic truth. Vision and an LLM
  may consume or supplement read-only evidence at defined offline seams but
  never replace API facts or participate in runtime control.

- **D008 — Sensor source contract:** gameplay facts publish as one immutable
  `sensor_frame.v1`; HTTP assembly time is never evidence time. Menu evidence
  is separately stamped, and request-time world/tile geometry is accepted only
  when tick, session, process, capture age, and geometry frame match.
- **D009 — Minimal task seam:** runtime sees only `Task`, `Decision`, and
  `TaskSnapshot`; task state is opaque. Passing verification always carries a
  typed `Outcome`, and task-specific interface/dialogue/inventory requirements
  travel as immutable constraints that can narrow but never weaken engine
  safety invariants.
- **D010 — Definition binding:** all proven Lumbridge facts live in exactly one
  immutable built-in definition. One minimal validated profile selects it; one
  separate bounded runtime configuration owns machine/session limits. The
  definition supplies facts to the explicit FSM and never interprets its
  transitions.
- **D011 — Input ownership and proof:** gameplay and login retain different
  fresh validators but submit typed intents to one `InputCoordinator`. The
  Arduino transport is private; a receipt is successful only with ordered
  activation/ACK evidence and final `STOP_ALL -> DISARM -> STATUS` proof.

## Phases and acceptance

| Phase | Status | Acceptance |
|---|---|---|
| 0. Freeze proven baseline | Complete (`beb9cbb`) | Full diff audited; Python/Java suites pass; terminal trace reaches `COMPLETE`; tracked golden replay passes; stale proof docs corrected; checkpoint committed/tagged. |
| 1. Governing contract | Complete (`f4c091e`) | Product vision, architecture, rules, status, and this plan describe the OSRS-specific engine and prohibited expansion. |
| 2. Coherent sensor truth | Complete (`b645d83`) | Atomic tick `SensorFrame`; source-based freshness; mixed/stale/missing/menu/schema tests; bounded live observe. |
| 3. Minimal task seam | Complete (`56b8b8b`) | Runtime has no concrete woodcut imports/phase checks; typed outcomes; fake task runs unchanged runtime/safety/verifier. |
| 4. One task/site definition | Complete (`0c8ec9e`) | One immutable Lumbridge definition and one validated default profile; unsafe/malformed values fail closed; replay unchanged. |
| 5. Arduino boundary | Complete | One `InputCoordinator`; no production bypass; bounded pointer policy; command/ACK plus authoritative final firmware status. |
| 6. EngineFrame + overlay | Next | One immutable diagnostic truth; passive click-through overlay mirrors it and has no control authority. |
| 7. Demonstration capture | Pending | Read-only record/inspect commands produce hashed JSONL, manifest, timeline, screenshots, and reviewed semantic suggestions. |
| 8. Frontend contracts | Pending | Minimal facade proves list/validate/start/pause/stop/status/demo operations without duplicating task or safety logic. |
| Final regression | Pending | Full suites/replay pass; bounded fresh live observation and safe default-cycle evidence; cleanup and audits confirmed. |

## Phase 0 completed work

- Audited branch `rescue/m1-sensor-spine` at parent `a843915` and the complete
  13-file live-hardening diff.
- Fresh validation: 114 Python tests and 22 Java tests passed before the replay
  was added; the final Phase 0 validation includes the new replay tests.
- Classified the 51 ignored vertical-slice traces (48 blocked, 2 error,
  1 terminal complete) and preserved the key evidence hashes without committing
  machine/session-specific logs.
- Kept transient unavailable route geometry as a wait but restored terminal
  blocking for contradictory labeled projection identity.
- Covered the verified Escape bank-close fallback when the visible close widget
  has no usable point.
- Added `run.cmd replay` and the full semantic golden cycle.
- Final Phase 0 validation: 116 Python tests passed; a forced fresh Java run
  executed 22 tests with zero failures, errors, or skips; `git diff --check`
  passed.
- Checkpoint `beb9cbb` is tagged
  `baseline-proven-woodcut-bank-return-2026-07-10` and was not pushed.

## Phase 1 completed work

- Made `docs/PRODUCT_VISION.md` the durable product contract and explicitly
  retired the rescue contract as the active phase.
- Defined the target engine pipeline, current-vs-target implementation table,
  profile/definition/runtime/invariant ownership, diagnostic authority, and
  restart rules in `docs/ARCHITECTURE.md`.
- Added persistent rules for task-specific FSMs, non-overridable safety,
  Arduino-only input, RuneLite authority, passive vision, offline-only LLM use,
  memory separation, bounded live work, and phase checkpoint discipline.
- Kept this checkpoint documentation-only; runtime behavior is unchanged.
- Re-ran the golden replay, all 116 Python tests, and a forced fresh 22-test
  Java suite successfully before the Phase 1 commit.

## Phase 2 completed work

- Replaced independently updated cache payload truth with one atomic immutable
  `SensorFrame` containing baseline, inventory, activity, bank UI, and dialogue
  facts plus per-fact provenance and explicit availability.
- Made login and capture-failure frames replace prior complete frames without
  carrying facts forward.
- Versioned the endpoint response to `plugin_snapshot_response.v2`, separated
  `assembledAtUtc` from source capture time, and based freshness on the source.
- Bound world-model and tile geometry to source tick, session, process, recent
  capture time, and a camera/window `geometryFrameId`; bound menu evidence to
  the real post-menu-sort sample.
- Updated the immutable Python observation and safety gate to preserve and
  enforce frame/menu provenance. Added `docs/SENSOR_CONTRACT.md` and a
  cross-language Java-schema/Python-parser fixture test.
- Validation: golden replay 2 passed; all 123 Python tests passed; a forced
  fresh Java run executed 39 tests across 6 suites with no failures, errors, or
  skips; `git diff --check` passed.
- Bounded read-only live check: the new endpoint served response v2/frame v1,
  but RuneLite was at `LOGIN_SCREEN`. It published only baseline, explicitly
  marked the other four facts unavailable, returned `WARN`, and `observe`
  refused loaded-scene status. The launched dev client was then closed and port
  8893 was verified closed. This is fail-closed contract evidence, not gameplay
  proof.

## Phase 3 completed work

- Added a four-operation structural `Task` protocol with bounded observation
  requests, opaque decisions, immutable snapshots, and typed terminal status.
- Removed `TaskPhase`, `TaskProgress`, log IDs, and log-count convenience from
  shared models. The explicit woodcut FSM now owns them.
- Made runtime depend only on the protocol; it never imports the woodcut task,
  compares its phases, or reads mutable progress. A fake task exercises the
  same runtime without engine changes.
- Replaced task-specific verification strings with immutable typed outcomes and
  generic item, movement, plane, interface, and dialogue specifications.
- Split SafetyGate evaluation into non-overridable engine invariants and typed
  interface/dialogue/inventory constraints attached by the task. Removed the
  global bank-state requirement and shared `climb`/plane-2 assumptions.
- Rejected every executable action missing verification before dry-run success
  or any input-interface invocation.
- Added `docs/TASK_CONTRACT.md`. Final gate: golden replay 2 passed, all 136
  Python tests passed, a forced fresh 39-test Java suite passed, and
  `git diff --check` passed.

## Phase 4 completed work

- Added exactly one deeply immutable/versioned built-in definition containing
  the proven resource, bank, work area, routes, transitions, inventory
  predicates, deadlines, dialogue expectations, and evidence provenance.
- Bound the still-explicit `WoodcutBankTask` FSM to one validated default
  profile. Unknown definitions, malformed identifiers, and cycle goals other
  than the proven single cycle fail clearly; no profile loader was added.
- Removed every Lumbridge coordinate, object/item ID, route, and task deadline
  from `task.py`. Shared engine layers are import-tested against definition and
  profile data.
- Added immutable capped `RuntimeConfig` for endpoint/Arduino/polling and hard
  runtime bounds. Profiles contain no safety or runtime switches.
- Made the canonical RuneLite request use neutral `scene_object_census`; exact
  selected-definition facts assign resource/route/bank meaning downstream.
  Scene rows strip task-semantic labels and projection selection is
  request/distance/key based. Golden replay entities pass with all upstream
  candidate hints false.
- Corrected a truncated 62-character bank-close evidence digest in the golden
  fixture after verifying the retained trace's 64-character SHA-256.
- Added `docs/DEFINITIONS_AND_PROFILES.md`. Final gate: golden replay 2 passed,
  all 169 Python tests passed, a forced fresh 71-test Java suite passed, and
  `git diff --check` passed.

## Phase 5 completed work

- Added one non-reentrant `InputCoordinator` transaction for gameplay and
  saved-session login. No production module other than the coordinator imports
  the private Arduino transport or calls its raw operations.
- Replaced the gameplay session owner with `CoordinatedActionInterface` and
  migrated prompt clicks plus bank-close/dialogue Escape to typed coordinator
  intents while preserving fresh exact hover/menu/widget validation.
- Added a pure deterministic relative pointer policy with exact arrival,
  target-aware braking, bounded velocity/acceleration, verified-canvas
  containment, bounded feedback correction, and no randomization.
- Added a non-truncating redacted Arduino command ledger and immutable input
  receipts. Success requires ordered activation evidence followed by
  acknowledged `STOP_ALL`, `DISARM`, and wire `STATUS` proving disarmed with
  zero held keys/buttons and no unresolved or failed command evidence.
- Added recursive import/call/software-fallback boundary tests and full failure
  tests for ACKs, cleanup, unsafe status, ledger closure, context cancellation,
  pointer divergence, login, and runtime receipt propagation.
- Added `docs/INPUT_COORDINATOR.md`. Final gate: golden replay 2 passed, all 220
  Python tests passed, a forced fresh 71-test Java suite passed, and
  `git diff --check` passed.

## Prohibited during this mission

- A second gameplay task or site, multiple woodcut areas, a generic navigation
  framework, planner, behavior tree, task DSL, knowledge fabric, automatic
  learning, or raw demonstration replay.
- YOLO/model dependencies, runtime LLM control, MCP, a second telemetry
  endpoint, a full GUI, dynamic plugin/profile frameworks, compatibility layers
  for deleted architecture, or broad unrelated plugin rewrites.
- Anti-detection, stealth, evasion, or randomization intended to avoid
  detection; any weakening of freshness, identity, geometry, binding, menu,
  PIN, verification, Arduino-only input, or cleanup invariants.

## Remaining limitations

- The live corpus is stitched and lacks complete raw observations, command/ACK
  receipts, and immutable source provenance.
- The last Phase 2 live check stopped at the login screen; it proves incomplete
  frame handling, not a loaded gameplay observation.
- Each new source tick forces a world-model refresh behind a 250 ms provider
  wait. Static review did not prove a failure, but final loaded-scene evidence
  must measure refresh/query timing and repeated provenance/timeout warnings.
- Phase 5 hardware behavior is exhaustively fake-transport tested; the final
  bounded live regression must still capture a real successful receipt and
  authoritative safe firmware STATUS after input.
- There is intentionally no external/profile file loader or second definition;
  the one validated in-code default is the only supported choice.
- No EngineFrame, overlay, demonstration recorder, or frontend facade exists.

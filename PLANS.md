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
- **D012 — Diagnostic authority:** runtime publishes one immutable latest
  `EngineFrame` from the real decision path. Task selection and ordered safety
  evidence are captured at their owners; overlay/readers may format that frame
  but never reselect, revalidate, or authorize actions.
- **D013 — Demonstration evidence is not authority:** manual capture is a
  bounded read-only consumer of the single endpoint. Artifacts are hashed and
  self-describing; inspection verifies them before emitting world/entity/action
  suggestions that are review-only, omit input coordinates, and can never
  activate task data or bypass normal safety proof.
- **D014 — Frontend lifecycle is composition, not authority:** one tokenized
  `EngineApplication` composes the existing task/runtime and demonstration
  owners. It returns their immutable values exactly, requires current run or
  capture IDs for commands, and acknowledges pause/safe stop only at runtime
  boundaries that cannot abandon input cleanup or typed verification.

- **D015 — One physical input coordinate space:** actionable telemetry geometry
  is Win32 virtual-desktop device pixels. RuneLite converts AWT user bounds
  with a proven single-monitor transform; Python rejects any other coordinate
  contract. Saved-session pregame assistance may use the exact PID-owned Win32
  client only after proving per-monitor-v2 awareness; gameplay remains confined
  to loaded-scene telemetry canvas bounds. Native gameplay preflight accepts
  only exact outer size plus at most one device pixel of AWT/native origin
  quantization, while still requiring native-client containment of the exact
  canvas; login geometry stays exact.

- **D016 — Observed pointer arrival:** the pure motion policy still targets one
  exact command-space waypoint at a time, but integer HID deltas may land on a
  coarser device-pixel lattice under display scaling. The coordinator uses an
  unknown-axis unit probe, a four-sided transfer envelope, and at most 64
  actual-feedback plans/512 MOVE commands across the complete Arduino
  transaction. It may activate only from a settled full-plan endpoint inside an
  explicit verified region, and fresh validation is bound to that actual point.
  Gameplay uses at most three pixels around the approved safe point; login may
  use the freshly recognized prompt bounds.

- **D017 — Inventory truth is structural:** the authoritative inventory fact is
  normalized before task use. A visible exact 28-slot inventory may prove an
  empty inventory when item rows are absent, and RuneLite `BLANKOBJECT` slots
  are never treated as held items.
- **D018 — Watchdog recovery is explicit and bounded:** post-move validation
  runs under a firmware-watchdog lease. At most one safe rearm plus complete
  semantic revalidation is allowed; a second expiry or any state-changing
  rejection blocks without an implicit retry.
- **D019 — Login ambiguity work is capped:** recognized prompt matching still
  scans the complete configured search region and fails on ambiguity, but pixel
  and candidate work have hard limits so adversarial or noisy frames cannot
  create unbounded login processing.
- **D020 — Canonical target and settled pointer remain distinct:** the immutable
  action retains its authoritative aim identity while fresh safety proof binds
  to the actual settled device-pixel endpoint. Transient frame warnings and
  reprojection jitter receive only bounded reobservation; they never relax
  identity, menu, geometry, or focus proof.
- **D021 — Camera recovery is task-specific input:** a stable unavailable route
  projection may request the shortest fixed-point yaw arc using 250 ms left or
  right holds, at most eight verified turns, and a typed camera-pose outcome.
  Missing evidence still waits, contradictory identity still blocks, and no
  generic navigation or camera planner exists.
- **D022 — Verification starts after preactivation work:** a sent action rebases
  its tick budget to the final preactivation observation. Ordinary actions keep
  the eight-tick definition deadline; route movement uses a separate 20-tick
  deadline. An intercepted walk target may select only one exact lower `Walk
  here`/`WALK` context row.
- **D023 — Unsent target invalidation is not a failed action:** the action layer
  emits a typed invalidation only when fresh exact hover proof fails before any
  activation. Runtime may discard one consecutive proposal only with a fully
  safe receipt whose ledger contains preactivation commands exclusively; the
  task suppresses that exact resource key for one fresh selection. A second
  consecutive invalidation blocks. Fresh restart reconciliation is separately
  limited to an exact return-route anchor, or the configured bank
  anchor/interaction radius with structurally empty inventory and known current
  bank state; an open bank closes first and a closed bank starts return step 0.
  Neither path grants cycle credit for the historical return.

- **D024 — Object aim points stay inside API shapes:** object activation uses
  the first present RuneLite shape in clickbox -> convex hull -> canvas tile
  order and chooses a point inside that actual shape and the viewport.
  `canvasLocation` alone is not authorization.
- **D025 — Live restart and resource recovery remain task-specific:** one
  verified resource no-yield may discard the old tree and reselect once; a
  second no-yield blocks. Fresh full/empty inventory may reconcile only to the
  furthest exact outbound/return route anchor. Structurally empty inventory may
  also resume within the configured bank anchor/interaction radius only while
  the current bank state is known: an open interface closes first even where a
  return step overlaps the bank area; when already closed, the furthest exact
  return-step match takes precedence, otherwise the bank-area fallback begins
  return step 0. Unknown bank state inside the interaction area cannot use an
  overlapping route match, and any reported PIN state blocks before either
  precedence path. No lane restores verification or historical cycle credit.
- **D026 — Cursor truth is observed, never remembered:** every cursor and
  point-owner sample establishes per-monitor-v2 device pixels on its current
  thread. A fresh cursor inside the RuneLite outer envelope may enter through a
  movement-only, exact-owner, one-axis bounded lane; unsupported displacement
  becomes typed cursor-state invalidation and may receive one safe
  reobservation without suppressing the target. Before a first MOVE, two
  timestep-separated identical samples, a fresh physical-button quiet proof,
  and one final unchanged cursor/foreground sample accept a stationary manual
  position as current truth and reject continuing motion or a late prior report.
- **D027 — Serial ACK is not Windows cursor proof:** never infer cursor arrival
  from an acknowledged MOVE or a prior command. Observe the current PMv2 cursor,
  never stack a second MOVE on an unproved effect, and turn unresolved cursor
  state into typed invalidation for at most one lane-specific fresh
  reobservation: login re-finds/re-screens the client, while gameplay requires a
  newer same-identity tick. Repetition remains fail-closed.
- **D028 — Loaded-scene login proof is absence-only:** if the normal prompt
  matcher caps on a coherent loaded scene, one larger bounded scan may search
  only the exact retained templates. The broad disconnect heuristic remains a
  `LOGIN_SCREEN` path. PASS still needs two increasing same-identity loaded
  ticks and a refreshed current proof after a slow scan.
- **D029 — The default runtime budget fits the fixed cycle:** the frozen ideal
  FSM has 64 executable actions, while the fresh live cycle completed in 82
  after normal bounded overhead. The default is 100 actions under the unchanged
  hard cap of 500; this changes no per-action safety, verification, input, or
  cleanup bound.
- **D030 — Sensors publish facts, not task meaning:** RuneLite exposes one
  neutral scene-object census and structurally identified dialogue facts. It
  does not classify resources, routes, services, skills, profiles, tasks, or
  desired classes. Spatial/projection ordering uses only factual geometry,
  identity, and distance; the selected definition/task assigns meaning.
- **D031 — External cursor recovery moves RuneLite, not the pointer:** after a
  bounded physical-button quiet dwell, an external stationary cursor may be
  recovered before serial connect by one exact, non-activating, no-resize
  translation of the pinned foreground RuneLite window on its containing
  monitor. Split outer/client/canvas geometry, PID/HWND, cursor stability, and
  final point ownership must all pass. The old intent is always discarded;
  login re-finds and re-screens the client, while gameplay requires a newer
  same-identity sensor tick. Any unproved post-mutation state is terminal, and
  the pointer never traverses foreign desktop surfaces.
- **D032 - Menu coordinates are correlation evidence, not activation
  authority:** RuneLite reports menu mouse position as integer source-canvas
  pixels. At the retained 175%-display fixed-client layout, fresh exact-menu
  correlation may differ from the settled PMv2 Win32 cursor by at most four
  device pixels. This does not enlarge the +/-3 activation region, API
  shape/canvas containment, canonical aim checks, or exact menu identity; five
  pixels still blocks. A different layout requires fresh measurement.
- **D033 - Window handoff and Arduino input share one cross-process lease:**
  the configured port lease is acquired before pointer preflight or any
  `SetWindowPos`, without opening or arming hardware, and remains held through
  backend close. Contention yields a safely-unsent empty-ledger receipt and
  cannot mutate RuneLite while another process owns an input transaction.
- **D034 - Owned Windows button release may lag its firmware ACK:** after an
  acknowledged `MOUSE_UP`, the source-blind Windows button reader may wait up to
  500 ms for the owned button to reach two consecutive all-clear samples. The
  reader sends no firmware command and does not renew the one-second watchdog;
  existing STATUS/rearm/revalidation gates remain mandatory before later input.
  It rejects any other button immediately and still blocks a persistent owned
  state. Same-button human input anywhere in the window remains inherently
  source-indistinguishable and best effort.
- **D035 - Delayed Windows MOVE feedback has an absolute no-input settlement
  bound:** start a monotonic clock before the serial MOVE so ACK latency counts.
  If ordinary samples do not show every commanded axis, use at most ten fixed
  20 ms polls with no Arduino command or watchdog refresh. The complete
  cumulative effect must be observed by 200 ms and two later identical whole-
  cursor samples by 240 ms. Every extended sample rechecks foreground, pinned point
  ownership, bounds, direction, gain, and uncommanded axes; final settlement
  additionally requires physical-button quiet and one unchanged owned sample.
  Discard the interrupted trajectory and freshly replan from the stable point.
  Receipts retain bounded counts, maxima, the last command/points/timings, and a
  settled, unresolved, or rejected outcome. Same-direction/in-gain buttonless
  human motion remains source-indistinguishable and subject to the fresh
  semantic/owner veto.

## Phases and acceptance

| Phase | Status | Acceptance |
|---|---|---|
| 0. Freeze proven baseline | Complete (`beb9cbb`) | Full diff audited; Python/Java suites pass; terminal trace reaches `COMPLETE`; tracked golden replay passes; stale proof docs corrected; checkpoint committed/tagged. |
| 1. Governing contract | Complete (`f4c091e`) | Product vision, architecture, rules, status, and this plan describe the OSRS-specific engine and prohibited expansion. |
| 2. Coherent sensor truth | Complete (`b645d83`, audit `f2007eb`) | Atomic tick `SensorFrame`; source-based freshness; mixed/stale/missing/menu/schema tests; deterministic real-Java fixture parsed by Python; bounded live observe. |
| 3. Minimal task seam | Complete (`56b8b8b`) | Runtime has no concrete woodcut imports/phase checks; typed outcomes; fake task runs unchanged runtime/safety/verifier. |
| 4. One task/site definition | Complete (`0c8ec9e`, audit `f2007eb`) | One immutable Lumbridge definition/task owns task meaning; neutral sensor facts; one validated default profile; unsafe/malformed values fail closed; replay unchanged. |
| 5. Arduino boundary | Complete (`8b71ebc`) | One `InputCoordinator`; no production bypass; bounded pointer policy; command/ACK plus authoritative final firmware status. |
| 6. EngineFrame + overlay | Complete (`a166d59`) | One immutable diagnostic truth; passive click-through overlay mirrors it and has no control authority. |
| 7. Demonstration capture | Complete (`51dbaaf`) | Read-only record/inspect commands produce verified hashed JSONL, manifest, timeline, bounded screenshots, and reviewed semantic suggestions. |
| 8. Frontend contracts | Complete (`0f21773`) | Minimal facade proves list/validate/start/pause/stop/status/demo operations without duplicating task or safety logic. |
| Final regression | Complete (`000a886`) | Displaced login/gameplay recovery, direct delayed-MOVE settlement, a complete current-checkpoint bank-and-return cycle, manual-cursor resampling, a user-performed `Walk here` demo, public artifact inspection, and post-demo cleanup are **PASS** at the retained layout. |

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
  enforce frame/menu provenance. Added `docs/SENSOR_CONTRACT.md`.
- Acceptance audit `f2007eb` added a newly assembled/old-source rejection, a
  byte-deterministic fixture emitted through the real Java
  `SensorFrame -> PluginLiveCache -> PluginSnapshotEndpoint` path, exact
  serialized fact-size checks, and Python parsing of that Java fixture.
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
  Acceptance audit `f2007eb` removed the plugin's candidate/type/skill fields,
  filtered semantic censuses, class/name/action hinting, and skill capture;
  Python ignores retired payload names and replay passes with no hint fields.
  Dialogue capture now uses pinned RuneLite widget identities and leaves exact
  prompt/option interpretation to the definition/task.
- Corrected a truncated 62-character bank-close evidence digest in the golden
  fixture after verifying the retained trace's 64-character SHA-256.
- Added `docs/DEFINITIONS_AND_PROFILES.md`. Final gate: golden replay 2 passed,
  all 169 Python tests passed, a forced fresh 42-test Java suite passed, and
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
  Python tests passed, a forced fresh 42-test Java suite passed, and
  `git diff --check` passed.

## Phase 6 completed work

- Extended the four-operation task seam with frozen diagnostic values carried
  by `Decision` and `TaskSnapshot`; runtime still has no concrete task or
  mutable progress dependency.
- Made the concrete FSM expose definition/profile IDs and route/cycle progress,
  then publish selected, eligible, and rejected target evidence from the same
  selection/classification path that drives actions. Rejections use stable
  codes and retain source tick plus geometry-frame provenance.
- Added ordered `SafetyEvaluation` values and carried the exact checks used by
  pre-move, post-move, context, and retry paths in immutable execution results.
  Diagnostics never rerun the gate.
- Added one atomic latest-only `EngineFramePublisher`. Terminal frames retain
  task/binding/progress, observation identity, decision evidence, safety,
  pending/last verification, typed outcome, real input receipt, derived cleanup
  proof, and blocker.
- Added the opt-in Windows overlay with verified click-through/no-activate
  styles, required green/amber/optional-red rendering, stale-geometry
  suppression, no input handlers, and failure isolation. Disabled mode creates
  no overlay thread or window.
- Added `docs/ENGINE_FRAME.md`. Final gate: golden replay 2 passed, all 241
  Python tests passed, a forced fresh 42-test Java suite passed, and
  `git diff --check` passed.

## Phase 7 completed work

- Reused the sole `POST /snapshot` endpoint for three bounded additive needs:
  globally sequenced client/menu/click tails, NPC-only actor census, and the
  existing collision window. Java hard-caps menu/tail/actor/collision rows and
  binds world evidence to the atomic frame before exposure.
- Added a read-only recorder that requires loaded-scene/session/PID proof,
  deduplicates endpoint sequences, downsamples pointer evidence to 20 Hz,
  records semantic hover/click plus before/after outcomes, stops on identity or
  sequence reset, and optionally captures only verified-canvas crops outside a
  bank-PIN surface.
- Added self-contained JSONL, manifest, semantic summary, Markdown timeline,
  screenshot, and SHA-256/byte-size artifacts. The manifest records commit,
  dependencies, schemas, request controls, source provenance, observable gaps,
  and explicit no-input/no-replay/no-activation rules.
- Added an inspector that rejects unsafe paths/symlinks, oversized or unexpected
  files, hash/size tampering, invalid schemas, and discontinuous recorder
  sequences before deriving any suggestion. Valid suggestions contain reviewed
  world/entity/action/plane facts only and omit screen/canvas/mouse coordinates.
- Added `run.cmd record-demo NAME`, `run.cmd inspect-demo PATH`, focused Python
  and Java wire tests, and `docs/DEMONSTRATIONS.md`.
- Final gate: golden replay 2 passed, all 267 Python tests passed, the forced
  fresh 51-test Java suite passed across 6 suites, and `git diff --check`
  passed.

## Phase 8 completed work

- Added one in-process `EngineApplication` composition facade that lists the
  exact supported task/definition, publishes a fresh frontend-safe profile
  contract, and reuses the authoritative profile binder at every start.
- Added monotonic run/capture IDs, serialized start/capture ownership, and
  mutually exclusive automation/demonstration workers. Delayed IDs cannot
  pause, stop, resume, or end a later operation.
- Added cooperative runtime pause/resume/safe-stop control. Pause is
  acknowledged only at no-input boundaries, observations held across pause are
  discarded, and a decided action always finishes its Arduino transaction,
  cleanup receipt, bounded verification, and typed transition before stop.
- Added exact facade reads of the latest `EngineFrame`, runtime-owned immutable
  statistics, and owner-produced blockers. Application lifecycle state remains
  separate and cannot reinterpret task, safety, verification, or cleanup truth.
- Added a foreground `run.cmd app` CLI for catalog/profile inspection,
  validation, dry-run, and explicit Arduino execute mode. `Ctrl+C` requests
  cooperative safe stop; no daemon, IPC protocol, or full GUI was added.
- Added a frozen dependency-free `VisionEvidence` future seam with exact crop
  transforms and model-space evidence. It is explicitly non-authoritative,
  cannot authorize input, and has no model or runtime integration.
- Documented the complete future GUI screen contract, restart rules, and
  Vision/LLM authority boundaries in `docs/FRONTEND_CONTRACT.md`.
- Final gate: golden replay 2 passed, all 301 Python tests passed, the forced
  fresh 51-test Java suite passed across 6 suites, facade catalog/schema/profile
  commands succeeded, and `git diff --check` passed.

## Final regression hardening complete

- Made RuneLite publish available canvas/window bounds only as Win32
  virtual-desktop device pixels. AWT user-space origin and extents are scaled
  separately with the monitor origin and Windows/JDK rounding; missing,
  spanning, non-axis-aligned, nonfinite, or otherwise unproven transforms fail
  closed.
- Kept projection source-canvas dimensions separate from displayed device-pixel
  bounds, included the coordinate space in geometry identity, and made Python
  reject available geometry without the exact schema and coordinate-space
  declaration.
- Kept normal gameplay transit inside loaded-scene telemetry canvas bounds. The
  exact visible PID-owned Win32 outer window is a movement-only recovery region
  for a freshly sampled cursor displaced just outside the canvas. The bounded
  lane never activates, requires exact point ownership and per-monitor-v2
  device-pixel proof, and hands off to the ordinary canvas planner only after a
  stable inset. Saved-session login separately uses its exact client boundary
  and rejects a screenshot whose dimensions differ from it.
- Offline hardening gate: golden replay 2 passed; all 311 Python tests passed;
  a forced fresh Java run executed 55 tests across 6 suites with zero failures,
  errors, or skips; an actual Windows subprocess verified per-monitor-v2; and
  `git diff --check` passed.
- A committed login-screen trial on a 175% display proved that each Arduino HID
  count moved about 1.75 Win32 device pixels. Exact center-pixel feedback then
  oscillated even though a settled plan endpoint was already safely inside the
  freshly recognized Play Now bounds. Both failed attempts clicked nothing and
  ended with acknowledged `STOP_ALL`, `DISARM`, and safe zero-held-input
  `STATUS` receipts.
- Kept the pure exact planner and made the coordinator translate toward the
  approved point with bounded command-space waypoints and actual feedback. An
  unknown axis starts with one count; every planner path reserves an eight-
  device-pixel per-count envelope on all four sides, observed transfer above
  four aborts, and the whole Arduino transaction is capped at 64 plans/512 MOVE
  commands even when it opens and selects a context row. A zero-step plan may
  prove an already-stable approved point; no transient crossing may activate.
  The actual endpoint is passed into fresh validation and must remain unchanged
  inside transit and activation bounds before and after it.
- Gameplay regions are clipped to +/-3 device pixels around the approved point,
  and SafetyGate hover validation is rebound to the actual settled point so two
  independent tolerances cannot drift apart. Login revalidates the complete
  detected prompt and exact window ownership at the actual point.
- Pointer-arrival hardening gate: all 323 Python tests passed, including long
  175% gameplay movement, every-position containment, 400% boundary transfer,
  initial/four-sided headroom, unsupported-gain, stable zero-step, cursor-drift,
  and transaction-wide plan/step caps; golden replay 2 passed; and a forced
  fresh Java run executed 55 tests across 6 suites with zero failures, errors,
  or skips.
- Subsequent checkpoints `18c352e` through `2a26199` added visible-empty
  inventory proof and blank-slot normalization; bounded watchdog and login
  matcher work; same-thread overlay teardown and top-level click-through host
  styling; canonical-versus-settled pointer evidence; bounded transient warning
  and reprojection retries; typed camera recovery; post-input verification
  rebasing; unique lower-context `Walk here`; the 20-tick route movement
  deadline; isolated no-effect calibration/replanning caps; exact return-route
  restart reconciliation without cycle credit; and typed preactivation target
  invalidation with one fresh alternate selection.
- Live regression proved a fresh coherent loaded scene at
  `(3214,3228,0)`, reconciled the exact empty-inventory return route, visually
  compared the passive overlay at route steps `9/15`, `12/15`, and `13/15`, and
  observed typed camera-pose and arrival outcomes with `cleanup: safe`.
- The same bounded process completed the historical return without credit,
  entered the genuine `cycles 0/1` harvest, collected six logs, and then
  correctly withheld activation when the selected tree's fresh hover evidence
  changed during pointer transit. Its receipt proved `STOP_ALL`, `DISARM`, safe
  zero-held firmware state, zero unresolved commands, and no click sent. That
  evidence produced checkpoint `2a26199`; the replacement path is typed,
  one-consecutive, preactivation-only, and alternate-target tested.
- Checkpoints `9528edf` through `0571c37` then added one verified resource
  no-yield retry, typed pointer-transfer diagnostics, one delayed-report credit,
  one-axis cursor headroom recovery, bounded projection-invalidated replanning,
  exact outbound restart reconciliation, and API-shape-interior object aim
  points.
- Checkpoints `7df39e5`, `004517b`, and `07aef8c` made manual cursor displacement
  a freshly observed state rather than implicit history: bounded client-to-
  canvas ingress, per-thread per-monitor-v2 cursor/owner sampling, and one extra
  no-input poll for a late Windows cursor report. `b673fd6` and `81b1657` bound
  dense loaded-scene template absence proof without letting the disconnect
  heuristic authorize or veto a coherent loaded world.
- The cursor diagnosis reproduced the same untouched physical point as
  `(2006,1226)` in a DPI-virtualized process and `(3510,2145)` after per-monitor-
  v2 awareness, exactly the display's 1.75 scale. The apparent loss of location
  was coordinate virtualization, while actual manual movement is now handled as
  fresh external cursor state.
- A later live login attempt reproduced the user's external-cursor failure at
  PMv2 point `(3446,1631)`: the engine knew the point exactly, but it was 25
  pixels beyond the RuneLite outer window and therefore rejected it twice with
  complete safe cleanup. D031 now performs a pre-serial stationary-window
  handoff, rigidly proves the distinct Win32 outer/client/canvas rectangles,
  discards the stale intent, and consumes only its own acknowledged mouse
  transition. Historical button activity receives a quiet dwell; new activity,
  identity/geometry drift, final point-owner mismatch, or an unproved async
  window mutation still blocks. Deterministic Python coverage is complete; the
  fresh physical login/gameplay regression remained pending at that checkpoint.
- A fresh process completed login safely but the first qualifying cycle reached
  the old 80-action default at return step `10/15` after 624.9 seconds. It had
  already harvested, banked, deposited to empty, and passed the earlier cursor
  no-effect boundary; the final receipt and runtime cleanup were safe. Checkpoint
  `aaa0290` corrected the stale 52-action runtime model to the real 64-action
  ideal and raised only the default budget to 100 under the unchanged 500 cap.
- Pre-audit checkpoint PID `11440`, session
  `plugin-11440-1783810438162`, then began with coherent empty-inventory route
  reconciliation and completed one uninterrupted default-profile cycle in
  698.2 seconds, 1,994 observations, and 82 actions. It returned to
  `west_trees`, published `cycles 1/1`, and ended with acknowledged `STOP_ALL`,
  `DISARM`, disarmed zero-held firmware status, zero unresolved commands, and
  closed ledger/backend. All default bounds held: 82/100 actions, 1,994/4,800
  observations, and 698.2/1,200 seconds. The intact ignored proof is under
  `_run_proofs/final_regression/20260711_cursor_reacquire_complete_cycle/`.
- Forced closeout gate: all 457 Python tests pass, golden replay 2/2 passes,
  `python -m compileall` and `git diff --check` pass, and a fresh Java rerun
  executed 66 tests across 6 suites with zero failures, errors, or skips. Counts
  come from the current externally configured Gradle build directory; the
  checkout-local `build/` contained stale reports and is not evidence.
- Acceptance-audit checkpoint `f2007eb`: all 461 Python tests pass, golden
  replay 2/2 passes, `python -m compileall` and `git diff --check` pass, and a
  forced Java rerun executed 76 tests across 8 suites with zero failures,
  errors, or skips. The Java-produced endpoint fixture regenerates
  byte-identically at SHA-256
  `80AF03C08681D242033D5ED4FBFF56AF6069263C40E0D290CABF5B7DDA549081` and
  parses as a coherent loaded Python Observation. Catalog, profile schema, and
  default-profile validation commands pass. This production sensor checkpoint
  postdates PID `11440`, so that retained cycle is no longer current-checkpoint
  proof; a new cycle was pending at that checkpoint.
- External-cursor offline gate: all 508 Python tests and the 216-test focused
  input set pass, golden replay remains 2/2, compile/diff/catalog/profile gates
  pass, and the forced Java suite remains 76/76 across 8 suites. Read-only live
  Win32 evidence matched the exact outer/client/canvas split on PID `1968`. At
  that checkpoint, physical login handoff and gameplay execution were the next
  gates.
- The post-cycle observation still passed fresh/coherent with no warnings.
  About 47 seconds after terminal completion (10 seconds after that observation),
  RuneLite emitted repeated OpenGL out-of-memory/invalid-operation errors and
  the Gradle-wrapper JVM, PID `500`, failed a native allocation. The launch stack
  then ended; RuneLite PID `11440` and listener `8893` are absent. Logs are
  retained beside the proof. This occurred after terminal engine/input cleanup
  and does not invalidate the cycle, but the manual demonstration then required
  a fresh client.

### Current external-cursor proof line

- Checkpoints `9e29487` through `ae8b9f8` make a quiet cursor takeover a
  freshly observed state instead of remembered coordinates. They add the
  stationary-window handoff, caller reobservation, owned-release settlement,
  login prompt/variant/watchdog hardening, exact one-pixel AWT/native origin
  reconciliation, a cursor-safe login activation footprint, bounded retained-
  layout menu-coordinate correlation, and a cross-process input/handoff lease.
- **PASS - displaced saved-session login component proof.** At `31e1391`,
  `login_activation_footprint_current.json` returned
  `loaded_scene_verified` in 34.172 seconds. The external handoff was safely
  unsent before serial connect with zero commands; disconnect, Play Now, and
  Click here to play then passed with acknowledged `STOP_ALL`, `DISARM`, safe
  zero-held status, and closed ledgers/backends. At `ae8b9f8`, all three prompt
  transactions again passed. `login_after_cross_process_lease.json` itself ends
  BLOCKED only because a later read-only template scan hit its candidate cap;
  `observe_after_cross_process_lease_login.json` then proved a fresh coherent
  loaded scene, and the no-input confirmation returned `loaded_scene_verified`
  with zero clicks. No single `ae8b9f8` artifact is an end-to-end displaced
  login run; the current claim composes that direct `31e1391` handoff with the
  current prompt/loaded results and focused cross-process-lease tests.
- **PASS - displaced gameplay recovery subcriterion.** The current-checkpoint
  setup held the cursor exactly at `(606,972)` over foreign PID `6120`, outside
  foreground RuneLite at x=700. The dry-run first proved an exact Tree `1276`
  action. The bounded live run then translated RuneLite to x=555 under the
  stationary cursor, reobserved, and continued Arduino gameplay. Its ten action
  attempts comprise the safely-unsent handoff plus nine sent Tree transactions
  (inferred from the window relocation, action count, and inventory increase
  from 10 to 19 logs). The final retained receipt is PASS with one acknowledged
  click, `STOP_ALL`, `DISARM`, disarmed zero-held status, zero unresolved/failed
  commands, and closed ledger/backend; its typed gameplay verification timed
  out under the short run bound.
- **FAIL - pre-`8f7c1b2` gameplay activation trial.** The exact Tree menu stayed
  fresh while RuneLite's scaled menu point was stably four device pixels left
  of the settled Win32 cursor. No click was sent and cleanup was safe. Eight
  no-input samples reproduced the same difference. `8f7c1b2` changes only that
  menu correlation to four pixels; activation and fresh aim remain +/-3 and a
  five-pixel mismatch still blocks.
- The 90-second combined proof ended top-level **BLOCKED** because its
  deliberately short wall-clock bound expired while verifying the final/tenth
  action attempt (the ninth Tree click). This does not change the handoff and
  continued-gameplay subcriterion above. At that checkpoint a complete cycle
  was not evaluated, and an earlier long run was interrupted by a real server
  disconnect; the later `6eef48c` proof below now supplies the complete current-
  checkpoint cycle.
- Historical `ae8b9f8` offline gate: 527 Python tests, golden replay 2/2, compile/diff,
  facade catalog/schema/profile validation, and `run.cmd test` pass. A forced
  Java rerun executed 76/76 tests across 8 suites with zero failures, errors,
  or skips from the configured external build directory.
- `31e1391` streamlined `AGENTS.md` so boundary-local deterministic algorithms,
  evidence-backed host fixes, and explicitly authorized wrapper execution are
  allowed without weakening Arduino-only input, loaded-scene, identity,
  geometry, fail-closed, or cleanup invariants.
- Checkpoints through `f3dce8d` added delayed-cursor settlement/replan handling
  and exact known bank-area restart reconciliation without historical cycle
  credit. The unattended `f3dce8d` run then made 71 action attempts and 1,939
  observations over 11.5 minutes, reached the fresh outbound bank route at
  `south_corridor_bridge` (`10/19`), and exposed one new boundary: the
  preparatory right-click's acknowledged Windows transition did not settle
  inside the old 100 ms window. The semantic `Walk here` row click was never
  sent; Escape cancellation and final firmware/ledger cleanup were fully safe.
  The user was asleep, so the evidence does not support manual interference.
- D034 extends only that source-blind owned-button window to 500 ms, retains two
  final all-clear samples, rejects other-button and persistent activity, sends
  no firmware command while waiting, and cannot renew the one-second watchdog.
  It does not claim to distinguish same-button human input. If the live failure
  repeats, diagnostics must retain elapsed/sample evidence before any further
  timing change.
- Unsuccessful execution now separately records whether the semantic click/key
  may have been written. Runtime blocks attempted activation without retry,
  safely-unsent discard, verifier credit, or an unsent semantic claim. A
  context opener alone does not cross that boundary. The boolean is persisted
  in EngineFrame/application JSON for one diagnostic truth.
- Checkpoint `07de1ef` commits the 500 ms owned-button bound and conservative
  activation truth. Its retained login proof passed `loaded_scene_verified` in
  27.109 seconds with three allowed clicks and complete safe cleanup (SHA-256
  `BC00AACF2D5AC5A218B057439F57577C4FB3F43DB9BD612C99D6E856B93D32C2`). This
  proves the code was live-integrated, not that a transition over 100 ms was
  absorbed.
- The retained `07de1ef` gameplay run lasted 747.893 seconds, made 91 action
  attempts and 1,997 observations. The combined retained client/plugin
  chronology already showed the prior bank/deposit/return and a fresh full
  inventory; this run advanced that reconciled outbound state to route step
  `16/19`. Transaction 91 then blocked before activation after eight
  acknowledged MOVEs because the final MOVE effect was not visible through the
  old roughly 60 ms sampling. Every command was terminal and cleanup proved
  `STOP_ALL`, `DISARM`, disarmed zero-held status, and closed ledger/backend
  (SHA-256
  `61D7DFE5C7C168941BD0E827DF5307B1853F0BA8F7B22FE467D7711D9800AA99`). The
  artifact cannot distinguish a late Windows report from lost/no effect. The
  user was asleep, so it does not support manual movement as the cause.
- Checkpoint `6eef48c` replaces that blind sampling limit with the
  absolute 200/240 ms contract and retained per-transaction feedback evidence.
  Its offline gate passes 560 Python tests, the 107-test coordinator suite, the
  154-test cross-boundary suite, compileall, diff-check, golden replay 2/2,
  `run.cmd test`, catalog, profile-schema, and default-profile validation. A
  forced noncached Java rerun executed 76/76 tests across 8 suites from the
  configured external build directory.
- **PASS - direct delayed-MOVE proof.** The `6eef48c` login passed
  `loaded_scene_verified` in 25.656 seconds with three allowed Arduino-only
  clicks. The final click retained one settled feedback wait: effect
  first/fully observed at 78 ms, three extra no-input polls, stable completion
  at 125 ms, and fully safe cleanup. Its SHA-256 is
  `C28224D84623D70F8B60D49512355590CB45B4B12BCD0BF0A97BC8ED33743B37`.
- A first bounded current-checkpoint gameplay continuation crossed the former
  step-16 cursor blocker, completed recovery plus a fresh harvest/bank/deposit,
  and reached empty-inventory return step `9/15`. Its configured 100-action cap
  ended the 2,137-observation/807.586603-second run with safe cleanup, not a
  cursor failure (SHA-256
  `420FC2929A713EC8EF787492209073B02DE93F44B78D70898B2EA9D56D8DDBA5`).
- **PASS - complete current-checkpoint cycle.** From that exact return state,
  one final bounded continuation reached `COMPLETE`, `cycles 1/1`, and
  `west_trees` in 89 actions, 2,214 observations, and 810.725564 seconds. It had
  no blocker and proved acknowledged `STOP_ALL`, `DISARM`, disarmed zero-held
  STATUS, zero unresolved commands, and closed ledger/backend (SHA-256
  `B91B1025CD9343991A46ABB55045CA63DA5DB978748466338E7B264CAF83130D`). The
  retained post-observation was loaded/fresh/coherent with no warnings or
  missing capabilities. No bot process or Arduino lease remained.
- **PASS - manual cursor resampling.** The final login safely blocked before COM
  connect while the physical cursor was at desktop x=0. After the user moved it
  into RuneLite, the next attempt freshly observed that position, completed the
  Arduino-only prompt flow, passed `loaded_scene_verified`, and closed with
  acknowledged `STOP_ALL`, `DISARM`, safe zero-held status, zero unresolved
  commands, and no lease.
- Checkpoints `c8888bb` and `000a886` retain a demonstration through the
  endpoint's bounded sensor-frame/world-model handoff without weakening the
  Java mismatch rejection. Only the exact all-dynamic-payloads-absent shape is
  tolerated for five monotonic seconds; session/PID, core scene, tick, hot-tail,
  raw-payload, warning, or capability contradictions still stop capture.
- **PASS - accepted manual demonstration.** The clean-commit `000a886` artifact
  `demo_runs/20260712T170027843742Z_final-manual-walk-000a886-final/` records a
  semantic `Walk here` click at source tick 1060 and player movement from
  `(3197,3238,0)` to `(3196,3237,0)` at tick 1064. It is `valid: true`, has no
  errors or ambiguities, and the public `run.cmd inspect-demo` command returned
  `VERIFIED_WITH_GAPS`. Its generated candidates are review-only and
  `never_automatic`.
- Final gate: 570 Python tests, focused demonstration tests 30/30, golden replay
  2/2, compileall, `run.cmd test`, and diff-check passed. The accepted
  `events.jsonl` SHA-256 is
  `582488FD366CC7F08C9D848890B181C0CD9B130599E9F57325D779A88916C26E`;
  the inspector proof SHA-256 is
  `CBF2FD726E7FE1B5CAF770E9686CC4EA5063C76F0D07CA2A6B8264583965AA16`.
- **PASS - final shutdown.** Graceful close of exact RuneLite PID `12712` timed
  out after 15 seconds, so only that bound PID was force-stopped. PID `12712`,
  listeners 8893/8890, OSRS Python workers, repository Java processes, and the
  Arduino lease were all then confirmed absent.

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

- The original baseline corpus is stitched and lacks complete raw observations,
  command/ACK receipts, and immutable source provenance. The 2026-07-11 PID
  `11440` proof is a separate uninterrupted pre-audit cycle with its terminal
  JSON and final receipt preserved in ignored local evidence. Current evidence
  now proves displaced login/gameplay recovery, direct 78 ms delayed-MOVE
  settlement, and a complete current-checkpoint bank-and-return cycle.
- Each new source tick can still force a world-model refresh behind a 250 ms
  provider wait. A client-thread query can also return at the next sensor tick;
  the endpoint correctly rejects that mismatch, while demonstration recording
  now tolerates only the exact bounded all-dynamic-payloads-absent handoff. This
  remains a thin acquisition-latency hardening opportunity, not a second cache
  or a final-regression blocker.
- The later RuneLite GPU errors and Gradle-wrapper PID `500` native-memory
  failure are launch-stack stability limitations, not engine/input cleanup
  failures. Their error and replay logs remain in the ignored proof directory.
- There is intentionally no external/profile file loader or second definition;
  the one validated in-code default is the only supported choice.
- The demonstration path intentionally cannot observe global raw mouse-button
  or keyboard transitions; it records RuneLite semantic click evidence and
  declares those coverage gaps in every manifest.
- The implemented facade intentionally has no full GUI, daemon, or IPC layer.
  The overlay has been visually compared against live route, camera, target,
  verification, and cleanup evidence. The user-performed demonstration, public
  inspection, and post-demo client cleanup are now accepted final-regression
  evidence.
- The four-device-pixel menu correlation is proven only for the retained
  fixed-client layout on the 175% display. A different layout requires fresh
  measurement; activation authority remains at +/-3 pixels everywhere.
- Structural tests cover neutral `Chatmenu` dialogue capture, and the complete
  current-checkpoint cycle confirms the live staircase prompt/options remained
  compatible with the pinned widget shape.
- `EngineFrame` intentionally retains only the latest execution receipt. A
  delayed-feedback receipt is complete for its transaction, but a successful
  one-reobservation retry can replace it in terminal run output; bounded prior-
  attempt history remains a future observability decision, not control state.
- `input_transaction_receipt.v1` gained additive `cursorFeedback` evidence.
  Current artifacts serialize it; older v1 artifacts may omit the field.

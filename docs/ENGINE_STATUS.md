# Engine Status

## Current milestone

**Stationary-RuneLite external-cursor reacquisition is implemented and
live-proven.**

Normal production cursor recovery now keeps the exact telemetry-owning
RuneLite window stationary and moves only the freshly observed PMv2 cursor
through the existing Arduino-only `InputCoordinator`. The replacement policy,
focused/full deterministic gates, and exactly one bounded no-click live cursor
test are **PASS** at the retained fixed-client 175% layout. Historical D031
window-translation evidence remains below only as retired context.

Current input milestone: reacquire an external cursor without moving or
resizing RuneLite, discard the pre-movement intent, and require fresh normal
recognition and SafetyGate validation before any later activation.

Current demonstration checkpoints: `c8888bb demonstrations: tolerate bounded
world model handoff` and `000a886 demonstrations: settle manual movement
evidence`.

Current input contract: one exact PID/root HWND, invariant outer/client/canvas
geometry, PMv2 virtual-desktop cursor truth, the existing shared Arduino lease,
the source-blind 500 ms owned-button bound plus absolute 200 ms MOVE-effect /
240 ms stability contract, conservative post-activation classification, and
persisted receipt/EngineFrame truth.

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
- Inventory truth includes exact visible-empty evidence: a complete 28-slot
  widget may prove structural emptiness, and `BLANKOBJECT` slots normalize away
  before the immutable Observation reaches task logic.
- The canonical request consumes one neutral scene-object census. The endpoint
  exposes no resource/route/service classifiers, task/class hints, or
  name/action/kind filters; Python ignores retired semantic census names. Exact
  selected-definition facts assign task meaning downstream.
- Dialogue capture uses pinned RuneLite option/continue widget identities and
  publishes arbitrary prompt/option text as facts. Exact staircase wording is
  interpreted only in the definition/task; hidden or ambiguous widget surfaces
  fail closed.
- `tests/fixtures/java_snapshot_endpoint.json` is emitted through the real Java
  frame/cache/endpoint path, regenerates byte-identically, carries real payload
  sizes, and parses into a coherent loaded Python Observation.
- Available input geometry is explicitly Win32 virtual-desktop device pixels.
  RuneLite converts AWT user bounds with one proven monitor transform and
  Python rejects missing or different coordinate-space declarations.
- Runtime consumes only the structural `Task` contract: bounded observation
  request, opaque decision, typed verification application, safely-unsent
  proposal discard, and immutable status snapshot. It has no woodcut import,
  phase comparison, or progress access.
- `WoodcutBankTask` explicitly models the one supported ordinary-tree cycle.
- The FSM is bound to exactly one immutable `LUMBRIDGE_WEST_TREES_V1`
  definition and one validated one-cycle default profile. All Lumbridge IDs,
  coordinates, route facts, deadlines, predicates, and provenance live there.
- Endpoint/Arduino/polling and runtime limits live separately in immutable,
  finite, engine-capped `RuntimeConfig`. The supported default is 100 actions
  under an unchanged hard maximum of 500; the profile cannot change either.
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
- A separate 2026-07-11 pre-audit process completed the default
  profile uninterrupted: PID `11440`, session
  `plugin-11440-1783810438162`, 698.2 seconds, 1,994 observations, 82/100
  actions, terminal `COMPLETE` at `west_trees`, and `cycles 1/1`. Its final
  receipt and runtime cleanup prove `STOP_ALL`, `DISARM`, disarmed zero-held
  firmware status, zero unresolved commands, and closed ledger/backend. This
  remains strong input/cycle evidence but no longer proves the later `f2007eb`
  production sensor checkpoint.
- All live gameplay input remains Arduino-only; no software fallback exists.
- Historical D031 displaced-login behavior has a stitched component proof. The
  direct `31e1391` run safely handed off with zero commands, passed the
  disconnect/Play Now/Click here transactions with complete cleanup, and
  reached a loaded scene. At `ae8b9f8`, the three prompt transactions and
  loaded outcome passed again, while focused tests cover lease contention. No
  single `ae8b9f8` artifact is a direct end-to-end displaced-login run. This is
  evidence for the retired window-translation policy, not current production
  cursor-recovery behavior.
- Historical D031 displaced gameplay recovery was proven as a subcriterion at
  the retained layout. With the cursor stationary outside RuneLite over foreign
  PID `6120`, the engine moved only the pinned window, reobserved, and sent nine
  Tree interactions; inventory increased from 10 to 19 logs. The
  bounded run ended top-level BLOCKED when its deliberately short runtime
  expired while verifying the final/tenth action attempt (the ninth Tree
  click), and its final PASS receipt/cleanup is fully safe. That proof remains
  historical and does not authorize moving or resizing RuneLite now.
- Gameplay and saved-session login now submit immutable approved intents to one
  `InputCoordinator`; neither can open an Arduino session or call raw input.
- Current production cursor recovery keeps RuneLite stationary. The engine
  binds the telemetry PID and exact foreground root HWND, retains native outer/
  client plus telemetry-canvas geometry, and never reaches a window-position/
  size mutation or software cursor API from the recovery path.
- Gameplay waits for the telemetry-owning RuneLite root to become foreground
  and blocks if the bounded focus wait expires. Saved-session login may use only a
  bounded `SetForegroundWindow` focus attempt for the exact visible,
  non-minimized root and proves PID/HWND plus outer/client geometry unchanged
  before and after; failure returns manual attention rather than moving RuneLite.
- A freshly sampled cursor anywhere outside the canvas but inside the proven
  PMv2 virtual desktop enters through one connected movement-only transaction
  under the existing cross-process Arduino lease. It moves through the existing
  coordinator/transport to a neutral inset inside the canvas after protocol-safe
  ARM proves zero firmware-held keys/buttons. It sends no click or key and
  continuously preserves foreground, PID/root HWND, virtual bounds,
  physical-button release, bounded motion, and bit-for-bit outer/client/canvas
  geometry. Exact point ownership is required after canvas entry.
- The movement-only receipt retains additive `cursorReacquisition` before/after
  cursor and geometry evidence plus completion, geometry-unchanged, and no-
  activation facts. Authoritative cleanup runs on success or failure. Typed
  invalidation discards the old intent: gameplay needs a strictly newer same-
  PID/session tick whose source is fresh, wall-clock-fresh, and coherent, plus
  exact geometry, before recognition/SafetyGate validation; login refetches,
  re-finds, and re-screens the exact client. Login permits at most two cursor-
  recovery attempts before an explicit manual-attention result.
- Exactly one bounded live cursor test on 2026-07-12 pinned PID `12164`, root
  HWND `7210234`, outer `(397,390,2243,1585)`, native client
  `(409,390,2219,1573)`, and canvas `(416,437,2151,1519)`. With RuneLite still
  foreground, the externally placed cursor started at `(3180,1062)`, completely
  outside the RuneLite outer window, and 82 acknowledged Arduino `MOVE`
  commands settled it at `(1497,1189)` in neutral canvas bounds
  `(1483,1188,17,17)`. No click, mouse-button, or key command was present.
  Before/after receipt geometry matched bit-for-bit. The tick-197 intent was
  typed `cursor_state_invalidated`; runtime obtained a new decision from tick
  202 and then stopped at its deliberate one-action cap before activation. An
  independent post-proof remained loaded/fresh/coherent at tick 309 with the
  cursor unchanged across two samples. Cleanup ended
  `STOP_ALL -> DISARM -> STATUS`, final firmware was disarmed with zero held
  keys/buttons, every command was acknowledged, all command-failure counters
  were zero, ledger/backend were closed, and the COM6 lease was absent.
  The exact test client then closed gracefully without force; its Gradle wrapper,
  ports 8893/8890, live task process, and COM6 lock were all absent in
  `environment_cleanup.json`.
  The user prepared the start over a foreign surface, but `pre.json` did not
  serialize `WindowFromPoint` owner PID/HWND and `(3180,1062)` was not a desktop
  corner. The machine artifact therefore proves a start outside the entire
  RuneLite window, not independent foreign-application identity; the live test
  is not repeated because this milestone permits exactly one.
- The ignored live bundle is
  `_run_proofs/cursor_policy/20260712_stationary_runelite_reacquire/`.
  SHA-256 values for `pre.json`, `live_runtime.json`, and `post.json` are
  respectively
  `63A937300774262E762F9A8C0D8CE68A98287E8D9309EC0599872E5BE2521695`,
  `5723331F2B1C479CC8948D00DE932C05E9675519ABDEDEA7906DB6369D1F96A8`,
  and `1E7F5A1038D6EED6D2934C91209A59C6FB1E839815B85FADF4CF7543D9F0B59E`.
- Every cursor and point-owner read independently establishes per-monitor-v2
  device pixels on the calling thread. A failed/ineffective context change or
  `GetCursorPos` failure blocks; the engine never substitutes `(0,0)` or a
  remembered command endpoint.
- Every pointer transaction consumes only historical released-button bits at a
  pre-serial baseline, then requires two quiet physical-button samples. New or
  held activity blocks again around motion and activation. After Arduino
  `MOUSE_UP`, a source-blind reader allows at most 500 ms for that button to
  reach two consecutive all-clear samples. Other-button or persistent activity
  blocks. The host reader sends no firmware command and does not renew the
  one-second watchdog; existing STATUS/rearm/revalidation gates still apply.
  Same-button human activity inside that window is inherently
  source-indistinguishable. The final clear proof prevents residual held or
  queued state from contaminating later input; it cannot undo a separate human
  activation. Final activation also requires the exact pinned root HWND/PID
  from `WindowFromPoint`.
- Object aim points lie inside the viewport and the first present RuneLite API
  shape in clickbox -> convex hull -> canvas tile order. `canvasLocation` alone
  is not activation proof, and a present stronger shape cannot fall through.
- `CoordinatedActionInterface` preserves exact post-move hover/menu/widget
  checks, context-row revalidation, and the verified bank-close Escape path.
- If wire evidence shows that a semantic widget click, key, direct object click,
  or final context row may have been written before an unsuccessful receipt,
  `activation_attempted` blocks runtime retry and verification credit. A
  preparatory right-click context opener alone is not the semantic action. The
  conservative boolean is retained in the terminal EngineFrame/application
  artifact alongside the unsuccessful receipt.
- The deterministic pointer policy produces only bounded relative motion with
  velocity/acceleration caps and target-aware braking. Ordinary action transit
  stays inside the canvas; the distinct movement-only reacquisition lane is
  bounded by the verified virtual desktop until canvas entry and by the canvas
  afterward.
- The policy retains one exact target, while the coordinator binds activation to
  the actual stable device-pixel endpoint. The coordinator executes at most 64
  exact command-waypoint plans/512 MOVE commands across the complete Arduino
  transaction, beginning an unknown axis with a unit probe. Every planner path
  requires an eight-device-pixel per-count envelope on all four sides, so a
  reversed or cross-axis response within that declared envelope remains inside
  verified bounds; observed transfer above four fails closed. Only a complete-
  plan endpoint inside the explicit activation region is eligible. Fresh
  validators receive that actual point and cursor stability is checked before
  and after validation. Gameplay activation stays within +/-3 pixels of the
  approved safe point; template-backed saved-session login uses only a tight
  cursor-safe footprint inside its freshly recognized prompt bounds.
- A monotonic clock starts before each serial MOVE, so ACK latency counts. If
  ordinary samples lack any commanded axis, the coordinator discards the old
  trajectory and uses at most ten fixed 20 ms no-input polls. Full cumulative
  effect must be observed by 200 ms and two later identical whole-cursor samples
  by 240 ms. Every extended sample rechecks focus, pinned point owner, bounds,
  direction, gain, and uncommanded axes; physical-button quiet and a final
  unchanged owned sample precede a fresh plan. No command, STATUS, rearm, or
  watchdog refresh is sent during settlement. Same-direction/in-gain buttonless
  human motion remains source-indistinguishable and subject to fresh semantic
  validation.
- Stable route-projection failure may use only the definition-owned camera lane:
  shortest fixed-point yaw direction, 250 ms holds, at most eight verified
  turns, and a typed camera-pose outcome. Missing evidence still waits and
  contradictory identity still blocks.
- A sent action's tick verification window begins at the final preactivation
  observation. Ordinary actions retain the eight-tick deadline; route movement
  has a separate 20-tick deadline. An intercepted walk target can use only one
  exact unique lower `Walk here`/`WALK` context row.
- A fresh target-lifecycle mismatch before activation is classified by the
  action layer as typed invalidation, not a failed post-action verification.
  Runtime may discard only one consecutive proposal when cleanup is fully safe
  and the ledger contains preactivation commands exclusively. The task then
  suppresses that exact tree key for one fresh selection; the next equivalent
  failure blocks.
- Physical cursor-state invalidation is a separate typed unsent lane. It may
  receive one fresh reobservation with no target suppression only when the
  receipt has the matching failure kind, preactivation-only ledger, and complete
  cleanup; repetition blocks. A completed connected reacquisition qualifies
  only with retained unchanged-geometry/no-activation evidence.
- Before pointer preflight, the host acquires the same cross-process port lease
  used by the later Arduino session without opening or arming hardware. It keeps
  that lease through any connected cursor movement, cleanup, and backend close.
  Contention leaves an empty closed ledger and cannot open serial, MOVE, activate
  input, use software cursor APIs, or mutate RuneLite.
- RuneLite's integer source-canvas menu point has a retained-layout-only
  four-device-pixel correlation bound to the settled Win32 cursor. Actual
  activation and both source/fresh canonical aim checks remain +/-3, exact menu
  identity is still required, and five pixels blocks.
- If normal login template work caps on a coherent loaded scene, one larger
  bounded scan checks only the two exact retained templates. It excludes the
  disconnect-dialog heuristic, cannot authorize input, and supports PASS only
  through absence plus two fresh increasing same-PID/session loaded ticks. Slow
  scans refresh the proof and enforce a two-second maximum age.
- Every connected transaction records a non-truncated command/ACK ledger and
  attempts `STOP_ALL`, `DISARM`, and wire `STATUS`. Success requires the final
  firmware report to prove disarmed with zero held keys/buttons and no missing,
  failed, or unresolved command evidence. Additive `cursorFeedback` retains
  wait/settled counts, maxima, and the last command/points/timings/outcome; every
  recorded wait must settle for input success. Additive `cursorReacquisition`
  retains PMv2 virtual/neutral bounds, before/after cursor, exact bound PID/root
  HWND and outer/client/canvas geometry, completion, unchanged geometry, and
  no-activation evidence.
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
- The overlay host is the actual top-level click-through tool window, and Tcl
  creation/teardown stays on its owning thread. Live passive inspection has
  matched route progress, target/candidate status, camera outcomes,
  verification, and `cleanup: safe` against the underlying EngineFrame.
- The sole snapshot endpoint now exposes bounded demonstration-only
  client/menu/click tails with one global Java-assigned sequence, an NPC-only
  actor census, and the existing collision window. Actor/collision provenance
  must match the atomic frame before Python can consider the Observation
  coherent.
- `run.cmd record-demo NAME` records no input. It requires a loaded scene and
  exact session/PID binding, rate-limits pointer evidence, records semantic
  clicks plus before/after observations, stops on source identity/sequence
  discontinuity, and limits screenshots to verified canvas regions outside a
  bank-PIN surface.
- Finalized demonstration artifacts contain JSONL, commit/dependency/schema and
  session provenance, semantic JSON/Markdown summaries, optional images, and
  SHA-256 plus byte-size evidence for the complete file set.
- `run.cmd inspect-demo PATH` verifies safe paths, symlinks, file set/limits,
  hashes, schemas, and recorder sequence before emitting any candidate. Valid
  candidates contain review-only world/action/plane facts plus known object IDs
  or NPC IDs correlated through census evidence. Scene/NPC/collision/menu caps
  remain explicit; walk/player/widget/incomplete menu evidence cannot become an
  entity candidate. Input coordinates are omitted, activation is never
  automatic, and invalid evidence emits no suggestions.
- Demonstration and screenshot modules import no runtime, task, safety, login,
  input coordinator, Arduino, or software input authority.
- `EngineApplication` now exposes the exact one-task/one-definition catalog,
  fresh profile schema, authoritative profile validation, and fresh
  task/runtime/control construction for each start.
- Monotonic run and capture IDs reject delayed commands. Automation and manual
  demonstration workers are serialized and mutually exclusive, including
  concurrent start races.
- Runtime pause is acknowledged only at a no-input boundary and discards an
  Observation held across the pause. Once an action is decided, safe stop waits
  for its Arduino transaction, cleanup receipt, bounded verification, and typed
  transition. Pause never extends the hard runtime bound.
- The facade returns the exact latest `EngineFrame`, runtime-owned immutable
  statistics, and owner-produced blockers. It does not select targets, run
  safety, verify, own Arduino, or infer diagnostic truth.
- `run.cmd app` provides catalog/profile inspection, validation, foreground
  dry-run, and explicit Arduino execute mode. `Ctrl+C` becomes cooperative safe
  stop; there is no daemon, IPC surface, or full GUI.
- The future GUI screen/restart contract is documented. The frozen
  `VisionEvidence` type is only a dependency-free non-authoritative seam; no
  vision model or runtime consumer was added.

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

The original 2026-07-10 baseline proof is stitched across bounded continuation
runs made while the route was being corrected. Its five-event terminal trace
proves only the last waypoint and terminal state. It is not an uninterrupted raw
replay, and the summaries omit full observations, menus, geometry, and command
receipts. The golden fixture records this caveat and hashes the key ignored
artifacts.

The separate retained 2026-07-11 proof is uninterrupted outcome evidence from
the pre-audit checkpoint. A same-PID/session pre-observation was coherent and
empty at the exact return-route anchor; the process completed the historical
return without credit, recorded exactly 28 log gains, bank activity, deposit to empty,
and a genuine `cycles 1/1` return to `west_trees`. The post-observation remained
fresh/coherent, empty, bank-closed, and warning-free. `execute.json` retains the
terminal frame and transaction `input-00000082`, not receipts/history for
actions 1-81, so it proves the uninterrupted cycle outcome and final cleanup but
is not a complete raw audit of every intermediate transaction.

The historical D031 cursor-window-handoff bundle is narrower host/input
evidence, not a completed-cycle claim and not proof of the replacement
stationary-window production policy. Its login conclusion is compositional
rather than one direct current-checkpoint execution.
`login_activation_footprint_current.json`
proves the displaced login handoff and all three prompt transactions at
`31e1391`. At
`ae8b9f8`, `login_after_cross_process_lease.json` records three more PASS prompt
transactions but ends BLOCKED when a subsequent read-only template scan hits
its bounded candidate cap; the following coherent loaded observation and
zero-click loaded confirmation establish the successful outcome. Despite their
filenames, `login_current_external_success.json` and
`login_visual_retry_success.json` have top-level status BLOCKED and remain
diagnostic failures, not success evidence. Content and status are authoritative
throughout this folder: `login_success.json` is also BLOCKED;
`login_complete.json` and `login_final.json` are ERROR; and
`execute_gameplay_quantization_retry.json` is a zero-byte interrupted output.
None is decisive success evidence.

For gameplay, `gameplay_external_actionable_current_lease_setup.json` proves
that RuneLite moved relative to an unchanged cursor without pointer input before
execution. The bounded run then relocated only RuneLite, reobserved, and grew
the inventory from 10 to 19 logs. Runtime output retains only its last execution
receipt, so the first safely-unsent handoff and nine sent interactions are
inferred from the setup, geometry relocation, action count, and inventory delta;
the first eight interactions advanced runtime, while the ninth has the retained
PASS input receipt and post-run inventory evidence but its typed verification
timed out. This proves displaced gameplay recovery at the retained layout, not
nine typed verification passes or a full bank-and-return cycle.
Windows does not identify whether physical pointer displacement came from a
particular human or device, so the no-input relative-displacement setup proves
the geometry condition and recovery behavior rather than source attribution.

## Operational oddities observed

- The user's apparent manual-cursor location loss reproduced as DPI
  virtualization: one untouched physical point read `(2006,1226)` in a
  non-aware process and `(3510,2145)` in per-monitor-v2 device pixels, exactly
  the 1.75 display scale. Per-thread sampling now removes that split.
- During one-time recovery of the pre-fix stranded cursor, the exact client
  window was temporarily placed over the unchanged cursor. A first restore call
  made before per-monitor-v2 awareness landed at `(2063,826)` instead of
  `(1179,472)`; it was immediately corrected after establishing PMv2. No
  software cursor input was used.
- The later recurring manual-takeover failure reproduced independently without
  user action at physical PMv2 cursor `(3446,1631)`. RuneLite PID `1968`/HWND
  `328854` was foreground, but the point was 25 pixels beyond outer
  `(1179,472,2243,1585)`, 37 beyond client
  `(1191,472,2219,1573)`, and owned by the visible ChatGPT root window. Both
  login attempts correctly sent no MOVE/click and ended with acknowledged
  `STOP_ALL`, `DISARM`, safe zero-held status, and zero unresolved commands.
  The defect was therefore policy coverage, not forgotten coordinates. The
  then-new pre-serial window handoff covered this exact geometry under the
  historical D031 policy; that window-translation policy is now retired.
- An earlier transaction received acknowledged MOVE commands but Windows still
  reported no X movement at the ordinary sample. Cursor, telemetry, foreground,
  and exact HWND ownership agreed, so manual interference was not supported.
  The firmware ACK proves command handling rather than OS application; the
  additional no-input poll fixed that boundary, and the final 82-action run did
  not repeat the failure.
- The pre-audit proof itself completed and the post-observation passed before
  RuneLite emitted repeated GPU `GL_OUT_OF_MEMORY`/`GL_INVALID_OPERATION`
  errors. About 47 seconds after terminal completion, the Gradle-wrapper JVM
  (PID `500`, not telemetry PID `11440`) failed a native allocation. The launch
  stack then ended; client PID `11440` and listener `8893` are absent. Crash and
  compiler-replay logs are retained with the proof. A non-fatal unrelated
  `NpcAggroAreaPlugin` null-player exception also appeared during startup; the
  execute stderr otherwise contains only the normal focus instruction wrapped
  by PowerShell as `NativeCommandError`.
- Subsequent live testing separated several bounded edge cases from manual
  interference: delayed observation of the coordinator's owned button release,
  cursor occlusion of login glyphs, one-frame prompt rendering, disconnect
  dialog variants, matcher watchdog cost, one-pixel AWT/native origin
  quantization, and integer source-canvas menu quantization. Each now has
  focused fail-closed coverage.
- The pre-`8f7c1b2` gameplay mismatch was stable: settled Win32
  `(1854,991)` versus fresh RuneLite menu `(1850,991)`, with eight identical
  no-input samples and exact `Chop down` / `Tree` identity. The four-pixel bound
  applies only to menu correlation; it does not widen activation authority.
- That four-pixel allowance is evidence-backed only for the retained
  fixed-client layout on the 175% display. It is not a general cross-layout
  calibration.
- An earlier post-fix long-run attempt reached a real "You were disconnected
  from the server" dialog. The cause is unproven; it prevented that run from
  producing a terminal gameplay receipt and is not evidence of an input-engine
  failure.
- The unattended `f3dce8d` run lasted from `05:01:03Z` to `05:12:32Z`, made 71
  action attempts and 1,939 observations, and reached the fresh outbound bank
  route at `south_corridor_bridge`, step `10/19`. Transaction 71 attempted the
  preparatory right-click context-menu opener: all 33 commands, including its
  single `MOUSE_DOWN`/`MOUSE_UP`, were acknowledged, but Windows did not settle
  the owned button inside the old 100 ms deadline. Whether the menu visibly
  opened was not reobserved before failure. The semantic `Walk here` row
  click was never sent. The acknowledged Escape cancellation and final `STOP_ALL`,
  `DISARM`, safe zero-held `STATUS`, zero unresolved commands, and closed
  ledger/backend all passed.
- The user was asleep during that unattended failure, so the evidence does not
  support manual interference as its cause. Windows button state remains
  source-blind, and the artifact does not retain raw `GetAsyncKeyState` samples
  or elapsed settle latency. It proves only that 100 ms was insufficient once;
  the later `07de1ef` run proves the 500 ms code was live-integrated and that the
  old terminal blocker did not recur, but no artifact measures a transition
  over 100 ms or proves that longer bound was exercised.
- The retained `07de1ef` login proof passed `loaded_scene_verified` in 27.109
  seconds with all three allowed clicks, full command/ACK evidence, and safe
  cleanup. Its SHA-256 is
  `BC00AACF2D5AC5A218B057439F57577C4FB3F43DB9BD612C99D6E856B93D32C2`.
- The unattended `07de1ef` gameplay run lasted 747.893 seconds, made 91 action
  attempts and 1,997 observations. The combined retained client/plugin
  chronology already showed the prior bank/deposit/return and a fresh full
  inventory; this run advanced that reconciled outbound state to step `16/19`
  at `bank_floor_south_2`. Transaction 91 had eight acknowledged MOVEs and no
  activation, then blocked because the final MOVE effect remained invisible to
  the old roughly 60 ms sample window. All 18 commands were terminal; final
  `STOP_ALL`, `DISARM`, disarmed zero-held STATUS, zero unresolved commands, and
  closed ledger/backend passed. Its SHA-256 is
  `61D7DFE5C7C168941BD0E827DF5307B1853F0BA8F7B22FE467D7711D9800AA99`.
  The artifact cannot distinguish a late Windows report from lost/no effect.
  The user was asleep, so this failure also does not support manual movement as
  its cause. A later PASS observation was console-only and is not claimed as a
  retained JSON artifact.
- Checkpoint `6eef48c` then produced direct live delayed-MOVE proof during a
  25.656-second saved-session login. All three allowed clicks passed. The final
  click retained one settled feedback wait: effect first/fully observed at
  78 ms, three extra no-input polls, stable completion at 125 ms, and no
  unresolved command. Every click ended with acknowledged `STOP_ALL`, `DISARM`,
  disarmed zero-held STATUS, and closed ledger/backend. The proof SHA-256 is
  `C28224D84623D70F8B60D49512355590CB45B4B12BCD0BF0A97BC8ED33743B37`.
- The first `6eef48c` gameplay continuation began from full inventory at
  outbound step `16/19`. It crossed the old terminal boundary, completed the
  restart recovery, harvested and banked a fresh inventory, deposited to empty,
  and reached return step `9/15` before the configured 100-action cap. Its
  2,137 observations over 807.586603 seconds ended with a successful final
  action receipt and authoritative safe cleanup; this was a bounded limit, not
  a cursor failure. The proof SHA-256 is
  `420FC2929A713EC8EF787492209073B02DE93F44B78D70898B2EA9D56D8DDBA5`.
- From that exact empty-inventory return step, one final bounded continuation
  completed a new current-checkpoint cycle in 89 actions, 2,214 observations,
  and 810.725564 seconds. Terminal state was `COMPLETE`, `cycles 1/1`, reason
  `arrived at route step west_trees`, with no blocker and fully safe final
  receipt/runtime cleanup. Its SHA-256 is
  `B91B1025CD9343991A46ABB55045CA63DA5DB978748466338E7B264CAF83130D`.
  A retained post-run observation then passed fresh/coherent/loaded with empty
  inventory, no warnings/missing capabilities, and SHA-256
  `2CF4755C91074505A683919066FAA14B6B06BEBED1E6AED54ABD262DE59A6F6C`.
  No bot process or Arduino lease remained. The user was asleep throughout, so
  this direct regression required no manual mouse positioning or assistance.
  All `6eef48c` artifacts are retained under
  `_run_proofs/final_regression/20260711_cursor_handoff_complete_cycle/`.
- The final demonstration login first rejected the physical cursor at exact
  desktop x=0 before opening COM or sending a command. After the user moved it
  into RuneLite, the next Arduino-only attempt freshly sampled and adopted that
  position, completed login, and passed loaded-scene plus authoritative cleanup
  checks. This directly proves manual cursor repositioning is current observed
  state rather than hidden coordinate history.
- The accepted read-only demonstration at clean commit `000a886` recorded a
  semantic `Walk here` click at source tick 1060 and the resulting player-world
  change from `(3197,3238,0)` to `(3196,3237,0)` at tick 1064. Its 45-second
  artifact contains 3,897 events and two bounded screenshots, is `valid: true`,
  and has no errors or ambiguities. `run.cmd inspect-demo` independently
  returned `VERIFIED_WITH_GAPS`; its candidates remain review-only and
  `never_automatic`.
- Post-demo shutdown first requested graceful close of the exact bound RuneLite
  PID. When it did not exit within 15 seconds, only that PID was force-stopped.
  The bound PID, listeners 8893/8890, OSRS Python workers, repository Java
  processes, and Arduino lease were then all confirmed absent.
- The current client log also contains unrelated/nonfatal startup and plugin
  noise: a reflective-access exception, repeated NpcAggroArea null-player
  subscriber exceptions during login-state transitions, World Hopper ping/DNS
  failures, an LWJGL JNI-version warning, and a WDDM performance notification.
  This proof bundle contains no new `GL_OUT_OF_MEMORY`, `GL_INVALID_OPERATION`,
  or native-allocation failure.

## Validation

- Phase 0 baseline Python suite: 116 passed.
- Phase 0 forced fresh Java suite: 22 passed, 0 failed, 0 errors, 0 skipped.
- Golden replay: 28 chop actions, 19-step outbound route, bank
  open/deposit/close, 15-step return route, one completed cycle.
- Phase 1 documentation gate repeated the replay, all 116 Python tests, and a
  forced fresh 22-test Java run successfully.
- Phase 2 gate: golden replay 2 passed; 123 Python tests passed; forced fresh
  Java suite 39 passed across 6 suites with zero failures/errors/skips.
- Phase 3 gate: golden replay 2 passed; 136 Python tests passed; forced fresh
  Java suite 39 passed across 6 suites with zero failures/errors/skips.
- Phase 4 gate: golden replay 2 passed; 169 Python tests passed; forced fresh
  Java suite 42 passed across 6 suites with zero failures/errors/skips.
- Phase 5 gate: golden replay 2 passed; 220 Python tests passed; forced fresh
  Java suite 42 passed across 6 suites with zero failures/errors/skips.
- Phase 6 gate: golden replay 2 passed; 241 Python tests passed; forced fresh
  Java suite 42 passed across 6 suites with zero failures/errors/skips.
- Phase 7 gate: golden replay 2 passed; 267 Python tests passed; forced fresh
  Java suite 51 passed across 6 suites with zero failures/errors/skips.
- Phase 8 gate: golden replay 2 passed; 301 Python tests passed; forced fresh
  Java suite 51 passed across 6 suites with zero failures/errors/skips; facade
  catalog, profile-schema, and default-profile validation commands succeeded.
- Final-regression device-pixel hardening gate: golden replay 2 passed; 311
  Python tests passed; forced fresh Java suite 55 passed across 6 suites with
  zero failures/errors/skips; an actual Windows subprocess verified exact
  per-monitor-v2 awareness.
- Pointer-arrival hardening gate: 323 Python tests passed; golden replay 2
  passed; and a forced fresh Java run executed 55 tests across 6 suites with
  zero failures, errors, or skips. Coverage reproduces long movement on the
  observed 175% HID/device-pixel lattice, records every point inside bounds,
  exercises the 400% supported ceiling plus four-sided insufficient headroom,
  unsupported transfer, and transaction-wide context-row caps, and proves
  cursor drift or bounded-plan exhaustion produces no click and still completes
  safe cleanup.
- Forced closeout gate through `aaa0290`: 457 Python tests passed, golden replay
  remains 2/2, `python -m compileall` and `git diff --check` pass, and the fresh
  Java rerun executed 66 tests across 6 suites with zero failures, errors, or
  skips. Those counts come from the current externally configured Gradle build
  directory; checkout-local `build/` reports were stale and are not evidence.
- Acceptance-audit gate at `f2007eb`: 461 Python tests passed; golden replay
  remains 2/2; `python -m compileall`, `git diff --check`, catalog,
  profile-schema, and profile validation pass; and a forced Java rerun executed
  76 tests across 8 suites with zero failures, errors, or skips. The current
  external Gradle reports were counted directly. The deterministic Java fixture
  SHA-256 is
  `80AF03C08681D242033D5ED4FBFF56AF6069263C40E0D290CABF5B7DDA549081`.
- Historical external-cursor window-handoff gate: 508 Python tests passed and
  the focused Arduino/coordinator/login/runtime set passed 216/216; golden replay remains
  2/2; `python -m compileall`, `git diff --check`, catalog, profile-schema, and
  default-profile validation pass. A forced Java rerun executed 76 tests across
  8 suites with zero failures, errors, or skips. Read-only live PMv2 proof on
  PID `1968`/HWND `328854` matched outer
  `(1179,472,2243,1585)`, client `(1191,472,2219,1573)`, and contained canvas
  `(1199,520,2151,1519)`. At that checkpoint, physical login/gameplay execution
  was the next gate.
- Historical `ae8b9f8` gate: 527 Python tests passed; golden replay passed 2/2;
  `python -m compileall`, `git diff --check`, `run.cmd test`, catalog,
  profile-schema, and default-profile validation passed. A forced Java rerun
  executed 76 tests across 8 suites with zero failures, errors, or skips; counts
  came from the configured external Gradle build directory. Focused tests also
  proved that historical cross-process lease contention could not reach window
  mutation, serial open, or input.
- `07de1ef` owned-transition offline gate: 544 Python tests passed, including a
  focused 148-test action/runtime/EngineFrame/application/Arduino suite; golden
  replay passed 2/2; `python -m compileall`, `git diff --check`, `run.cmd test`,
  catalog, profile-schema, and default-profile validation passed. A forced Java
  rerun executed 76 tests across 8 suites with zero failures, errors, or skips
  from the configured external Gradle build directory. Tests cover >100 ms
  delayed owned state, 500 ms timeout, late other-button rejection, clear-streak
  reset, source-blind same-button limits, and written/ambiguous/rejected/failed
  widget and key activation evidence.
- D035 delayed-MOVE offline gate: 560 Python tests and the focused 154-test
  cross-boundary suite pass; coordinator-only coverage passes 107 tests.
  `python -m compileall` and
  `git diff --check`, `run.cmd test`, golden replay 2/2, catalog,
  profile-schema, and default-profile validation pass. A forced noncached Java
  rerun executed 76 tests across 8 suites with zero failures, errors, or skips
  from `C:\Users\badto\AppData\Local\Temp\osrs-telemetry-build-a231df6a`.
  New cases cover normal and login-reacquisition slow ACKs, effect first
  observed after 200 ms, instability at 240 ms, staggered axes, point-owner
  loss, physical-button activity, final drift, clock failure, safe BLOCKED
  cleanup classification, and nonempty EngineFrame serialization.
- Historical D031 live handoff evidence: displaced saved-session login and
  displaced gameplay recovery subcriteria are PASS at the retained layout.
  Login is the stitched `31e1391` direct handoff plus then-current prompt/loaded and lease-test
  chain described above, not one direct `ae8b9f8` run. The bounded gameplay run
  executed 10 action attempts, increased inventory from 10 to 19 logs, and
  retained a final PASS transaction with acknowledged `STOP_ALL`, `DISARM`, safe
  firmware status, zero unresolved/failed commands, and closed ledger/backend.
  Its top-level BLOCKED status records only the deliberately short runtime
  expiring during the tenth attempt's verification; a complete cycle was not
  evaluated.
- Earlier live attempt at `f3dce8d`: the 11.5-minute run reached outbound step
  `10/19` and then ended BLOCKED on the old 100 ms owned-button settlement
  boundary before the semantic context-row click. Cleanup was fully safe. Its
  SHA-256 is
  `8F1DB084865262C58B322F6B52D0C439BE59611DAD7A0C5FFA70F40400242960`.
- Prior live attempt at `07de1ef`: the 12.5-minute run reached outbound step
  `16/19` and then ended BLOCKED before activation on an acknowledged MOVE whose
  Windows effect was not observed inside the old roughly 60 ms samples. Cleanup
  was fully safe. Its SHA-256 is
  `61D7DFE5C7C168941BD0E827DF5307B1853F0BA8F7B22FE467D7711D9800AA99`.
- Current `6eef48c` live gate: saved-session login passed in 25.656 seconds and
  directly settled one delayed cursor effect first/fully seen at 78 ms, with
  three extra polls and stable completion at 125 ms. A bounded 100-action
  recovery run then crossed the former step-16 blocker and progressed through a
  fresh harvest/bank/deposit to empty-inventory return step `9/15`, ending only
  on its configured action limit with safe cleanup. From that exact state, the
  final continuation completed `cycles 1/1` at `west_trees` in 89 actions,
  2,214 observations, and 810.725564 seconds. Terminal and post-run cleanup were
  safe; the retained post-observation was loaded/fresh/coherent with no warnings
  or missing capabilities. No Python bot or Arduino lease remained.
- Final demonstration hardening gate at `000a886`: all 570 Python tests passed,
  including the focused 30-test recorder/inspector set; golden replay passed
  2/2; `python -m compileall`, `run.cmd test`, and `git diff --check` passed.
  The accepted artifact is
  `demo_runs/20260712T170027843742Z_final-manual-walk-000a886-final/`. Its
  `events.jsonl`, `manifest.json`, `summary.json`, and `timeline.md` SHA-256
  values are respectively
  `582488FD366CC7F08C9D848890B181C0CD9B130599E9F57325D779A88916C26E`,
  `CC28419C61E60A87FD8DDCB39A1301230CD8228D464CD4FB1B279E5871488EC4`,
  `63F49A69FB4C8073A3ED2C347B6B4C6D733123ACAB0AC1EF38FB58544A5B555C`,
  and `76FA67BCD398388186D0DAD69DE79BA26CAADD0C30E9A843288187DF68A8F1C6`.
  The retained public-inspector proof SHA-256 is
  `CBF2FD726E7FE1B5CAF770E9686CC4EA5063C76F0D07CA2A6B8264583965AA16`.
- Stationary-RuneLite cursor-reacquisition gate: the focused input/login/runtime
  set passed 359/359 and the complete Python suite passed 576/576; golden replay
  remained 2/2; `python -m compileall` and `git diff --check` passed. A forced
  noncached Java/Gradle rerun executed 76 tests across 8 suites with zero
  failures, errors, or skips. The exactly-one live test and its cleanup proof
  are recorded above; no full woodcut/bank cycle or automated login attempt was
  run for this milestone.
- Final read-only audit after that single live test found that gameplay's
  post-reacquisition gate required a newer exact-geometry tick but did not also
  enforce `fresh`, `cache_wall_clock_fresh`, and `source_coherent` before task
  recognition. The host-only gate was tightened and a three-shape focused test
  plus the full suites above passed. The live movement is not repeated under
  the exactly-one cap; its post-proof independently records a qualifying fresh,
  wall-clock-fresh, coherent same-session frame at tick 309.
- Pre-audit live gate: PID `11440`/session
  `plugin-11440-1783810438162` loaded coherently with no warnings, then completed
  in 698.2 seconds with 1,994 observations and 82 actions. Terminal state was
  `COMPLETE`, reason `arrived at route step west_trees`, progress `cycles 1/1`,
  no blocker, and fully safe final receipt/runtime cleanup. The ignored proof is
  `_run_proofs/final_regression/20260711_cursor_reacquire_complete_cycle/`.
  It predates `f2007eb` and is not current-checkpoint sensor proof.
- The bounded Phase 2 live observation served response v2/frame v1 at the
  RuneLite login screen. Only baseline was available; inventory, activity,
  bank UI, and dialogue were explicitly unavailable. `observe` returned
  `loadedScene=false`, and the launched client/port were closed afterward.
- `git diff --check`: passed.

## Remaining limitations and next work

- The D035 delayed-MOVE path and a complete default cycle are now directly live-
  proven. The separate 500 ms owned-button code is live-integrated and its old
  terminal blocker did not recur, but no artifact measures a button transition
  over 100 ms. Windows same-button state remains source-indistinguishable.
- Cross-monitor mixed-DPI virtual-desktop cursor ingress is deterministically
  covered and safely fail-closed but has not been physically exercised on this
  one-monitor machine. The retired asynchronous window-position limitation no
  longer applies to production cursor recovery because that path cannot mutate
  RuneLite geometry.
- The single live artifact directly proves stationary-window Arduino cursor
  ingress and cleanup, but predates the final host-only freshness/coherence
  replan guard added by audit. That guard is deterministically covered rather
  than live-repeated; the retained post-frame proves the required fresh state
  existed later, not that the final guard itself was exercised live.
- The four-device-pixel menu correlation bound is proven only on the retained
  fixed-client 175% layout. Arbitrary layouts require fresh measurement; the
  activation, canonical-aim, target-bound, and exact-menu gates remain strict.
- The final user-performed manual demonstration, `inspect-demo` verification,
  and post-demo endpoint/client cleanup are **PASS**. No live client was left
  available after acceptance.
- The pre-audit proof package retains only the terminal gameplay receipt rather than
  all 82 transaction receipts. The runtime result and plugin timeline strongly
  prove the cycle outcome, but the artifact is not a full action-by-action audit.
- EngineFrame/application status intentionally retains only the latest execution
  receipt. Each delayed-feedback transaction is self-contained, but a permitted
  fresh retry can replace that earlier receipt in terminal run output; bounded
  prior-attempt history remains a future observability decision.
- Current `input_transaction_receipt.v1` JSON includes additive
  `cursorFeedback` and movement-only `cursorReacquisition`; older v1 proof
  artifacts may omit either field.
- There is intentionally no external profile loader, second definition, or
  generic navigation/transition framework.
- The passive overlay has been compared against fresh live EngineFrames; a final
  terminal-state view is desirable but is not an architecture blocker.
- The RuneLite endpoint does not expose global raw mouse-button or keyboard
  transitions. Demonstration manifests declare those gaps and retain semantic
  `MenuOptionClicked` evidence instead.
- A client-thread world-model query can occasionally land one sensor tick after
  the endpoint's immutable frame. The endpoint still rejects that mismatched
  model. The recorder tolerates only the exact, bounded absence shape while
  retaining independent hot-tail continuity; identity, session, tick, payload,
  warning, or capability contradictions remain terminal.
- Sensor, task, definition, profile, runtime-configuration, input, diagnostic,
  demonstration, and frontend composition contracts are implemented. The final
  manual demo/inspection is accepted, and no client, endpoint, worker, Java
  process, or Arduino lease remained after shutdown.

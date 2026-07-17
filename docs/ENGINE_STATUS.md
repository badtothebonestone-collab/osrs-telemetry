# Engine Status

## Current milestone

### Task-agnostic gathering platform

**The task-platform implementation and deterministic final gate pass. Current-
build loaded-scene and gameplay proof remains unavailable, so this is not a
live mining-route or current woodcut-cycle claim.**

The application now exposes one `gather_bank` task with two immutable built-in
definitions: the proven `lumbridge_west_trees_v1` regression baseline and
`lumbridge_swamp_copper_v1`. Both construct the same `GatherBankTask` and pass
through the existing `EngineApplication -> TaskRuntime -> SafetyGate ->
InputCoordinator -> Arduino -> Verifier -> EngineFrame` spine. The legacy
`WoodcutBankTask` name is compatibility-only; no mining-specific runtime,
safety gate, input owner, verifier, or observation path was added.

Definitions now declare task type and exact required capabilities plus typed
resource/area/bank/inventory/equipment/target/lifecycle/recovery/navigation
policy. Binding rejects a definition when those requirements exceed the one
runtime. The current gathering runtime supports game-object interaction, fixed
routes and object transitions, deposit-all, target continuity, camera
acquisition, equipment observation, scheduled start, composable stop
conditions, and restart reconciliation. Fallback banks, withdrawal/resupply,
automatic equipment management, and production NPC interaction geometry remain
explicitly unsupported.

Equipment is captured in the existing tick-aligned inventory sensor fact and
published as immutable `EquipmentObservation`. The copper definition requires
known evidence containing an allowed pickaxe. Unknown equipment waits; known
missing equipment blocks; the task neither equips nor withdraws a pickaxe.
Legacy fixtures that omit equipment remain readable as unknown and cannot
authorize mining.

Profiles now support a scheduled UTC start and OR-composed cycle, gathered-item,
inventories-banked, inventory-full, duration, and absolute-time stops. At least
one stop condition is required. A profile can request a lower action cap and
fresh restart reconciliation but cannot exceed definition/runtime ceilings or
weaken safety. Typed item outcomes carry quantity deltas; task snapshots expose
bounded lifecycle, metrics, and reconciliation evidence. Resource no-yield,
bank-unavailable, and target-continuity recovery budgets are task-owned.

The three adjacent hardening improvements are explicit:

- `Task.apply_verification()` returns typed `VerificationDisposition`, so the
  runtime no longer selects recovery from gathering semantics or verifier
  failure kinds;
- current verifier item outcomes carry exact positive quantity deltas into task
  counters and EngineFrame metrics instead of normal-path `+1` assumptions; and
- missing exact bank targets receive a bounded definition-owned wait whose
  counter resets on exact bank recovery/open state and blocks when exhausted,
  with no fallback guess.

Final focused/adversarial results and measured deltas are recorded in the
Validation section.

The strict `osrs_bot.task_definition.v1` authoring boundary provides `validate`,
`inspect`, `explain`, and deliberately non-runnable `scaffold` commands. The two
runnable examples mirror the built-ins. The deliberate NPC-fishing example
proves that naming unsupported `npc_interaction_geometry` does not make it
runnable. `--definition-file` can bind an explicitly supplied runnable
gathering definition to profile-schema, profile-validation, dry-run, or opt-in
execute through the same `GatherBankTask`. It is not installed or advertised in
the built-in GUI/catalog; validation alone sends no input, and execute retains
all normal Arduino/safety/verification/cleanup gates.

The copper object IDs `10943`/`11161`, ore `436`, supported pickaxe IDs, and
Lumbridge Swamp East anchor `(3226,3146,0)` are pinned to upstream RuneLite
source hashes in definition provenance. The authored swamp surface route reuses
the established castle transitions but has **not** been live-replayed in this
checkout.

The clean consolidation base was independently revalidated before this work:
984/984 Python tests, 127/127 forced-fresh Java tests, replay 7/7, compilation
79/79, firmware protocol 8/8, focused input gates, and bounded synthetic soak
passed. Those are baseline results, not the new platform's final gate.

At milestone start no RuneLite/Java/Python client and no `8890`/`8893` listener
was present. A bounded read-only observation failed with connection refused
before any input. Arduino Leonardo ports enumerated but were not opened. No
current mining route, woodcut cycle, Arduino gameplay action, firmware upload,
or flash is claimed. The final validation section must be updated only from
actual retained outputs.

See [`TASK_PLATFORM.md`](TASK_PLATFORM.md) and
[`DEFINITIONS_AND_PROFILES.md`](DEFINITIONS_AND_PROFILES.md).

### Previous telemetry pipeline production-soak continuation

**The pressure-path reliability hardening and repeatable soak harness are
integrated on the repository-consolidation branch. A current-build live
RuneLite after sample is unavailable because the production telemetry endpoint
on `8893` was not running; no production input or firmware change occurred.**

This continuation separates the 10,000-identity raw census safety ceiling from
the 64-row returned/definition-enrichment ceiling, applies every request's
projection budget before either cached or new projections enter the response,
and revokes exact-priority absence when a present matching raw row was omitted
by the response cap. Malformed endpoint JSON returns `400` without retaining
endpoint admission. Planned host fetches bind the returned center, anchor
source, radius, purpose, and exact priority sets to the request, so a concurrent
or stale response cannot lend completeness or absence authority to another
plan.

Typed retryable `503 endpoint_busy` now publishes neutral
`ENDPOINT_BACKPRESSURE` and `endpoint_backpressure_wait` evidence, does not
spend the ordinary observation-error budget, and is bounded to eight
consecutive retries. The production live-evidence subscriber only enqueues to a
bounded 256-frame queue; one daemon writer performs JSON and filesystem work.
Its receipt reports queue capacity/high-water, dropped frames, bounded recorder
errors, and writer shutdown state. Repeatable synthetic coverage is available
as `python -m osrs_bot.telemetry_soak` and `run.cmd telemetry-soak`, emitting the
stable `telemetry_pipeline_soak.v1` evidence family.

The selected writable integration checkout reconstructs the complete dirty
authoritative baseline and then applies the independently verified 27-file
telemetry delta. The original authoritative and isolated checkouts remain
preserved in the external consolidation recovery record; no telemetry change is
stranded outside the publication branch.

The fresh repository-consolidation gate is **PASS**: 984/984 Python tests,
127/127 forced-fresh Java tests across 12 suites with 4/4 tasks executed,
retained replay 7/7, and 79/79 tracked Python files compiled. The repeatable
synthetic soak ran 5,000 serial samples plus 1,000 calls at each of 1/2/4/8
concurrent pollers, produced one result signature at every level, and returned
to one thread.

Python 3.12 creates mode-`0700` temporary directories that the restricted
Windows sandbox token cannot re-enter. The exact full suite therefore used a
test-only `os.mkdir` mode-`0777` wrapper; product code, test selection, and
assertions were unchanged. The stock failure occurred at filesystem setup or
cleanup before affected assertions, not in engine behavior.

The fresh serial soak grew RSS by 2,797,568 bytes. At eight pollers parse
p50/p95/p99/maximum was 0.1444/0.2799/0.7982/53.2393 ms. Fresh 1,001-row target
classification p50/p95/p99/maximum was 0.0768/0.1647/0.2469/0.7983 ms with 33
identity evaluations, one ranked candidate, and 32 bounded rejection records.
EngineFrame publication p50/p95/p99/maximum was
0.0030/0.0032/0.0059/0.2113 ms.

The fresh dense Java benchmark was 7.511/13.368/13.803/13.803 ms for refresh
and 1.194/3.392/3.557/3.563 ms for exact-source reuse, with 4,225 scanned and
discovered identities but only 64 enriched, projected, and returned rows. The
retained handoff comparison remains explicit: exact-source p95 regressed
83.89%, exact-source maximum regressed 9.39%, and dense refresh maximum
regressed 14.82%. The milestone benefit is bounded correctness, not a blanket
latency improvement.

### Previous telemetry, observation, and decision-pipeline reliability gate

**The bounded telemetry/observation/decision implementation, focused tests,
complete regression, retained replay, and forced-fresh Java gate are PASS.
Current-build loaded-scene timing remains a documented live gap because neither
local telemetry listener was available.**

The canonical scene request is now player-centered or explicitly anchored.
Java performs a bounded identity census before definition/action enrichment and
projection, reuses exact-source raw work, and admits client-thread work through
one active plus one newest pending request. The endpoint is bounded and returns
retryable backpressure instead of accumulating snapshots. Python performs a
bounded read/parse, quarantines contradictory whole-row duplicates, builds one
immutable exact-key/object-ID index, preserves typed completeness and pipeline
evidence through EngineFrame, and supplies phase-specific task query budgets.
Target selection is row-order independent, rejection evidence is bounded, and
an exact lock cannot be declared depleted or switched merely because an
incomplete/capped frame omitted it. SafetyGate still fails closed on explicit
incomplete or contradictory authoritative target evidence; absent/legacy census
authority is readable but cannot activate an object or prove absence. If a
legacy client drops a planned anchor, exact key, or budget, runtime revokes that
fetch's coverage and absence authority.

The forced-fresh 4,225-object synthetic Java benchmark scanned/discovered 4,225
and enriched/projected/returned 64. Fresh p50/p95/maximum was
7.520/12.327/12.419 ms, exact-source hits were 0.715/1.527/4.815 ms, and payload
bytes were 107,896/107,900/107,902. Python 64-row parsing improved from
0.973/1.955/5.386 ms to 0.791/1.059/2.202 ms; immutable exact-key lookup improved
from 1.031 to 0.077 microseconds (13.4x); and structurally oversized 1,000-row
input is rejected in 0.049/0.104/0.213 ms instead of accepted in
10.611/15.232/18.105 ms. The 1,001-row target benchmark improved from
0.4388/0.6717/2.2055 ms to 0.0669/0.1557/0.4046 ms.

Final validation is **PASS**: `run.cmd test` completed 973/973 Python tests and
its normal Java gate; forced fresh Java `--rerun-tasks` completed 124/124 tests
across 12 suites with all four Gradle tasks executed; retained replay completed
7/7; and compile, input-boundary, and retained Java snapshot-fixture checks
passed. A bounded live observation was attempted only after these deterministic
gates, but the production `8893` endpoint was not listening and the request
failed with connection refused. It sent no input, created no operator-versus-
engine action
evidence, and required no input cleanup. No current-build live after timing is
claimed. Firmware was not flashed. Detailed contracts and limitations are in
[`TELEMETRY_PIPELINE.md`](TELEMETRY_PIPELINE.md).

### Concurrent movement, camera, targeting, pointer, and timing work

**Movement, camera, targeting, pointer, and timing quality are being upgraded
inside the existing engine contracts. The target-locked coarse-to-fine camera
offline gate and the pointer-containment/recovery deterministic and loaded-scene
Arduino-only gates are PASS. Camera production evidence is VERIFIED_WITH_GAP
because a server disconnect split acquisition and interaction across runs.**

The current worktree adds classified definition route points, polyline progress
and farthest-supported lookahead, collision/scene shortcut evidence, proactive
yaw/pitch framing, authoritative polygon aim candidates, and run-seeded curved
pointer/timing variation. `EngineFrame`, the passive overlay, and the GUI are
being extended as views of the owner-produced route, framing, geometry,
candidate, pointer, and timing decisions; they do not select or authorize them.
Focused and full regressions now cover the camera and pointer milestones. No
current-milestone repeated Tree interaction, complete cycle, uninterrupted
camera-acquisition-to-interaction run, or final commit claim is made here yet.
The retained live claims are the outside-viewport pointer recovery and fresh
route interaction plus the split-run camera evidence described below.

### Previous operator GUI milestone

**The first full operator GUI is implemented and live-proven over the prior
engine contracts.**

`run.cmd gui` launches one Tkinter/ttk desktop application with Run, Live
Status, Demonstrations, and Diagnostics tabs. Its controller calls only the
high-level `EngineApplication` facade and renders the latest `EngineFrame`; it
does not select targets, evaluate safety, verify actions, open Arduino, or send
raw input.

The current GUI lifecycle contract adds a presentation-only state over those
owners: CONNECTING, READY, OBSERVING, RUNNING, PAUSED, COMPLETE, BLOCKED,
SAFE_STOPPED, DISCONNECTED, STALE, or ERROR. Current live, Last known, and
terminal evidence are separate. Source age uses the Observation's existing
`maxSourceAgeMillis`; PID/session or run changes invalidate the old frame;
Start Live requires a fresh coherent loaded current identity; and an active
runtime blocks rather than continuing under a different PID/session.

The 2026-07-12 lifecycle-truth acceptance is **PASS** for stale display,
endpoint/process disconnect, historical labeling, disabled Start Live,
text-only overlay clearing, new PID/session reconnect, and GUI restart without
restored geometry. The production-action recheck is **NOT YET EVALUATED**:
the world disconnected again after the second bounded saved-session recovery,
before a new live run crossed its final coherent-state/handoff gate. No third
recovery was attempted. Evidence is under
`_run_proofs/gui_state_lifecycle/20260712T214105Z/`.
The final deterministic gate passed 657 Python tests, golden replay 2/2, 71
Java tests across 8 suites, the 7-test input boundary, compileall, and diff
checks.

The earlier 2026-07-12 base-GUI acceptance at the retained fixed-client 175%
layout is
**PASS**. Observe Only displayed a fresh loaded scene, player/inventory facts,
and Tree `1276` without opening an input session. The bounded Start Live test
used COM6 through the production `InputCoordinator`, counted two actions,
retained a PASS receipt and typed `item_quantity_increased` outcome, acknowledged
Pause/Resume, and ended `SAFE_STOPPED`. Final cleanup proved `STOP_ALL`,
`DISARM`, safe zero-held `STATUS`, zero unresolved commands, and closed
ledger/backend.

Computer Use supplied only operator setup and GUI interaction. Those manual
login, positioning, GUI-button, and demonstration actions are explicitly not
production-engine evidence. The ignored acceptance bundle is
`_run_proofs/gui_acceptance/20260712T195506Z/`.

Frozen baseline: commit `beb9cbb`, tag
`baseline-proven-woodcut-bank-return-2026-07-10`.

Regression command:

```powershell
.\run.cmd replay
```

## Proven baseline and current-build implementation

Historical proof statements below retain their original evidence boundary.
Task-platform bullets describe current code until the new final gate is recorded;
they are not live mining or current-cycle claims.

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
- `GatherBankTask` is the one implemented gather/bank/return FSM. Woodcut and
  copper mining select different immutable definitions through the same task
  contract and runtime.
- The built-in registry contains `LUMBRIDGE_WEST_TREES_V1` and
  `LUMBRIDGE_SWAMP_COPPER_V1`. Exact IDs, coordinates, route facts, deadlines,
  predicates, capabilities, equipment rules, and provenance live in the
  selected definition.
- The copper definition requires authoritative equipment evidence containing
  one permitted pickaxe. Equipment is a core fact on the same Observation; no
  auto-equip, withdrawal, resupply, or inventory fallback is implemented.
- Endpoint/Arduino/polling and runtime limits live separately in immutable,
  finite, engine-capped `RuntimeConfig`. The supported default is 100 actions
  under an unchanged hard maximum of 500. Definitions and profiles can lower
  the effective action/runtime budget but cannot enlarge operator ceilings;
  the observation cap remains operator-owned.
- The shared model has no log ID or woodcut phase. Task-specific item,
  interface, plane, and dialogue requirements are immutable action/spec
  constraints, while SafetyGate invariants remain non-overridable.
- Verification passes carry one typed `Outcome`; diagnostic reason strings do
  not select state transitions. A non-woodcut waypoint fake passes through the
  real runtime, SafetyGate, and Verifier without engine changes.
- Exact Tree `1276`, fixed outbound/return route steps, both upward stairs,
  exact bank booth, deposit-all-logs, verified Escape close, and terminal
  `COMPLETE` are represented in the committed golden replay.
- Copper Rocks `10943`/`11161`, ore `436`, the upstream mine anchor, equipment
  gate, and authored route are represented in the new definition and synthetic
  task-platform coverage. The swamp surface route has no live proof yet.
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
  shape in clickbox -> convex hull -> canvas tile order. The engine insets the
  authoritative polygon, excludes unusable regions, scores bounded interior
  candidates, and records the selected candidate and seed. `canvasLocation`
  alone is not activation proof, and a present stronger shape cannot fall
  through. Fresh exact hover/menu evidence remains the final veto.
- `CoordinatedActionInterface` preserves exact post-move hover/menu/widget
  checks, context-row revalidation, and the verified bank-close Escape path.
- If wire evidence shows that a semantic widget click, key, direct object click,
  or final context row may have been written before an unsuccessful receipt,
  `activation_attempted` blocks runtime retry and verification credit. A
  preparatory right-click context opener alone is not the semantic action. The
  conservative boolean is retained in the terminal EngineFrame/application
  artifact alongside the unsuccessful receipt.
- The seed-reproducible pointer policy produces only bounded relative motion
  with varied curved paths, velocity/acceleration caps, and distance/target-
  size-aware braking. Selected control points, duration, timing, seed, decision
  context, endpoint correction, and settled point remain diagnostic evidence.
  Ordinary action transit, every planned/feedback/correction point, settlement,
  context row, and activation stay inside the fixed 16-device-pixel inset of the
  authoritative viewport. The distinct geometry-only movement-only
  reacquisition lane is bounded by the verified virtual desktop until canvas
  entry and by the neutral padded viewport region afterward.
- The policy retains one exact target, while the coordinator binds activation to
  the actual stable device-pixel endpoint. Ordinary gameplay executes one
  initial trajectory plus at most two feedback-correction replans; cursor
  reacquisition retains a separate bounded movement allowance. Both remain
  under the 512-MOVE transaction cap, beginning an unknown axis with a unit
  probe. Every planner path
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
- Route and object framing use only the definition/task-owned camera lane. It
  distinguishes barely visible from usable/well framed, biases usable screen
  area toward travel, uses a general yaw/screen deadband, and chooses bounded
  correction-sensitive yaw or supported pitch holds. Route decisions include a
  fresh projection envelope and a bounded leading-edge allowance calibrated from
  the user's manual long-walk evidence; object framing remains stricter. Exact
  Trees, bank booths, and transition objects may now enter camera acquisition
  while off-screen, including a bounded seeded same-tile search when world
  bearing is unavailable, but activation still requires fresh authoritative
  geometry and exact hover/menu proof. Every change requires a typed camera-pose
  outcome and fresh replacement geometry; the outcome retains actual yaw/pitch
  deltas and changed frame IDs. Missing evidence still waits and contradictory
  identity still blocks.
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
- Physical cursor-state invalidation is a separate typed unsent lane. Only a
  first unexpected direction, unsupported gain, cross-axis response, padded-
  viewport exit, or point-owner mismatch may trigger the one geometry-only
  movement recovery. The original target/intent is discarded; one strictly
  newer fresh/coherent observation must rerun recognition and SafetyGate before
  one pointer retry. Changed PID/HWND or geometry, physical input, incomplete
  cleanup, a non-pointer/unsent retry, or repetition blocks. A completed
  connected reacquisition qualifies only with retained unchanged-geometry/no-
  activation evidence.
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
  cycle progress without runtime reading mutable FSM internals. Route decisions
  use forward polyline progress, remaining distance, corridor deviation,
  mandatory point classification, scene/collision support, visibility, and
  backtracking/zigzag evidence to select a useful future point without skipping
  a transition, constrained turn, or arrival.
- Decisions carry the exact selected/eligible/rejected evidence produced by the
  task's real selection path, including stable rejection codes and source
  geometry provenance, plus bounded route, framing, aim-candidate, seed, and
  selected timing evidence.
- Safety evaluations record the ordered checks actually used. Execution results
  retain those checks across bounded retries without diagnostic re-evaluation.
- `TaskRuntime` publishes one monotonic latest `engine_frame.v1` at observation,
  decision, execution, verification, and terminal boundaries. Terminal frames
  retain the last receipt, typed outcome, derived cleanup state, and blocker.
- The optional Windows overlay renders only EngineFrame: selected green,
  alternatives amber, optional rejected red, plus compact status text. It
  renders rectangles only for a fresh current OBSERVING/RUNNING presentation.
  Paused, stale, disconnected, identity-mismatched, old-run, and terminal
  frames are text-only. Terminal banners are bounded and then reduce to a
  non-target indicator. The host verifies click-through/no-activate window
  styles before display; overlay failure cannot alter engine control.
- Rendering the new route corridor/mandatory/skipped targets, framing region,
  authoritative/inset shape, aim candidates/selection, and pointer path from the
  same frame is part of the current milestone; its live visual acceptance is
  **NOT YET EVALUATED**.
- The overlay host is the actual top-level click-through tool window, and Tcl
  creation/teardown stays on its owning thread. Live passive inspection has
  matched route progress, target/candidate status, camera outcomes,
  verification, and `cleanup: safe` against the underlying EngineFrame.
- The sole snapshot endpoint now exposes bounded demonstration-only
  client/menu/click/camera-input tails with one global Java-assigned sequence,
  an NPC-only actor census, and the existing collision window. Actor/collision
  provenance must match the atomic frame before Python can consider the
  Observation coherent.
- `run.cmd record-demo NAME` injects no input. It requires a loaded scene and
  exact session/PID binding, rate-limits pointer evidence, records semantic
  clicks plus before/after observations, and enables only a fixed, renewable
  two-second lease for candidate camera keys and middle-button gestures from
  the focused RuneLite canvas. Finalization explicitly disables the lease.
  Typed text, other buttons/keys, bank-PIN/input-field state, and OS-global
  hooks remain outside the capture boundary. A candidate control is not called
  positioning unless a nonzero camera-pose delta precedes an exact,
  non-consumed semantic click. Identity/sequence discontinuity remains
  terminal, while exact world-model and interaction-menu provenance handoffs,
  individually or combined during the already bounded capture, retain the
  independently bound hot tail instead of masquerading as a logout.
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
- `EngineApplication` exposes one gathering task with the woodcut and copper
  definitions, a definition-aware expanded profile schema, authoritative
  profile/capability validation, and fresh task/runtime/control construction for
  each start.
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
- `run.cmd app` remains the diagnostic CLI. `run.cmd gui` provides the
  in-process operator frontend without adding a daemon, IPC surface, web
  server, endpoint, or second control system.
- The GUI uses non-daemon workers, a thread-safe result queue, generation
  tokens, current run/capture IDs, a 300-event bound, revalidated harmless
  settings, monotonic connection retention, and cooperative close. Its
  presentation retains exact lifecycle/frame/identity/freshness/diagnostic
  facts while separating current, historical, and terminal text. The frozen
  `VisionEvidence` type remains a dependency-free non-authoritative seam with
  no model or runtime consumer.

## Governing direction

- The product is a small OSRS-specific engine, not a general agent framework.
- The proven Lumbridge woodcut cycle is the regression baseline. Current
  flexibility comes from capability-validated profiles and immutable woodcut/
  copper task-site definitions feeding one explicit gathering FSM.
- Profiles and definitions can never weaken engine invariants.
- RuneLite API facts remain authoritative. Vision may supplement or veto but
  cannot replace semantic API truth. No model dependency is active.
- One implemented `InputCoordinator` owns every Arduino session, and one
  implemented `EngineFrame` owns read-only diagnostic truth.
- A future LLM may read offline evidence but has no runtime control authority.
- Static definitions, active FSM state, run history, and demonstration evidence
  remain separate; unsafe ephemeral state is never restored after restart.
- Fishing remains unsupported until production NPC interaction geometry exists.
  Combat and quest task types require their enumerated observation, safety,
  policy, verification, and recovery capabilities before implementation;
  QuestHelper/Wiki knowledge may be pinned read-only provenance only.

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

- Task-platform milestone deterministic/offline gate: **PASS**. Full Python
  discovery passed 1,033/1,033; the explicit application/frontend/GUI set passed
  117/117; retained replay passed 7/7; and all 84 current Python files compiled.
  The final public `run.cmd test` exited zero with 1,033 Python tests and a
  129/129 Java run across 12 suites with 4/4 tasks executed. The independent
  forced-fresh Java rerun likewise passed 129/129 across 12 suites with
  4/4 tasks executed; Java snapshot-fixture tests passed 2/2; firmware protocol
  passed 8/8; capability/transport passed 78/78; and InputCoordinator/static-
  boundary tests passed 139/139.
- Authoring validation accepted both runnable examples, rejected the unsupported
  NPC-fishing example and deliberately non-runnable scaffold, and validated
  built-in plus external-definition profiles. Publication hygiene found no
  strong secret-pattern hit, changed file over 1 MiB, or generated artifact in
  the branch; architecture inspection, documentation audit, and
  `git diff --check` passed.
- The final synthetic soak passed 5,000 serial samples plus 500 calls at each of
  1/2/4/8 pollers, retained one result signature per level, and returned to one
  thread. Warm-parse p50/p95/p99/max was
  0.1427/0.2572/0.3476/0.7779 ms; decode-plus-parse was
  0.1738/0.3020/0.4245/2.1205 ms; EngineFrame publication was
  0.0031/0.0033/0.0060/0.1186 ms; and the 1,001-row woodcut target decision was
  0.0651/0.1135/0.1547/0.9402 ms with 33 identity evaluations. Copper and
  woodcut per-definition target p95 were 0.1270 and 0.1215 ms. Eight-poller
  p50/p95/p99/max was 0.1463/0.2942/0.5665/49.1670 ms.
- Against the same 5,000-sample baseline, warm-parse p95 changed -5.93%,
  decode-plus-parse p95 -2.27%, EngineFrame p95 -42.11%, 1,001-row target p95
  -33.00%, and RSS growth -35.01%. The comparison also records
  decode-plus-parse p99 +9.32% and target maximum +5.60%; maxima are scheduler-
  sensitive, so no blanket latency improvement is claimed.
- Current loaded-scene/live gameplay remains unavailable. Final read-only
  enumeration found no RuneLite/Python client and no `8890`/`8893` listener;
  the only Java process was a Gradle daemon. Leonardo COM6/COM7 enumerated but
  no port was opened, no input was sent, and no firmware was flashed. Therefore
  no current hardware-cleanup receipt, live mining route, or woodcut cycle is
  implied by the deterministic gate.
- Production-soak continuation: **PASS** in the integrated checkout. Full Python
  984/984 with the documented test-only sandbox ACL harness; forced-fresh Java
  127/127 across 12 suites with 4/4 Gradle tasks executed; retained replay 7/7;
  Python compilation 79/79; repeatable 5,000-sample serial plus 1/2/4/8-poller
  synthetic soak PASS. Focused cache/budget, malformed-request recovery,
  strict-response-shape, bounded backpressure, target-continuity, frontend
  vocabulary, GUI/EngineFrame compatibility, Arduino input-boundary, firmware
  protocol, and asynchronous-recorder gates pass. Loaded-scene live after
  evidence was unavailable because the production `8893` endpoint was not
  listening; no input or firmware change occurred.
- Phase 11 telemetry/observation/decision-pipeline gate: 973/973 Python tests
  plus the normal Java gate through `run.cmd test`; forced fresh Java 124/124
  across 12 suites with 4/4 Gradle tasks executed; retained replay 7/7; compile,
  input-boundary, and retained snapshot-fixture checks passed. Current-build
  loaded-scene timing was unavailable because local `8890`/`8893` listeners
  were absent; connection refusal occurred before input.
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
- There are two built-in definitions and a strict external definition boundary.
  An explicitly supplied runnable gathering file may execute through the same
  foreground facade/runtime, but is not installed or advertised in the built-in
  GUI/catalog. Fallback banks, withdrawal/resupply, auto-equip, production NPC
  interaction, and generic navigation/transition planning remain absent.
- The passive overlay renders the same latest EngineFrame and presentation
  facts as the GUI. Historical and terminal text may remain visible, but stale,
  disconnected, identity-mismatched, and terminal target geometry is always
  cleared; the overlay is never independent proof that a later RuneLite scene
  is loaded.
- The RuneLite endpoint does not expose global raw mouse-button or keyboard
  transitions. It now exposes only privacy-bounded in-process camera controls:
  candidate W/A/S/D or arrows and middle-button press/drag/release on the exact
  focused RuneLite canvas during a fixed, renewable two-second demonstration
  lease. Demonstration manifests keep the global-input gap explicit. Review
  semantics distinguish input method from association: exact object actions
  and exact/high-confidence Walk activations may receive bounded camera links,
  while pose-changing unlinked gestures remain exploratory/unassociated and
  cancelled or no-pose-effect gestures remain explicit. All are review-only.
- Live camera/object acceptance at
  `_run_proofs/movement_targeting_quality/20260712T215958.353-0500/` captured a
  mixed keyboard/middle episode, exact current-frame Oak-tree clickbox
  containment, and reference timing fields. The artifact inspected as valid
  with declared coverage gaps; production input remained Arduino-owned. A
  later artifact at `demo_runs/20260713T025735882565Z_camera-object-resolved`
  retained an exact Oak click and candidate RIGHT/middle controls but showed a
  zero camera-pose delta, so it does not prove that those controls positioned
  the camera.
- A client-thread world-model query can occasionally land one sensor tick after
  the endpoint's immutable frame. The endpoint still rejects that mismatched
  model. After a fully bound start, the recorder tolerates only the exact known
  world-model, interaction-hot, or combined handoff shape for the remaining
  duration/event-bounded capture while retaining independent hot-tail
  continuity; identity, session, tick, payload, warning, or capability
  contradictions remain terminal. Live GUI smoke evidence at
  `_run_proofs/movement_targeting_quality/20260713T163414Z_recorder_handoff_continuity/`
  ran for the requested 20 seconds, retained five real handoff gaps and 1,360
  events, finalized for `duration_elapsed`, and inspected `valid: true` with no
  errors or ambiguities.
- A follow-up Oak demonstration exposed a live-shape regression: two requested
  60-second recordings stopped after 2.543 and 3.930 seconds because the endpoint
  retained its stale `interaction_hot` diagnostic payload while marking that
  capability unavailable. The recorder now accepts only the exact duplicated,
  schema- and session/PID-bound diagnostic copy alongside the independently
  validated hot tail; it still never uses that stale menu snapshot as interaction
  authority. Requested duration is persisted and cross-checked, terminal gaps
  retain bounded payload keys, and the GUI distinguishes duration completion from
  operator or unexpected early stops. Live proof at
  `_run_proofs/movement_targeting_quality/20260713T171233Z_recorder_interaction_handoff_fix/`
  includes full 15-, 12-, and 18-second `duration_elapsed` artifacts. The 12-second
  run retained a real interaction-hot handoff plus a verified Skills click; the
  18-second run retained 12 verified tab clicks and eight world-model handoffs.
  The exact combined form is deterministic-test proven but did not recur during
  the bounded post-fix live session, so no combined live occurrence is claimed.
- The user's three subsequent clips remain independently valid under their
  original byte-exact summary/timeline contract. The application-owned ephemeral
  review recovers two manual Walk targets to the bank, approximately 20.6 and
  12.1 tiles apart under the qualified review estimate, and four return targets,
  approximately 25.6, 6.1, 4.1, and 4.0 tiles. Polyline comparison selects the
  bank and resource directions respectively, with no measured backtracking in
  either retained target sequence. The Oak-only clip retains two exact Oak tree
  / Chop down activations and separates three exploratory camera episodes from
  one action-linked episode.
- The older Top-floor, Bank, and Bottom-floor clicks correctly remain ambiguous:
  those artifacts predate exact activation-surface capture and cannot be
  rewritten. The user's later `20260713T183737323467Z_wood-cutting-to-bank` and
  `20260713T183823171405Z_bank-to-woodcutting` captures now live-prove exact
  `context_menu_row` identity for Top-floor Staircase `56230`, Bank booth `18491`,
  and Bottom-floor Staircase `56231` without treating row coordinates as object
  aim geometry. Both finalized artifacts remain independently hash-valid.
- Versioned camera V4, context-menu timing V1, and manual-route V2 review fix the
  defects exposed by those captures without rewriting V3/V1 artifacts. Exact
  object review links the Bank middle drag at 2,501 ms while Walk remains bounded
  to 2,500 ms; overlapping camera-key transitions remain one coherent episode.
  Context-menu-open lower bounds are 1,313 ms Top-floor, 1,063 ms Bank, and
  1,359 ms Bottom-floor. The return distance review is
  `[3.0, 8.062, 29.12, 4.123, 5.385]`; later age-2/14/18 player samples remain
  diagnostic-only, while the 33.38-tile to-bank click is explicitly a one-tick
  estimate. The GUI prefers these corrected ephemeral review fields while
  retaining finalized summary/timeline bytes unchanged.
- Recorder/review regression for this increment is **PASS**: 762 Python tests,
  71 Java tests across eight suites, focused 121-test recorder/application/GUI
  coverage, compileall, and diff check all pass. The three saved artifacts also
  re-inspect `valid: true`. No production action or additional manual RuneLite
  cycle was run for this bounded recorder change.
- Recorder interpretation follow-up is **PASS**: all 773 Python tests, focused
  132-test recorder/review/application/GUI coverage, golden replay 2/2, the
  9-test production-input boundary, compileall, `run.cmd test`, and diff checks
  pass. A forced Java rerun executed 71 tests across eight suites with no
  failures, errors, or skips. Both new user artifacts re-inspect
  `VERIFIED_WITH_GAPS` with no hash rewrite. No production input or additional
  manual RuneLite cycle was needed for this review-only correction.
- Sensor, task, definition, profile, runtime-configuration, input, diagnostic,
  demonstration, frontend composition, and operator GUI contracts are
  implemented. The GUI-recorded manual demo inspected as valid with declared
  gaps, and final shutdown checks cover the GUI worker, client/endpoint, Java
  process, and Arduino lease.

## Bounded pointer containment and recovery status

This submilestone is **PASS**. `InputCoordinator` remains the only production
input owner and the Arduino remains the only production input backend. Ordinary
gameplay pointer movement, feedback corrections, transit, settlement, and
activation are confined to a fixed 16-device-pixel inset of the authoritative
viewport. Ordinary motion has one initial trajectory and at most two correction
replans. Cursor reacquisition remains a separate movement-only lane with its own
bounded virtual-desktop-to-neutral-canvas contract.

Deterministic acceptance is **PASS**: the focused pointer/action/runtime/safety/
task/login/input-boundary gate passed 445/445; the complete Python regression
passed 878/878; golden replay passed 2/2; `run.cmd test` passed; the forced Java
rerun executed 71 tests across eight suites with no failures, errors, or skips;
the static production-input boundary passed 9/9; and compileall and diff checks
passed. Coverage includes all viewport edges and corners, device-pixel/high-DPI
conversion, curved and linear paths, direction reversal, unsupported transfer
gain, cross-axis motion, an acknowledged MOVE followed by viewport exit, owner/
PID/HWND mismatch, zero activation before recovery, exact geometry and physical-
input gates, one fresh retry, second-failure blocking, cleanup, and ordinary zero-
or-minimal-correction success.

The retained loaded-scene proof is under
`_run_proofs/pointer_containment_recovery/20260715T035111.396056Z/`. Its exact
binding was PID `7880`, HWND `656388`, session
`plugin-7880-1784086893381`, outer `(1221,213,2243,1585)`, client
`(1233,213,2219,1573)`, canvas `(1241,261,2151,1519)`, viewport
`(1252,273,1440,1009)`, and gameplay-safe inset
`(1268,289,1408,977)`, all in PMv2 device pixels. The operator placed the
cursor at `(459,854)`, outside the viewport and owned by Notepad PID `11560` /
HWND `131198`, then returned RuneLite to the foreground. Physical buttons were
up and the physical-input history was quiet. An earlier setup run needed two
bounded Arduino camera-key corrections before a pointer decision existed and is
not counted as the qualifying proof.

The qualifying artifact records exactly two transactions. `input-00000001` at
source tick `872` discarded decision
`navigate_to_bank:walk:route:west_approach_bridge:872:1`, sent 57 MOVE commands
and no MOUSE_DOWN, MOUSE_UP, or KEY_PRESS, used three reacquisition plans (two
correction replans), and moved `(459,854)` to neutral `(2317,1020)` through the
virtual desktop. Its receipt is BLOCKED with
`cursor_reacquired_reobserve_required`, `noActivationSent: true`, identical
before/after geometry, complete STOP_ALL/DISARM/STATUS cleanup, disarmed zero-
held firmware, and closed ledger/backend. A strictly newer loaded, fresh,
wall-clock-fresh, coherent observation at tick `876` produced new decision
`navigate_to_bank:walk:route:west_approach_bridge:876:2` after recognition and
SafetyGate reran. `input-00000002` sent five MOVEs followed by the proof's only
MOUSE_DOWN/MOUSE_UP, used two gameplay plans (one correction replan), retained
all movement inside `(1268,289,1408,977)`, settled at `(2356,1072)`, and crossed
the activation boundary `(2353,1068,7,7)` only after three final validation
samples. It also ended with complete STOP_ALL/DISARM/STATUS cleanup, disarmed
zero-held firmware, zero unresolved/failed/missing-ACK commands, and closed
ledger/backend. Total qualifying counts are 62 MOVEs, five plans, three
correction replans, and one activation.

All 65 ordered recovery samples and 14 ordered retry samples are retained in
`engine_frames.jsonl`; the manifest, frames, and receipt SHA-256 values are
`061041310DF201B351E98B8EE712F24344FD0A354347C57A172C9C3F6BBB3EDE`,
`50B0CF205C05FA8178A2CE6AFCC588A079F2E553E7215657FF7A44F93725D186`, and
`4A41B850367AE34A9D379CC77CC1794182D2CFF7F587A4E748A1EE34A8CE5A57`.
Post-proof observation retained the same PID/session/HWND and exact geometry,
cursor owner PID/HWND matched RuneLite, and physical input remained quiet. The
action-limit terminal state deliberately prevented a third action. Final
shutdown confirmed no RuneLite/Java/Python worker, no 8890/8893 listener, and an
available COM6 lease; the operator-only Notepad setup window was also closed.

The live artifact proves the required initially-outside recovery and fresh
interaction. Direction/gain/cross-axis and post-ACK outside-viewport fault
injection were intentionally deterministic rather than live; no broader live
fault-injection claim is made. No firmware file, software-cursor path, window-
movement path, controller, or parallel recovery owner was added.

## Target-locked coarse-to-fine camera status

The implementation and offline acceptance are **PASS**. The gathering FSM now
named `GatherBankTask` owns one `CameraAcquisitionEpisode`; `InputCoordinator` remains
the only production input owner and the Arduino typed camera key intent remains
the only production camera actuation path. The episode locks one exact object,
tile, or route target together with source session/PID and exact client/canvas/
viewport rectangles. It releases the target only when it becomes invalid or
depleted, loses authoritative identity, becomes unsafe, or fails its bounded
non-improvement budget. A pinned client/canvas/viewport rectangle or process/
session change invalidates rather than silently retargeting the episode.

The final camera goal is an acceptable safe range combining authoritative
visible/clickbox ratio, a configurable central framing region, viewport-edge
margin, route-direction bias, valid pitch, and desired zoom classification.
Desired yaw comes from the player-to-target world bearing with shortest-turn
wraparound; fresh projection supplies screen correction for the optional fine
step. One coarse correction and at most one fine correction are allowed by
default. Activation waits until the episode is ready on fresh authoritative
geometry. No random direction or safety-bound change is permitted.

Verified typed camera-pose receipts feed a bounded response model containing
direction, requested hold, observed yaw/pitch delta, no-effect/pose-limit, and
fresh overshoot evidence. Remaining error and the measured direction-specific
rate choose the next hold. A left/right reversal is rejected unless a fresh
changed-geometry response proves overshoot. An UP/DOWN no-effect at an unchanged
pitch suppresses that same direction until pose changes. Every sent correction
still requires an acknowledged receipt plus a newer pose and changed geometry
before old projection evidence can be reused.

Every hold in this historical proof was clamped through injected
`CameraKeyCapabilities`. The then-installed v1 protocol adapter supplied 250 ms,
matching that firmware limit; its CAPS text advertised `holdKeys=1` without a
numeric maximum. No negotiated value above 250 ms and no wheel actuation is
claimed by this proof. The later versioned capability milestone described below
changes source and host contracts but has not been flashed, so it does not
retroactively change these measurements. At this milestone boundary, materially
unsatisfied zoom reported typed `zoom_required_but_unavailable` and sent no
repeated compensating keys.

The retained trace at
`_run_proofs/movement_targeting_quality/20260713T220522.280174Z` contained 79
camera actions in bursts `2,2,10,12,2,27,2,1,21`, 36 yaw reversals (31 without
same-target fresh overshoot proof), seven target switches within bursts, and six
DOWN no-effects at pitch `1024` (five redundant after the first limit). Its
cleanup was complete. The new comparison-only two-action envelope is at most 18
camera actions across those nine episodes: at least 61 fewer, or 77.2%. This
does not assert that counterfactual interactions would have occurred.

Offline acceptance is **PASS**: the complete Python regression passed 903/903;
`run.cmd replay` passed 7/7 across the golden cycle and retained camera trace;
and the forced Java `--rerun-tasks` build was `BUILD SUCCESSFUL` with all 4 tasks
executed.

Production evidence is retained under
`_run_proofs/camera_controller_live/20260715T051415.0193041Z/`. The first run's
qualifying evidence is
`qualifying/20260715T053544.948938Z/`. One episode kept exact locked target
`route:west_wall_corner`: an acknowledged 250 ms RIGHT coarse correction changed
yaw `0 -> 1109` with fresh changed geometry, and an acknowledged 80 ms RIGHT fine
correction changed yaw `1109 -> 1588` with another fresh changed geometry. No
yaw reversal or pitch command occurred. Framing advanced
`not_visible -> barely_visible -> usable`, after which an out-of-inset cursor
caused the existing coordinator to send ten Arduino-only recovery MOVEs with no
activation. The configured action limit then stopped that run safely.

The game server disconnected afterward. Computer Use performed only separate
operator reconnect setup and is not production evidence. The reconnected
boundary retained exact PID `11304`, root HWND `3735924`, the same session, and
unchanged outer/client/canvas/viewport geometry. Historical physical-input
activity was consumed before production continued. The existing coordinator
required a second neutral recovery and sent ten Arduino MOVEs, zero clicks, and
zero keys with complete cleanup.

A fresh one-action continuation is retained at
`qualifying_continuation/20260715T054845.604479Z/`. It selected the same
`route:west_wall_corner` target and executed one Arduino pointer Walk interaction:
49 MOVEs, two pointer correction replans (the allowed maximum), one MOUSE_DOWN,
and one MOUSE_UP. The player arrived from `(3200,3238)` to `(3196,3234)`, and the
transaction completed full cleanup. A later RIGHT camera proposal visible in the
terminal frame was never sent.

Across both production runs the exact totals are two KEY_PRESS, 69 MOVE (ten
first-run recovery, ten post-reconnect recovery, and 49 interaction), one
MOUSE_DOWN, and one MOUSE_UP. All five transactions completed STOP_ALL, DISARM,
and authoritative STATUS with zero held input. Every ledger/backend closed and
the Arduino lease was released. Production used no software input and no window
mutation. The canonical compact evidence is `camera_live_analysis.json`; final
host/process/port cleanup is retained in `environment_cleanup.json`.

The result is **VERIFIED_WITH_GAP**: the disconnect split the camera episode and
the interaction across two `EngineApplication` runs, so the stricter requirement
for one uninterrupted loaded-scene run is not met. The measured 250 ms hold moved
1,109 yaw units but left 5,035 world-bearing units. The 80 ms fine hold moved 479
units and safe usable framing was reached. Combined with the retained replay,
this supports the conclusion that the current 250 ms limit materially constrains
large otherwise-correct coarse turns. No firmware, wheel, middle drag, chord,
raw KEY_DOWN/KEY_UP, software-input, window mutation, `InputCoordinator`, or
parallel input path changed.

## Observability stabilization status

The current bounded increment is implemented and regression-verified. It adds immutable,
additive timing and exact passive wait-state evidence to the existing runtime,
execution, InputCoordinator, Arduino-ledger, verification, EngineFrame, and GUI
presentation seams. It does not change camera or pointer behavior and does not
add control authority.

The final passive vocabulary and meanings are:

| State | Meaning |
| --- | --- |
| `WAITING_FOR_NEXT_SCENE_UPDATE` | An owner is waiting for the next eligible scene/source update; this expected wait is not an Arduino fault. |
| `WAITING_FOR_SOURCE_COHERENCE` | Current sources are not yet mutually coherent/fresh enough to proceed; all existing coherence gates remain exact. |
| `INPUT_TRANSACTION_BUSY` | The sole coordinator transaction/lease is occupied or in progress; this is serialization, not a command outcome. |
| `CURSOR_FEEDBACK_SETTLING` | A bounded pointer/Windows feedback all-clear is still settling; it does not claim command failure. |
| `ARDUINO_HEALTH_STALE` | Passive Arduino readiness/health evidence has aged beyond its presentation bound; it is not a command result. |
| `ARDUINO_COMMAND_FAILED` | Connect, negotiate, arm, serial write, ACK, rejection, or command cleanup failed; presentation is immediate. |
| `SENSOR_STALE` | The underlying Observation/source evidence violates its safety freshness bound; the engine safety fact is immediate. |
| `PRESENTATION_FRAME_STALE` | The displayed EngineFrame/run association is expired or mismatched; old presentation data has no current authority. |

Expected waits display immediately as neutral wait states. The GUI may debounce
only a momentary passive stale display state, for at most 500 ms. It may not
debounce `ARDUINO_COMMAND_FAILED`, alter the exact presentation or underlying
safety state, or infer an Arduino failure from a freshness wait. Old run/frame
evidence must be cleared as soon as the run ID changes.

`lastExecution.activationAttempted` is an enclosing EngineFrame execution fact.
The compact GUI must read it beside, not inside,
`lastExecution.receipt`. New timing/observability and command-duration fields
are optional for legacy readers and fixtures.

Acceptance status for this increment:

- focused deterministic suites: **PASS**, 411/411;
- complete Python regression through `run.cmd test`: **PASS**, 859/859;
- Java regression through `run.cmd test`: **PASS**;
- forced Java rebuild/test with `--rerun-tasks`: **PASS**, 4/4 tasks executed;
- golden replay: **PASS**, 2/2;
- retained Java snapshot fixture checks: **PASS**, 2/2;
- input-boundary and software-input static gate: **PASS**, 9/9; and
- live gameplay cycle: **NOT RUN / OUT OF SCOPE**.

A milestone-only checkpoint commit was not created. The authoritative worktree
was already dirty across the same runtime, EngineFrame, GUI, coordinator, and
test files for the movement/camera milestone, so a commit could not isolate
this increment without reconstructing or overwriting pre-existing work.

## Versioned camera-input capability expansion status

The source implementation is **IMPLEMENTED, DETERMINISTICALLY AND FULL-
REGRESSION VERIFIED, AND NOT FLASHED**. `InputCoordinator` remains the only
production input owner,
`_ArduinoHIDTransport` remains private, and production input remains Arduino-
only. There is still one runtime, one lease, one command ledger, one cleanup
path, and no task-visible raw command surface. No generic `KEY_DOWN`/`KEY_UP`,
middle drag, chord, software-input path, window mutation, controller, or parallel
recovery path was added.

The proposed firmware identifies as `arduino_hid.v2` / `2.0.0` and advertises
exact `input_capabilities.v2`. Its retained pointer/button/generic-key/cleanup
limits remain 20 relative counts and 250 ms. It adds only:

- `cameraKeyHold=1`, `cameraKeys=left,right,up,down`, and
  `maxCameraHoldMs=600`; and
- `wheel=1` with `maxWheelStep=3`.

`CAMERA_HOLD` accepts one approved direction and 1--600 ms, performs press/wait/
release atomically, and then reports exact requested/applied duration. `WHEEL`
accepts only nonzero signed amounts from -3 through 3 and reports exact
requested/applied amount. The 600 ms maximum is compile-time constrained below
the 1,000 ms watchdog. Invalid, zero, negative-duration, oversized, malformed,
extra-token, unarmed, unsupported-key, and unknown commands release all tracked
input and disarm. Generic key presses retain their old short bound.

The host constructs one frozen negotiated `InputCapabilities` from exact
identity, CAPS, and STATUS agreement. Every typed intent declares a frozen
`RequiredInputCapabilities` and fails before activation when support or limit is
absent. Receipts retain the required and negotiated capability values, exact
activation boundary, requested/applied hold or wheel value, timing/command
ledger, later pose/zoom verification, and final cleanup. Old
`input_transaction_receipt.v1` and `engine_frame.v1` fixtures remain readable
when these additive fields are absent.

Camera zoom is one semantic task request for the already locked target, not a
generic wheel API. Before `WHEEL`, the normal loaded/fresh/coherent, PID/session,
focus, geometry, physical-input-quiet, bank/PIN/dialogue/text-input, and
SafetyGate rules all remain mandatory. The coordinator additionally proves the
actual cursor already lies inside the fixed pointer-safe world viewport and is
owned by the exact foreground root HWND; it does not move the cursor for zoom.
After ACK, the shared verifier requires a newer same-process/session/location
observation, expected-sign `zoom3d` movement, changed geometry identity,
unchanged yaw/pitch, and unchanged protected UI. Unchanged or contradictory zoom
blocks, and the locked episode cannot burn repeated wheel or yaw/pitch attempts
to compensate.

Compatibility is explicit: old host plus old v1 firmware remains the historical
baseline; current host plus v1 firmware preserves pointer/short-key/cleanup but
types camera hold and wheel as unavailable before activation; current host plus
exact v2 firmware enables only the newly advertised bounded operations; and old
v1-only host plus v2 firmware safely rejects the protocol and is not a supported
deployment pair. Any rollout must therefore be host-first.

Deterministic and complete regression evidence is:

- firmware source-contract/golden harness: **PASS**, 8/8 tests across 25 command
  vectors;
- capability plus full Arduino transport modules: **PASS**, 78/78 tests;
- full InputCoordinator plus ownership/software-input boundary gate: **PASS**,
  139/139 tests;
- complete Python regression: **PASS**, 949/949 tests;
- Python compileall for host, tests, and protocol harness: **PASS**;
- golden-cycle and retained-camera replay: **PASS**, 7/7 tests;
- forced Java rebuild/regression: **PASS**, 105/105 tests across 10 suites with
  zero failures, errors, or skips and all four Gradle tasks executed;
- Leonardo firmware compile: **PASS**, 14,038/28,672 flash bytes and
  682/2,560 RAM bytes; and
- audited documentation/code diff whitespace check: **PASS** for the protocol
  files.

The harness binds exact CAPS text, constants, dispatch, handler ordering, fatal
cleanup, limits, ACK shapes, and watchdog relationships to a deterministic
Python model. The Leonardo compile proves the sketch builds; it does not execute
the compiled handlers on hardware. Actual HID timing and wheel/`zoom3d` polarity
remain post-flash live-proof questions. The completed offline gates above are not
a hardware-installation or live-input claim.

No upload command was run and no board was flashed. Existing historical 250 ms
camera evidence remains v1 evidence only. The proposed v2 firmware must not be
treated as installed, negotiated from hardware, or live-proven.

The proposed bounded host-first flash and live-proof procedure is:

1. Freeze the intended host/firmware diff and record its commit or source hashes.
   Rerun the protocol harness, complete Python and forced Java suites, golden
   replay, input-boundary/static gates, Leonardo compile, and final diff/status
   review. Stop on any failure.
2. Deploy or start the current host while the device still runs v1. Prove the
   exact serial device/VID/PID, v1 negotiation, refusal of camera hold/wheel
   before activation, and acknowledged `STOP_ALL -> DISARM -> STATUS` with zero
   held input. Close the ledger/backend and release the lease.
3. Present the exact firmware diff, compiled board target and size, deterministic
   results, compatibility consequence, safety analysis, and this procedure to
   the user. Obtain a new explicit user approval that specifically authorizes
   flashing this board. Prior authorization for computer use or testing is not
   flash approval.
4. With all engine workers stopped, the lease free, no sensitive text field
   focused, and the exact Leonardo/serial target re-proven, flash only the
   reviewed v2 sketch. Do not flash a fallback, another board, or an automatic
   rollback without separate approval.
5. After reset, use the current host for a no-gameplay handshake: exact
   `IDENTIFY`, exact v2 CAPS, and STATUS proving disarmed with zero keys/buttons.
   Reject any version, field, limit, watchdog, device, or status mismatch.
6. Run one cleanup-only armed transaction and require acknowledged
   `STOP_ALL -> DISARM -> STATUS`, a complete closed ledger/backend, and a
   released lease before any camera operation.
7. Pin one loaded-scene RuneLite PID/root HWND and exact unchanged outer/client/
   canvas/viewport geometry. Prove physical-input quiet, PIN/dialogue/bank/text
   safety, and place the cursor manually at a neutral point inside the world
   inset. Lock one safe target. Send at most one conservative typed camera hold;
   require its exact ACK and a fresh changed-pose/geometry result, then complete
   cleanup.
8. Only if a fresh decision for the same locked target materially requires zoom,
   send at most one signed magnitude-one typed zoom. Require cursor ownership,
   exact unchanged geometry, exact ACK, expected-sign fresh `zoom3d` movement,
   unchanged yaw/pitch/UI, and complete cleanup. Unchanged or contradictory zoom,
   target/identity/geometry drift, physical input, or cleanup failure is
   terminal; do not try the opposite sign or another wheel step.
9. Only after both applicable camera proofs pass, obtain another fresh
   recognition/SafetyGate decision and permit at most one ordinary safe target
   interaction. Finish with acknowledged `STOP_ALL`, `DISARM`, authoritative
   zero-held STATUS, closed ledger/backend, released lease, and retained exact
   receipts/samples/timings. Record all commands and explicitly distinguish any
   Computer Use setup from Arduino production evidence.

This procedure is proposed only. Steps 4--9 remain prohibited until the user
explicitly approves the flash after every deterministic and full-regression gate
passes.

# Architecture

## Governing target

The engine is an OSRS-specific contract pipeline:

```text
Profile
  -> validated Task/Site Definition
  -> explicit task-specific FSM

RuneLite API facts
  -> immutable atomic SensorFrame
  -> immutable Observation

Observation + task decision
  -> ActionIntent + VerificationSpec
  -> SafetyGate
  -> InputCoordinator
  -> PointerMotionPolicy
  -> Arduino transport + firmware

Fresh Observation
  -> Verifier
  -> typed Outcome
  -> task FSM

Runtime state and evidence
  -> immutable EngineFrame
  -> recorder / passive overlay / future thin GUI
```

There is one observation truth, one runtime orchestrator, one active explicit
task FSM, one safety gate, one Arduino session owner, one typed verification
path, and one read-only diagnostic truth. Diagnostics have no control authority.

The bounded architecture migration is complete through Phase 8. The current
column is implemented; the target column now names only preservation rules and
the later GUI consumer.

| Layer | Current baseline | Governing target |
|---|---|---|
| Sensor | atomic game-tick `SensorFrame` plus separately stamped client/menu evidence | preserve the same single source contract while adding no second endpoint |
| Observation | immutable source-coherent `Observation` | preserve the same single truth for task-neutral seams |
| Definition/profile | one immutable `LUMBRIDGE_WEST_TREES_V1` plus one validated one-cycle default profile | preserve typed validation; add no dynamic loader during this mission |
| Task | minimal `Task` protocol plus definition-bound concrete `WoodcutBankTask` FSM | preserve the protocol and explicit task-specific transitions |
| Decision | opaque state/action plus immutable selected/eligible/rejected evidence from the actual task path | preserve this task-neutral evidence contract for frontends |
| Safety | one `SafetyGate`, explicitly split between engine invariants and typed task constraints | preserve the same non-overridable boundary |
| Runtime configuration | immutable endpoint/Arduino/poll/bound values with engine caps | same contract consumed by every frontend |
| Input | one `InputCoordinator`, deterministic pointer policy, private Arduino transport, immutable wire receipts | preserve the sole-owner boundary for every future caller |
| Verification | one `Verifier` returning immutable typed `Outcome` values | preserve the same typed pathway |
| Diagnostics | one immutable latest `EngineFrame` plus optional passive click-through overlay | preserve one no-authority status contract for recorder/frontend readers |
| Demonstration evidence | bounded read-only recorder plus tamper-verifying semantic inspector | preserve append-only, no-replay, review-only evidence |
| Frontend | tokenized `EngineApplication` facade plus minimal foreground CLI | future GUI consuming the same facade without engine logic |

## Ownership boundaries

The following categories must never collapse into one unvalidated configuration
blob:

| Category | Owns | Must not own |
|---|---|---|
| Profile | selected definition, bounded goals, supported preferences | engine safety switches, raw IDs/routes, endpoint or serial internals |
| Task/site definition | versioned OSRS IDs, actions, areas, bank, route, transitions, predicates, provenance | mutable task state, hardware, runtime lifecycle |
| Runtime configuration | endpoint, Arduino port, poll rate, hard limits | task meaning or weaker safety rules |
| Engine invariants | freshness, binding, focus, geometry, menu proof, PIN refusal, verification, cleanup | user-overridable options |

The first implementation contains exactly one built-in Lumbridge definition and
one default profile. Adding a definition does not create a generic planner: it
provides validated facts to a still-explicit task FSM.

## Current implementation

### Sensor

The RuneLite plugin owns the only live telemetry cache. `PluginLiveCache`
publishes one immutable `SensorFrame` through one atomic reference; partial or
login captures replace the prior frame and explicitly mark missing facts. Its
localhost service exposes exactly three read-only routes:

- `GET /health`
- `GET /schema`
- `POST /snapshot`

The endpoint does not apply configuration or accept gameplay commands.
`plugin_snapshot_response.v2` separates frame capture time from
`assembledAtUtc`. Core facts share one frame tick/session/process identity.
World-model and tile geometry must also match the frame's camera/window
fingerprint before the endpoint merges them. Menu evidence is independently
stamped from its real post-menu-sort sample.

The canonical adapter requests one neutral `scene_object_census`; the endpoint
does not expose resource/route/service classifiers and Python ignores those
retired payload names. Scene rows contain factual identity, actions, location,
and projection only. Projection selection uses the explicit request, factual
distance, and stable object key. Selected definitions assign resource, route,
and bank meaning downstream. Dialogue capture is also structural: pinned
RuneLite widget identities expose raw prompt/option facts, while exact
staircase wording remains definition/task-owned.

The same endpoint exposes demonstration-only bounded `client_tick_tail`,
`actor_census`, and `collision_window` needs. Hot rows from client tick,
post-menu-sort, and `MenuOptionClicked` share one monotonic sequence and retain
per-lane drop counters. Actor rows are NPC-only; nested interacting-player data
is omitted. Actor/collision evidence is exposed only after the endpoint binds
its source tick, capture, session, process, and geometry frame to the atomic
observation. These additive views do not create a second observation or input
surface.

### Observation

`osrs_bot.observation.ObservationClient` sends one canonical snapshot request
and produces one immutable `Observation`:

```text
player, location, plane, inventory, nearby_objects, menus, widgets,
game_state, camera yaw/pitch, source timestamp, assembly timestamp,
frame identity, tick, canvas bounds, optional client-window bounds
```

Source coherence, freshness, canvas bounds, warnings, and missing capabilities
travel with the same object because they determine whether any action is safe.
Object census rows are deduplicated by stable object key. Canvas coordinates are converted to
screen coordinates once, at this boundary. Menu samples preserve the explicit
top/default entry, scene parameters, client-tick sequence, and sampled pointer.
When RuneLite opens a context menu, the adapter also exposes the transformed
menu bounds and deterministic visible row bounds used by the input path.
Actions and verifications are bound to the plugin session; live input is also
bound to the exact telemetry-owning RuneLite process.

The optional device-pixel `clientWindow*` envelope is all-or-none, has positive
dimensions, and must contain the canvas. It represents the outer window and is
ownership/movement-only cursor-ingress evidence, never an activation region;
the input preflight matches it to `GetWindowRect` and separately proves the
canvas inside the actual Win32 client. Object aim points are likewise paired to
authoritative geometry: the point must be inside the viewport and the first
present API shape in clickbox -> convex hull -> canvas tile order. A present
shape never falls through to weaker geometry, and `canvasLocation` alone is
diagnostic unless that shape contains it.

`LOGGED_IN` alone is not loaded-scene proof. The plugin requires a local player
and an explicitly absent Welcome to Gielinor panel before publishing
`scenePlayable=true`; `Observation.loaded_scene` requires that bit as well.

No downstream module reads raw JSON or a plugin cache.

### Task

`osrs_bot.task_contract` exposes five operations: request bounded observation
projections, decide from one immutable observation, apply one typed verification
result, discard a proposal that the input boundary proves was never activated,
and publish an immutable running/complete/blocked snapshot. Task state is opaque
to runtime. Runtime does not import the concrete task, compare its phases, or
inspect its mutable progress. An executable action without a verification
specification is rejected before an input boundary is called.

`WoodcutBankTask` is an explicit state machine:

```text
FIND_TREE -> CHOP -> VERIFY_LOGS
    ^                    |
    |                    v (inventory full)
    +------------- NAVIGATE_TO_BANK -> OPEN_BANK
                                           |
                                           v
NAVIGATE_TO_TREES <- CLOSE_BANK <- VERIFY_DEPOSIT <- DEPOSIT_LOGS
        |
        v
     COMPLETE
```

The task accepts one validated `BoundProfile`. The sole built-in definition
owns all resource/bank selectors, areas, route steps and transitions, inventory
predicates, tick expectations, and evidence provenance. The profile owns only
the selected definition and one-cycle goal. Neither owns mutable FSM state or
engine safety controls.

The two definition routes are fixed tuples of walk targets and staircase
interactions. Only the current walk target is requested from RuneLite. Missing
projection evidence waits without input, and a present labeled projection with
contradictory identity blocks. A stable exact target whose projection remains
non-actionable may enter the definition-owned camera-recovery lane: shortest
fixed-point yaw direction, 250 ms key hold, at most eight typed and verified
camera-pose changes. No planner substitutes a route or camera strategy.

Staircases accept a live direct `Climb-up`/`Climb-down` action when it is the
default. If the live default is generic `Climb`, the explicit `STAIR_DIALOGUE`
state selects exactly one matching numbered up/down option and verifies the
plane change.

The task emits an `Action` and a generic `VerificationSpec`. It never sends the
action and never evaluates its own verification. It consumes only typed
outcomes from the shared verifier.

The same concrete selection/classification path also emits immutable
`DecisionEvidence`: exact selected target, eligible candidates, and rejected
candidates with stable codes. `TaskSnapshot` publishes the bound definition and
profile plus route/cycle progress. Runtime reads only these task-contract values
and never inspects mutable FSM progress.

### Safety and action

`SafetyGate` first checks non-overridable engine invariants: coherent/fresh
source evidence, loaded scene, session/process/focus binding, source tick, PIN
refusal, exact identity, geometry, canvas bounds, and menu provenance. It then
checks immutable task constraints carried by the action: expected interface
state/plane/readability, exact dialogue choice, and permitted inventory. These
constraints can only narrow an action. Bank plane and staircase wording are not
shared safety constants.

Each public safety evaluation records its actual ordered subchecks. The action
layer carries those values, including bounded retry attempts, in
`ExecutionResult`; diagnostics do not rerun SafetyGate.

`CoordinatedActionInterface` and the saved-session login helper submit typed
approved intents to the sole `InputCoordinator`. The coordinator then:

1. creates one private backend and empty in-memory command ledger without
   opening serial;
2. proves a bounded physical-button quiet dwell, samples the actual current
   cursor in the calling thread's PMv2 device-pixel context, pins the exact
   foreground root HWND/PID, matches the telemetry outer window to
   `GetWindowRect`, and proves the canvas inside the actual Win32 client;
3. when the stationary cursor is beyond that outer window, performs one
   bounded non-activating window translation under the cursor, closes with an
   empty safely-unsent receipt, discards every stale coordinate, and requires a
   fresh login scan or newer same-identity gameplay tick before another intent;
4. only after a valid preflight opens and arms the private Arduino transport;
5. when the fresh cursor is just outside the canvas but still inside the exact
   pinned outer window on one axis, performs only the bounded movement-only
   ingress and proves stable canvas headroom before continuing;
6. retains the canonical action identity/aim separately from the actual settled
   cursor, selects bounded command-space waypoints toward the exact observed
   screen point, runs the pure exact planner for each waypoint with bounded velocity,
   acceleration, braking, four-sided transfer headroom, transaction-wide plan
   and MOVE caps, and actual-feedback correction, then accepts only a complete-
   plan settled endpoint inside the caller's explicit activation region;
7. if an acknowledged MOVE has an unchanged ordinary sample, polls once more
   without another MOVE and independently validates both sample intervals before
   applying the existing bounded delayed/no-effect accounting;
8. passes that actual stable device-pixel endpoint to the caller's
   lane-specific validator under a checked firmware-watchdog lease; if that
   validator outlives the lease, performs at most one explicit safe rearm and
   reruns the same semantic validator, while a second expiry blocks input;
9. for pointer lanes, repeatedly requires quiet physical buttons and exact
   `WindowFromPoint` root ownership around the newer menu/widget proof; typed
   key lanes instead require their exact camera/interface/dialogue constraint;
10. when the exact action is a unique lower context entry, opens the menu,
   derives that row from RuneLite menu geometry, moves to it, revalidates the
   fresh open-menu sample and pointer, and clicks it once;
11. otherwise clicks the exact default entry or submits the one approved key,
    then consumes only its own acknowledged Windows button transition;
12. records each command and firmware acknowledgement without truncation; and
13. ends every attempted connection with acknowledged `STOP_ALL`, `DISARM`, and
   wire `STATUS` proving disarmed with zero held inputs before closing.

The immutable `InputReceipt` is successful only when the activation and final
cleanup sequence are present in order, every command is terminal and
acknowledged, no ledger entry is unresolved, the final firmware state is safe,
and both ledger and transport close. The raw transport methods are private and
only the coordinator imports them. A state-changing firmware rejection is
never retried implicitly. There is no software-input fallback.

The action layer may emit typed `TARGET_EVIDENCE_INVALIDATED` when an adaptive
object/walk proposal exhausts bounded fresh hover reobservation before
activation, or `CURSOR_STATE_INVALIDATED` when real cursor/ownership/bounds
evidence changes. Runtime may discard at most one consecutive such proposal,
and only when the immutable receipt proves the matching failure kind, either
complete connected cleanup or a closed empty pre-serial ledger/backend, and no
activation. Target invalidation suppresses
the exact resource key for one fresh alternate; cursor invalidation preserves
the target and merely reobserves. Reason text is diagnostic and never selects
the transition. Any activation, incomplete cleanup, mismatch, or repetition
blocks.

### Saved-session login assistance

`run.cmd login COMx` is a bounded helper beside the task engine, not a second
planner or recovery framework. It recognizes only the retained Play Now and
Click here to play visual templates plus the narrowly bounded historical
idle-disconnect OK geometry, binds the exact telemetry process and RuneLite
client window, submits the prompt through `InputCoordinator`, revalidates it
after moving the Arduino mouse, and verifies a telemetry transition afterward.
It never types text. Continue,
credential, MFA, unknown, and ambiguous surfaces fail closed.

If the normal prompt matcher reaches its work cap on a coherent loaded scene, a
larger but still bounded read-only fallback scans only the two exact retained
templates. It excludes the broad disconnect heuristic and cannot authorize
input. Template absence can support loaded-scene PASS only after two increasing
ticks from the same PID/session; a scan-aged observation is refreshed and must
be coherent and no older than two seconds.

### Verification and runtime

`Verifier` evaluates only observations later than the action baseline and fails
at the declared tick deadline. After a sent action, runtime shifts that baseline
to the final preactivation observation so bounded pointer motion and semantic
revalidation do not consume the post-action proof window. A passing result must
include one stable typed outcome. Task-specific item IDs and dialogue
expectations are supplied in the verification specification rather than
embedded in shared control flow. The runtime adds a wall-clock and observation
bound so a frozen client cannot wait forever.

`RuntimeConfig` separately validates endpoint/token/Arduino/polling and hard
observation/action/runtime/verification limits. Its finite engine-owned caps
cannot be changed by a profile or task definition. The default action budget is
100, above the frozen 64-action ideal and observed 82-action complete cycle,
under the unchanged hard maximum of 500.

Walk verification passes after authoritative movement closer or arrival. Route
movement owns a 20-tick definition deadline, distinct from the eight-tick
ordinary action deadline. The task then waits until player location is stable
for four game ticks before it advances the waypoint, preventing repeated clicks
while pathing.

When the bank-close widget lacks usable geometry, the task may emit only the
exact Escape close intent and only when RuneLite explicitly reports keyboard
close support. The normal post-action bank-closed verification still applies.

`python -m osrs_bot task` stops at the first proposal. `--execute` is the only
task mode that constructs an `InputCoordinator`. The runtime stops on completion,
block, transport failure, verification failure, or a configured bound.

### Golden replay

`run.cmd replay` executes the sanitized fixture in
`tests/fixtures/golden_lumbridge_cycle.json`. It freezes the final task route,
action kinds, typed verification sequence, and terminal cycle state. Its
provenance records hashes of the bounded live component traces and explicitly
states that those traces were stitched and do not contain complete raw sensor
or SafetyGate evidence.

## EngineFrame and passive diagnostics

The real runtime publishes one immutable `EngineFrame` after observation,
decision, execution, verification, and terminal boundaries. It contains
task/state, definition/profile IDs, route progress, selected target identity
and geometry, eligible and rejected candidates with codes, camera pose,
decision reason, typed action key/hold evidence, ordered safety checks, pending
and last verification, typed outcome, execution receipt, final cleanup state,
and current blocker. The atomic publisher retains only the latest monotonic
frame; it is not a history store.

The optional overlay consumes this exact frame. It uses green for the selected
target, amber for eligible alternatives, and optional red for rejected
candidates. It suppresses rectangles when source tick/geometry provenance no
longer matches the displayed Observation. The actual root top-level host owns
the verified Win32 click-through, non-focusable, layered, and tool-window-only
styles; Tcl creation and teardown remain on that host thread. It has no input
handlers, target selection, SafetyGate calls, or Arduino imports, and an overlay
failure cannot alter runtime control.

The recorder, implemented CLI facade, and future GUI consume immutable read
contracts. Readers may
format or filter it but may not reselect a target, recalculate safety, mutate
the FSM, import Arduino control, or authorize input.

## Demonstration evidence

`osrs_bot.demonstration` is a read-only evidence path over the existing snapshot
endpoint. It requires a coherent loaded `Observation`, then binds every poll to
the same RuneLite session/process. Atomic-frame provenance also covers the
bounded NPC census and collision window. Client, menu, and semantic-click tails
share one Java-assigned monotonic sequence; overlap is deduplicated and a reset
stops capture. Pointer samples are bounded to 20 Hz. Screenshots are read-only,
bounded inside verified canvas geometry, and suppressed for a bank PIN.

Finalization produces portable JSONL, manifest, semantic summary, concise
timeline, optional crops, and SHA-256/size evidence. Inspection verifies the
complete declared file set, paths, symlinks, limits, hashes, schemas, and
contiguous recorder sequence before deriving anything. Invalid evidence yields
no suggestions. Scene/NPC/collision/menu cap metadata remains explicit. Valid
candidates contain authoritative world anchors, actions, plane transitions,
known game-object IDs, or NPC IDs correlated through an exact same-tick census.
Walk, player, widget, incomplete, and uncorrelated menu identifiers cannot
become entity candidates. Input coordinates are omitted and every candidate is
review-only and never automatically active. The recorder imports no runtime,
safety, task, input, login, or Arduino authority.

## Application facade and frontend

`EngineApplication` is the implemented composition root. It lists the exact one
task/definition, returns the frontend-safe profile schema, reuses authoritative
profile validation, creates a fresh task/runtime/control for each start, returns
the exact latest EngineFrame and runtime-owned statistics, reports exact
blockers, and owns mutually exclusive run/demonstration worker lifecycles.

Run and capture commands carry monotonic local IDs, so delayed UI commands
cannot affect a later operation. Pause is acknowledged only at a no-input
boundary; an Observation held across pause is discarded. Once decision emits
an executable action, Arduino transaction, cleanup, bounded verification, and
typed transition finish before pause/safe-stop acknowledgement. Pause never
extends the hard runtime bound. No thread is killed and safe stop opens no extra
hardware session.

The facade imports the concrete task only to compose the sole supported engine.
It has no target selection, SafetyGate, Verifier, InputCoordinator, Arduino, or
raw-input calls. `run.cmd app` exposes catalog/profile and foreground run
commands; Ctrl+C becomes cooperative safe stop. There is no daemon, IPC layer,
or GUI. See `docs/FRONTEND_CONTRACT.md`.

## Vision and LLM boundaries

RuneLite remains authoritative for identity and semantic state. The implemented
dependency-free `VisionEvidence` seam contains capture time, exact
crop/transform, model/version, class/confidence, bounds or mask, and
occlusion/image-quality status. It is frozen, explicitly non-authoritative, and
has no runtime consumer or model dependency. Vision may later supplement or
veto an unsafe condition, or propose a point inside API-confirmed geometry; it
may not overwrite session, tick, player, inventory, entity, menu, or widget
facts.

No LLM participates in runtime control. A future offline assistant may read
immutable definitions, demonstration artifacts, run history, and diagnostic
evidence. It may not emit executable input, mutate an active profile, or bypass
the FSM, `SafetyGate`, `InputCoordinator`, or `Verifier`.

## Memory and restart

Static definition knowledge is immutable and version controlled. Current task
state belongs only to the active FSM. Run history and demonstration evidence are
separate append-only records and cannot authorize behavior.

After restart, never restore pending verification, source/client ticks, menu
samples, old screen coordinates/clickboxes, session-bound targets, or armed
input state. Reobserve the game, validate a fresh profile/definition binding,
and reconcile the FSM before any new action.

The sole narrow reconciliation derives new state from current evidence rather
than restoring it: a structurally empty inventory outside the work area may
resume only the furthest matching anchor/radius on the exact built-in return
route, with matching plane. Partial, off-route, or wrong-plane states still
block. Completing that historical return grants no cycle credit; the active
process must perform a new full cycle.

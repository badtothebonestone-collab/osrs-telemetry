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

The architecture is being migrated in bounded checkpoints. The current column
describes the Phase 7 implementation; the target column names only contracts
still owned by later phases.

| Layer | Current baseline | Governing target |
|---|---|---|
| Sensor | atomic game-tick `SensorFrame` plus separately stamped client/menu evidence | preserve the same single source contract while adding no second endpoint |
| Observation | immutable source-coherent `Observation` | preserve the same single truth for task-neutral seams |
| Definition/profile | one immutable `LUMBRIDGE_WEST_TREES_V1` plus one validated one-cycle default profile | preserve typed validation; add no dynamic loader during this mission |
| Task | minimal `Task` protocol plus definition-bound concrete `WoodcutBankTask` FSM | preserve the protocol and explicit task-specific transitions |
| Decision | opaque state/action plus immutable selected/eligible/rejected evidence from the actual task path | preserve this task-neutral evidence contract for frontends |
| Safety | one `SafetyGate`, explicitly split between engine invariants and typed task constraints | preserve the same non-overridable boundary |
| Runtime configuration | immutable endpoint/Arduino/poll/bound values with engine caps | same contract consumed by the future facade |
| Input | one `InputCoordinator`, deterministic pointer policy, private Arduino transport, immutable wire receipts | preserve the sole-owner boundary for every future caller |
| Verification | one `Verifier` returning immutable typed `Outcome` values | preserve the same typed pathway |
| Diagnostics | one immutable latest `EngineFrame` plus optional passive click-through overlay | preserve one no-authority status contract for recorder/frontend readers |
| Demonstration evidence | bounded read-only recorder plus tamper-verifying semantic inspector | preserve append-only, no-replay, review-only evidence |
| Frontend | `run.cmd` | thin application facade and future GUI consuming the same contracts |

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

The canonical adapter requests one neutral `scene_object_census`. Filtered
resource/route/service censuses remain endpoint diagnostics. Scene rows omit
candidate/type/skill labels, and projection selection uses only the explicit
projection request, factual distance, and stable object key. Candidate labels
neither bias scene geometry nor authorize task behavior.

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
game_state, source timestamp, assembly timestamp, frame identity, tick
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

`LOGGED_IN` alone is not loaded-scene proof. The plugin requires a local player
and an explicitly absent Welcome to Gielinor panel before publishing
`scenePlayable=true`; `Observation.loaded_scene` requires that bit as well.

No downstream module reads raw JSON or a plugin cache.

### Task

`osrs_bot.task_contract` exposes exactly four operations: request bounded
observation projections, decide from one immutable observation, apply one typed
verification result, and publish an immutable running/complete/blocked
snapshot. Task state is opaque to runtime. Runtime does not import the concrete
task, compare its phases, or inspect its mutable progress. An executable action
without a verification specification is rejected before an input boundary is
called.

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

The two definition routes are fixed tuples of walk targets and staircase interactions.
Only the current walk target is requested from RuneLite. Missing or temporarily
non-actionable projection evidence waits without input; a present labeled
projection with contradictory identity blocks. No planner substitutes a route.

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

1. opens one private Arduino transport, command ledger, and armed transaction;
2. constrains deterministic relative movement to the observed RuneLite canvas;
3. moves to the exact observed screen point with bounded velocity,
   acceleration, braking, and feedback corrections;
4. asks the caller's lane-specific validator for fresh evidence;
5. requires a newer menu sample whose top/default entry, scene parameters, and
   pointer position match the intended target;
6. when the exact action is a unique lower context entry, opens the menu,
   derives that row from RuneLite menu geometry, moves to it, revalidates the
   fresh open-menu sample and pointer, and clicks it once;
7. otherwise clicks the exact default entry or submits the one approved key;
8. records each command and firmware acknowledgement without truncation; and
9. ends every attempted connection with acknowledged `STOP_ALL`, `DISARM`, and
   wire `STATUS` proving disarmed with zero held inputs before closing.

The immutable `InputReceipt` is successful only when the activation and final
cleanup sequence are present in order, every command is terminal and
acknowledged, no ledger entry is unresolved, the final firmware state is safe,
and both ledger and transport close. The raw transport methods are private and
only the coordinator imports them. There is no software-input fallback.

### Saved-session login assistance

`run.cmd login COMx` is a bounded helper beside the task engine, not a second
planner or recovery framework. It recognizes only the retained Play Now and
Click here to play visual templates plus the narrowly bounded historical
idle-disconnect OK geometry, binds the exact telemetry process and RuneLite
client window, submits the prompt through `InputCoordinator`, revalidates it
after moving the Arduino mouse, and verifies a telemetry transition afterward.
It never types text. Continue,
credential, MFA, unknown, and ambiguous surfaces fail closed.

### Verification and runtime

`Verifier` evaluates only observations later than the action tick and fails at
the declared tick deadline. A passing result must include one stable typed
outcome. Task-specific item IDs and dialogue expectations are supplied in the
verification specification rather than embedded in shared control flow. The
runtime adds a wall-clock and observation bound so a frozen client cannot wait
forever.

`RuntimeConfig` separately validates endpoint/token/Arduino/polling and hard
observation/action/runtime/verification limits. Its finite engine-owned caps
cannot be changed by a profile or task definition.

Walk verification passes after authoritative movement closer or arrival. The
task then waits until player location is stable for four game ticks before it
advances the waypoint, preventing repeated clicks while pathing.

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
and geometry, eligible and rejected candidates with codes, ordered safety
checks, pending and last verification, typed outcome, execution receipt, final
cleanup state, and current blocker. The atomic publisher retains only the
latest monotonic frame; it is not a history store.

The optional overlay consumes this exact frame. It uses green for the selected
target, amber for eligible alternatives, and optional red for rejected
candidates. It suppresses rectangles when source tick/geometry provenance no
longer matches the displayed Observation. Its verified Win32 extended styles
make it click-through, non-focusable, layered, and tool-window-only. It has no
input handlers, target selection, SafetyGate calls, or Arduino imports, and an
overlay failure cannot alter runtime control.

The recorder, future CLI facade, and GUI consume immutable read contracts.
Readers may
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
review-only and never automatically active. The recorder imports no runtime, safety, task,
input, login, or Arduino authority.

## Frontend target

The frontend facade exposes only high-level lifecycle and read contracts: list
supported tasks/definitions, inspect and validate profile fields, start, pause,
request safe stop, read `EngineFrame`, read statistics/blockers, and begin/end
demonstration capture. It delegates all task, safety, input, and verification
logic to the engine.

## Vision and LLM boundaries

RuneLite remains authoritative for identity and semantic state. A future
`VisionEvidence` record contains capture time, exact crop/transform,
model/version, class/confidence, bounds or mask, and occlusion/image-quality
status. Vision may supplement or veto an unsafe condition, or propose a point
inside API-confirmed geometry; it may not overwrite session, tick, player,
inventory, entity, menu, or widget facts. No vision model dependency is part of
the active mission.

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

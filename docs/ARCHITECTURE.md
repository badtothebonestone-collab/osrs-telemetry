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

The architecture is being migrated in bounded checkpoints. Names below marked
**current** exist in the Phase 0 baseline; names marked **target** are governing
contracts for a later phase and must not be described as implemented early.

| Layer | Current baseline | Governing target |
|---|---|---|
| Sensor | atomic game-tick `SensorFrame` plus separately stamped client/menu evidence | preserve the same single source contract while adding no second endpoint |
| Observation | immutable source-coherent `Observation` | preserve the same single truth for task-neutral seams |
| Task | concrete `WoodcutBankTask` | minimal task protocol with explicit task-specific FSM implementations |
| Decision | `Action` + `Verification` | task-neutral `ActionIntent` + `VerificationSpec` |
| Safety | one `SafetyGate` | engine invariants separated from typed task constraints |
| Input | `ArduinoActionInterface` and login helper own sessions | one `InputCoordinator`, separate pointer policy, internal transport methods |
| Verification | string outcome from one `Verifier` | stable typed `Outcome` |
| Diagnostics | CLI result dictionaries and traces | one immutable `EngineFrame` |
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

## Current baseline implementation

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

### Observation

`osrs_bot.observation.ObservationClient` sends one canonical snapshot request
and produces one immutable `Observation`:

```text
player, location, plane, inventory, nearby_objects, menus, widgets,
game_state, source timestamp, assembly timestamp, frame identity, tick
```

Source coherence, freshness, canvas bounds, warnings, and missing capabilities
travel with the same object because they determine whether any action is safe. Object census
rows are deduplicated by stable object key. Canvas coordinates are converted to
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

The two routes are fixed tuples of walk targets and staircase interactions.
Only the current walk target is requested from RuneLite. Missing or temporarily
non-actionable projection evidence waits without input; a present labeled
projection with contradictory identity blocks. No planner substitutes a route.

Staircases accept a live direct `Climb-up`/`Climb-down` action when it is the
default. If the live default is generic `Climb`, the explicit `STAIR_DIALOGUE`
state selects exactly one matching numbered up/down option and verifies the
plane change.

The task emits an `Action` and a `Verification` specification. It never sends
the action and never decides whether its own verification passed.

### Safety and action

`SafetyGate` checks the source tick, scene freshness, target identity, geometry,
screen bounds, widget state, and all-log deposit constraint before movement.

`ArduinoActionInterface` then:

1. connects and arms the Arduino;
2. constrains movement to the observed RuneLite canvas;
3. moves to the exact observed screen point;
4. fetches a fresh observation;
5. requires a newer menu sample whose top/default entry, scene parameters, and
   pointer position match the intended target;
6. when the exact action is a unique lower context entry, opens the menu,
   derives that row from RuneLite menu geometry, moves to it, revalidates the
   fresh open-menu sample and pointer, and clicks it once;
7. otherwise clicks the exact default entry once; and
8. runs `STOP_ALL`, `DISARM`, and close in a `finally` block.

There is no software-input fallback.

### Saved-session login assistance

`run.cmd login COMx` is a bounded helper beside the task engine, not a second
planner or recovery framework. It recognizes only the retained Play Now and
Click here to play visual templates plus the narrowly bounded historical
idle-disconnect OK geometry, binds the exact telemetry process and RuneLite
client window, revalidates the prompt after moving the Arduino mouse, and
verifies a telemetry transition afterward. It never types text. Continue,
credential, MFA, unknown, and ambiguous surfaces fail closed.

### Verification and runtime

`Verifier` evaluates only observations later than the action tick and fails at
the declared tick deadline. The runtime adds a wall-clock and observation bound
so a frozen client cannot wait forever.

Walk verification passes after authoritative movement closer or arrival. The
task then waits until player location is stable for four game ticks before it
advances the waypoint, preventing repeated clicks while pathing.

When the bank-close widget lacks usable geometry, the task may emit only the
exact Escape close intent and only when RuneLite explicitly reports keyboard
close support. The normal post-action bank-closed verification still applies.

`python -m osrs_bot task` stops at the first proposal. `--execute` is the only
task mode that constructs an Arduino backend. The runtime stops on completion,
block, transport failure, verification failure, or a configured bound.

### Golden replay

`run.cmd replay` executes the sanitized fixture in
`tests/fixtures/golden_lumbridge_cycle.json`. It freezes the final task route,
action kinds, typed verification sequence, and terminal cycle state. Its
provenance records hashes of the bounded live component traces and explicitly
states that those traces were stitched and do not contain complete raw sensor
or SafetyGate evidence.

## EngineFrame and diagnostics target

The real runtime will publish one immutable `EngineFrame` containing task/state,
definition/profile IDs, route progress, selected target identity and geometry,
eligible and rejected candidates with codes, ordered safety checks, pending and
last verification, typed outcome, execution receipt, final cleanup state, and
current blocker.

The recorder, passive overlay, CLI, and future GUI consume this exact frame.
They may format or filter it but may not reselect a target, recalculate safety,
mutate the FSM, import Arduino control, or authorize input. The overlay is
click-through, non-focusable, and optional.

## Demonstration and frontend targets

Manual demonstration recording is a read-only evidence path. It synchronizes
RuneLite facts, pointer/events visible to the process, before/after observations,
optional bounded images, annotations, hashes, and provenance into portable JSONL
plus a manifest and timeline. Inspection may suggest definition or fixture
changes, but raw coordinates are never replayed and suggestions never activate
without normal review, tests, and safety proof.

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

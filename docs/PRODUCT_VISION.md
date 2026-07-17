# Product Vision

## Product

This repository builds a small, modular, OSRS-specific automation engine. It
turns authoritative RuneLite observations into bounded, verified actions sent
through one Arduino HID path.

The active product is a task platform, not a one-off woodcut script. One generic
`GatherBankTask` executes immutable definitions through the same application,
runtime, safety, input, verification, and diagnostic owners. The first catalog
contains:

- Lumbridge west ordinary Trees -> Lumbridge Castle bank -> return; and
- Lumbridge Swamp East copper -> Lumbridge Castle bank -> return.

The woodcut definition remains the proven regression baseline. The copper IDs,
item IDs, equipment choices, and mine anchor are upstream-derived; its authored
surface route is deterministic but is not yet live-proven.

This milestone intentionally supersedes the older one-task/one-definition
product prohibition. It preserves every safety invariant and the single control
spine.

## Intended user experience

The application should let an operator:

1. launch one application;
2. choose a supported task family and task/site definition;
3. inspect required capabilities, resource, area, equipment, bank, route,
   provenance, and known limitations before starting;
4. choose bounded lifecycle options, including a scheduled UTC start and one or
   more OR-composed stop conditions;
5. start, pause, inspect, and safely stop the engine;
6. see exactly what the engine observes, targets, rejects, executes, verifies,
   recovers, reconciles, and blocks; and
7. author and validate a strict immutable definition without creating a new
   runtime or bypassing safety.

The frontend stays thin. Task logic, safety, RuneLite parsing, Arduino control,
verification, recovery, and mutable task state never belong in GUI code.

## Product boundaries

This product is not:

- a general game-agent framework or generic AI planner;
- a behavior tree, scripting language, or executable task DSL;
- a knowledge fabric, autonomous learned policy, or anti-detection system;
- an LLM-controlled runtime;
- a dynamic plugin marketplace; or
- a second telemetry, safety, input, verification, or diagnostic path.

The strict JSON authoring schema represents typed immutable facts and bounded
policies. It cannot encode arbitrary control flow. Each supported task family
retains an explicit OSRS-specific FSM.

## One engine spine

All supported definitions use this path:

```text
RuneLite SensorFrame -> Observation -> EngineApplication -> TaskRuntime
  -> GatherBankTask -> SafetyGate -> InputCoordinator -> Arduino
  -> Verifier -> GatherBankTask -> EngineFrame
```

There is one observation truth, one composition root, one runtime orchestrator,
one active task FSM, one safety gate, one Arduino session owner, one typed
verification path, and one immutable diagnostic truth. Adding a definition adds
facts, not another control system.

## Extension model

### Profile

A profile chooses one definition plus bounded lifecycle options. Stop conditions
are OR-composed: cycles, gathered item quantity, inventories banked, inventory
full, elapsed duration, or absolute UTC stop. A profile may also request a UTC
start, a lower action cap, and fresh restart reconciliation. At least one stop
condition is required. Profiles never contain safety-off switches.

### Task/site definition

An immutable versioned definition owns OSRS facts and task-owned bounded policy:
resource and item IDs, interactions, work areas, bank and route anchors,
mandatory transitions, inventory/equipment predicates, target selection,
lifecycle ceilings, recovery limits, verification expectations, capabilities,
and provenance.

Definitions negotiate required capabilities against the one runtime. Unknown or
unsupported capabilities fail before execution. Fallback banks, withdrawal and
resupply, automatic equipment management, and production NPC interaction
geometry are explicitly unsupported in the current gathering runtime.

### Runtime configuration

Machine/session configuration owns endpoint, Arduino port, polling, hard action/
observation/runtime limits, and one engine behavior policy. A profile can only
narrow applicable limits. Definitions do not own transport or weaken runtime
ceilings.

### Engine invariants

Non-overridable rules include coherent source freshness, loaded-scene proof,
PID/session/focus binding, exact identity, canvas and target geometry, hover/menu
proof, bank-PIN refusal, later verification, bounded execution, and
authoritative cleanup. Neither a profile nor a definition may weaken them.

## Truth and control

RuneLite API facts remain authoritative for session/ticks, player location and
plane, inventory and equipment, object identity/actions, menus, widgets,
interfaces, skills, and combat values. Equipment is a tick-aligned core fact in
the existing sensor/Observation path. Missing legacy equipment evidence remains
unknown and cannot authorize a definition that requires a tool.

Vision may later supplement or veto an unsafe visual condition, or propose a
point inside API-confirmed geometry. It may never overwrite authoritative API
identity, state, session, tick, inventory/equipment, menu, or widget facts.

All automated input, including saved-session login assistance, flows through one
`InputCoordinator`. The Arduino transport is private; there is no software-input
gameplay fallback. Exact post-move revalidation, later typed verification, bank-
PIN refusal, and `STOP_ALL -> DISARM -> STATUS` cleanup remain mandatory.

## Lifecycle, recovery, and restart

Scheduled start waits without creating input authority. Stop conditions are
checked at safe FSM boundaries; if the bank is open, normal verified close is
completed before terminal completion. Pause and safe stop remain cooperative,
and runtime ceilings remain authoritative.

Resource no-yield retry, target-continuity omissions, temporarily unavailable
bank evidence, and restart reconciliation are bounded task-owned policy.
Recovery cannot be inferred by the runtime or GUI from a diagnostic string.

On restart, never restore pending verification, old coordinates/clickboxes,
source ticks, menu samples, target locks, session-bound geometry, or armed input
state. Reobserve, rebind, and reconcile only from current authoritative evidence
and exact definition areas/routes. Historical route completion earns no new
cycle credit.

## Diagnostics and evidence

The runtime publishes one immutable `EngineFrame` containing task/binding/
lifecycle state, route and target evidence, equipment truth, ordered safety
checks, pending and last verification, execution receipt, recovery/reconciliation
status, cleanup, and blockers. The application facade, CLI, GUI, overlay, and
recorders consume that truth; they do not recreate it or authorize actions.

Manual demonstrations remain append-only, read-only evidence. They may suggest a
reviewed definition/fixture update but never replay coordinates or activate
task data automatically.

## Authoring experience

The task authoring tool validates exact `osrs_bot.task_definition.v1` JSON,
inspects a concise definition summary, explains supported/unsupported
capabilities, and emits a complete but deliberately non-runnable scaffold.
Unknown/missing fields, ambiguous scalar/array shapes, duplicates, inconsistent
routes/planes/anchors, unsafe deposit-all retention, equipment-capability
mismatch, unsupported capabilities, and unarmed scaffolds fail clearly.

Validated external definitions use the same immutable `TaskSiteDefinition`
contract. An operator may explicitly supply one to the foreground facade for
schema inspection, profile validation, dry run, or opt-in Arduino execution.
The file is revalidated and bound directly to the same `GatherBankTask`; it is
not installed or advertised in the built-in GUI/catalog. Validation alone sends
no input, and `--execute` retains every normal runtime and safety gate.

## Future families

Fishing requires production NPC interaction geometry; the demonstration-only
NPC census is not sufficient. Combat requires explicit combat-state, health,
food, prayer, equipment, targeting, loot, escape, and resupply capabilities.
Quest work requires a quest-state provider, versioned step preconditions, item
handling, dialogue/travel orchestration, and bounded recovery.

QuestHelper may later supply pinned read-only step metadata and the OSRS Wiki
may supply pinned, versioned knowledge and provenance. They cannot be live
control authority or bypass the FSM, `SafetyGate`, `InputCoordinator`, or
`Verifier`.

## Current success criterion

The task-platform milestone succeeds when woodcutting and copper mining run
through the same spine; definition/profile/capability, schedule/stop/restart,
equipment, authoring, recovery, and diagnostic contracts are adversarially
tested; existing replay and complete Python/Java gates pass; measurements and
limitations are retained; and current live availability is reported truthfully.

No live mining route, current woodcut cycle, Arduino gameplay action, or firmware
flash is implied by an offline gate.

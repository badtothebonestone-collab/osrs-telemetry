# Task Platform

## Milestone boundary

This milestone turns the proven Lumbridge woodcut cycle into a small,
task-agnostic OSRS automation platform. It deliberately supersedes the older
one-task/one-definition prohibition in `AGENTS.md`, `PLANS.md`, and the prior
status documents. It does **not** supersede any safety invariant: one coherent
observation, exact identity and geometry, bank-PIN refusal, Arduino-only
production input, typed verification, bounded execution, and authoritative
cleanup remain mandatory.

The platform is OSRS-specific. It is not a general planner, behavior-tree
runtime, task DSL, learned policy, dynamic plugin loader, or LLM-controlled
action surface.

## One control spine

Every supported task uses the same control path:

```text
RuneLite SensorFrame -> immutable Observation
  -> EngineApplication -> TaskRuntime -> GatherBankTask
  -> Action + VerificationSpec -> SafetyGate
  -> InputCoordinator -> Arduino HID
  -> fresh Observation -> Verifier -> typed Outcome
  -> GatherBankTask -> immutable EngineFrame
```

There is no mining runtime beside the woodcut runtime. `EngineApplication`
selects a validated definition and constructs the same `GatherBankTask` and
`TaskRuntime`. `SafetyGate`, `InputCoordinator`, the private Arduino transport,
`Verifier`, and `EngineFrame` remain single owners.

## Definition model and capability negotiation

An immutable `TaskSiteDefinition` supplies facts and bounded policy to the
generic gathering FSM:

- task type and required capabilities;
- resource identity, action, produced items, and work area;
- preferred bank and typed—but currently unsupported—fallback banks;
- fixed outbound/return routes and mandatory object transitions;
- inventory, equipment, target-selection, lifecycle, navigation, recovery, and
  verification policy; and
- versioned provenance and limitations.

Binding computes `unsupported_capabilities` as the definition's requirements
minus the runtime-supported set. A definition that asks for unsupported
behavior is rejected before a run. This is capability negotiation, not a
permission switch: supported definitions can narrow behavior but cannot weaken
engine invariants.

The active gathering runtime supports game-object interaction, fixed-route
navigation, object transitions, deposit-all banking, target continuity, camera
acquisition, equipment observation, scheduled start, composable stop
conditions, and restart reconciliation.

The following typed requests are intentionally unsupported in this milestone:

- fallback-bank selection;
- bank withdrawal and resupply;
- automatic equipment management; and
- NPC interaction geometry.

The types exist so unsupported definitions fail clearly. They are not partially
implemented features.

The built-in catalog is not the only explicit CLI input. A runnable strict JSON
gathering definition may be supplied with `--definition-file` to profile-schema,
profile-validation, dry-run, or opt-in execute commands. It is decoded before
binding, must use only supported capabilities, and must have an exact matching
profile definition ID. It then uses this same spine for one process; it is not
installed or advertised in the built-in GUI/catalog. File validation alone
sends no input, and execute mode still requires every Arduino/safety/verification
gate.

## Built-in gathering definitions

### Woodcutting regression baseline

`lumbridge_west_trees_v1` retains the proven ordinary Tree (`1276`) and Logs
(`1511`) cycle, the Lumbridge Castle bank booth (`18491`), its fixed routes and
stair transitions, and the historical golden replay. This definition remains
the regression baseline.

### Copper mining

`lumbridge_swamp_copper_v1` selects Copper rocks (`10943`, `11161`) with `Mine`,
produces Copper ore (`436`), starts at the RuneLite Lumbridge Swamp East mining
anchor `(3226,3146,0)`, and uses the same Lumbridge Castle bank and staircase
objects. It requires authoritative equipment evidence for one supported
pickaxe; unknown or missing equipment waits or blocks before a mining action.
The task never equips, withdraws, or resupplies a pickaxe.

The object IDs, item IDs, pickaxe IDs, and mine anchor are derived from pinned
RuneLite generated/constants sources whose URLs and SHA-256 values are stored in
the definition provenance. The surface route between the swamp and castle is
authored and deterministic, but it has **not** been live-replayed in this
checkout. It must not be presented as proven gameplay navigation until a fresh
loaded-scene rehearsal validates it.

Fishing is not a built-in definition. Production observation currently has
bounded game-object projection and safety authority; the existing NPC census is
demonstration-only and does not provide production NPC interaction geometry.
Adding fishing therefore requires that capability and its safety/verification
tests rather than treating fishing spots as game objects.

## Equipment is a core fact

RuneLite captures the equipment container in the same tick-aligned inventory
sensor fact and the Python adapter publishes it as immutable
`EquipmentObservation`. It is not a second endpoint or a task-specific cache.
Legacy fixtures that omit equipment remain readable as `known=false`; they
cannot authorize a definition that requires equipment.

Equipment policies identify acceptable equipped item IDs. Inventory fallback
and automatic equip are disabled unless a future definition and runtime both
negotiate the corresponding capabilities. This prevents deposit-all from
silently banking a required tool and prevents an unknown equipment state from
being treated as permission.

## Run lifecycle and stop composition

Profiles select a definition and bounded user goals. Supported stop conditions
are composed with OR semantics: the first satisfied condition completes the
run at a safe task boundary. Current conditions are cycles, gathered item
quantity, inventories banked, inventory full, elapsed duration, and an absolute
UTC stop. A profile may also set a UTC start, an action cap no greater than the
engine/definition ceilings, and whether fresh restart reconciliation is
allowed. At least one stop condition is required.

The application waits for a scheduled start without opening a new input path.
Pause and safe stop remain cooperative. If completion is detected while the
bank interface is open, the FSM closes it through the normal verified path
before declaring completion. Effective runtime and action caps are the minimum
of the operator configuration, definition lifecycle, and any profile limit, so
definitions and profiles can narrow but never enlarge operator ceilings. The
operator-owned observation cap is unchanged.

## Restart and recovery

Restart never restores pending verification, old coordinates, target locks,
menu samples, source ticks, session-bound geometry, or armed input state. The
generic task reconciles only from a fresh coherent observation and the selected
definition's exact work area, bank, inventory predicates, and fixed-route
anchors. Historical route completion grants no cycle credit.

Resource no-yield retry, incomplete target continuity, and temporarily
unavailable bank evidence are task-owned bounded recovery policies. The runtime
or diagnostics do not interpret reason strings to invent transitions. Any
activation ambiguity, unsupported capability, identity/geometry drift,
reported bank PIN, missing cleanup, or exhausted recovery bound fails closed.

## Adjacent hardening improvements

Three adjacent improvements are part of the platform measurement and regression
scope:

1. `Task.apply_verification()` returns a typed task-owned
   `VerificationDisposition`. `TaskRuntime` accepts only `RECOVERED` while the
   task remains running, or a blocked task snapshot. It no longer couples its
   recovery branch to gathering phases or verifier failure kinds. EngineFrame
   retains the disposition beside the exact verification result.
2. Successful item verification carries the exact positive
   `item_quantity_delta` calculated by `Verifier`. The task's gathered-item
   counter and EngineFrame metrics consume that typed delta, so multi-item gains
   are not silently reduced to one by the normal current verifier path. Legacy
   outcomes without the additive field remain readable under their conservative
   compatibility behavior.
3. Missing exact bank targets receive only the selected definition's bounded
   `max_bank_unavailable_frames` re-observation budget. Finding/opening the bank
   resets the counter; exhausting it blocks with no fallback-bank guess. This is
   separate from restart reconciliation and does not treat absence diagnostics
   as authority.

The retained deterministic gate and measured before/after results are recorded
in `docs/ENGINE_STATUS.md`. This contract does not turn synthetic evidence into
a live-production claim.

## Future task boundaries

Combat is not implemented. A combat definition must negotiate authoritative
combat-state observation, health, food, prayer, equipment, target-selection,
loot, escape, and resupply policies before activation. A combat FSM must still
emit typed actions and verification through the same safety/input spine.

Quest automation is not implemented. It requires an authoritative quest-state
provider, versioned step preconditions, bounded item handling, dialogue and
travel orchestration, and explicit recovery. QuestHelper may be a read-only
source of versioned step metadata; the OSRS Wiki may be pinned, versioned
knowledge or provenance. Neither may directly authorize input, replace live
RuneLite facts, or bypass the task FSM, `SafetyGate`, `InputCoordinator`, or
`Verifier`.

## Validation truth

The consolidation base passed its retained replay, full Python/Java suites,
static input-boundary checks, firmware protocol harness, and bounded synthetic
telemetry soak before this milestone. Those results are baseline evidence, not
proof of the new task platform.

The current platform's deterministic, adversarial, replay, full-suite, input,
firmware, authoring, soak, publication-hygiene, and diff gates pass and are
recorded in `docs/ENGINE_STATUS.md`. At milestone start the telemetry listeners
were absent and the read-only observation attempt was refused before input. A
follow-up current-build continuation subsequently proved a loaded/fresh/
coherent woodcut scene, exact typed world and requested-tile provenance
handoffs, and one Arduino `CAMERA_HOLD left 327` action with changed-pose
verification. Its final `STOP_ALL -> DISARM -> STATUS` receipt proves disarmed
firmware, zero held input and command errors, closed ledger/backend, and a
released COM6 lease. The exact v2 firmware was already installed; this
continuation did not flash it. This bounded component evidence is not a live
mining route, ordinary target interaction, bank flow, or current woodcut cycle.

# Definitions, Profiles, and Runtime Configuration

The task platform separates immutable OSRS facts, user lifecycle choices,
machine/session configuration, and non-overridable engine invariants. These
categories are validated independently and never collapse into one permissive
configuration blob.

The active milestone intentionally replaces the former one-definition limit.
Both built-in definitions feed the same explicit `GatherBankTask`; neither
creates a new runtime, safety gate, input path, verifier, or observation path.

## Immutable task/site definitions

`TaskSiteDefinition` contains:

- stable definition ID, display name, version, and task type;
- required runtime capabilities;
- resource ID, interaction kind, exact object name/action/IDs, produced items,
  and typed operating area/planes;
- preferred bank and typed fallback-bank list;
- exact fixed routes, classified waypoints, object transitions, anchors,
  arrival radii, and plane changes;
- allowed, deposited, retained, and withdrawal inventory predicates;
- required/permitted equipment policy;
- target-selection, lifecycle, recovery, navigation, and verification policy;
  and
- versioned provenance with explicit evidence and limitations.

Construction rejects malformed or mutable collection shapes, booleans used as
integers, invalid/duplicate IDs, inconsistent route anchors or planes, globally
duplicate task-owned identifiers, unsafe inventory/deposit relationships, and
policy/capability mismatches.

The definition supplies facts and bounds. `GatherBankTask` owns the mutable FSM,
target locks, route progress, gathered/banked counters, recovery counters, and
restart reconciliation. The definition is not an executable transition
language.

## Capability negotiation

A definition's `capabilities` are compared to
`RUNTIME_SUPPORTED_CAPABILITIES` during binding. Any remainder appears as
`unsupported_capabilities`, and the binding fails before a run.

The current gathering runtime supports:

- game-object interaction;
- fixed-route navigation and object route transitions;
- deposit-all banking;
- target continuity and camera acquisition;
- equipment observation;
- scheduled start and composable stop conditions; and
- fresh restart reconciliation.

The type system also names capabilities that are not implemented. Fallback-bank
selection, withdrawal/resupply, automatic equipment management, and NPC
interaction geometry are rejected. A JSON definition cannot enable them merely
by naming them.

## Built-in registry

The immutable built-in registry currently contains two entries.

### `lumbridge_west_trees_v1`

This is the preserved regression definition:

- ordinary Tree object `1276`, action `Chop down`;
- Logs item `1511`;
- Lumbridge west work area;
- Lumbridge Castle bank booth `18491`;
- fixed outbound and return routes with exact staircase transitions; and
- historical fixture provenance and limitations.

Its golden replay remains the strongest deterministic end-to-end semantic
regression. Historical physical evidence retains the caveats described in
`docs/ENGINE_STATUS.md`.

### `lumbridge_swamp_copper_v1`

This definition adds copper mining through the same gathering FSM:

- Copper rocks `10943` and `11161`, name `Rocks`, action `Mine`;
- Copper ore `436`;
- RuneLite Lumbridge Swamp East anchor `(3226,3146,0)`;
- Lumbridge Castle bank booth `18491` and the existing staircase identities;
  and
- an equipped-tool requirement accepting the enumerated bronze through crystal
  pickaxe item IDs in the definition.

The object and item constants and mining-site anchor are derived from pinned
RuneLite sources. Their SHA-256 values are embedded in definition provenance:

- generated `ObjectID.java`:
  `1eb19a73b335c7f3b4e470ad38eed2232684f13fcf3172f46d79dc2223a9c321`;
- generated `ItemID.java`:
  `d524a9e2e7ca4255b4499484d1e4bc8be637c79e9494b464c5cb16e01c358c6e`;
  and
- `MiningSiteLocation.java`:
  `fb3748f83b6a98bdc6fbaa917e256a82c9ce1f39588acd79f01eb598e021fa2d`.

The swamp-to-castle surface waypoints are authored and deterministic. They have
not been live-replayed in this checkout, so catalog presence is not a live route
claim. A fresh loaded-scene rehearsal must validate them before production use
is described as proven.

Fishing is deliberately absent. The existing NPC census is demonstration-only;
the production control path does not yet have authoritative NPC projection,
interaction geometry, completeness, and safety proof.

## Equipment observation

RuneLite captures equipment beside inventory in the same atomic sensor fact.
The Python adapter exposes one immutable `EquipmentObservation` with known/
unknown state, slot counts, and exact items. No second endpoint or task-specific
cache exists.

Definitions with required equipment must negotiate `equipment_observation`.
Unknown equipment waits; a known set containing none of the allowed tools
blocks. The current runtime neither equips nor withdraws an item and does not
use an inventory tool fallback for the mining definition. That preserves
deposit-all safety.

Legacy fixtures without equipment remain readable as unknown, but they cannot
authorize copper mining.

## Profile

`Profile` contains strict user-selectable lifecycle choices:

- `profileId` and `definitionId`;
- nullable cycle, item-quantity, inventories-banked, duration, and absolute-time
  goals;
- inventory-full completion;
- optional UTC scheduled start;
- optional lower action cap; and
- whether to perform fresh restart reconciliation.

At least one stop condition is required. Stop conditions use OR composition:
the first satisfied condition completes the run at a safe task boundary. Goal
values are bounded by the selected definition's lifecycle policy. Independent
operator `RuntimeConfig` limits remain authoritative: effective action and
runtime caps use the most restrictive applicable value, so a stricter runtime
cap may preempt a longer profile goal and is never expanded to fit it. The
observation cap remains operator-owned.

Profile start/stop timestamps are normalized to UTC and must be ordered.
Unknown/missing fields and invalid scalar types fail clearly. Profiles contain
no freshness, focus, PID/session, geometry, menu, PIN, verification, transport,
or cleanup switches.

`profile_contract(definition)` publishes the exact frontend-safe shape as
`osrs_profile_contract.v2`. `validate_profile_values()` uses the same strict
decoder and built-in binder used by `EngineApplication.start()`.

Inspect the selected profile contract or validate values through the public
facade:

```powershell
.\run.cmd app catalog
.\run.cmd app profile-schema --definition-id lumbridge_swamp_copper_v1
.\run.cmd app validate-profile --definition-id lumbridge_swamp_copper_v1
```

Lifecycle flags are also available to `validate-profile` and `run`, including
`--no-cycle-goal`, `--item-quantity-goal`, `--inventories-banked-goal`,
`--duration-seconds`, `--start-at-utc`, `--stop-at-utc`,
`--stop-when-inventory-full`, `--profile-max-actions`, and
`--no-reconcile-on-start`.

## Strict authoring boundary

The external envelope schema is exactly `osrs_bot.task_definition.v1`:

```json
{
  "schema_version": "osrs_bot.task_definition.v1",
  "runnable": true,
  "definition": {}
}
```

Every nested object has an exact field set. Arrays remain arrays; scalar-or-
array shortcuts are rejected. Set-like arrays reject duplicates. Validation
checks cross-object IDs, route/anchor/plane consistency, deposit-all retention,
equipment capability, required gathering capabilities, known unsupported
capabilities, provenance, and all typed constructor invariants. Resource and
bank selectors each accept at most the observation adapter's 32 priority object
IDs, so an authoring-valid definition is also requestable by the runtime.

The authoring CLI is read-only except for an explicitly requested scaffold
output:

```powershell
python -m osrs_bot.task_authoring explain
python -m osrs_bot.task_authoring validate .\examples\task_definitions\lumbridge_west_trees_v1.json
python -m osrs_bot.task_authoring inspect .\examples\task_definitions\lumbridge_swamp_copper_v1.json
python -m osrs_bot.task_authoring scaffold --output .\my_task.json
```

`--json` is supported by `validate`, `inspect`, and `explain`. `scaffold` emits a
complete-shape document with `runnable:false`, invalid placeholder IDs, and
placeholder provenance. It is intentionally rejected until the author replaces
the placeholders, supplies evidence, and explicitly sets `runnable:true`.
`--force` is required to replace an existing scaffold path.

The committed examples under `examples/task_definitions/` mirror the built-in
woodcut and copper definitions. `unsupported_npc_fishing_v1.json` is deliberately
invalid and demonstrates fail-closed `npc_interaction_geometry` negotiation.
Successful validation does not automatically install an external definition in
the application catalog. An operator can explicitly pass a runnable gathering
file to the foreground facade:

```powershell
.\run.cmd app profile-schema --definition-file .\my_task.json
.\run.cmd app validate-profile --definition-file .\my_task.json
.\run.cmd app run --definition-file .\my_task.json
.\run.cmd app run --definition-file .\my_task.json --execute --arduino-port COM6
```

The strict loader runs before schema, profile, or runtime construction. Only
`TaskType.GATHERING` and runtime-supported capabilities bind. The profile's
`definitionId` is taken from the file; an explicitly repeated `--definition-id`
must match exactly. An external profile contract advertises only that file ID.
Its `cycleGoal` default is `null` when the definition does not support cycle
stops, so another supported stop condition must be supplied.
The file is bound directly to the same `GatherBankTask` for that process and is
not persisted or advertised in the built-in GUI/catalog. Validation and dry run
send no production input; `--execute` retains Arduino-only input and all normal
safety, verification, bounds, bank-PIN, and cleanup gates.

## Runtime configuration

`RuntimeConfig` separately owns endpoint/token/request timeout, Arduino port,
polling, observation/action/runtime/verification limits, and behavior policy.
Values are immutable, finite, positive, and engine-capped. Execute mode requires
an Arduino port. Configuration contains no object facts, route transitions, or
safety-off switches.

Effective action and runtime caps are the minimum of the operator-owned
`RuntimeConfig`, definition lifecycle limits, and any profile limit. A profile
or definition can therefore shorten a run, but can never enlarge the operator
ceiling to accommodate a later schedule or longer lifecycle. The observation
cap remains entirely operator-owned. These limits do not weaken any per-action
safety or verification rule.

## Restart and recovery

`reconcileOnStart` never restores historical task state. It permits the task to
derive a new state from one fresh current observation and the selected
definition's exact work area, bank area, inventory predicates, and route
anchors. Pending verification, target locks, source/menu ticks, coordinates,
session identity, input state, and prior cycle credit are never restored.

Recovery budgets for resource no-yield, unavailable bank evidence, and
incomplete target continuity are task-owned and bounded by `RecoveryPolicy`.
`Task.apply_verification()` returns a typed recovery disposition; runtime does
not inspect gathering phases or verifier failure kinds. The bank-unavailable
counter resets when the exact bank returns or is open and blocks on exhaustion.
Diagnostics and runtime do not interpret strings to add an untyped retry.

Verifier item outcomes carry exact quantity deltas into gathered-item counters
and EngineFrame metrics. This preserves multi-item progress and avoids a normal-
path `+1` assumption while keeping legacy additive outcomes readable.

See `docs/TASK_PLATFORM.md` for the end-to-end platform and future capability
boundaries.

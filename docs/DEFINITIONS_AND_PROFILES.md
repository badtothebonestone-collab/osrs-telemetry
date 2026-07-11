# Definitions, Profiles, and Runtime Configuration

Phase 4 implements exactly one built-in task/site definition and one validated
default profile. These are typed Python values committed with the engine, not a
loader, registry, task language, or external configuration schema.

## Built-in definition

`LUMBRIDGE_WEST_TREES_V1` is an immutable, versioned graph of the facts proven
by the golden cycle:

- ordinary Tree selector and produced item ID;
- supported work-area anchor and radius;
- exact Lumbridge Castle bank selector, anchor, plane, and interaction radius;
- fixed outbound and return route steps, staircase identities/actions, and
  expected plane transitions;
- permitted/deposited inventory item predicates;
- action/resource deadlines and route-settle requirement;
- exact route-dialogue expectations; and
- baseline fixture identity, evidence hashes, and limitations.

Construction validates every nested value. Mutable collection shapes,
booleans masquerading as integers, invalid IDs/planes/radii/deadlines, duplicate
step IDs, incoherent route planes/anchors, and inventory predicates excluding
the produced item are rejected.

The definition supplies facts only. `WoodcutBankTask` still owns all mutable
progress, target selection, and explicit FSM transitions. There is no generic
navigator or data-driven transition interpreter.

## Profile

The current `Profile` contains only:

- a profile ID;
- the selected definition ID; and
- a cycle goal.

The default binding selects `lumbridge_west_trees_v1` and exactly one cycle.
Unknown definitions, malformed identifiers, and any other goal fail clearly.
There is no file loader yet. A profile has no freshness, focus, PID/session,
geometry, menu, PIN, verification, input, cleanup, or runtime-limit switches.

## Runtime configuration

`RuntimeConfig` separately owns endpoint/token/request timeout, optional Arduino
port, polling, and observation/action/runtime/verification bounds. Values are
finite, positive, immutable, and capped by engine-owned maxima; execute mode
requires an Arduino port. It contains no task IDs, object facts, routes, or
safety switches.

## Neutral sensor facts

The canonical snapshot request now asks RuneLite for one neutral
`scene_object_census`. The adapter still understands the endpoint's supported
filtered diagnostic censuses, but the task authorizes resource, route, and bank
meaning only through exact selected-definition facts. Scene rows contain no
candidate/type/skill labels, and projection scheduling uses explicit request
state, distance, and stable identity rather than task hints. RuneLite candidate
hint flags are not authorization.

The committed golden fixture remains independent expected evidence. Tests
compare every definition route/provenance fact to it, then replay the explicit
FSM through the default binding. The fixture's previously truncated 62-character
bank-close trace digest was corrected from the retained trace's verified
64-character SHA-256.

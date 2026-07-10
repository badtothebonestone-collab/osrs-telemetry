# Architecture

## Sensor

The RuneLite plugin owns the only live telemetry cache. Its localhost service
exposes exactly three read-only routes:

- `GET /health`
- `GET /schema`
- `POST /snapshot`

The endpoint does not apply configuration or accept gameplay commands.

## Observation

`osrs_bot.observation.ObservationClient` sends one canonical snapshot request
and produces one immutable `Observation`:

```text
player, location, plane, inventory, nearby_objects, menus, widgets,
game_state, timestamp, tick
```

Freshness, canvas bounds, warnings, and missing capabilities travel with the
same object because they determine whether any action is safe. Object census
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

## Task

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

## Safety and action

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

## Saved-session login assistance

`run.cmd login COMx` is a bounded helper beside the task engine, not a second
planner or recovery framework. It recognizes only the retained Play Now and
Click here to play visual templates plus the narrowly bounded historical
idle-disconnect OK geometry, binds the exact telemetry process and RuneLite
client window, revalidates the prompt after moving the Arduino mouse, and
verifies a telemetry transition afterward. It never types text. Continue,
credential, MFA, unknown, and ambiguous surfaces fail closed.

## Verification and runtime

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

## Golden replay

`run.cmd replay` executes the sanitized fixture in
`tests/fixtures/golden_lumbridge_cycle.json`. It freezes the final task route,
action kinds, typed verification sequence, and terminal cycle state. Its
provenance records hashes of the bounded live component traces and explicitly
states that those traces were stitched and do not contain complete raw sensor
or SafetyGate evidence.

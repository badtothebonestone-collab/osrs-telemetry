# Event Types

Events are compact JSON records with:

- `schemaVersion`
- `tickId`
- `eventSeq`
- `timestampUtc`
- `eventType`
- `payload`

## Categories

Combat:

- `HitsplatApplied`
- `ProjectileMoved`
- `GraphicsObjectCreated`
- `InteractingChanged`
- `AnimationChanged`
- `NpcDeath`

Inventory:

- `ItemContainerChanged`
- `ItemSpawned`
- `ItemDespawned`
- `ItemQuantityChanged`

UI:

- `WidgetLoaded`
- `WidgetClosed`
- `MenuOpened`

Var:

- `VarbitChanged`
- `VarClientIntChanged`
- `VarClientStrChanged`

Entity:

- `NpcSpawned`
- `NpcDespawned`
- `NpcChanged`
- `PlayerSpawned`
- `PlayerDespawned`
- `PlayerChanged`

Skills:

- `StatChanged`

World:

- `GameStateChanged`
- `OverheadTextChanged`

Unknown event types should be preserved and categorized as `unknown` by export
tools.

## Payload Notes

Actor payloads are compact and include actor type, index, id for NPCs, hashed
player name for players, world location when available, animation, health, and a
small interacting-target summary.

Menu telemetry is `MenuOpened` only. It captures menu affordances but does not
click, invoke, reorder, or mutate menu entries.

Var-client string values are truncated to avoid large raw payloads.

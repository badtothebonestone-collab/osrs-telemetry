# Telemetry Schema

Telemetry is stored as JSON Lines. Each line is one complete JSON object.

## Tick Records

Tick records are ordered by `tickId` and live in either:

```text
ticks.jsonl
ticks\ticks-*.jsonl
```

Required top-level fields:

- `schemaVersion`
- `tickId`
- `timestampUtc`

Common optional top-level fields:

- `gameState`
- `localPlayer`
- `inventory`
- `equipment`
- `skills`
- `npcs`
- `players`
- `widgets`
- `sceneObjects`
- `groundItems`
- `status`
- `activePrayers`
- `captureErrors`
- `writerQueueSize`
- `writerDroppedRecords`

`status` summarizes read-only local status such as run energy, weight, HP,
prayer, health ratio, and current interacting target.

`activePrayers` contains all known prayers with their varbit and active state.

`captureErrors` is normally empty. If a capture layer fails, the tick still gets
written and the failed layer name is listed here.

## Event Records

Event records live in either:

```text
events.jsonl
events\events-*.jsonl
```

Required top-level fields:

- `schemaVersion`
- `tickId`
- `timestampUtc`
- `eventType`

Common optional fields:

- `eventSeq`
- `payload`

`tickId` links each event to the latest game tick observed by the collector.
Events emitted between two game ticks will share the most recent `tickId`.

## Tick/Event Joins

Analysis tools should use `tickId` as the primary timeline key:

- Use ticks for periodic state.
- Use events for changes and high-frequency transitions.
- Join an event to the tick with the same `tickId` for local state context.

## Segment Consumption

Read segment files in sorted filename order. A single session's `tickId` values
should increase across segment boundaries. If following a live session, reopen
the newest segment only after a newer segment appears.

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
- `framePath`
- `frameCaptureStatus`
- `frameCaptureSource`
- `frameCaptureWarning`
- `captureErrors`
- `writerQueueSize`
- `writerDroppedRecords`

`status` summarizes read-only local status such as run energy, weight, HP,
prayer, health ratio, and current interacting target.

`activePrayers` contains all known prayers with their varbit and active state.

`captureErrors` is normally empty. If a capture layer fails, the tick still gets
written and the failed layer name is listed here.

`framePath` is a session-relative path to the captured frame for the tick, such
as `frames/frame-tick-00000001.jpg`. Frame files are retention-managed side
data, so the path may reference a file that has since expired.

`frameCaptureStatus` is one of:

- `QUEUED`: frame copied and queued for off-thread writing.
- `WRITTEN`: reserved for tools that post-process completed frame writes.
- `DISABLED`: frame capture disabled or interval invalid.
- `SKIPPED_INTERVAL`: tick did not match the configured screenshot interval.
- `DROPPED_QUEUE_FULL`: frame queue was full; tick was still written.
- `CAPTURE_FAILED`: frame capture failed; tick was still written.

`frameCaptureSource` identifies how the frame was captured:

- `RUNELITE_ONLY`: default. Captured from RuneLite's rendered frame image.
- `SCREEN_RECTANGLE`: opt-in Java `Robot` screen rectangle fallback.

`frameCaptureWarning` is normally absent. For `SCREEN_RECTANGLE`, tools should
show that overlapping windows may be captured because the fallback reads screen
pixels.

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

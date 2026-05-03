# Telemetry Schema

Telemetry is stored as JSON Lines. Each line is one complete JSON object.

## Tick Records

Tick records are ordered by `tickId`. The canonical current writer layout is:

```text
ticks\ticks-*.jsonl
```

Older read-only sessions may use legacy flat `ticks.jsonl`.

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

`framePath` is a session-relative path associated with the tick's requested
frame, such as `frames/frame-tick-00000001.jpg`. Frame files are
retention-managed side data, so the path may reference a file that has since
expired.

`frameCaptureStatus="QUEUED"` means frame capture/write was requested, not that
the file is guaranteed to exist yet. Frame writing is asynchronous, so tools may
briefly see `QUEUED` and `frameExists=false` for the newest active tick. Tools
should mark that as pending only inside their freshness grace window. If the
tick is older, or the session has moved on, a missing referenced frame should be
treated as expired/deleted side data rather than corrupt tick telemetry.

`frameCaptureStatus` is one of:

- `QUEUED`: frame capture/write requested.
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

## Frame Index Records

Frame timing diagnostics live in:

```text
frame_index.jsonl
```

Each line is a session-local sidecar record for one requested frame after the
collector knows the terminal frame state. These records are not required to
parse tick/event telemetry, but they are useful for diagnosing capture delay,
writer queue delay, encoding time, and dropped frames.

Common fields:

- `schemaVersion`
- `tickId`
- `framePath`
- `captureSource`
- `status`
- `requestedAtUtc`
- `capturedAtUtc`
- `enqueuedAtUtc`
- `writtenAtUtc`
- `captureLatencyMs`
- `queueLatencyMs`
- `writeLatencyMs`
- `totalLatencyMs`
- `width`
- `height`
- `sizeBytes`
- `droppedFrameCount`
- `error`

`status` is one of:

- `WRITTEN`: frame image was encoded and written.
- `DROPPED_QUEUE_FULL`: frame image was captured but not accepted by the frame
  writer queue.
- `CAPTURE_FAILED`: frame capture failed before queueing.
- `WRITE_FAILED`: frame image reached the writer but could not be written.
- `WRITE_REJECTED`: writer rejected an invalid frame path.

## Event Records

Event records live in the canonical current writer layout:

```text
events\events-*.jsonl
```

Older read-only sessions may use legacy flat `events.jsonl`.

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

## Generated Tool Outputs

`telemetry-viewer\latest_state.py` writes session-local generated cache files
under `latest\`. `telemetry-viewer\export_session.py` writes generated summaries
under `exports\`:

```text
latest\latest_tick.json
latest\latest_status.json
latest\latest_events.json
exports\session_index.json
exports\tick_summary.jsonl
exports\event_summary.jsonl
exports\frame_index_summary.jsonl
```

These outputs are derived from source session records. Exported frame fields
such as `frameExists`, `framePending`, and `frameExpiredOrMissing` are
point-in-time tool-derived values.

# Telemetry Folder Layout

Telemetry sessions are written under:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

Each session has a timestamp-like session id. The segmented layout below is the
canonical current writer output:

```text
sessions\<session_id>\
  manifest.json
  ticks\
    ticks-000001.jsonl
    ticks-000002.jsonl
  events\
    events-000001.jsonl
    events-000002.jsonl
  frames\
    frame-tick-00000001.jpg
    frame-tick-00000002.jpg
    frame-tick-00000003.jpg
  dictionaries\
    items.json
    npcs.json
    objects.json
  latest\
    latest_tick.json
    latest_status.json
    latest_events.json
  exports\
    session_index.json
    tick_summary.jsonl
    event_summary.jsonl
```

Older sessions may use the legacy flat layout:

```text
sessions\<session_id>\
  ticks.jsonl
  events.jsonl
```

Tools support both layouts. New writer output remains segmented; the flat files
are a read-only compatibility fallback.

Frame files are optional side data referenced by tick records. A tick remains
valid if its referenced frame has been deleted by retention cleanup.

## Rolling Segments

Ticks and events rotate independently. Segment names are zero-padded so lexical
sort order is also chronological order. Consumers should read all matching files
in sorted order:

```text
ticks\ticks-*.jsonl
events\events-*.jsonl
```

For live following, tail the newest tick segment and periodically check whether
a newer segment has appeared. When it appears, close the old file and begin
reading the new one.

## Manifest

`manifest.json` is the session index written by the collector. Important fields:

- `sessionId`: folder/session identifier.
- `startedAtUtc`: UTC session start timestamp.
- `endedAtUtc`: UTC shutdown timestamp when the session is closed.
- `schemaVersion`: schema version for telemetry records.
- `active`: true while the session is being written.
- `currentTickSegment`: session-relative path to the open tick segment.
- `currentEventSegment`: session-relative path to the open event segment.
- `tickSegmentIndex`: current tick segment number.
- `eventSegmentIndex`: current event segment number.
- `tickCount`: total tick records written by this session.
- `eventCount`: total event records written by this session.
- `droppedRecords`: records dropped because the writer queue was full.
- `frameCount`: frame files successfully written by this session.
- `droppedFrameCount`: frames dropped because the frame queue was full.
- `deletedFrameCount`: frame files deleted by frame-specific cleanup.
- `screenshotEveryTicks`: configured screenshot tick interval.
- `screenshotFormat`: configured frame file format, `jpg` or `png`.
- `maxFrameStorageMb`: active-session frame folder cleanup cap.
- `frameCleanupIntervalSeconds`: how often active frame cleanup runs.
- `frameCaptureMode`: configured capture mode, usually `RUNELITE_ONLY`.
- `allowScreenRectangleFallback`: whether screen-rectangle fallback is enabled.
- `lastUpdatedUtc`: UTC timestamp of the last manifest write.

## Dictionaries

Dictionaries map IDs to names discovered during collection:

- `items.json`: item id to item name.
- `npcs.json`: npc id to npc name.
- `objects.json`: object id to object name.

Tick and event records generally keep IDs compact and rely on dictionaries for
name lookup.

## Frames

Screenshot frames are requested from RuneLite's draw manager once per configured
game tick and written off the client thread:

```text
frames\frame-tick-00000001.jpg
frames\frame-tick-00000002.jpg
```

Tick records reference frames with a relative `framePath`, for example
`frames/frame-tick-00000001.jpg`. Frame cleanup may delete old files when
`maxFrameStorageMb` is exceeded. Consumers should treat a missing referenced
frame as expired frame data, not corrupt telemetry.

`framePath` means a frame was requested or associated with the tick.
`frameCaptureStatus="QUEUED"` means frame capture/write was requested. The frame
file may still be pending briefly while the asynchronous writer finishes. For a
newest active tick, `QUEUED` with `frameExists=false` inside the tool grace
window is usually pending, not missing. Older missing referenced frames usually
mean frame retention deleted the image.

`RUNELITE_ONLY` is the default capture mode. It uses RuneLite's rendered frame
image and follows the current RuneLite/game canvas size without reading random
desktop pixels.

`SCREEN_RECTANGLE` is an opt-in fallback. It uses Java `Robot` to capture the
current screen rectangle occupied by the RuneLite canvas. Because it reads
screen pixels, overlapping windows can appear in the frame. Tick records mark
this with `frameCaptureSource="SCREEN_RECTANGLE"` and a `frameCaptureWarning`.

Frame cleanup is separate from global retention and runs every
`frameCleanupIntervalSeconds`. It deletes oldest frame files first and does not
delete a frame currently being written.

## Latest-State Cache

`telemetry-viewer\latest_state.py` can follow the newest active session and
maintain a small generated cache for live consumers:

```text
latest\
  latest_tick.json
  latest_status.json
  latest_events.json
```

Writes are atomic: the tool writes a temporary file and replaces the final JSON
file after the write completes. Consumers can poll these files without parsing
the full session stream. The cache is derived from telemetry files only; it does
not interact with RuneLite.

Legacy flat sessions are readable by the general tools, but `latest_state.py`
follows newest active sessions and may not consider a legacy session active
unless manifest/active metadata exists.

## Exports

`telemetry-viewer\export_session.py` writes generated summaries under the
session:

```text
exports\
  session_index.json
  tick_summary.jsonl
  event_summary.jsonl
```

These files are derived outputs and can be regenerated from the source session
records.

# Telemetry Folder Layout

Telemetry sessions are written under:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

Each session has a timestamp-like session id and owns its own manifest, tick
segments, event segments, frame files, and dictionaries:

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
```

Older sessions may use the legacy flat layout:

```text
sessions\<session_id>\
  ticks.jsonl
  events.jsonl
```

Tools should support both layouts.

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
- `currentTickSegment`: relative path to the open tick segment.
- `currentEventSegment`: relative path to the open event segment.
- `tickSegmentIndex`: current tick segment number.
- `eventSegmentIndex`: current event segment number.
- `tickCount`: total tick records written by this session.
- `eventCount`: total event records written by this session.
- `droppedRecords`: records dropped because the writer queue was full.
- `frameCount`: frame files successfully written by this session.
- `droppedFrameCount`: frames dropped because the frame queue was full.
- `screenshotEveryTicks`: configured screenshot tick interval.
- `screenshotFormat`: configured frame file format, `jpg` or `png`.
- `maxFrameStorageMb`: active-session frame folder cleanup cap.
- `lastUpdatedUtc`: UTC timestamp of the last manifest write.

## Dictionaries

Dictionaries map IDs to names discovered during collection:

- `items.json`: item id to item name.
- `npcs.json`: npc id to npc name.
- `objects.json`: object id to object name.

Tick and event records generally keep IDs compact and rely on dictionaries for
name lookup.

## Frames

Screenshot frames are copied from RuneLite's canvas once per configured game
tick and written off the client thread:

```text
frames\frame-tick-00000001.jpg
frames\frame-tick-00000002.jpg
```

Tick records reference frames with a relative `framePath`, for example
`frames/frame-tick-00000001.jpg`. Frame cleanup may delete old files when
`maxFrameStorageMb` is exceeded. Consumers should treat a missing referenced
frame as expired frame data, not corrupt telemetry.

## Latest-State Cache

`telemetry-viewer\latest_state.py` can follow the newest active segmented
session and maintain a small cache for live consumers:

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

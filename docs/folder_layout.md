# Telemetry Folder Layout

Telemetry sessions are written under:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

Each session has a timestamp-like session id and owns its own manifest, tick
segments, event segments, and dictionaries:

```text
sessions\<session_id>\
  manifest.json
  ticks\
    ticks-000001.jsonl
    ticks-000002.jsonl
  events\
    events-000001.jsonl
    events-000002.jsonl
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
- `lastUpdatedUtc`: UTC timestamp of the last manifest write.

## Dictionaries

Dictionaries map IDs to names discovered during collection:

- `items.json`: item id to item name.
- `npcs.json`: npc id to npc name.
- `objects.json`: object id to object name.

Tick and event records generally keep IDs compact and rely on dictionaries for
name lookup.

## Future Screenshots

Future screenshot or frame capture should attach to ticks using `tickId`, for
example by adding a `framePath` field to a tick or by writing a separate frame
index keyed by `tickId`. The current layout leaves room for a future `frames\`
folder without changing tick/event segment consumption.

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

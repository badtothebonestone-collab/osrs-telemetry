# Telemetry Schema

Telemetry is stored as JSON Lines. Each line is one complete JSON object.

## Recording Modes

Normal live mode is compact-packet based. Raw tick/event/frame recording is now
optional debug/audit output rather than the live substrate.

| Mode | Normal files written | Raw/debug files |
| --- | --- | --- |
| `LIVE_COMPACT_ONLY` | `live_packets\live-*.ndjson`, `live_packet_index.json`, rolling `interaction_geometry\live` files | Raw ticks/events/frames disabled |
| `LIVE_COMPACT_WITH_FRAMES` | Compact packets plus rolling live files | Frames enabled at configured interval; raw ticks/events disabled |
| `DEBUG_RECORDING` | Compact packets when enabled | Full raw tick/event/frame recording for replay, audit, batch geometry, and training |
| `HYBRID_DEBUG` | Compact packets | Reserved for sampled or warning-triggered raw snapshots |

The RuneLite config is grouped into Normal Live, Visual QA Overlay, Frames /
Visual Capture, Debug / Audit Recording, Retention / Storage, and Advanced /
Experimental sections. Existing config keys are preserved so saved settings are
not silently reset.

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
- `cameraX`, `cameraY`, `cameraZ`
- `cameraYaw`, `cameraPitch`
- `viewportWidth`, `viewportHeight`, `viewportXOffset`, `viewportYOffset`
- `canvasWidth`, `canvasHeight`
- `localPlayer`
- `inventory`
- `equipment`
- `skills`
- `npcs`
- `players`
- `widgets`
- `sceneCaptureSummary`
- `sceneIndexSummary`
- `sceneProjectionSummary`
- `sceneObjectDeltas`
- `visibleSceneObjectRefs`
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
- `sceneCaptureDurationMillis`
- `snapshotBuildDurationMillis`

`status` summarizes read-only local status such as run energy, weight, HP,
prayer, health ratio, and current interacting target.

`activePrayers` contains all known prayers with their varbit and active state.

`sceneCaptureSummary` is a read-only diagnostic object for the bounded scene
scan. It records `sceneCaptureMode`, whether the scan covered the full current
plane, configured radius/max scene object cap, scanned plane, scanned tile
bounds and dimensions, objects seen/captured/skipped by cap, capture ratio, and
per-layer counts for game, wall, decorative, and ground objects. It does not add
extra object payload or any interaction behavior; it only explains whether
`sceneObjects` was capped or radius-limited for that tick.

`STATIC_SCENE_INDEX_DIAGNOSTIC` is an opt-in static scene memory mode. In that
mode, `sceneObjects` is intentionally kept empty or light while the tick may
include `sceneIndexSummary`, `sceneProjectionSummary`, `sceneObjectDeltas`, and
`visibleSceneObjectRefs`. `sceneObjectDeltas` contains compact new/updated/
despawned records keyed by `objectKey`; `visibleSceneObjectRefs` contains
projected refs that should be used for per-tick visual QA. `objectKey` is a
best-effort stable identity made from plane, world/scene tile, object layer,
object id, hash when available, and orientation, so same-id objects at different
locations stay distinct.

`sceneIndexSummary` reports static index size, present object count, delta
counts, whether a full resync happened, resync reason, index cap status, and
index build/update timings. `sceneProjectionSummary` reports projection state
hash, whether camera/viewport/player projection state changed, refresh mode,
objects considered/updated/reused, visible refs, geometry counts, and projection
duration. These fields are diagnostic-only and read-only.

`sceneCaptureDurationMillis` and `snapshotBuildDurationMillis` are lightweight
timing diagnostics for short dev sessions. `writerQueueSize` and
`writerDroppedRecords` remain the primary writer pressure indicators.

Projection-v1 tick fields are read-only camera, viewport, and canvas values
captured from RuneLite getters. NPC and non-local player records may also include
nullable `localX`, `localY`, `canvasPoint`, `clickboxBounds`,
`convexHullBounds`, `onScreen`, `geometryAvailable`, and `geometryWarning`
fields, plus `npcName` and `npcNameSource` when a read-only lookup fills a
missing or transformed name. Scene object records may include nullable
`objectName`, `objectNameSource`, `localX`, `localY`, `canvasLocation`,
`canvasTilePolygon`, `clickboxBounds`, `clickboxPolygon`, `convexHullBounds`,
`convexHullPolygon`, `onScreen`, `geometryAvailable`, and `geometryWarning`.
Ground item records may include nullable `itemName`, `itemNameSource`, and tile
geometry such as `localX`, `localY`, `canvasTilePolygon`, `canvasCenter`,
`onScreen`, `geometryAvailable`, and `geometryWarning`.
Projection failures are non-fatal: missing/null geometry means the target could
not be projected for that tick, while the rest of the tick remains valid. This
telemetry does not add overlays, input hooks, menu actions, clicks, or
client-state mutation.

## Compact Live Packets

Compact live packets are the default staged bridge for live sidecars. In
normal live mode they are written without raw tick/event/frame recording under:

```text
live_packets\live-*.ndjson
live_packets\live_packet_index.json
live_packets\latest_segment.txt
```

Each line is one versioned packet envelope:

```json
{
  "schema": "osrs_telemetry_live_packet.v1",
  "packetType": "live_baseline_packet.v1",
  "sessionId": "2026-05-09_12-00-00",
  "tick": 123,
  "sequence": 456,
  "timestampUtc": "2026-05-09T17:00:00Z",
  "payload": {}
}
```

The compact bridge is read-only observed telemetry. It does not add overlays,
input hooks, clicking, menu invocation, automation, action routing, or
client-state mutation. Python sidecars still own target libraries, profiles,
candidate scoring, task interpretation, context responses, QA tooling, and any
future vision/model work.

Packet types:

- `live_baseline_packet.v1`: compact tick/game state, player facts,
  camera/viewport fields, latest frame path, scene capture mode, and
  source-cap/completeness summary.
- `live_scene_delta_packet.v1`: scene capture/index summaries plus compact
  `sceneObjectDeltas` new/updated/despawned records. It intentionally does not
  include full `sceneObjects` arrays.
- `live_projection_packet.v1`: projection summary plus compact visible refs
  with `objectKey`, on-screen/geometry flags, aim point, geometry source, and
  small geometry bounds. Heavy polygons are excluded by default. When
  `compactLiveIncludeClickableHull`, `compactLiveIncludeCanvasTilePolygon`,
  `compactLiveIncludeConvexHull`, or `compactLiveIncludeHeavyGeometry` is
  enabled, or when the read-only telemetry debug overlay is enabled with
  clickable hull/tile geometry, capped visible refs may also include
  `clickableHull`, `clickboxPolygon`, `convexHull`, `convexHullPolygon`, and
  `canvasTilePolygon`. Packet polygons are compact point objects such as
  `{"x": 123, "y": 456}`. `geometryEmission` reports the requested geometry
  types, the per-tick `compactLiveGeometryMaxRefs` cap, emitted polygon refs,
  skipped refs, and whether the cap was hit. Those polygons are observed
  geometry only; they are not action commands.
- `live_inventory_packet.v1`: compact inventory/equipment state, slot count,
  free/filled slots, total item quantity, signature, non-empty item slots, and
  whether compact inventory delta tracking is available.
- `live_inventory_delta_packet.v1`: compact read-only inventory change packet
  emitted when the observed inventory signature or slot occupancy changes. It
  can include before/after signatures, changed slots, added/removed items,
  quantity changes, free/filled slot transitions, and `inventoryFull`.
- `live_activity_packet.v1`: raw observed animation, pose, interacting target,
  status fields, previous animation/pose/interacting signatures, and changed
  field names. Task-state interpretation remains outside Java.
- `live_navigation_packet.v1`: compact read-only navigation readiness facts:
  player tile/world location, collision summary/hash, map dimensions, blocked
  tile counts, and scene bounds. It does not contain routes or movement
  instructions.
- `live_collision_window_packet.v1`: bounded local collision flags around the
  player for lightweight reachability QA. It includes window bounds, radius,
  dimensions, row-encoded flags, and a window hash. It is not a movement or
  route packet.
- `live_collision_grid_packet.v1`: optional debug-only full collision flag grid
  packet. Disabled by default; normal live mode emits the summary/hash packet
  instead.
- `live_writer_health_packet.v1`: raw writer and compact live packet queue,
  drop, write-error, and frame-drop diagnostics.

The defaults preserve existing raw recording behavior and also enable compact
live packets for normal live mode. Compact packets are bounded by retention:
the default segment size is 64 MB, the default retention budget is 512 MB, and
the default retained segment count is 16. Older saved RuneLite profiles may
still have the `emitCompactLivePackets` setting disabled; enable it for normal
live mode.

Python Phase 2 consumption is source-selectable. The live processor
supports `--input-source raw-ticks`, `--input-source compact-packets`, and
`--input-source auto`. Auto mode prefers compact live packets when
`live_packet_index.json` and a recent latest segment are present, otherwise it
falls back to raw tick JSONL with a visible warning for backward compatibility
and audit/debug sessions. `--require-compact-packets` fails fast when compact
packets are missing or stale, which is useful for proving that live mode is not
using raw tick fallback.

Compact packet mode converts baseline, scene-delta, projection, inventory,
inventory-delta, activity, navigation, local collision-window, optional debug
collision-grid, and writer-health packets into the same rolling live candidate files under
`interaction_geometry\live`, so context-service consumers do not need a new
response schema.

Compact packet mode is field-tolerant. If a packet omits a value needed by a
profile, Python marks the capability as missing or warns rather than inventing
state. It does not silently switch to broad raw scene processing unless
`--input-source auto` selected the raw fallback because compact packets were not
available.

Inventory fields use explicit meanings:

- `inventorySlotCount` / `slotCount`: known inventory capacity for the packet.
- `filledSlots`: occupied inventory slots.
- `freeSlots`: empty inventory slots.
- `itemCount`: compatibility alias for total item quantity across occupied
  slots.
- `totalItemQuantity`: explicit total quantity sum.
- `inventoryFull`: derived from `freeSlots == 0` when slot count is known.
- `inventoryDeltaTrackingKnown`: true when the live processor has enough
  rolling tick state or compact delta capability to distinguish "no recent
  change observed" from "delta tracking unavailable."

Activity fields are observed facts. `live_activity_packet.v1` may include
`previousAnimation`, `previousPoseAnimation`, `previousInteractingSignature`,
`interactingSignature`, `changedFields`, `activityChanged`, and `eventSource`.
Python uses these to populate `recentActivityEvents` and conservative apparent
state labels such as `idle`, `animating`, `interacting`, or `unknown`; Java does
not infer task intent.

Realtime liveness distinguishes `live_assumed` from `unknown`. In delta mode,
`live_assumed` means the candidate is currently present in the candidate stream
and no direct depletion/despawn delta was observed. `unknown` means liveness is
off, missing, or unavailable. `degraded` indicates budget pressure or direct
stale/despawned/depleted evidence.

`live_status.json` and `context_response.v1` diagnostics include the active
input source, compact packet availability/recentness, fallback reason, latest
compact packet sequence, and latest compact segment so sidecars can tell whether
normal live mode is using compact packets.

`interaction_geometry\live\live_event_timeline.jsonl` is a bounded read-only
timeline of notable live context changes. Each line uses schema
`live_context_event.v1`:

- `generatedAtUtc`
- `tick`
- `eventType`
- `severity`: `info`, `warn`, or `error`
- `summary`
- `details`
- `relatedCandidate`
- `previousValue`
- `currentValue`
- `source`: currently `live_target_processor`
- `profile`

Events are summaries only. They do not contain click, movement, menu, or input
commands. Typical event types include candidate changes, target liveness or
depletion changes, inventory changes, activity changes, reachability changes,
input-source/fallback changes, and live health changes such as budget or
write-failure state. `context_request.v1` can request recent timeline entries
with `needs: ["events"]`; `maxEvents` caps the returned event list separately
from `maxCandidates`. The context service returns the capped entries as both
`events` and `recentEvents` for compatibility with existing dashboard helpers.
Compact responses omit bulky event `details`; full responses preserve the
recorded event detail objects.
The live processor keeps this file bounded by `--event-timeline-limit`
(default 200) and can skip timeline output with `--disable-event-timeline`.

`interaction_geometry\live\overlay_debug_state.json` is a tiny read-only file
for the optional RuneLite debug overlay. It uses schema
`telemetry_overlay_debug_state.v1` and is rewritten atomically by the live
processor.

Top-level fields:

- `generatedAtUtc`
- `latestTick`
- `profile`
- `status`
- `player`: world and scene tile fields.
- `summary`: candidate count, cap, budget, and write-failure summary.
- `latestEventSummary`, `latestEventTick`, `warningEventCount`, and legacy
  `lastEventTick`: compact timeline status for the overlay panel.
- `targets`: capped candidate summaries only.
- `collisionWindow`: availability, bounds, radius, and player scene tile.
- `safety`: read-only/draw-only flags.

Target summaries may include rank, `isBest`, `isNearest`, class/name/id,
category, world and scene tile, source/latest tick, on-screen and geometry
flags, quality tier/score, target liveness, `livenessInterpretation`, direct
reachability, path length, `interactionRadiusTiles`, collision-window
membership, capped reachability evidence, `labelParts`, `overlayLabel`,
`overlayColor`, aimPoint, compact bounds, and small polygons when available.
Clickable hull overlay fields are:

- `clickableHull`: preferred observed clickbox/clickable area polygon. The live
  overlay debug state stores points as `{"points": [{"x": 123, "y": 456}, ...]}`.
- `clickboxPolygon`: raw clickbox polygon when available.
- `convexHull`: convex hull fallback polygon when clickbox geometry is missing.
- `canvasTilePolygon`: tile polygon fallback/debug geometry.
- `clickableHullAvailable`, `clickableHullMissingReason`, and
  `geometrySource`: compact diagnostics for visual QA.

The overlay geometry fallback order is clickable hull, clickbox polygon, convex
hull, canvas tile polygon, bounds, then aim point only. If
`overlay_debug_state.json` reports `boundsOnly` targets, the polygon was not
available to the overlay state writer for those targets. `overlayColor` uses gray
for depleted/stale targets, red for blocked
reachability, green for reachable reachability, and yellow for unknown
reachability. The overlay can optionally show one latest event summary in its
status panel; it never draws a full event list.

`live_status.json` and `overlay_debug_state.json` include lightweight geometry
matching counters for visual QA:

- `candidateHullDirectMatches`: candidates with hull geometry already attached
  or matched by `objectKey`.
- `candidateHullFallbackMatches`: candidates matched to compact hull geometry by
  stable fallback keys.
- `candidateHullMissing`: candidates that remained bounds/aim-only after
  matching.
- `compactHullRefsAvailable` and `compactHullRefsUnused`: compact refs with
  polygon geometry and how many were not used by the current candidates.
- `hullLimit`: overlay-only cap for how many top-ranked targets may carry
  polygon payloads in `overlay_debug_state.json`.
- `bestHullAvailable` and `nearestHullAvailable`: whether the best and nearest
  overlay targets have clickable hull geometry after ranking and cap application.
- `hullRankBuckets`: clickable hull counts for `rank1`, `ranks2to5`,
  `ranks6to10`, and `ranks11plus`.
- `polygonTargetsSuppressedByHullCap`: ranked overlay targets that had polygon
  geometry available but did not copy it because they were beyond
  `--overlay-debug-hull-limit`.
- `compactLiveHullsEmitted`, `compactLiveHullDroppedByCap`,
  `compactLiveHullDroppedNullClickbox`, `compactLiveHullDroppedOffscreen`, and
  `compactLiveHullDroppedNoCanvasIntersection`: Java-side emission counters.

When clickable hull geometry is enabled, Java now prioritizes polygon emission
for visible refs with clickbox geometry closest to the player scene tile, then
closest to the screen center, before applying `compactLiveGeometryMaxRefs`. This
keeps capped geometry aligned with nearby/high-priority targets instead of
spending the cap on arbitrary scene iteration order.
The file does not include full collision grids, broad scene dumps, action
commands, mouse/keyboard fields, or menu invocation fields.

## Local Collision Window And Reachability QA

Navigation telemetry is read-only context. It does not click, walk, send input,
invoke menus, mutate client state, or execute routes.

Normal compact live mode emits `live_navigation_packet.v1` when compact packets
are enabled. Its payload is intentionally small:

- `player`: `worldX`, `worldY`, `plane`, `sceneX`, `sceneY`, `localX`,
  `localY` when available.
- `collision.collisionKnown`: whether a collision map summary was available.
- `collision.mapWidth` / `collision.mapHeight`: local collision map size,
  typically `104x104`.
- `collision.blockedMovementTileCount`: count of tiles with movement-blocking
  collision flags.
- `collision.blockedFullTileCount`: count of fully movement-blocked tiles.
- `collision.collisionHash`: summary hash for change detection.
- `bounds`: scene min/max bounds.
- `source`: world-view/base-plane metadata and read-only warnings.

Normal compact live mode also emits `live_collision_window_packet.v1` when
compact navigation packets are enabled. Its payload is a bounded local grid:

- `plane`
- `playerSceneX` / `playerSceneY`
- `windowRadius`
- `minSceneX` / `maxSceneX` / `minSceneY` / `maxSceneY`
- `width` / `height`
- `encoding`: currently `json-rows-int-flags`
- `flags`: row-encoded collision flags for tiles inside the window
- `collisionWindowTileCount`
- `collisionWindowHash` / `windowHash`
- `mapWidth` / `mapHeight`
- `generatedFromPlane`
- `warnings`

The default window radius is 24 scene tiles, clamped between 8 and 52. Full
collision-grid packets remain disabled by default and are debug-only.

`live_target_processor.py` converts this into
`interaction_geometry\live\live_navigation_summary.json` with:

- `collisionKnown`
- `playerTileKnown`
- `mapBounds`
- `mapWidth` / `mapHeight`
- `blockedMovementTileCount`
- `blockedFullTileCount`
- `collisionHash` / `signature`
- `obstaclesKnown`
- `collisionWindowAvailable`
- `collisionWindowRadius`
- `collisionWindowBounds`
- `collisionWindowHash`
- `collisionWindowTick`
- `reachabilityComputed`
- `fullCollisionGridAvailable`
- `warnings` and `notes`

Candidate packets may include a compact `navigation` object:

- `collisionKnown`
- `collisionWindowAvailable`
- `targetInCollisionWindow`
- `playerTileKnown`
- `targetTileKnown`
- `samePlane`
- `distanceTiles`
- `directReachability`: `reachable`, `blocked`, or `unknown`
- `pathLengthTiles`
- `checkedTiles`
- `reachabilityConfidence`
- `reachabilityEvidence`
- `missingNavigationFields`
- `conservativeMode`

The current local reachability helper is conservative: it uses 4-direction BFS
inside the local collision window and searches for the target tile or a walkable
adjacent tile. Diagonal movement and full long-distance pathfinding are
deferred. If the local window is present, context responses report
`navigationReadiness.status="local"` and include per-candidate navigation
results. If only the summary exists, status is `summary`; if collision data is
missing, status is `unknown`.

`live_packet_index.json` summarizes the rolling segment set:

- `schema`: `live_packet_index.v1`
- `activeSegment` and `latestSegment`
- `segments`: one entry per retained segment, with sequence/tick range, byte
  count, and packet counts by type
- `latestTick` and `latestSequence`
- retention settings and total pruned segment count

`latest_segment.txt` contains the current active segment filename. It is a tiny
pointer for tailing tools so they can follow the live stream without scanning
old segments. Segment retention prunes only completed `live-*.ndjson` files
inside `live_packets`; the active segment is never deleted. Retention can be
bounded by bytes, segment count, and tick window.

NPC, object, and ground item names are best-effort read-only definition
lookups. Scene objects prefer valid impostor names when an object definition can
transform. If a definition is hidden or unavailable, derived tooling falls back
to stable ID labels such as `SceneObject[12345]`, `GroundItem[995]`, or
`Tile[3200,3200,0]`.

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
frames\frame_index.jsonl
```

This is line-oriented JSONL: each line is a complete JSON object. Records are a
session-local sidecar for frame lifecycle and timing events. They are not
required to parse tick/event telemetry, but they are useful for diagnosing
capture delay, writer queue delay, encoding time, dropped frames, and retention
cleanup.

Common fields:

- `schemaVersion`
- `eventType`
- `tickId`
- `framePath`
- `captureSource`
- `status`
- `requestedAtUtc`
- `capturedAtUtc`
- `enqueuedAtUtc`
- `writtenAtUtc`
- `deletedAtUtc`
- `captureLatencyMs`
- `queueLatencyMs`
- `writeLatencyMs`
- `writeDelayMs`
- `frameWriteDelayMs`
- `totalLatencyMs`
- `frameTotalLatencyMs`
- `width`
- `height`
- `bytes`
- `sizeBytes`
- `droppedFrameCount`
- `error`
- `reason`

`eventType`, when present, describes the lifecycle record:

- `FrameRequested`: frame capture/write was requested.
- `FrameWritten`: frame image was written.
- `FrameDropped`: frame was dropped before write completion.
- `FrameDeleted`: frame file was deleted or expired by retention.
- `FrameFailed`: frame capture or write failed.

`status` is one of:

- `WRITTEN`: frame image was encoded and written.
- `DROPPED_QUEUE_FULL`: frame image was captured but not accepted by the frame
  writer queue.
- `CAPTURE_FAILED`: frame capture failed before queueing.
- `WRITE_FAILED`: frame image reached the writer but could not be written.
- `WRITE_REJECTED`: writer rejected an invalid frame path.
- `DELETED`, `EXPIRED`, or related deleted/expired values: frame file was
  removed by retention or cleanup after the source tick remained valid.

Shared Python tools normalize raw frame-index records into fields including:

- `frameWritten`
- `frameWriteDelayMs`
- `frameTotalLatencyMs`
- `frameCaptureLatencyMs`
- `frameQueueLatencyMs`
- `frameIndexStatus`
- `latestFrameIndexEvent`

`latest_state.py` surfaces the latest frame timing in `latest_tick.json` and
`latest_status.json`. `replay_viewer.py` shows frame timing for the selected
tick. `telemetry_launcher.py` shows latest frame write delay, total latency, and
frame-index status plus FrameWritten, FrameDropped, and FrameDeleted counts in
Telemetry Health. `validate_session.py` reports dropped, failed, and deleted
frame counts; normal expired/deleted frames are not fatal by themselves.

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
such as `frameExists`, `framePending`, `frameExpiredOrMissing`,
`frameWritten`, `frameWriteDelayMs`, `frameTotalLatencyMs`,
`frameCaptureLatencyMs`, `frameQueueLatencyMs`, and `frameIndexStatus` are
point-in-time tool-derived values. `export_session.py` writes
`exports\frame_index_summary.jsonl`, adds frame-index counts and timing
statistics to `session_index.json`, and joins frame timing into
`tick_summary.jsonl` by `tickId` when available.

## Recording Modes

The Java writer reports its recording behavior in `manifest.json` and in
`live_writer_health_packet.v1` payloads.

Common fields:

- `recordingMode`: `LIVE_COMPACT_ONLY`, `LIVE_COMPACT_WITH_FRAMES`,
  `DEBUG_RECORDING`, or `HYBRID_DEBUG`.
- `rawTickRecordingEnabled`: whether full `ticks\ticks-*.jsonl` records are
  being written.
- `rawEventRecordingEnabled`: whether full `events\events-*.jsonl` records are
  being written.
- `frameRecordingEnabled`: whether `frames\*` and `frame_index.jsonl` are being
  written.
- `compactPacketRecordingEnabled`: whether compact live packets are active.
- `rawTicksWritten`, `rawTicksSuppressedByMode`, `rawEventsWritten`,
  `rawEventsSuppressedByMode`, `framesWritten`, and
  `framesSuppressedByMode`: counters for written or mode-suppressed data.

Mode behavior:

- `LIVE_COMPACT_ONLY` is the normal live mode. Compact packets under
  `live_packets\` are emitted; full raw ticks/events and frames are not written.
- `LIVE_COMPACT_WITH_FRAMES` emits compact packets and bounded frame capture;
  full raw ticks/events remain off.
- `DEBUG_RECORDING` preserves the historical full session layout for audit,
  replay, batch geometry builders, and training/debug datasets.
- `HYBRID_DEBUG` is reserved for compact live plus sampled debug snapshots.

Compact-only live sessions may not contain `ticks\`, `events\`, `frames\`, or
`frame_index.jsonl`. That is expected and is not capture loss. Use compact
packet health and source completeness fields, including
`sourceSceneKnowledgeComplete` and `sourceCapHit`, to evaluate live capture
health. Tools that require raw ticks should ask for `DEBUG_RECORDING` sessions
instead of failing with generic file-not-found errors.

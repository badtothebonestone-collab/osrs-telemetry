# Telemetry Schema

Telemetry is stored as JSON Lines. Each line is one complete JSON object.

## Recording Modes

Normal live mode is compact-packet based. Raw tick/event/frame recording is now
optional debug/audit output rather than the live substrate.

| Mode | Normal files written | Raw/debug files |
| --- | --- | --- |
| `LIVE_COMPACT_ONLY` | `live_packets\live-*.ndjson` by default; daily daemon keeps context in memory and writes only optional overlay state | Raw ticks/events/frames disabled |
| `LIVE_COMPACT_WITH_FRAMES` | Compact packets plus optional visual QA frames; rolling live files only if legacy/debug tools are explicitly started | Frames enabled at configured interval; raw ticks/events disabled |
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

Compact live packets are the default staged bridge for live sidecars. Normal
live mode uses the compact packet file bridge; the local TCP stream is
experimental and optional. The file bridge writes without raw tick/event/frame recording
under:

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

## Live Config Doctor

`live_config_doctor.py` emits `live_config_doctor.v1`. It is a read-only
workflow diagnostic that inspects the current session files and optional local
health endpoints.

Top-level fields:

- `schema`
- `generatedAtUtc`
- `mode`: `daily`, `visual_qa`, `debug_audit`, or
  `plugin_snapshot_experimental`
- `status`: `PASS`, `WARN`, or `FAIL`
- `sessionPath`
- `summary`
- `compactPackets`
- `stream`
- `pluginSnapshot`
- `contextService`
- `processes`
- `issues`
- `warnings`
- `failures`
- `fixSuggestions`
- `topWarnings`

The `summary` object normalizes the fields that most often cause live-stack
confusion: active input source, recording mode, raw tick/event/frame flags,
compact packet availability and recency, compact stream state, plugin-snapshot
state, `windowTicks`, `candidateOutputWindow`, `livenessMode`, overlay target
count/limit, overlay geometry mode, collision-window status, budget status, and
write failures.

Each issue has:

- `severity`: `WARN` or `FAIL`
- `code`: stable machine-readable diagnosis code
- `message`: human-readable finding
- `suggestion`: optional copy/paste fix suggestion

The doctor does not edit RuneLite config. Preset modes only classify the current
configuration and explain what to change manually.

## Workflow Presets

The plugin exposes fixed telemetry workflow presets through RuneLite config and,
when the plugin snapshot endpoint is enabled, through localhost preset
endpoints. Presets mutate telemetry plugin configuration only; they do not call
RuneLite Client APIs, click, type, invoke menus, execute in-game commands, or
mutate game state.

Preset config keys:

- `workflowPreset`: `DAILY_LIVE`, `DAILY_SNAPSHOT_NO_FILE`, `VISUAL_QA`,
  `DEBUG_AUDIT`, `PLUGIN_SNAPSHOT_EXPERIMENTAL`, or `CUSTOM`
- `presetPreviewOnly`: preview selected preset without saving changes
- `applyWorkflowPreset`: toggle trigger that applies the selected preset and
  resets to false

Preset endpoints:

- `GET /presets`
- `POST /preset/preview`
- `POST /preset/apply`

`telemetry_preset_request.v1`:

```json
{
  "schema": "telemetry_preset_request.v1",
  "preset": "DAILY_LIVE"
}
```

`telemetry_preset_response.v1`:

```json
{
  "schema": "telemetry_preset_response.v1",
  "preset": "DAILY_LIVE",
  "status": "PASS",
  "preview": false,
  "changes": [
    {
      "key": "emitCompactLiveStream",
      "oldValue": "true",
      "newValue": "false",
      "changed": true
    }
  ],
  "warnings": [],
  "readOnlyGameState": true
}
```

The preset endpoint does not accept arbitrary config keys. The only accepted
input is a fixed preset name, and only whitelisted `osrs-telemetry` keys are
changed.

## Plugin Snapshot Bridge

The plugin snapshot bridge is the next read-only bridge shape. It is disabled
by default and is not the stable daily path; compact packet files remain the
stable bridge. The explicit Daily Snapshot No-File path enables the endpoint and
uses `live_core_daemon.py --daily-mode snapshot-no-files` while disabling the
compact packet file mirror.

Java now keeps a small in-memory `PluginLiveCache` of the latest compact packet
payload by packet type. The cache is updated from the same compact enqueue path
that feeds the file bridge and experimental stream. Payloads are copied into
serialized JSON strings, so request handlers never hold mutable capture maps and
never call RuneLite APIs.

Opt-in config:

- `enablePluginSnapshotEndpoint`: start the local cached snapshot endpoint.
  Default: false.
- `pluginSnapshotHost`: default `127.0.0.1`.
- `pluginSnapshotPort`: default `8893`.
- `pluginSnapshotAuthToken`: optional `X-Plugin-Snapshot-Token` value.
- `pluginSnapshotMaxProjectionRefs`: cap for projection refs in responses.
- `pluginSnapshotMaxResponseBytes`: maximum response size.
- `pluginSnapshotAllowNonLocalHost`: default false.
- `pluginSnapshotEnabledInNormalLive`: experimental opt-in only; normal live
  still uses compact packet files.

Endpoints:

- `GET /health` returns `plugin_snapshot_health.v1` with cache packet types,
  latest tick/sequence, cache age by type, and cache health counters.
- `GET /schema` returns supported request/response schemas, needs, and limits.
- `POST /snapshot` accepts `plugin_snapshot_request.v1` and returns
  `plugin_snapshot_response.v1` from cached compact payloads only.

Supported snapshot `needs` are `baseline`, `scene_delta`, `projection`,
`inventory`, `inventory_delta`, `activity`, `navigation`, `collision_window`,
`writer_health`, and `watch_values`.

Example request:

```json
{
  "schema": "plugin_snapshot_request.v1",
  "requestId": "example",
  "needs": ["baseline", "projection", "inventory", "navigation", "collision_window", "writer_health"],
  "snapshotTier": "hot",
  "profileHint": "woodcutting",
  "classHint": "tree",
  "targetTypeHint": "sceneObject",
  "requireOnScreen": true,
  "requireGeometryAvailable": true,
  "desiredClasses": ["tree"],
  "maxCandidatesHint": 100,
  "maxAgeTicks": 5,
  "maxProjectionRefs": 100,
  "includeGeometry": false,
  "includeCollisionWindow": true,
  "includeWatchValues": false,
  "responseMode": "compact",
  "projectionFieldMode": "compact"
}
```

Snapshot responses include `schema`, `requestId`, `generatedAtUtc`,
`latestTick`, `snapshotTier`, `status`, `freshness`, `payloads`, `missingCapabilities`,
`warnings`, `serviceTimingMillis`, `responseSizing`, and `cacheHealth`.

Projection refs are prioritized before capping by generic usefulness:
on-screen scene objects with geometry, stable IDs/locations, and player-near
scene coordinates come first. `projectionFieldMode=compact` keeps only
candidate-building fields and omits heavy geometry/debug metadata. Geometry is
omitted unless requested, and response payloads are sanitized to remove fields
that look like input, command, menu, or click-command surfaces. The endpoint has
no arbitrary file serving and no command routes. If a capped response still
exceeds `pluginSnapshotMaxResponseBytes`, the endpoint returns
`errorCode=response_too_large` with `responseSizing` diagnostics; this means the
endpoint is available but the requested response was too large.

`snapshotTier` gives the endpoint a working-set size target without making the
cap architectural:

- `hot`: default small working set, currently 100 refs from Python.
- `expanded`: broader working set, currently 500 refs from Python.
- `audit`: large bounded debug working set, currently 2000 refs from Python and
  still limited by endpoint config and `pluginSnapshotMaxResponseBytes`.

Task/profile hints are optional and advisory. Python sends `profileHint`,
`taskHint`, `classHint`, `targetTypeHint`, `requireOnScreen`,
`requireGeometryAvailable`, `desiredClasses`, and `maxCandidatesHint` so the
Java endpoint can prioritize cached refs before capping. The endpoint uses only
cached projection fields such as name/id/objectKey/targetType/onScreen/geometry;
request handlers still do not call RuneLite APIs, scan the scene, or execute
actions.

Python Phase C adds experimental `live_target_processor.py --input-source
plugin-snapshot`. The processor posts `plugin_snapshot_request.v1` to
`/snapshot`, asks for cached baseline, scene delta, projection, inventory,
inventory delta, activity, navigation, collision window, writer health, and
watch values, then converts the response payloads into the same synthetic tick
shape used by compact packet files. Context service and brain clients continue
reading the rolling live output files written by the processor.

Projection conversion accepts both raw payloads and packet-envelope payloads.
The recognized ref lists are `visibleObjectRefs`, `visibleSceneObjectRefs`,
`projectedRefs`, `refs`, `targets`, `sceneObjects`, and
`projectedSceneObjects`. Each ref is normalized into the compact scene-object
shape used by file input: id/hash/name/kind, world/scene/local coordinates,
`onScreen`, `geometryAvailable`, aim point, bounds, and optional hull/tile
geometry. If only sanitized `bounds` are present, Python uses them as compact
geometry for candidate scoring.

Plugin snapshot processor status fields include:

- `inputSourceActive=plugin-snapshot`
- `pluginSnapshotAvailable`
- `pluginSnapshotLatestTick`
- `pluginSnapshotStatus`
- `pluginSnapshotWarnings`
- `pluginSnapshotMissingCapabilities`
- `pluginSnapshotRequestMillis`
- `pluginSnapshotHttpRequestMillis`
- `pluginSnapshotResponseReadMillis`
- `pluginSnapshotParseMillis`
- `pluginSnapshotJsonParseMillis`
- `pluginSnapshotEndpointServiceMillis`
- `pluginSnapshotConvertMillis`
- `pluginSnapshotPrefilterMillis`
- `pluginSnapshotWorldBuildMillis`
- `pluginSnapshotCandidateSelectMillis`
- `pluginSnapshotOutputSerializeMillis`
- `pluginSnapshotOutputWriteMillis`
- `pluginSnapshotOverlayStateWriteMillis`
- `pluginSnapshotStatusWriteMillis`
- `pluginSnapshotTotalActiveMillis`
- `pluginSnapshotBottleneck`
- `pluginSnapshotResponseBytes`
- `pluginSnapshotPayloadTypes`
- `pluginSnapshotProjectionRefs`
- `pluginSnapshotProjectionCapped`
- `pluginSnapshotTier`
- `pluginSnapshotMaxProjectionRefs`
- `pluginSnapshotEscalated`
- `pluginSnapshotEscalationReason`
- `pluginSnapshotInitialRefs`
- `pluginSnapshotFinalRefs`
- `pluginSnapshotProjectionRefListPath`
- `pluginSnapshotRefsConverted`
- `pluginSnapshotFieldPresentCounts`
- `pluginSnapshotFieldMissingCounts`
- `pluginSnapshotConversionWarnings`
- `pluginSnapshotWorldTargetsBuilt`
- `pluginSnapshotCandidatesBeforeFilters`
- `pluginSnapshotCandidateRejectReasons`
- `pluginSnapshotTicksSkippedAsUnchanged`
- `pluginSnapshotNoChangePolls`
- `pluginSnapshotCandidateSignature`
- `pluginSnapshotCandidateOutputSkippedUnchanged`
- `pluginSnapshotOutputBytesSkipped`
- `pluginSnapshotRefsBeforePrefilter`
- `pluginSnapshotRefsAfterPrefilter`
- `pluginSnapshotPrefilterRejectReasons`
- `pluginSnapshotClassificationCacheSize`
- `pluginSnapshotClassificationCacheHits`
- `pluginSnapshotClassificationCacheMisses`
- `pluginSnapshotHttpConnectionReused`
- `pluginSnapshotHttpReconnects`
- `pluginSnapshotEndpointErrors`
- `pluginSnapshotTimeouts`

The plugin-snapshot timing fields are exclusive where practical and do not
include follow-mode sleep time. `pluginSnapshotBottleneck` is derived from the
largest plugin-snapshot bucket and can be one of `endpoint_service`,
`http_request`, `response_read`, `json_parse`, `conversion`, `prefilter`,
`world_build`, `candidate_select`, `output_serialize`, `output_write`, or
`unknown`. Unchanged snapshot ticks skip candidate rebuilding. Unchanged
candidate signatures skip heavy candidate/world-target output rewrites while
continuing to refresh lightweight status.

The processor does not make plugin-snapshot the default. `--input-source auto`
continues to prefer compact packet files unless
`--auto-prefer-plugin-snapshot` is explicitly supplied. Use
`--compare-input-sources plugin-snapshot-vs-file` before considering it for a
normal workflow.

Manual local check after enabling the config:

```powershell
Invoke-RestMethod http://127.0.0.1:8893/health
Invoke-RestMethod http://127.0.0.1:8893/schema

$request = @{
  schema = "plugin_snapshot_request.v1"
  needs = @("baseline", "projection", "inventory", "navigation", "collision_window", "writer_health")
  maxAgeTicks = 5
  maxProjectionRefs = 100
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
```

Experimental processor commands:

```text
python telemetry-viewer\live_target_processor.py --latest-session --input-source plugin-snapshot --plugin-snapshot-tier hot --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
python telemetry-viewer\live_target_processor.py --latest-session --input-source plugin-snapshot --plugin-snapshot-tier expanded --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Comparison:

```text
python telemetry-viewer\live_target_processor.py --latest-session --compare-input-sources plugin-snapshot-vs-file --profile woodcutting --latest 5
```

Snapshot conversion diagnostic:

```text
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500 --dump-synthetic-shape
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --tier-sweep
```

If comparison reports `projection refs capped` and snapshot candidates are zero
while compact packet files have candidates, the likely cause is cap/order
coverage rather than missing endpoint health. Increase the request cap and, if
needed, the RuneLite snapshot endpoint cap while keeping
`pluginSnapshotMaxResponseBytes` bounded.

If `/snapshot` returns `response_too_large`, the endpoint is reachable but the
requested capped response exceeded `pluginSnapshotMaxResponseBytes`. Compact
mode applies `projectionFieldMode=compact`, prioritizes useful refs before the
cap, and omits heavy geometry/debug fields by default. Tune
`--plugin-snapshot-max-projection-refs`, `pluginSnapshotMaxProjectionRefs`, and
`pluginSnapshotMaxResponseBytes` together.

`--dump-synthetic-shape` reports where refs are placed in the internal tick,
which paths the world-target builder reads (`sceneObjects`,
`visibleSceneObjectRefs`, and `projectedSceneObjects`), and how many refs are
accepted before profile/candidate filters. A nonzero converted-ref count with
zero accepted refs indicates a conversion path mismatch; nonzero accepted refs
with zero candidates indicates profile/classification or cap coverage.

The first direct stream transport is a localhost TCP NDJSON server inside the
plugin. It is disabled by default until opted in through RuneLite config:

- `emitCompactLiveStream`: start the loopback stream publisher.
- `compactLiveStreamHost`: default `127.0.0.1`; non-loopback addresses are
  rejected by the publisher.
- `compactLiveStreamPort`: default `8891`.
- `compactLiveStreamQueueSize`: bounded pending stream packet queue.
- `compactLiveStreamCircuitBreakerEnabled`: pause stream publishing when stream
  writes or queue pressure look unsafe.
- `compactLiveStreamMaxWriteMillis`: stream worker write-time budget before the
  circuit breaker trips.
- `compactLiveStreamDisableSeconds`: temporary stream pause after a circuit
  breaker trip.
- `compactLiveStreamAlsoWriteFiles`: keep the compact packet file bridge as a
  debug mirror while the stream is enabled.

The stream sends the same `osrs_telemetry_live_packet.v1` envelope, one JSON
packet per line. It drops stream packets instead of blocking RuneLite when the
queue is full or no local consumer is connected. It does not expose remote
network access by default and does not add input, click, menu, or action fields.

The Python stream consumer buffers packets by tick. A tick is promoted for
candidate/context generation only after the required baseline and projection
packets have arrived. Incomplete ticks are retained in a bounded buffer and
reported through live status instead of clearing the previous good candidate
context. Stream socket wait/reconnect timing is reported separately from active
processing time.

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
- `live_watch_values_packet.v1`: optional/future compact packet for bounded
  read-only watch values requested through the context service. Values are
  keyed by alias and may include `changed`, `unavailableReason`, and budget
  state. The first implementation exposes builtin watch values from Python and
  keeps Java dynamic watch polling marked as future.
- `live_writer_health_packet.v1`: raw writer, compact file bridge, compact
  stream queue/client/drop/write-error, packet-count-by-type, latest-tick-by-
  type, and frame-drop diagnostics.

Compact stream status fields written by `live_target_processor.py` include:

- `compactStreamConnected`
- `compactStreamPacketsSeen`
- `compactStreamPacketsProcessed`
- `compactStreamPacketsByType`
- `compactStreamLatestTickByType`
- `compactStreamMissingRequiredTypesForLatestTick`
- `compactStreamTickBufferSize`
- `compactStreamTicksWaitingForProjection`
- `compactStreamProcessedCompleteTicks`
- `compactStreamSkippedIncompleteTicks`
- `compactStreamReadMillis`
- `compactStreamParseMillis`
- `compactStreamWaitMillis`
- `compactStreamReconnectMillis`
- `compactStreamDisconnectedDurationMillis`
- `compactStreamSocketTimeouts`
- `compactStreamProjectionPacketsSeen`
- `compactStreamRequiredTypesSatisfied`
- `compactStreamCanBuildCandidates`
- `streamFallbackToFile`
- `streamFallbackReason`
- `compactLiveStreamPacketsOfferedByType`
- `compactLiveStreamPacketsSentByType`
- `compactLiveStreamPacketsDroppedByType`
- `compactLiveStreamCircuitBreakerTripped`

The defaults preserve existing raw recording behavior and also enable compact
live packets for normal live mode. Compact packets are bounded by retention:
the default segment size is 64 MB, the default retention budget is 512 MB, and
the default retained segment count is 16. Older saved RuneLite profiles may
still have the `emitCompactLivePackets` setting disabled; enable it for normal
live mode.

Python consumption is source-selectable. The live processor supports
`--input-source compact-stream`, `--input-source compact-packets`,
`--input-source raw-ticks`, and `--input-source auto`. Auto mode prefers compact
packet files when `live_packet_index` and a recent latest segment are present.
It only tries the experimental stream when packet files are unavailable or
stale, otherwise it falls back to raw tick JSONL with a visible warning for
backward compatibility and audit/debug sessions. `--require-compact-packets`
means a compact transport is required: stream mode satisfies it, and file mode
still fails fast when packet files are missing or stale.

Daily launcher flows use `--input-source compact-packets --require-compact-packets`.
If `live_status.json` shows `inputSourceActive=compact-stream`, zero
candidates, and missing `live_baseline_packet.v1` or
`live_projection_packet.v1`, the stream transport is incomplete; switch the
launcher/input source back to compact packet files.

Compact packet mode converts baseline, scene-delta, projection, inventory,
inventory-delta, activity, navigation, local collision-window, optional debug
collision-grid, watch-values, and writer-health packets into the same rolling live candidate files under
`interaction_geometry\live`, so context-service consumers do not need a new
response schema.

Stream status fields in `live_status.json` include `compactStreamConnected`,
`compactStreamReconnects`, `compactStreamPacketsSeen`,
`compactStreamPacketsProcessed`, `compactStreamReadMillis`, and
`compactStreamParseMillis`. Writer-health packets can additionally report
`compactLiveStreamClientCount`, `compactLiveStreamPacketsWritten`,
`compactLiveStreamPacketsDropped`, `compactLiveStreamPacketsDroppedNoClients`,
`compactLiveStreamWriteErrors`, per-type offered/sent/dropped packet counts,
and circuit-breaker state.

Compact packet mode is field-tolerant. If a packet omits a value needed by a
profile, Python marks the capability as missing or warns rather than inventing
state. It does not silently switch to broad raw scene processing unless
`--input-source auto` selected the raw fallback because compact packets were not
available.

Inventory fields use explicit meanings:

- `inventorySlotCount` / `slotCount`: known inventory capacity for the packet.
- `filledSlots`: occupied inventory slots.
- `freeSlots`: empty inventory slots.
- `items`: filled item entries. Each entry preserves the real inventory `slot`;
  consumers must not infer the slot from list position.
- `items[].slot`: zero-based inventory slot index, normally `0..27` for the
  backpack.
- `items[].itemId`: observed item ID.
- `items[].quantity`: observed item quantity. Missing quantity is interpreted
  as `1` by progress counters.
- `itemCount`: compatibility alias for total item quantity across occupied
  slots.
- `totalItemQuantity`: explicit total quantity sum.
- `inventoryFull`: derived from `freeSlots == 0` when slot count is known.
- `inventoryDeltaTrackingKnown`: true when the live processor has enough
  rolling tick state or compact delta capability to distinguish "no recent
  change observed" from "delta tracking unavailable."
- `resourceCounts`: optional compact task resource summaries. For woodcutting,
  `woodcutting_logs` counts item IDs `1511`, `1521`, `1519`, `1517`, `1515`,
  and `1513` and includes `byItemId`, `matchedItemIds`, and `matchedSlots`.
- `slotDiagnostics`: consistency checks for duplicate filled slots, invalid
  slot indexes, and `filledSlots + freeSlots == inventorySlotCount`.

Compact inventory may omit empty slots, but it must never re-index filled slots
based on list order. Slot `0`, middle slots, and slot `27` are all valid filled
positions. Use `diagnose_inventory_slots.py` to inspect the live slot table and
resource counts when progress appears to miss a specific backpack position.

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

## Capability Registry and Watch Requests

`telemetry-viewer\capability_registry.json` uses schema
`capability_registry.v1`. Each capability entry includes:

- `id`
- `description`
- `status`: `available`, `watchable`, `unavailable`, `debug_only`, or `future`
- `source`: compact packet type, live file, context endpoint, or future Java API
- `updateFrequency`
- `latencyClass`
- `normalLiveAllowed`
- `debugAuditOnly`
- `missingReason`
- `relatedTasks`

The context service adds runtime status through `GET /capabilities`, including
whether a capability is available now, missing/stale, watchable, or unsupported
for the current live session.

`telemetry-viewer\watch_library.json` uses schema `watch_library.v1`. Watch
definitions are conservative and bounded:

- `alias`
- `type`: `varbit`, `varp`, `varclient_int`, `varclient_str`,
  `item_container`, `widget_summary`, or `builtin`
- `id`, `group`, `child`, or `containerId` when applicable
- `sampleMode`: `on_change`, `every_tick`, or `interval`
- `intervalTicks`
- `ttlTicks`
- `maxEmitPerTick`
- `normalLiveAllowed`
- `debugAuditOnly`

`POST /watch-request` accepts schema `context_watch_request.v1` and returns
`context_watch_response.v1`. Requests are rejected if they contain wildcard or
unbounded identifiers, unsupported types, action/input/menu fields, unsafe
normal-live settings, or limit violations. Accepted requests are written to the
small bounded request file:

```text
<session>\live_requests\watch_requests.json
```

The first implementation writes and validates the request file and exposes
builtin watch values in:

```text
<session>\interaction_geometry\live\live_watch_values.json
```

Java-side dynamic watch polling and `live_watch_values_packet.v1` emission are
marked as a future capability unless explicitly implemented later. Watch values
are read-only observations and never contain click, input, movement, menu, or
action commands.

## Brain Core Resource Progress

`telemetry-viewer\task_resources.json` uses schema `task_resources.v1`. It
maps task resource groups to item IDs so external read-only clients can count
task resources from compact inventory snapshots. The initial `woodcutting`
resource group is `woodcutting_logs` and includes log item IDs:

- `1511` logs
- `1521` oak logs
- `1519` willow logs
- `1517` maple logs
- `1515` yew logs
- `1513` magic logs

`brain_core.py` can persist `brain_state.v1`. Daily resource progress is
computed by `telemetry-viewer\resource_progress.py` and uses
`resource_progress_state.v1` inside `brain_state.v1.resourceProgress`.
The daily policy is monotonic held-vs-baseline for the current baseline:

```text
displayedGoalProgress = max(previousDisplayedGoalProgress, currentHeldCount - baselineHeldCount, 0)
```

Old cumulative fields are ignored in daily mode.

Resource progress fields include:

- `resourceBaselineCounts`
- `resourceCurrentCounts`
- `resourceProgress`
- `goalResourceGroup`
- `progressSource`: `inventory_snapshot_held_vs_baseline`,
  `baseline_initialized`, `baseline_pending`, `inventory_snapshot_invalid`,
  `observe_only`, or `unknown`
- `lastInventorySignature`
- `lastSeenTick`

`brain_decision.v1.progress` reports current held resources, baseline count,
net change from baseline, matched slots, source, reason, and any resource-count
warnings. `brain_decision.v1.goalProgress` includes `resourceGroup`,
`goalCount`, `baselineHeldCount`,
`baselineEstablished`, `currentHeldCount`, `previousResourceCount`,
`netChangeFromBaseline`, `displayedGoalProgress`, `gainedSinceStart`,
`complete`, `source`, `matchedSlots`, `matchedItemIds`,
`lastProcessedInventorySignature`, `lastProcessedInventoryTick`,
`duplicateSnapshot`, `progressUpdateApplied`, `progressUpdateReason`, and
warnings. Deprecated fields such as `observedGained`, `observedRemoved`,
`cumulativeGained`, and `cumulativeLostOrRemoved` may appear as `null` for
compatibility and must not be used for daily progress.

The first snapshot after `--reset-brain-state` initializes the baseline and does
not count currently held logs as newly gained. Later snapshots are idempotent:
the same inventory signature does not change progress. A lower held count does
not reduce `gained since start` during the current session/state; this prevents
stale, skipped, filtered, or budget-limited polls from flashing `0 / goal`.
Reset brain state or start a new session/state to start a new baseline. Moving a
log between slots does not count as gaining another log because the total
resource quantity is unchanged. This is read-only progress interpretation only;
it does not emit action, click, movement, input, or menu commands.

A valid progress snapshot requires session identity, latest tick, inventory
signature, known current held count, and either real inventory item slots or
sufficient task resource counts. If `inventory.items` exists it wins over
`inventory.resourceCounts`; `itemId=null` is never counted and can never appear
with `counted=true`. If only `resourceCounts` are available, held count may be
summary-derived, but matched slots are not fabricated. If the current signature
is missing, the source is `inventory_snapshot_invalid` or `baseline_pending` and
no baseline is established.

If an old state file contains nonzero cumulative fields, progress history is
ignored with:

```text
old cumulative progress history ignored; daily progress uses held-vs-baseline snapshot count
```

When `live_core_daemon.py` runs with default writes off, progress diagnostics
should query daemon memory instead of old rolling files:

```text
python telemetry-viewer\diagnose_brain_progress.py --from-daemon --daemon-url http://127.0.0.1:8890 --task woodcutting --goal-count 5
```

In PowerShell, pass persistent state files via a variable such as
`$brainState = Join-Path $env:USERPROFILE ".osrs-telemetry\brain_state_woodcutting.json"`;
literal `%USERPROFILE%` paths are a cmd.exe convention and may create confusing
workspace folders if used directly.

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
- `summary`: candidate count, overlay mode, intent marker count, cap, budget,
  and write-failure summary.
- `latestEventSummary`, `latestEventTick`, `warningEventCount`, and legacy
  `lastEventTick`: compact timeline status for the overlay panel.
- `intentState`: optional `overlay_intent_state.v1` marker payload for daily
  brain intent overlay mode.
- `markers`: optional top-level mirror of intent markers for RuneLite overlay
  compatibility.
- `targets`: capped candidate summaries in visual/debug mode, or a small
  drawable view of intent markers in daily mode.
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

## Streamlined Live Daemon Schemas

`telemetry-viewer\live_core_daemon.py` is the daily in-memory sidecar. Daily
Stable Compact reads compact packet files; Daily Snapshot No-File uses cached
plugin snapshots and remains experimental. The daemon builds the same candidate and
context state as the legacy live processor and serves context-service-compatible
HTTP responses from memory. It is read-only and does not expose click, mouse,
keyboard, menu, invoke, execute, or command endpoints.

Health and status endpoints keep the existing schemas:

- `GET /health` returns `context_health.v1` with `service=live_core_daemon`,
  `liveCoreDaemonActive=true`, `inputSourceActive`, `dailyMode`,
  `noFileDaily`, `compactPacketFilesRequired`, `compactPacketFilesWriting`,
  `candidateCount`, `writeDebugLiveFiles`, and `overlayStateWritten`.
- `GET /status` returns `context_status.v1` with the same daemon markers plus
  the latest live processor status fields.
- `POST /context` and `POST /context/batch` return `context_response.v1` from
  the in-memory live state.

Daily mode does not write rolling live files by default. `--write-overlay-state`
writes only the capped `telemetry_overlay_debug_state.v1` file for the RuneLite
debug overlay. `--write-debug-live-files` intentionally re-enables the legacy
rolling files for debugging:

```text
interaction_geometry\live\live_status.json
interaction_geometry\live\live_candidates.jsonl
interaction_geometry\live\live_context_index.json
interaction_geometry\live\live_activity_state.json
interaction_geometry\live\live_navigation_summary.json
interaction_geometry\live\live_performance_summary.json
interaction_geometry\live\live_event_timeline.jsonl
```

Daily overlay mode writes brain intent markers by default:

- `--overlay-mode intent`: write `overlay_intent_state.v1` markers and draw only
  the selected brain target plus a small backup set.
- `--overlay-mode candidates`: visual QA path that draws capped candidate
  markers.
- `--overlay-mode debug`: broad diagnostic overlay mode.

`overlay_intent_state.v1` appears inside `telemetry_overlay_debug_state.v1` as
`intentState` and uses:

- `schema`
- `generatedAtUtc`
- `latestTick`
- `activeTask`
- `activeIntent`
- `status`
- `selectedTargetKey`
- `rawBestTargetKey`
- `stableForTicks`
- `missingForTicks`
- `retainedDueToGrace`
- `switchReason`
- `switchAuditTail`
- `backupKeys`
- `markers[]`

Intent marker fields are generic so future brain tasks can show bankers,
booths, destination tiles, waypoints, UI targets, warnings, or diagnostics
without hardcoding woodcutting in the plugin. Supported fields include
`markerType`, `label`, `reason`, `confidence`, `source`, `targetType`,
`selected`, `role`, `priority`, `classId`, `id`, `hash`, `objectKey`,
`markerId`, `markerVersion`, `kind`, `layer`, `worldX`, `worldY`, `plane`,
`sceneX`, `sceneY`, `localX`, `localY`, `aimPoint`, `bounds`,
`clickableHullAvailable`, `clickableHull`, `clickboxPolygon`, `convexHull`,
`canvasTilePolygon`, `geometrySource`, `projectionMode`, `projectionStale`,
`projectionFallbackReason`, `tick`, `reachability`, `liveness`, and
`qualityTier`. Intent markers are read-only observations; they do not contain
action, click, input, keyboard, mouse, menu, invoke, or execute fields.

The daemon deduplicates intent markers by stable target identity before writing
this payload. If the selected marker and a backup/candidate describe the same
object, the selected marker keeps `markerType=selected_target`,
`selected=true`, and `role=selected`, inherits the best available geometry from
the duplicate, and the duplicate backup is suppressed.

Daily daemon status may include `overlayMode`, `intentMarkerCount`,
`candidateMarkersSuppressed`, and `overlayStateBytes`. It may also include the
in-memory intent stabilizer diagnostics `rawBestTarget`,
`stabilizedIntentTarget`, `intentStableForTicks`, `intentSwitchReason`,
`intentHardSwitch`, `intentSoftSwitch`, `intentPreviousTargetKey`,
`intentCandidateWasRetained`, `intentCandidateWasSwitched`,
`intentSwitchedThisTick`, `intentRetainedDueToGrace`,
`intentCurrentMissingTicks`, `intentCurrentMissingThisTick`,
`intentCurrentInvalidReason`, `intentSwitchAuditTail`,
`intentInterruptReason`, `intentStabilizerMillis`, and
`intentCandidatesConsidered`.

The stabilizer is a jitter filter, not a decision blocker. It hard-switches on
task/profile/intent changes, explicit stale/depleted/despawned/unreachable
targets, forced switches, inventory/task transitions, or higher-priority
interrupts. It only applies soft stickiness when the current target is still
valid and a replacement is merely marginal, has not persisted yet, or the
selected target is briefly absent from the current candidate slice. The default
transient-missing grace is two ticks. Backup identities are stabilized too:
selected identity is excluded from backups, previous backups are preferred while
valid, and backup replacement waits for the normal candidate validity path. The
stabilizer is in-memory only and writes no JSON or NDJSON files in daily mode.

RuneLite overlay rendering treats last-known `aimPoint` and polygon payloads as
fallbacks. When a marker has stable world/scene/local identity, the overlay
first tries to resolve the current scene object and draw its live clickbox. If
that cannot be resolved, it falls back to stored clickable hull/clickbox/convex
geometry, compact bounds, live tile projection, last-known aim point, and
finally label-only drawing. This keeps selected intent markers visually closer
to the target while the camera moves between telemetry ticks without allowing
the overlay to choose the task target.

When `--brain-task woodcutting` is used, `--goal-count N` enables read-only
resource progress tracking for `brain_decision.v1`. Without a goal count, the
daemon treats the brain as observe-only: it may report held log counts, but it
does not accumulate gained/lost progress or mark a goal complete. File-backed
brain state is scoped to session path, task, goal count, and resource group;
`--reset-brain-state` clears the baseline before the next inventory snapshot.
Normal daily compact mode keeps frame recording off, so missing frame-path
messages are not considered daily warnings.

Resource progress state uses the `resource_progress_state.v1` schema inside
`brain_state.v1.resourceProgress`. The tracker stores baseline signature/count,
last inventory signature/tick, current held count, displayed held-vs-baseline
goal progress, goal completion, and repair warnings. Daily progress does not use
observed gained/removed counters. Old state without this schema is not trusted
for cumulative history.
`matchedSlotDetails` with `summaryDerived=true` are diagnostic summaries from
`resourceCounts`; they are not treated as real item slots, and `itemId=null` is
never counted as a real resource item.

Transient incomplete inventory polls retain the last valid progress result
instead of recomputing progress as zero. `brain_decision.v1.goalProgress` may
report:

- `progressRetainedFromPrevious`
- `retainedReason`
- `retainedAgeTicks`
- `progressDropReason`
- `progressHeldReason`
- `progressInvalidSnapshotCount`
- `progressRetainedPreviousCount`
- `progressFlickerPreventedCount`
- `lastProgressInvalidReason`
- `lastProgressRetainedTick`
- `lastValidProgressTick`
- `lastValidInventorySignature`

`progressHeldReason=valid_inventory_count_decreased_retained_monotonic_progress`
means a lower valid held count was observed, but the visible `gained since
start` value stayed monotonic for the current baseline.
`progressRetainedFromPrevious=true` means the daemon protected the daily display
from an invalid, stale, or incomplete poll.

`run_daily_gauntlet.py` is a read-only daily health check. It can query daemon
health/status and warn about duplicate `live_core_daemon.py` processes, a
simultaneous legacy `live_target_processor.py`, or a separate `context_service.py`
while the daemon is already serving context.

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

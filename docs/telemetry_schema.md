# Telemetry Schema

Telemetry uses several bounded JSON formats. Historical raw/debug datasets may
use JSON Lines, but the old live packet NDJSON/JSONL archive is no longer part
of runtime.

## Retired Live Packet Archive

The append-only live packet archive has been removed from normal runtime:

- `live_packets\live-*.ndjson`
- `live_packets\live-*.jsonl`
- live packet index/tail readers as live truth
- compact packet file-source daemon mode
- compact stream/file mirror runtime mode

It cannot be enabled through RuneLite config or Python CLI flags. Old files on
disk are legacy cleanup only:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
```

The current pipeline and plugin UI key list are documented in
`telemetry-viewer/docs/current_pipeline_manifest.md`,
`telemetry-viewer/pipeline_manifest.json`, and
`telemetry-viewer/config_keys.json`. Use `context_service.py --pipeline-health`
to confirm active/retired components and stale config-key cleanup status.

Use `PluginSnapshotEndpoint`, `WorldModelCache`, Knowledge Fabric queries,
`current_debug_context`, `replay_scenario.v1`, `script_authoring_context.v1`,
session memory, and sparse visual debug bundles instead.

## Recording Modes

Normal live mode is Snapshot No-File. Raw tick/event/frame recording controls
are retired from the normal RuneLite UI; old saved keys are cleaned on plugin
startup. Historical debug datasets may still contain these records, but they
are not the live substrate.

| Mode | Normal files written | Raw/debug files |
| --- | --- | --- |
| `LIVE_COMPACT_ONLY` | No live packet files; daemon keeps context in memory and writes only optional latest-state overlay/debug JSON | Raw ticks/events/frames disabled |
| `LIVE_COMPACT_WITH_FRAMES` | No live packet files; optional visual QA frames only | Frames enabled at configured interval; raw ticks/events disabled |
| `DEBUG_RECORDING` | No live packet files; explicit debug outputs only | Full raw tick/event/frame recording for replay, audit, batch geometry, and training |
| `HYBRID_DEBUG` | No live packet files | Reserved for sampled or warning-triggered raw snapshots |

The normal RuneLite config surface is now Core, Snapshot Endpoint, and Overlay.
Advanced bounded geometry/collision settings are hidden from the normal UI, and
retired recording/preset keys are unset only within the `osrs-telemetry` config
group.

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

## Runtime Live Context

The runtime live bridge is now endpoint/query based:

- Java plugin exposes `PluginSnapshotEndpoint` on `127.0.0.1:8893`.
- Java keeps `WorldModelCache`, `PluginLiveCache`, and `client_tick_hot.v1` in
  bounded memory.
- Python `live_core_daemon.py` uses `--daily-mode snapshot-no-files
  --input-source plugin-snapshot`.
- Knowledge Fabric and MCP-style tools expose compact read-only queries.
- Explicit replay/script/debug bundles may save bounded JSON on request.

There is no live packet NDJSON/JSONL envelope schema for runtime. Historical
`osrs_telemetry_live_packet.v1` files may exist only in old sessions and should
be handled through maintenance reporting/pruning.

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
retired packet-archive status, plugin-snapshot state, `windowTicks`,
`candidateOutputWindow`, `livenessMode`, overlay target count/limit, overlay
geometry mode, collision-window status, budget status, and write failures.

Each issue has:

- `severity`: `WARN` or `FAIL`
- `code`: stable machine-readable diagnosis code
- `message`: human-readable finding
- `suggestion`: optional copy/paste fix suggestion

The doctor does not edit RuneLite config. Preset modes only classify the current
configuration and explain what to change manually.

## Workflow Presets

Workflow preset config controls are retired from the normal RuneLite settings
UI. The localhost preset endpoints remain read-only-game-state helper endpoints
for compatibility and can only mutate whitelisted telemetry plugin settings;
they do not call RuneLite Client APIs, click, type, invoke menus, execute
in-game commands, or mutate game state.

Retired preset config keys cleaned on plugin startup:

- `workflowPreset`
- `presetPreviewOnly`
- `applyWorkflowPreset`

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
      "key": "pluginSnapshotEndpointEnabled",
      "oldValue": "false",
      "newValue": "true",
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

The plugin snapshot bridge is the stable read-only live bridge. The daily
Snapshot No-File path enables the endpoint and
uses `live_core_daemon.py --daily-mode snapshot-no-files --input-source
plugin-snapshot`. There is no compact packet file mirror.

Java keeps a small in-memory `PluginLiveCache` of the latest live payload by
payload type. The cache is updated from the plugin capture path; it does not
feed any file bridge or experimental stream. Payloads are copied into
serialized JSON strings, so request handlers never hold mutable capture maps and
never call RuneLite APIs.

Opt-in config:

- `enablePluginSnapshotEndpoint`: start the local cached snapshot endpoint.
  Default: false.
- `pluginSnapshotHost`: default `127.0.0.1`.
- `pluginSnapshotPort`: default `8893`.
- `pluginSnapshotAuthToken`: optional `X-Plugin-Snapshot-Token` value.
- `pluginSnapshotAllowNonLocalHost`: default false.

Hidden developer config:

- `pluginSnapshotMaxProjectionRefs`: cap for projection refs in responses.
- `pluginSnapshotMaxResponseBytes`: maximum response size.

Retired alias cleaned on startup:

- `pluginSnapshotEnabledInNormalLive`: migrated to `enablePluginSnapshotEndpoint=true`
  when it was previously set, then unset.

Endpoints:

- `GET /health` returns `plugin_snapshot_health.v1` with cache packet types,
  latest tick/sequence, cache age by type, and cache health counters.
- `GET /schema` returns supported request/response schemas, needs, and limits.
- `POST /snapshot` accepts `plugin_snapshot_request.v1` and returns
  `plugin_snapshot_response.v1` from cached compact payloads only.

Supported snapshot `needs` are `baseline`, `scene_delta`, `projection`,
`inventory`, `inventory_delta`, `activity`, `navigation`, `collision_window`,
`interaction_hot`, `client_tick_tail`, `writer_health`, and `watch_values`.

Example request:

```json
{
  "schema": "plugin_snapshot_request.v1",
  "requestId": "example",
  "needs": ["baseline", "interaction_hot", "projection", "inventory", "navigation", "collision_window", "writer_health"],
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
  "projectionFieldMode": "compact",
  "includeMenuEntries": true,
  "menuEntryLimit": 5
}
```

Snapshot responses include `schema`, `requestId`, `generatedAtUtc`,
`latestTick`, `snapshotTier`, `status`, `freshness`, `payloads`,
`clientTickHot`, legacy `hoverMenu`, legacy `lastMenuOptionClicked`,
`missingCapabilities`, `warnings`, `serviceTimingMillis`, `responseSizing`, and
`cacheHealth`.

### Client Tick Hot State

`client_tick_hot.v1` is the fast interaction layer. It is sampled from
RuneLite client-thread events and kept in a bounded in-memory cache. It is not a
continuous file output.

Events:

- `ClientTick`: latest local mouse/canvas timing sample.
- `PostMenuSort`: latest sorted menu state at the current mouse position. This
  predicts what the next left click will do.
- `MenuOptionClicked`: latest menu action accepted by the client after a click.
  This proves whether the click was `Walk here`, `Chop down`, or another action.

Compact fields:

- `schema=client_tick_hot.v1`
- `clientTick`, `wallTimeMillis`, `monotonicTimeNanos`,
  `gameTickAtSample`, `gameState`, `sessionId`, `sessionPath`
- `mouse`: `canvasX`, `canvasY`, `isInCanvas`
- `postMenuSort` / `hoverMenu`: source event, mouse canvas position,
  `topOption`, `topTarget`, `topType`, `topIdentifier`, `topParam0`,
  `topParam1`, `entryCount`, capped `entries`, and `menuOpen`
- `lastMenuOptionClicked`: source event, option, target, type, identifier,
  params, item id, consumed flag, and mouse canvas position
- `latency`: ages for client/post-menu/click samples, buffered sample counts,
  and dropped sample counts

`interaction_hot` returns the compact hot state. `client_tick_tail` returns the
same shape with optional bounded tails controlled by `maxClientTickSamples`,
`maxMenuSamples`, `maxClickedSamples`, `includeMenuEntries`, and
`menuEntryLimit`. The default Java ring buffer cap is 128 samples.

Readiness also derives liveness fields from this payload: `clientTickHotFresh`,
latest PostMenuSort age, last clicked-menu age, `isLoggedIn`, and
`staleReason`. `LOGIN_SCREEN` or another non-logged-in game state is reported as
a recovery/bootstrap problem; a logged-in stale hot cache points at plugin
hot-state or daemon refresh.

### Safe Aimpoint Contract

`safe_aimpoint.v1` separates candidate validity from actionability. A target can
be a valid candidate and still be unsafe to click if its raw center is outside
the visible/interactable viewport.

Core fields:

- `status`: `PASS` or `FAIL`
- `actionable`, `validButUnsafe`, and `unsafeReasons`
- `canvasX`, `canvasY`: selected safe canvas point when available
- `source`: `hoverConfirmedVisibleHull`, `visibleHullInterior`,
  `clippedClickboxInterior`, `clickboxCenter`, `boundsCenter`, or `fallback`
- `insideCanvas`, `insideViewport`, `insideInteractableRegion`, `uiBlocked`
- `distanceToViewportEdgePx`, `distanceToCanvasEdgePx`
- `clippedVisibleAreaPx`, `clippedVisibleAreaRatio`
- `hoverConfirmed`, `hoverTopOption`, `hoverTopTarget`
- `rawAimPoint`, `rawCenterInsideViewport`, `safePointInsideViewport`
- `sampledAimpoints`, `acceptedAimpoint`, `rejectedAimpoints`
- `rejectionReason`

The action proposer stores this as `targetExplanation.safeAimPoint`. If the safe
aimpoint fails for a resource target, the proposal is explanatory but
non-executable and reports missing capability `safe_aimpoint`. Projection
sentinel coordinates such as `2147483647` are invalid aim points and must not
satisfy readiness. Common unsafe reasons include `centerOffViewport`,
`centerOutsideInteractableRegion`, `noVisibleInteractableGeometry`, and
`uiBlocked`.

### Service Route Context

`service_route_context.v1` is the bounded service-navigation prior used when a
task needs a service target but no bank booth, banker, bank chest, or deposit
target is currently visible. It is produced in memory by `service_route_core.py`
from `telemetry-viewer\profiles\service_routes.json` plus live candidates.

Route priors are not truth. Static OSRS/wiki/manual knowledge can seed labels,
rough anchors, expected menu options, and expected plane changes, but live
RuneLite telemetry remains authoritative for exact world tile, plane, object id,
visibility, click geometry, menu option, and whether a route step can be
clicked.

Core fields:

- `schema=service_route_context.v1`
- `routeAvailable`, `routeId`, `routeVerifiedLive`, `routeConfidence`
- `routeNodes`, `routeEdges`: a bounded route graph. Nodes may be world tile
  anchors, live object anchors, stair/ladder transitions, bank/service targets,
  or fallback scouting points. Edges describe route operations such as
  `walk_to`, `reacquire_visible_target`, `interact_climb_up`,
  `wait_for_plane_change`, and `interact_bank`.
- `routeSteps`: the ordered low-confidence step prior. For Lumbridge Castle
  bank this is staged through west approach, entrance/courtyard, first-stairs
  search, first climb-up, second climb-up, and bank service.
- `routeStepStatus`: `service_target_visible`, `route_interaction_visible`,
  `retained_service_anchor`, `static_route_prior`, `route_anchor_missing`, or
  `route_missing`
- `currentStepIndex`, `currentStep`, `currentNodeId`, `nextEdge`
- `currentNavigationTarget`: a low-confidence `service_route_anchor` world tile
  used for scouting/pathing, or a previously observed service anchor used only
  as a navigation target until visible again. Neither is a transition click.
- `routeContext`: a nested `route_context.v1` summary for current-area/source
  selection. It records whether the current player location is a known route
  source, nearby known source, unmapped source, or wrong source for the route.
  It also exposes `routeMode`, `selectedServiceAnchor`,
  `selectedApproachNode`, and route-source mismatch details.
- `routeMode`: `explicit_route`, `reverse_route`, `goal_directed_fallback`,
  `local_frontier_to_service`, or `unknown`.
- `selectedServiceAnchor`: the destination service goal, such as the Lumbridge
  Castle bank anchor. This is a navigation goal, not a click target unless a
  live service object is visible/actionable.
- `selectedApproachNode`: the destination-centered approach node chosen from
  the current player location, such as castle entrance/courtyard before stair
  search.
- `goalDirectedFallback`: true when the current source area is unmapped or
  mismatched but a known service anchor exists.
- `visibleInteractionTarget`: a live stairs/ladder/door-like object that can be
  proposed as `interact_service_route_object`
- `visibleServiceTarget`: the existing service target when bank/booth/banker is
  visible; normal `open_service` handling wins
- `routeObjectsVisible`, `routeObjectsActionable`, `serviceObjectsVisible`,
  `routeRelevantObjects`, `routeRelevantActionableObjects`,
  `visibleButRouteIrrelevantObjects`, and `selectedRouteObjectPresent`:
  route/service object counts that are separate from Tree/Oak resource
  candidate counts. A service route can legitimately show resource safe `0/N`
  while `routeRelevantActionableObjects > 0`.
- `routeObjectCensus`: a bounded `service_route_object_census.v1` summary. It
  lists route-transition/service candidates separately from resource
  candidates, records source lane counts, top route objects, rejection reasons,
  and whether a visible object was route-relevant, actionable, or merely
  visible-but-route-irrelevant.
- `serviceObjectCensus`: a bounded `service_object_census.v1` summary nested
  under the service-route context. It reports service candidates separately
  from both resources and route transitions: `serviceObjectCandidatesTotal`,
  `bankBoothCandidates`, `bankerCandidates`, `depositBoxCandidates`,
  `visibleServiceObjects`, `actionableServiceObjects`,
  `routeRelevantServiceObjects`, `routeRelevantActionableServiceObjects`,
  `visibleButRouteIrrelevantServiceObjects`,
  `rejectedServiceObjectsByReason`, scan source/limit fields, and
  `topServiceObjects` with projection status and relevance. At
  `lumbridge_castle_bank`, a route-relevant actionable service object becomes
  `visibleServiceTarget` and sets `actionReady=true` for `open_service`.
- `selectedRouteObjectRelevance`: `route_relevance.v1` for the selected object.
  It checks route id, current route step, expected action/target kind, plane,
  expected plane change, distance to the route search area/corridor, and
  whether clicking the object would advance the route.
  `selectedServiceObject`, `selectedServiceAction`,
  `selectedServiceObjectRelevance`, `serviceObjectRejectedReason`, and
  `serviceObjectInterceptReady` mirror the same decision for Bank booth,
  Banker, Deposit box, or Bank chest candidates. `selectedServiceAction`
  prefers the current route step's expected action, such as `Bank`, over less
  useful object actions such as `Collect`.
- `interactionExpectedOptions`, `interactionExpectedTargets`,
  `expectedPlaneChange`
- `observedAnchors`, an in-memory cache of route objects seen live in the
  current daemon. Anchors include object id/name/world tile/actions,
  `lastSeenTick`, `confidence`, and `verificationSource`.
- `completedSteps`, derived from live plane/location evidence and successful
  interactions. A later floor can mark earlier stair steps completed, but does
  not make unseen future steps clickable.

`interact_service_route_object` uses the same safe aimpoint and client-tick
hover confirmation path as resource clicks. A fresh client-tick hover that
predicts a route object action, such as `Climb-up Staircase`, is recorded as
hover-discovered evidence. It may intercept waypoint walking only when route
relevance is resolved for the active route step; otherwise it is reported as
`hover_confirmed_but_route_unresolved` and is not clicked from hover alone.
After the click, lifecycle verification expects route progress such as plane
change, player location change, route-step change, or service readiness.

#### Route Context

`route_context.v1` keeps Lumbridge service navigation from being tied to one
tree cluster. It is emitted inside `service_route_context.v1` and summarizes
the current source area, destination goal, and route mode:

- `currentLocation`, `currentPlane`
- `currentAreaLabel`, `currentAreaConfidence`, `currentAreaSource`
- `resourceArea`: current or remembered resource-area centroid/bounds when
  known from live collection or profile anchors
- `serviceGoal`: selected service anchor, service type, plane, confidence, and
  source
- `routeMode`: `explicit_route`, `reverse_route`, `goal_directed_fallback`,
  `local_frontier_to_service`, or `unknown`
- `selectedRouteId`, `selectedEntryNode`, `selectedApproachNode`
- `routeSourceStatus`: `known_source`, `nearby_known_source`,
  `unmapped_source`, or `wrong_route_source`
- `routeSourceMismatch`: distance and diagnostic details when the active route
  prior's source does not match the current location
- `blockerReason`: a clear diagnostic when no route/source/goal can be used

When the current player location matches the configured west-tree source, the
route remains `explicit_route`. When the location is not a known source but the
Lumbridge Castle bank anchor is known, the route switches to
`goal_directed_fallback`: it chooses an approach node from the current
coordinates and lets pathing produce a safe local frontier waypoint toward that
approach. Route objects and service objects still outrank waypoint walking as
soon as live telemetry makes them route-relevant and actionable.
Approach nodes are considered complete after direct arrival, or after the
player has moved beyond the node along the bankward corridor. This keeps a
bridge/approach marker from being selected again behind the player during
goal-directed fallback.

For destinations outside the current collision window,
`pathing_context.v1` may expose `localFrontierWaypoint`,
`frontierDistanceBefore`, `frontierDistanceAfterEstimate`, and `progressScore`.
These fields explain how the local scout waypoint reduces distance toward the
selected service/approach anchor without clicking the far anchor directly.

`open_service` lifecycle verification accepts path-to-interact evidence before
the bank UI opens. If the click does not immediately open a bank/deposit UI but
the player tile changes, service/path distance decreases, or the route status
advances, the result is `service_object_pathing_to_object` and the next action
remains blocked while OSRS continues walking to the service object. A timed-out
service click without UI or movement is `service_object_no_progress`.

`bank_ui_context.v1` includes compact bank/deposit interface state plus the
current inventory summary. When a bank/deposit UI is readable, it may also
include `inventorySlots`, a bounded list of bank-side inventory item widgets
with slot, item id, quantity, bounds, aim point, visible state, actions, and
source. `bank_operation_context.v1` uses those widgets to expose
`resourceItemSlotBounds`, `resourceItemWidgets`, and `resourceDisplayName` for
selective resource depositing. If the bank is closed after service but the
retained inventory summary proves no target resources remain, the bank
operation context reports `bankingComplete=true` with
`completionReason=no_resource_items_held` instead of waiting for a readable bank
again.

`routeWaypointSelection` can appear in an action proposal target explanation
when adaptive waypoint selection chooses from structured path/route tiles. It
records mode, reason, selected tile, considered tile count, lookahead, horizon,
and selected waypoint distance. Structured alternates replace arbitrary dense
pixel probing around an occluded waypoint.

Route tile projections are advisory until they produce actionable canvas
geometry. Degenerate origin polygons, tiny projection bounds, and off-viewport
tile projections are rejected for canvas clicks; route navigation can then try
structured alternate path tiles or stop safely.

`actionTargetSource` and `actionability` are attached to action proposal
payloads and target explanations. Valid sources are `static_route_prior`,
`retained_anchor`, `route_context_goal`, `local_frontier_waypoint`,
`live_projected_waypoint`, `live_route_object`, `live_service_object`,
`live_resource_candidate`, `hover_discovered_object`, and `unknown`.
Valid actionability values are `advisory_only`, `needs_live_projection`,
`needs_hover_confirmation`, `executable`, `stale`, and `blocked`. Static route
priors and route context goals are never directly executable; retained anchors
must be upgraded by fresh projection or hover evidence. Executable proposals
must be backed by current live state and correct intent-specific hover/menu
evidence.

Resource target explanations may also include `resourceSelectionReason` and
`reacquiredFromResourceTarget`. For woodcutting, target selection can use a
known Woodcutting level when present. If the level is unknown, basic live
`Tree` candidates are preferred over higher-level Oak/Willow candidates when
both exist; this prevents a low-level live client from looping on a target that
only produces a no-progress level-required message. Hover confirmation,
safe aimpoint, freshness, and actionability still gate the click.

`route_projection_status.v1` is attached to route waypoint target explanations
after plugin tile projection. It records `worldTile`, `canvasPoint`,
`canvasTileBounds`, `inCanvas`, `inViewport`, `degenerateProjection`,
`tinyProjection`, `offscreen`, `uiBlocked`, `edgeClipped`, edge distances,
`projectedVisibleAreaPx`, `projectedVisibleAreaRatio`, `partiallyOffscreen`,
`objectOccluded`, hover option and target when known, `actionableByCanvas`,
`actionableByMinimap`, `classification`, and `rejectionReason`. This is the
quick answer to whether a route tile is visible, edge-clipped, offscreen,
degenerate, occluded, or non-actionable. Edge-clipped route tiles should be
alternate-waypoint or camera-reacquire candidates, not direct canvas clicks.

### Action Trace V2 Additions

`action_trace.v2` records the selected target explanation, including
`rawAimPoint` and `safeAimPoint`, the intended canvas/screen point, hover
confirmation samples, clicked-menu before/after samples, human input governor
metrics, camera input metrics, game-tick verification timeline, and final
classification.

Loop execution also records:

- skipped hover/geometry checks separately from actual click attempts
- `cancelHoverFailures`, `walkHereHoverFailures`, and stale hover samples
- target suppression/reacquisition fields: `targetsSuppressed`,
  `suppressedTargets`, `targetReacquireRounds`, `targetReacquireWaits`, and
  `targetReacquireWaitMillis`
- stale/static proposal reacquire fields: `staleProposalDetected`,
  `staleProposalSource`, `reacquireAttempted`, `reacquireResult`,
  `freshTargetFound`, `freshTargetSource`, and `reasonIfNoFreshTarget`
- clicked-menu mismatch fields under `clientTick.menuMismatch`: expected
  intent, hover-before-click, actual clicked menu, classification, mismatch
  reason, and likely causes such as stale hot sample, hover flip, target
  occlusion, or focus issue
- `menu_flip_mismatch` as an action classification when hover predicted the
  intended action but the actual `MenuOptionClicked` event reported another
  menu action
- volatile navigation hover fields under `clientTick`: `menuTailVolatility`,
  `recentMenuTail`, `volatileHoverZone`, and `volatileReasons`. These are
  derived from bounded `postMenuSortTail` samples near the intended waypoint;
  recent NPC/object/widget actions make a `Walk here` waypoint volatile and
  cause a no-click skip before mouse-down.
- service-route stability fields: `navigationInProgress`,
  `routeStability`, clicked waypoint tile, player location after the click,
  movement state, and replan-suppression reason. Immediate waypoint cycles are
  reported as `route_oscillation_detected`, `route_backtracking_detected`, or
  `route_wall_hugging_detected` instead of being clicked again.
- `actualClicks`, `expectedMenuClicks`, `walkHereClicks`, and `cancelClicks`
- optional final reconciliation result from `--final-reconcile-ms` and
  `--final-reconcile-game-ticks`
- timeout summary fields: `unresolvedTimeouts`, `timeoutReasons`,
  `timeoutActionTypes`, `timeoutRecoveredBy`, and `evidenceAfterTimeout`
- optional pacing fields: `pacingProfile`, `appliedDelayMs`, and
  `pacingReason`
- human input fields: `profile`, `movementGenerator`, mouse move and click-hold
  timing summaries, reaction-delay summaries, camera-hold summaries,
  `cameraDirectionSwitches`, and `directBackendBypassCount`

Projection refs are prioritized before capping by generic usefulness:
on-screen scene objects with geometry, stable IDs/locations, and player-near
scene coordinates come first. `projectionFieldMode=compact` keeps only
candidate-building fields and omits heavy geometry/debug metadata. Geometry is
omitted unless requested. The endpoint has no arbitrary file serving and no
command routes. If a capped response still
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

`live_target_processor.py --input-source plugin-snapshot` is the normal runtime
path. The processor posts `plugin_snapshot_request.v1` to `/snapshot`, asks for
cached baseline, scene delta, projection, inventory, inventory delta, activity,
navigation, collision window, writer health, watch values, world-model data, and
compact `interaction_hot`, then converts the response payloads into the bounded
in-memory/live-context shape used by the daemon and Knowledge Fabric. Context
service and AI clients read the daemon/query layer, not live packet archive
files.

Projection conversion accepts both raw payloads and packet-envelope payloads.
The recognized ref lists are `visibleObjectRefs`, `visibleSceneObjectRefs`,
`projectedRefs`, `refs`, `targets`, `sceneObjects`, and
`projectedSceneObjects`. Each ref is normalized into the compact scene-object
shape used by file input: id/hash/name/kind, world/scene/local coordinates,
`onScreen`, `geometryAvailable`, aim point, bounds, and optional hull/tile
geometry. If only compact `bounds` are present, Python uses them as compact
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
- `clientTickHot`
- `clientTickHotSchema`
- `clientTickLatest`
- `clientTickGameTickAtSample`
- `clientTickTopOption`
- `clientTickTopTarget`
- `clientTickPostMenuSortAgeMillis`
- `clientTickLastClickedOption`
- `clientTickLastClickedTarget`
- `clientTickLastClickAgeMillis`
- `clientTickSamplesBuffered`
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

The processor defaults to plugin-snapshot. `--input-source auto`, compact packet
file input, compact stream input, and raw-tick packet input have been removed
from normal runtime. Use WorldModel/Knowledge Fabric queries, replay scenarios,
or script-authoring context bundles for comparison/debug evidence.

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
python telemetry-viewer\live_target_processor.py --from-daemon --daemon-url http://127.0.0.1:8890 --input-source plugin-snapshot --plugin-snapshot-tier hot --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
python telemetry-viewer\live_target_processor.py --from-daemon --daemon-url http://127.0.0.1:8890 --input-source plugin-snapshot --plugin-snapshot-tier expanded --plugin-snapshot-host 127.0.0.1 --plugin-snapshot-port 8893 --plugin-snapshot-projection-field-mode compact --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --summary --benchmark
```

Snapshot conversion diagnostic:

```text
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --max-projection-refs 500 --dump-synthetic-shape
python telemetry-viewer\diagnose_plugin_snapshot.py --latest-session --profile woodcutting --tier-sweep
```

If diagnostics report `projection refs capped` and snapshot candidates are zero
while the world model shows relevant objects, the likely cause is cap/order
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

The former compact stream/file bridge has been retired with the live packet
archive. RuneLite no longer exposes config items to start a packet stream or to
mirror stream packets to `live_packets`. Python runtime CLIs no longer accept
compact packet, compact stream, raw-tick, or auto packet fallbacks as live truth.

Equivalent current data is exposed through endpoint/query payloads:

- `client_tick_hot.v1` for hover/menu/click proof
- `world_model_snapshot.v1` for loaded-scene objects, actors, collision,
  projection, minimap, and UI summaries
- Knowledge Fabric query results for route/resource/service/path/view context
- `writerHealth` status fields that explicitly report
  `livePacketsRuntimeRemoved=true`, `ndjsonRuntimeRemoved=true`,
  `jsonlRuntimeRemoved=true`, and `livePacketWriterActive=false`

Historical packet field names may still appear in old replay notes or
compatibility status fields, but they are not runtime sources and are not
callable writers.

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
input source, plugin/daemon freshness, world-model availability, data-quality
warnings, and retired packet-archive status fields such as
`livePacketsRuntimeRemoved=true` and `livePacketWriterActive=false`.

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
- `source`: plugin snapshot, world model, context endpoint, static library,
  session memory, or debug/replay artifact
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
unbounded identifiers, unsupported types, normal-live-disabled settings, or
limit violations. Accepted requests are written to the
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

Target summaries may include rank, `isBest`, `isNearest`, class/name/id,
category, world and scene tile, source/latest tick, on-screen and geometry
flags, quality tier/score, target liveness, `livenessInterpretation`, direct
reachability, path length, `interactionRadiusTiles`, collision-window
membership, capped reachability evidence, `labelParts`, `overlayLabel`,
`overlayColor`, aimPoint, compact bounds, `safeAimPoint`, `actionable`,
`validButUnsafe`, `validButUnsafeReason`, and small polygons when available.
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
- `safeAimpoints` and `executableTargets`: how many written overlay targets have
  a safe visible/interactable aimpoint and are action-ready.
- `invalidAimpointTargets`: written targets whose raw aim point was rejected as
  invalid, for example a projection sentinel coordinate.
- `edgeClippedCandidates`: written targets whose safe-aimpoint evaluation
  involved clipped/edge geometry.
- `selectedTargetPresent` and `selectedSafeAimPoint`: whether the selected/best
  marker exists and whether it has a safe actionable aimpoint.
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

The retired compact packet file/stream mode used to emit
`live_navigation_packet.v1`. Normal runtime no longer emits this packet as a
live file or stream. Current navigation context comes from the 8893 snapshot,
WorldModelCache, the 8890 daemon, and Knowledge Fabric queries such as
`query_path_frontier`. The retained schema summary is historical/debug context:

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

The retired compact navigation path also used
`live_collision_window_packet.v1`. Current collision/pathing truth is exposed
through WorldModelCache and capped query/debug bundles, not a packet archive.
Historical/debug payloads are bounded local grids:

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

`live_packet_index.json` and `latest_segment.txt` are retired legacy packet
archive files. Current runtime does not create them. If they exist in an old
session, use the maintenance report/prune commands; do not treat them as live
truth.

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
Snapshot No-File uses cached plugin snapshots and WorldModel/Knowledge Fabric
queries as the normal runtime path. The daemon builds candidate and context
state in memory and serves context-service-compatible HTTP responses. It is
read-only and does not expose click, mouse, keyboard, menu, invoke, execute, or
command endpoints.

Health and status endpoints keep the existing schemas:

- `GET /health` returns `context_health.v1` with `service=live_core_daemon`,
  `liveCoreDaemonActive=true`, `inputSourceActive`, `dailyMode`,
  `noFileDaily`, `livePacketsRuntimeRemoved`, `livePacketWriterActive=false`,
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

## Camera-Guided Waypoint Exposure

`camera_exposure_score.v1` is Python-side action telemetry used for
service-route navigation. It combines plugin tile projection, camera viewport,
and `client_tick_hot.v1` hover samples to decide whether a route waypoint is
visually exposed enough for a navigation click.

Important fields:

- `classification`: `exposed_walk_here`, `occluded_by_object`, `offscreen`,
  `edge_blocked`, `no_projection`, `no_camera_delta`, `worsening`, `timeout`,
  or `ambiguous`
- `score`
- `targetWorldTile`
- `waypointCanvasPoint`
- `projectionAvailable`, `projectionDeltaPx`
- `mousePositionMatchesProjection`
- `hoverOption`, `hoverTarget`, `hoverMenuClass`
- `hoverMatchesWalkHere`
- `blockingHoverOption`, `blockingHoverTarget`
- `distanceToViewportEdgePx`
- `waypointTileBounds`
- `onScreen`
- `geometryAvailable`
- `cameraYaw`, `cameraPitch`, `yawDelta`, `pitchDelta`

`action_trace.v2.reacquisition.cameraExposureAttempts` records bounded
closed-loop exposure attempts. Each attempt keeps the same `targetWorldTile`,
records `cameraMethod`, `cameraCommand`, held `cameraKeys`, `cameraMoved`,
`projectedCanvasBefore`, `projectedCanvasAfter`, `cameraViewportBefore`,
`cameraViewportAfter`, `exposureScoreBefore`, `exposureScoreAfter`, and compact
per-sample hover/projection records. Keyboard methods hold keys down while
sampling projection and `PostMenuSort`; middle-mouse drag is a pulse fallback
that releases before hover sampling. Camera exposure is only valid for
`navigation_waypoint_action`, where `Walk here` is the expected menu action. It
must not authorize resource-object, service-object, or route-transition clicks.
If the same world tile never reprojects to a fresh `Walk here` hover sample, the
executor skips the click. A camera adjustment is counted only when yaw/pitch or
the target projection changes.

## Dialogue State

`dialogue_state.v1` is a compact read-only plugin snapshot/cache packet for
chatbox dialogue that can block a route transition after a successful object
click.

Important fields:

- `active`
- `type`: `options`, `click_to_continue`, or `unknown`
- `promptText`
- `options[]`: `index`, `key`, `text`, `widgetGroup`, `widgetChild`, `bounds`,
  and `visible`
- `canUseNumberKeys`
- `canUseSpaceContinue`
- `source`
- `widgetRootIds`
- `latestClientTick`
- `wallTimeMillis`

`interface_dialogue_choice_action` is the action intent used when the active
service route expects a dialogue option. For the Lumbridge staircase prompt,
`planeChange="+1"` selects the option matching `Climb up`, usually key `1`, and
`planeChange="-1"` selects `Climb down`, usually key `2`. Number-key selection is
preferred when the packet says or strongly implies that numbered options are
usable; visible widget bounds are the fallback click target. `Click here to
continue` is separate and may use space only for continue prompts, not up/down
choice prompts.

`action_trace.v2.dialogue` records the prompt, available options, expected and
selected option, selection method, key pressed or widget clicked, and the route
state after verification.

## Intent-Aware Readiness

`live_readiness.v2` separates overall context health from the readiness of the
next action intent.

Important fields:

- `status`: overall `PASS`, `WARN`, or `FAIL`
- `currentIntent`: `resource_object_action`, `navigation_waypoint_action`,
  `service_object_action`, `route_transition_action`,
  `interface_dialogue_choice_action`, `camera_adjustment_action`, or `unknown`
- `actionReadiness.status`
- `actionReadiness.executionAllowed`
- `actionReadiness.blockers`
- `actionReadiness.warnings`
- `actionReadiness.checks`
- `actionReadiness.checksSkippedAsNotApplicable`
- `actionReadiness.checks.proposalActionTargetSource`
- `actionReadiness.checks.proposalActionability`
- `actionExecution.proposalActionTargetSource`
- `actionExecution.proposalActionability`
- `contextReadiness.status`
- `contextReadiness.warnings`
- `contextReadiness.applicableWarnings`
- `contextReadiness.nonApplicableContextWarnings`
- `staleFileSessionContext`
- `daemonSessionFresh`
- `pluginSnapshotFresh`
- `selectedResourceTargetFreshnessApplicable`
- `selectedResourceTargetFreshnessStatus`

The executor gates live input on `actionReadiness.executionAllowed`. Resource
object actions remain strict: they require selected resource/highlighter
agreement, freshness, visible geometry, safe aimpoint, and hover-confirmable
resource menu state. Navigation waypoint actions require a route/path waypoint,
fresh daemon/session state, plugin snapshot when active, fresh `client_tick_hot`
interaction state, and input geometry; they allow `Walk here` and do not
require the selected Tree/Oak resource target to be present in the current
highlighter source. Service-object and
route-transition actions require their own visible/actionable target and
expected menu option. Dialogue choice actions require active `dialogue_state`,
a route-matching expected option, and the input controller; they do not require
a route object or hover-confirmable `Walk here` target anymore. Context-only
mismatches remain visible under
`contextReadiness.warnings` so diagnostics do not hide them. The diagnostic
also separates `applicableWarnings` from `nonApplicableContextWarnings`; for
example, stale selected resource-target freshness is non-applicable while the
current intent is `navigation_waypoint_action`, but becomes applicable again
for `resource_object_action`. A stale newest filesystem session sets
`staleFileSessionContext=true` without overriding a fresh daemon/latest-live
session match.

If the current navigation proposal is `advisory_only`, `stale`, or `blocked`,
or if its source is still `static_route_prior` / `route_context_goal`,
readiness blocks execution with structured reasons such as
`static_target_not_executable` or `stale_target`. The action loop can perform a
bounded reacquire, but the stale/static proposal itself must not become a click.

Client-tick readiness blockers include `gameState`, `isLoggedIn`,
`staleReason`, hot-state ages, and a recovery hint. Execution must remain
blocked until `actionReadiness.executionAllowed=true` and the current
intent-specific hot-state requirement is satisfied.

### Location And Coordinate Sources

Daemon status and debug bundles prefer authoritative baseline/player location
from the plugin snapshot. If that is not available, they may expose
`collision_window_center_proxy` as a lower-confidence fallback. Route context
fields include `locationSource` and `locationConfidence` so a collision-window
center is not mistaken for an exact player tile.

VM input geometry records logical-to-physical conversion metadata whenever a
canvas point is resolved to a pyautogui screen point:
`coordinateSpace`, `scaleX`, `scaleY`, `screenPointBeforeScaling`,
`screenPointAfterScaling`, `windowBoundsSource`, and `canvasBoundsSource`.
`scaled_logical_to_physical` means the whole AWT logical screen point was
scaled to physical Windows/pyautogui pixels.

### Return Route Context

`return_route_context.v1` is emitted in daemon brain/status after service
completion when a resource return destination is known. It does not write a new
continuous file.

Important fields:

- `sourceRouteId`: service route being reversed or paired with a return route.
- `returnRouteId`: return route identifier.
- `state`: `service_complete`, `bank_ui_closing`, `return_route_ready`,
  `return_transition_actionable`, `returning_to_resource`,
  `resource_area_reached`, `resource_reacquired`, or `return_blocked`.
- `currentNodeId`, `nextEdge`, `currentStep`
- `targetResourceArea`
- `resourceAnchor`: source, world tile/area, plane, confidence, and age.
- `returnActionReady`
- `returnBlockedReason`

For Lumbridge Castle bank, return steps descend the bank-floor staircase and
first-floor staircase with `Climb-down` or the generic up/down dialogue's
`Climb down the stairs.` option, then navigate through the ground-floor /
castle-west approach nodes back to the west-tree resource area. A route-relevant
down staircase can produce `interact_service_route_object`; ordinary return
waypoints produce `return_to_resource_area`.

`resource_return_context.v1` may use a profile anchor when no live resource
memory is available. The profile anchor is low confidence and only seeds the
return route; live RuneLite telemetry still confirms route objects, planes,
waypoints, and final resource reacquisition.

### Full Lifecycle Soak Summary

`execute_next_action.py` loop summaries expose full-cycle counters for bounded
woodcut-bank-return validation:

- `lifecycleCyclesStarted` / `lifecycleCyclesCompleted`
- `collectionPhasesStarted`
- `inventoryFullEvents`
- `serviceRoutesStarted` / `serviceRoutesCompleted`
- `bankOpenEvents`
- `depositSuccesses`
- `serviceCompleteEvents`
- `returnRoutesStarted` / `returnRoutesCompleted`
- `resourceReacquisitions`
- `postServiceResourceCollections`
- `postServiceLogsCollected`
- `consecutiveNoProgress`
- `consecutiveTimeouts`
- `edgeRouteClicksRejected`
- `cameraReacquireOnEdgeCount`
- `unresolvedTimeouts`
- `timeoutReasons`
- `timeoutActionTypes`
- `timeoutRecoveredBy`

A lifecycle cycle completes only after collection reaches service, resources are
deposited, the return route reacquires the resource area, and at least one
post-service resource is collected. The stop flags
`--stop-after-lifecycle-cycles`, `--stop-after-service-cycles`, and
`--stop-after-post-service-logs` use those counters. `--max-total-actions`,
`--max-wall-time-minutes`, `--max-consecutive-no-progress`, and
`--max-consecutive-timeouts` are soak safety bounds.

Post-bank action selection treats `bankingComplete=true` and zero held target
resources as stronger than stale `serviceNeeded=true` proximity/service-object
signals. While the bank UI remains open, `close_bank` is the expected action;
after it closes, return/resource reacquisition should run instead of reopening
bank service.

For `select_resource_target`, observed results may include
`resourceProgressClassification`:

- `resource_click_confirmed_waiting`
- `resource_animation_started_pending`
- `resource_delayed_inventory_success`
- `resource_target_depleted_success`
- `resource_timeout_no_progress`
- `resource_timeout_reconciled_success`

`--resource-reconcile-ms`, `--resource-reconcile-game-ticks`, and
`--post-click-progress-tail-ticks` extend the final reconcile window for
resource clicks. If initial timeout is later proven successful by inventory,
resource count, progress, activity, depletion, or task-state evidence,
`delayedProgressReconciliation=true` is recorded and timeout counters are not
incremented for that action.
If a no-progress timeout leaves a fresh resource target selected, the live loop
may keep a bounded observation window before clicking again. Late inventory or
progress evidence is then reported as `resource_timeout_reconciled_success`
rather than an unresolved timeout.

### Human Input Governor

`human_input.v1` is embedded in `action_trace.v2.humanInput` and loop summaries.
It records the motor envelope used after fast perception has selected or
confirmed an action.

Important fields:

- `profile`: `instant_debug`, `steady`, `natural`, or `manual_calibrated`
- `movementGenerator`: configured, Fitts-guided, or variable path generator
- `movementCount`, `clickCount`, `keyHoldCount`
- `averageMouseMoveMs`, `mouseMoveMinMs`, `mouseMoveMaxMs`
- `averageClickHoldMs`, `clickHoldMinMs`, `clickHoldMaxMs`
- `averageReactionDelayMs`, `reactionDelayMinMs`, `reactionDelayMaxMs`
- `cameraHoldMinMs`, `cameraHoldAvgMs`, `cameraHoldMaxMs`
- `cameraDirectionSwitches`
- `liveInputBackend`
- `liveInputBackendRequired`
- `softwareInputAllowed`
- `backendCommandCount`
- `backendBlockedCommandCount`
- `directBackendBypassCount`

`action_trace.v2.cameraInput` mirrors the camera-specific subset for quick
debugging. Live executor paths should normally report
`directBackendBypassCount=0`; backend implementations are the low-level adapter
exception. `manual_calibrated` is a reserved profile name and currently uses the
natural envelope until a future calibration file is explicitly introduced.

### Arduino HID Live Input

`live_input_policy.v1` is attached to action traces and loop summaries when a
command can emit live input. `--execute`, `--hover-only`, and
`--camera-self-test` require Arduino HID by default. Software backends remain
available for dry-runs, screenshots, tests, and explicit unsafe/debug override
only.

Important fields:

- `liveInputBackend`: expected to be `arduino` for normal live execution
- `liveInputBackendRequired`
- `softwareInputAllowed`
- `backendIsArduino`
- `backendIsSoftware`
- `status`
- `blockReason`
- `arduino`: `arduino_hid_backend_status.v1`
- `monitor`: `arduino_hid_monitor_status.v1` when monitor-required mode is used

`arduino_hid_backend_status.v1` records serial port, baud, connection,
identified/armed state, session token hash, command count, ACK failures,
timeouts, identity, capabilities, firmware protocol, watchdog milliseconds,
`stopAllSent`, and latest Arduino error. The required firmware protocol is
`arduino_hid.v1`: `PING`, `IDENTIFY`, `CAPS`, `STATUS`, `ARM <token>`,
`DISARM`, `STOP_ALL`, relative `MOVE`, mouse down/up/click, key down/up/press,
and bounded key holds. `STOP_ALL` is allowed while disarmed and releases every
tracked key/button before clearing the armed state. `arduino_hid_monitor_status.v1`
records Raw Input evidence, expected VID/PID match, injected and lower-integrity
injected counters, latest Arduino event age, `monitorPass`, and
`monitorBlockReason`.

`input_integrity_status.v1` is the richer monitor/overlay schema used by the
Arduino-only live path. It is compact and can be read from
`interaction_geometry/live/input_integrity_status.json` or a local status URL.
It contains:

- `monitorRunning`, `monitorAgeMs`, and `generatedAtUtc`
- `arduinoExpected`: expected VID, PID, serial, device path, and COM port
- `arduinoDetected`: Raw Input device presence, keyboard/mouse presence,
  expected VID/PID/device-path/COM matches
- `arduinoActivity`: last mouse/keyboard/any-event ages plus Raw Input and
  event counters
- `injectionFlags`: `mouseInjectedCount`, `mouseLowerIlInjectedCount`,
  `keyboardInjectedCount`, `keyboardLowerIlInjectedCount`, and last injected
  event age
- `backend`: `liveInputBackend`, Arduino selection/armed state,
  `softwareInputAllowed`, and `directBackendBypassCount`
- `firmware`: `status`, `protocol`, `name`, `version`, `board`, `resetSafe`,
  `stopAll`, `watchdog`, `watchdogMs`, `armed`, `keysDown`,
  `mouseButtonsDown`, and `lastCommandAgeMs`
- `status`, `blockers`, and `warnings`

When `--arduino-require-monitor` is active, `execute_next_action.py` blocks
live execution if the status is stale/missing, the expected Arduino VID/PID or
device evidence is absent, injected or lower-IL injected counts increase during
the action window, the Arduino is unarmed for the live session, firmware does
not report `protocol=arduino_hid.v1`, reset safety / `STOP_ALL` / watchdog
capabilities are absent, firmware reports held keys/buttons, or
`directBackendBypassCount` is nonzero. Action traces include
`inputIntegrityStatusBefore`, `inputIntegrityStatusAfter`, monitor pass fields,
injected-count deltas, lower-IL deltas, and the live backend. Visual debug
bundles may include `input_integrity_status.json`.

`arduino_closed_loop_move.v1` is the behavioral cursor-safety trace for
Arduino absolute movement. It records `cursorPositionBefore`,
`cursorPositionAfter`, `targetScreenPoint`, `allowedRegion`, `movementChunks`,
`chunkCount`, `maxChunkSize`, `positionErrorPx`, `leftAllowedRegion`,
`foregroundWindowBefore`, `foregroundWindowAfter`,
`targetInsideAllowedRegion`, `cursorInsideAllowedRegion`,
`foregroundWindowAllowed`, and `movementAbortedReason`. On any abort the
backend sends `STOP_ALL` and `DISARM`.
Movement chunks also report bounded settle/retry evidence:
`chunkAckTimeMs`, `settleWindowMs`, `pollCount`,
`cursorDeltaObserved`, `rawInputDeltaObserved`,
`firstCursorDeltaTimeMs`, `firstRawInputDeltaTimeMs`,
`targetErrorBefore`, `targetErrorAfter`, and classifications such as
`move_chunk_success`, `move_chunk_delayed_success`,
`move_chunk_retry_success`, `move_chunk_rawinput_seen_cursor_no_move`,
`move_chunk_no_rawinput_no_cursor`, and `move_chunk_no_effect_abort`.
The calibration pass criteria are statistical: final target error inside
tolerance, cursor stayed in the allowed region, no clicks or keys, no injected
or lower-IL input, `directBackendBypassCount=0`, and no excessive consecutive
no-effect chunks.

`arduino_pointer_calibration_test.v1` is the no-click calibration result. It
contains the allowed RuneLite or calibration-window region, target points,
movement traces, max cursor error, foreground before/after, final firmware
status, monitor status, `clickSent=false`, `keySent=false`, and
`directBackendBypassCount=0`. It also includes `movementMetrics`,
`totalChunks`, `successfulChunks`, `retryChunks`, `noEffectChunks`,
`movementSuccessRate`, `maxPositionErrorPx`, and `finalPositionErrorPx`.

Until calibration is reviewed, Arduino RuneLite `--execute` and `--hover-only`
commands fail closed with `arduino_live_movement_block.v1` and
`reason=arduino_pointer_calibration_required`. Jagex Launcher startup
automation is also fail-closed by default; `runelite_bootstrap.v1` includes
`launcherAutomationAllowed`, `launcherAutomationBlockedReason`, and
`loginRecoveryMode`.

Firmware reset safety is part of the schema contract. The Arduino sketch must
release all keyboard keys and mouse buttons in `setup()`, start `armed=0`, clear
its key/button trackers, avoid motion during boot, avoid persisting an old
session token, and print a safe boot line such as
`OK BOOT armed=0 released=1 protocol=arduino_hid.v1`. A white-board reset is
therefore a secondary recovery action; physical USB unplug remains the final
panic stop.

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

The Java writer reports its recording behavior in `manifest.json` and
writer-health/status payloads.

Common fields:

- `recordingMode`: `LIVE_COMPACT_ONLY`, `LIVE_COMPACT_WITH_FRAMES`,
  `DEBUG_RECORDING`, or `HYBRID_DEBUG`.
- `rawTickRecordingEnabled`: whether full `ticks\ticks-*.jsonl` records are
  being written.
- `rawEventRecordingEnabled`: whether full `events\events-*.jsonl` records are
  being written.
- `frameRecordingEnabled`: whether `frames\*` and `frame_index.jsonl` are being
  written.
- `compactPacketRecordingEnabled`: always false for the retired packet archive.
- `livePacketsRuntimeRemoved`, `ndjsonRuntimeRemoved`, `jsonlRuntimeRemoved`:
  true when the runtime no longer supports packet archives.
- `livePacketWriterActive`: false in current builds.
- `rawTicksWritten`, `rawTicksSuppressedByMode`, `rawEventsWritten`,
  `rawEventsSuppressedByMode`, `framesWritten`, and
  `framesSuppressedByMode`: counters for written or mode-suppressed data.

Mode behavior:

- `LIVE_COMPACT_ONLY` is the normal live mode name, but it no longer emits
  compact packet files. Snapshot/WorldModel/Knowledge Fabric queries are the
  live path; full raw ticks/events and frames are not written.
- `LIVE_COMPACT_WITH_FRAMES` allows bounded frame capture without compact packet
  files; full raw ticks/events remain off.
- `DEBUG_RECORDING` preserves the historical full session layout for audit,
  replay, batch geometry builders, and training/debug datasets.
- `HYBRID_DEBUG` is reserved for sampled debug snapshots.

Compact-only live sessions may not contain `ticks\`, `events\`, `frames\`,
`frame_index.jsonl`, or `live_packets\`. That is expected and is not capture
loss. Use plugin snapshot health, world-model freshness, Knowledge Fabric
status, source completeness, and cap fields to evaluate live capture health.
Tools that require raw ticks should ask for `DEBUG_RECORDING` sessions instead
of failing with generic file-not-found errors.

## Resource Projection Recovery

`resource_projection_status.v1` explains why a Tree/Oak candidate is or is not
clickable. It separates logical resource discovery from executable geometry:

- `projectionSentinel`: compact projection values such as `2147483647` or
  other unrealistic coordinates were emitted by the client projection source.
- `projectionAvailable`: usable canvas point, bounds, or hull geometry exists.
- `safeAimPointAvailable`: the candidate has an accepted `safeAimPoint`.
- `classification`: `safe`, `projection_sentinel`, `no_projection`,
  `projection_pending`, `edge_clipped`, `offscreen`, `tiny_projection`,
  `degenerate_projection`, `projection_cap_hit`, `source_cap_hit`,
  `stale_projection`, or `no_safe_aimpoint`.
- `recoverySuggested`: the failure looks view/projection recoverable rather
  than a cap/source exhaustion problem.

Overlay/status summaries may include `bestLogicalResourceTarget` even when
`selectedExecutableResourceTarget=null`. In that case the tree exists as a route
or resource candidate, but no live click should execute until projection
recovery or a fresh candidate supplies a valid safe aimpoint and hover confirms
the expected `Chop down` action.

`resource_view_recovery` is verified separately from a resource click. It emits
`resourceProjectionRecoveryClassification` values such as
`resource_camera_reacquire_success`, `resource_projection_improved`,
`resource_projection_recovery_waiting`, or
`resource_projection_recovery_failed`. Unchanged sentinel/no-projection geometry
must fail as recovery failed instead of being counted as progress.

## Resource View Score

`resource_view_score.v1` is attached to resource proposals and visual debug
bundles when candidate selection depends on the current camera/worksite view.
It is advisory for selection, but a poor classification can produce a bounded
non-click `resource_view_recovery` action before any chop click is allowed.

Core fields:

- `worksiteId`, `worksiteAnchor`, `worksiteRadiusTiles`, `playerLocation`
- `cameraYaw`, `cameraPitch`
- `visibleResourceCandidates`, `executableResourceCandidates`
- `safeAimpointCount`, `centralSafeAimpointCount`
- `edgeClippedResourceCandidates`, `partiallyOffscreenResourceCandidates`,
  `occludedResourceCandidates`
- `candidateSpread`
- `selectedTargetName`, `selectedTargetWorldLocation`
- `selectedTargetDistanceFromWorksite`
- `selectedTargetEdgeDistancePx`
- `selectedTargetVisibleAreaRatio`
- `selectedTargetHoverReady`
- `selectedTargetPullsAwayFromWorksite`
- `lowLevelResourceCandidatesRejected`
- `score`
- `classification`
- `cameraRecoveryRecommended`

Classifications are `good_resource_view`, `usable_resource_view`,
`poor_edge_resource_view`, `poor_occluded_resource_view`,
`poor_single_candidate_view`, `needs_resource_camera_reacquire`,
`needs_worksite_recenter`, and `no_executable_resource_view`.

Action traces may include `resourceViewScoreBefore`,
`resourceCameraTriggeredBy`, `resourceCameraAdjustmentCount`,
`visibleResourceCandidatesBefore`, `visibleResourceCandidatesAfter`,
`executableResourceCandidatesBefore`, `executableResourceCandidatesAfter`,
`selectedTargetBefore`, `selectedTargetAfter`, `worksiteDriftScore`, and
`resourceViewRecoveryResult`.

## Visual Debug Bundle

`visual_debug_bundle.v1` is an optional action-run evidence artifact written
only when screenshot debug flags are supplied to `execute_next_action.py`.
Default live runs do not write these bundles.

Required `bundle.json` fields:

- `schema`: `visual_debug_bundle.v1`
- `reason`: event such as `resource_projection_recovery_start`,
  `resource_projection_recovery_end`, `route_waypoint_edge_rejected`,
  `route_source_mismatch`, `goal_directed_fallback_started`,
  `route_wall_hugging_detected`, `goal_directed_path_blocked`,
  `alternate_approach_node_selected`, `service_anchor_reached`,
  `route_object_reacquired`, `camera_reacquire_start`,
  `camera_reacquire_end`, `route_no_progress_timeout`, `resource_timeout`,
  `return_transition_pending`, `return_transition_retry_required`,
  `return_transition_retry_success`, `return_transition_reconciled_success`,
  `menu_flip_mismatch`, `unexpected_current_area`, `poor_resource_view`,
  `resource_camera_reacquire_start`, `resource_camera_reacquire_end`,
  `resource_target_edge_rejected`, `worksite_drift_detected`,
  `post_depletion_reacquire`, `no_executable_resource_view`, or
  `final_summary`
- `timestamp`
- `sessionPath`
- `bundleDir`
- `screenshotPath` when screenshot capture succeeds
- `screenshotCaptureFailed`
- `daemonStatusPath`
- `overlayDebugStatePath` when available
- `actionTraceExcerptPath` when available
- `playerLocation`, `playerLocationSource`, `playerLocationConfidence`, `plane`,
  `inventoryFreeSlots`, `resourceCount`
- `inventoryState`
- `currentIntent`, `phase`, `actionReadiness`
- `selectedActionIntent`, `selectedTargetSource`, `selectedActionTargetSource`,
  `selectedActionability`, `advisoryTargetSource`, `staleProposalDetected`,
  `staleProposalSource`
- `currentRouteMode`, `currentRouteNode`, `currentRouteEdge`
- `routeContextSummary`, `selectedServiceAnchor`, `selectedApproachNode`,
  `selectedWaypoint`
- `routeSourceMismatchDetails`, `pathingReason`, `wallLoopClassification`
- `projectionStatus`, `safeAimPointStatus`, `safeAimPointSummary`,
  `cameraState`, `resourceViewScore`, `resourceViewClassification`,
  `resourceCameraRecoveryRecommended`
- `clientTickHotSummary`, `latestHoverMenu`, `latestMenuOptionClicked`
- `hoverMenu`, `clickedMenu`, `classification`, `clickActionClassification`,
  `finalDecision`
- `actionProposalSummary`, `coordinateScaling`, `humanInput`
- `mousePosition`, `windowRect`, `canvasRect`
- `warnings`

Loop summaries expose `debugScreenshotBundlesCaptured`,
`debugScreenshotCaptureFailures`, `debugScreenshotBundlesSkippedByLimit`, and
`debugScreenshotBundlePaths`.

Visual debug bundles do not feed runtime targeting. They are used to compare
what the user/Codex saw with the existing telemetry-first decision path:
projection geometry, safe aimpoint status, client-tick hover,
`MenuOptionClicked`, route/service state, and HumanInputController traces.
If screenshot capture itself fails, the bundle still writes JSON evidence and
sets `screenshotCaptureFailed=true`; execution must not be unlocked or crashed
by screenshot availability.

## Phase-Scoped Reacquire Budgets

`input_control_execution_loop_result.v1.loopSummary` includes bounded
reacquire budget fields:

- `reacquireBudgetType`: `resource`, `service_object`, `service_inventory`,
  `route_transition`, `navigation_waypoint`, `camera_recovery`, or `unknown`.
- `reacquireAttemptsUsed`
- `reacquireLimit`
- `phaseScopedBudget`
- `budgetResetReason`
- `reacquireBudgetResets`
- `stoppedByReacquireLimit`
- `candidateWasActionableBeforeLimit`
- `staleProposalDetected`
- `staleProposalSource`
- `reacquireAttempted`
- `reacquireResult`
- `freshTargetFound`
- `freshTargetSource`
- `reasonIfNoFreshTarget`
- `routeTransitionSuppressionOverrides`
- `reacquireRoundsByBudget`

Budgets reset when the lifecycle phase, active intent, player plane, service
route node, or return route node changes. This prevents stale Tree/Oak,
service-object, or navigation-waypoint suppression from blocking a
post-service return stair. If an actionable route-transition target is
temporarily suppressed by stale hover evidence, the executor may clear that
specific suppression within the route-transition budget and retry normal hover
confirmation; it still does not click without fresh expected-menu proof.

## Return Transition Timeout Reconciliation

`route_transition_action_ledger.v1` is attached to route-transition
observations and to the compact visual debug action-trace excerpt. It records
the action id, intent, route id/node before and after, expected action,
object id/hash/name and world location, plane and player location before/after,
local destination before/after, clicked-menu samples, click timestamp/tick
fields, verification windows, optional `retryOfActionId`, whether the retry
used the same route object, and evidence booleans such as `menuClickMatched`,
`pathingStarted`, `localDestinationChanged`, `locationChanged`,
`distanceToObjectDecreased`, `routeNodeAdvanced`, `planeChanged`,
`dialogueOpened`, and `serviceStateAdvanced`.

Route-transition actions can be path-to-interact actions. If a stair click has
menu/pathing/local-destination evidence but no final plane or route-node change
yet, the verifier reports `return_transition_pending` or
`route_transition_pending` and keeps the next action blocked while movement is
still plausible. If the verification window ends with a confirmed route click
but no completion evidence, the result becomes
`return_transition_retry_required` or `route_transition_retry_required` rather
than a generic `no_change_timeout`.

If a later retry against the same route object succeeds, the retry result may
record `retryOfActionId` and
`routeTransitionProgressClassification=return_transition_retry_success` or
`route_transition_retry_success`. If later daemon evidence proves the original
transition action itself succeeded, the observed result may include:

- `delayedProgressReconciliation=true`
- `previousObservedResult`
- `previousResultOutcome`
- `routeTransitionProgressClassification`:
  `return_transition_reconciled_success` or
  `route_transition_reconciled_success`

Loop summaries separate these cases with `routeTransitionAttempts`,
`routeTransitionFirstTrySuccesses`, `routeTransitionPending`,
`routeTransitionRetryRequired`, `routeTransitionRetrySuccesses`,
`routeTransitionTrueTimeouts`, `routeTransitionReconciledSuccesses`,
`resolvedByRetry`, `resolvedByLateEvidence`, `pendingButSafe`,
`timeoutsByIntent`, and `retriesByIntent`. Transition verification windows can
be tuned with `--transition-verify-ms`, `--transition-verify-game-ticks`,
`--transition-pending-game-ticks`, and
`--transition-retry-after-stall-ticks`.

These fields are separate from resource inventory reconciliation. They exist so
full lifecycle summaries can distinguish true route-transition failures from
pending movement, retry-required transitions, retry successes, and late but
proven stair progress.
## World Model v2

`world_model_snapshot.v1` is the Java-side read-only model of the currently
loaded local RuneLite scene. It is not a whole-game map. The plugin keeps the
rich scene/object/collision data in memory and exposes bounded query payloads
through `/snapshot` only when a request includes world-model needs.

Supported needs include `world_model_summary`, `scene_object_census`,
`route_object_census`, `resource_object_census`, `service_object_census`,
`pathing_frontier`, `projection_audit`, `minimap_projection`,
`view_quality_inputs`, and `full_world_model_debug`. Default live use should
consume the compact summary and censuses; `full_world_model_debug` is for debug
bundles or explicit inspection.

The snapshot metadata includes session id/path, game tick, client tick, wall
time, game state, plane, base X/Y, player world location, camera yaw/pitch, and
viewport/canvas geometry. Object census records include kind, id/hash, name,
actions, world/scene/local location, route/service/resource classification,
Woodcutting level gates for resource objects, and compact projection status
when requested or relevant. Quality fields include `worldModelAvailable`,
`worldModelAgeMs`, `objectCensusCapHit`, `collisionAvailable`,
`projectionAuditAvailable`, and `projectionCapHit`.

Runtime remains bounded: no full-scene JSON is emitted every tick by default,
query responses honor object/collision caps, and cap hits are reported in the
response instead of silently hiding data.

Projection selection runs after the scene census is built. The cache
prioritizes nearby route/resource/service objects before generic scene clutter,
then records `projectionCapHit` when lower-priority objects were not projected.
This prevents loaded-scene scan order from hiding the currently visible Tree,
staircase, bank, or service object from compact query results.

## Knowledge Fabric

`knowledge_fabric_status.v1` is the Python-side read-only index layer over the
live world model, daemon/session memory, static route/target libraries, and
debug evidence. It does not replace the low-latency runtime path. Runtime
execution still uses the plugin snapshot endpoint on `8893`, the daemon on
`8890`, readiness checks, client-tick hover/menu proof, and
`HumanInputController`.

`live_world_index.v1` summarizes bounded indexes built from world-model query
payloads:

- `spatialIndexSummary`: objects with world location by plane.
- `objectActionIndexSummary`: object names and actions such as `Chop down`,
  `Climb-up`, `Bank`, `Deposit`, and `Open`.
- `routeObjectIndexSummary`: live route-transition and route-service objects.
- `resourceIndexSummary`: live resource candidates with skill gates.
- `serviceIndexSummary`: live bank/deposit/service objects.
- `projectionIndexSummary`: visible/offscreen/edge/sentinel/actionable counts.
- `collisionFrontierIndexSummary`: compact loaded-scene pathing frontier status.

`session_memory.v1` is current-session advisory memory. It can include observed
resource areas, service anchors, route objects, successful and failed waypoints,
menu-flip zones, camera view outcomes, and learned area labels. Session memory
is not executable by itself; old-session memory must be explicitly loaded as a
prior and still requires fresh live projection/hover evidence before action.

`static_knowledge_library.v1` summarizes `service_routes.json`,
`target_profiles.json`, and `target_library.json`. Static priors, service
anchors, route nodes, target classes, and skill requirements are advisory until
live verified. Current static skill requirements include basic Tree and Dead
tree at Woodcutting 1, Oak at 15, and Willow at 30.

`debug_evidence_index.v1` indexes sparse debug bundles and latest action-trace
files for inspection. Visual bundle copies may include
`knowledge_fabric_status.json`, `current_debug_context.json`,
`explain_current_blocker.json`, `resource_candidates.json`,
`service_candidates.json`, `route_objects.json`, `pathing_frontier.json`,
`view_quality.json`, `session_memory_summary.json`, and
`static_library_summary.json`.

Knowledge Fabric query responses use `knowledge_fabric_query_response.v1` style
fields: `schema`, `status`, `source`, `freshness`, `data`, `warnings`,
`capHit`, `truncated`, and `performanceStats` with `queryTimeMs`,
`responseBytes`, `objectCount`, and source-age fields when available.

`knowledge_fabric_current_debug_context.v1` is the query-first aggregate for
Codex. Its `data` object contains `liveStatus`, `readiness`,
`worldModelSummary`, `knowledgeFabricStatus`, `currentBlocker`,
`actionProposal`, `resourceCandidates`, `routeObjects`, `serviceObjects`,
`pathingFrontier`, `viewQuality`, `overlayHealth`, `inputIntegrity`,
`latestActionTraceSummary`, `latestVisualBundleSummary`,
`sessionMemorySummary`, `staticProfileSummary`, `queryFirstWorkflow`, and
`runtimeSafety`.

`knowledge_fabric_current_blocker_explanation.v1` is the compact blocker
answer. Its `data` object contains `primaryBlockerCategory`,
`primaryBlockerSummary`, `recommendedNextStep`,
`safeToRunBoundedLiveAction`, `codeChangeLikelyNeeded`, current
phase/cycle/intent, location, inventory, blockers, warnings, and evidence for
world-model freshness, action readiness, action target source/actionability,
selected target, safe aimpoint, hover/menu, route context, pathing frontier,
service anchor, overlay health, input integrity, and latest debug evidence.
Categories include login/liveness, plugin/daemon freshness, input/Arduino,
readiness/action gate, target/candidate, projection/safeAimPoint, hover/menu,
overlay-only, route/pathing, service/bank, session mismatch, static-prior-only,
and unknown.

The optional MCP adapter in `telemetry-viewer/mcp_server.py` exposes read-only
tools/resources for Codex or other local AI clients. It is local/stdio oriented
and intentionally does not expose click, key, camera, or live execution tools.
Read-only helper tools include the current debug context/blocker, resource,
service, route, path-frontier, view-quality, session memory, static library,
and script-authoring queries.

`script_authoring_context.v1` is a sparse, explicit bundle for future script or
profile work. It is written only when requested, normally under
`interaction_geometry/live/script_authoring_context/<timestamp>_<profile>/`.
Its `manifest.json` includes `createdAt`, `sessionPath`, `profile`,
`taskName`, `playerLocation`, `plane`, `inventorySummary`, `currentPhase`,
`currentIntent`, `blockerCategory`, `objectCounts`, `capWarnings`,
`staleWarnings`, `queryTimes`, `responseSizes`, and `recommendedNextSteps`.
Bundle files include current debug context, blocker explanation, world-model
summary, Knowledge Fabric status, scene/resource/route/service object evidence,
pathing frontier, collision summary, projection audit, view quality, overlay
state, input integrity, latest action trace excerpt, session memory, static
library summaries, and target/profile/route excerpts. Screenshot copying is
best-effort from the latest visual debug bundle.

`replay_scenario.v1` captures enough read-only state to replay candidate
selection, action proposal, readiness explanation, blocker classification,
pathing frontier explanation, and view-quality explanation offline. The replay
command produces `replay_scenario_result.v1` and sets `noLiveInput=true`; it
does not expose or perform mouse, keyboard, camera, click, or menu input.

`data_quality_report.v1` reports `worldModelFresh`, `clientTickFresh`,
`daemonFresh`, `pluginFresh`, `objectCount`, `collisionAvailable`,
`projectionAuditAvailable`, `capHits`, `truncationWarnings`, `staleSources`,
`missingExpectedSections`, `queryFailures`, `responseSizes`, `queryTimes`, a
`confidence` value (`high`, `medium`, or `low`), and `recommendedFixes`.

`debug_context_diff.v1` compares two current-debug-context, script-authoring,
or replay bundles. It highlights changes in player location, route node/edge,
object and candidate counts, cap warnings, blocker category/summary, pathing
frontier, view quality, selected action/target, and session-memory summaries
when those fields are present.

`knowledge_fabric_handoff_summary.v1` is a concise handoff response with the
current phase/intent, current blocker, latest success/failure evidence, most
relevant bundle path, recommended next diagnostic query, recommended coding
target, input safety summary, and tests to run if code changes.

## Arduino HID Focus Safety

`firmware_safety.v1` separates Arduino firmware state from VM focus state. It
reports `armed`, `keysDown`, `mouseButtonsDown`, `watchdogOk`, `watchdogMs`,
`stopAllAvailable`, `resetSafe`, `protocolOk`, `status`, `blockers`, and
`warnings`.

`vm_input_focus_safety.v1` reports whether the guest desktop may still be
wedged after a self-test even when firmware is safe. Fields include
`overlayFocusable`, `overlayClickThrough`, `overlayTopmost`,
`monitorWindowActive`, `foregroundWindowTitle`, `foregroundProcess`,
`postTestFocusTarget`, `postTestFocusRecovery`, and `postTestInputState`.
Allowed post-test states include `normal`, `unknown`,
`focus_may_be_captured`, `overlay_may_be_blocking`,
`monitor_may_be_blocking`, and `vmware_capture_may_be_stuck`.

Self-tests should not claim a clean recovery solely because firmware is safe.
If VM focus recovery is unknown, the input-integrity result remains `WARN`.

`arduino_usb_passthrough_diagnostics.v1` reports guest-visible Arduino COM
ports, sketch/bootloader VID/PID values when known, whether reset/upload was
performed, and host-side VMware `.vmx` autoconnect recommendations. It must not
recommend routing the real host mouse or keyboard into the guest.

## External Knowledge Layer

External OSRS facts use cache-first schemas and are advisory only. Live truth
remains RuneLite, PluginSnapshotEndpoint, WorldModelCache, and daemon/context
state.

Primary schemas:

- `external_knowledge_sources.v1`
- `external_knowledge_status.v1`
- `external_item_lookup.v1`
- `external_item_search.v1`
- `external_object_lookup.v1`
- `external_skill_requirement_lookup.v1`
- `external_area_lookup.v1`
- `task_probe_report.v1`
- `data_source_inventory.v1`
- `query_coverage_matrix.v1`
- `coverage_report.v1`

Cache path:

```text
%USERPROFILE%\.osrs-telemetry\external_knowledge_cache
```

Cache files include `item_id_map.json`, `item_name_map.json`,
`object_knowledge.json`, `npc_knowledge.json`, `skill_requirements.json`,
`location_knowledge.json`, `source_status.json`, `wiki_page_cache\`, and
`map_static_cache\`.

External facts include provenance fields where available: `source`,
`sourceId`, `sourceUrl`, `fetchedAt`, `confidence`, `stale`,
`liveOverridden`, and optional `error`. External facts never override a fresh
live RuneLite name, ID, location, projection, hover menu, or clicked menu
result.

External API behavior:

- disabled by default in hot runtime
- explicit refresh/search only
- User-Agent required
- serial cache-first requests
- no massive wiki or map downloads by default
- cache remains usable when stale

Representative commands:

```powershell
python telemetry-viewer\context_service.py --external-knowledge-status
python telemetry-viewer\context_service.py --external-lookup-item-id 1511
python telemetry-viewer\context_service.py --external-search-item "logs"
python telemetry-viewer\context_service.py --external-get-skill-requirement Oak
python telemetry-viewer\context_service.py --external-lookup-object Staircase
python telemetry-viewer\context_service.py --external-search-wiki "Lumbridge Castle bank" --external-refresh
```

`task_probe_report.v1` combines loaded-scene evidence, static libraries,
external cache facts, skill requirements, widgets/inventory, route/service
priors, and a suggested review-required profile skeleton. It is read-only and
does not send live input.

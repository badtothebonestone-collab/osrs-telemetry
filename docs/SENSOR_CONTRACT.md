# Sensor Contract

## Purpose

The engine accepts gameplay facts only through one immutable
`sensor_frame.v1`. The frame is captured and published by RuneLite as one
atomic cache value. HTTP request time is assembly time, not evidence time.

## Atomic game-tick frame

Every frame carries:

- `frameId`, `sourceTick`, `capturedAtUtc`, `completedAtUtc`, and capture
  duration;
- plugin session ID and telemetry-owning process ID;
- `geometryFrameId`, derived from camera, viewport, canvas, window, display
  scale, visibility, and focus geometry;
- coherent/complete flags plus available and unavailable fact lists; and
- per-fact source tick, capture timestamp, availability, errors, and immutable
  serialized payload size.

The five core facts are `baseline`, `inventory`, `activity`, `bank_ui`, and
`dialogue_state`. A login or failed capture publishes a new incomplete frame
with unavailable facts. It replaces the prior frame in full; no fact is filled
from an older publication.

## Snapshot response

`POST /snapshot` returns `plugin_snapshot_response.v2`. It captures the cache's
single atomic publication once, mirrors its metadata under `sensorFrame`, and
labels HTTP construction time `assembledAtUtc`. Freshness is calculated from
the frame's source capture time. The compatibility
`cacheWallClockFresh` field has the same source-derived value and is not based
on request assembly or cache insertion time.

Requested core payloads are emitted only when their fact is available in that
captured frame. A missing, mixed-tick, incomplete, future-dated, or stale frame
produces explicit warnings/missing capabilities and cannot produce a `PASS`
loaded-scene observation.

### Inventory evidence

RuneLite's `ItemContainer` is authoritative whenever it exists. When it is
null, the plugin may recover inventory evidence only from the first exact
visible IF3 inventory view in this order: ordinary inventory, bank-side
inventory, then deposit-box inventory. A widget fallback is available only
when its direct dynamic-child array contains exactly 28 non-null slots with
unique indexes `0..27`. Every slot must be either an exact empty sentinel or a
positive, non-placeholder item ID with positive quantity. RuneLite exposes
empty IF3 inventory children as either `itemId=-1, quantity=0` or the gameval
`BLANKOBJECT` placeholder (`itemId=6512, quantity=1`); both are normalized to
`-1,0` before publication. Any other use of the placeholder is malformed.
Missing, extra, duplicate, out-of-range, or malformed slot evidence leaves the
inventory fact unavailable; absence is never synthesized as an empty inventory.

The inventory fact records its selected source as `item_container`,
`inventory_widget`, `bank_side_widget`, or `deposit_inventory_widget`. A
visible 28-slot empty widget can therefore prove known-empty inventory without
weakening the coherent loaded-scene gate.

## Dynamic geometry and menu evidence

`input_geometry.v1` publishes actionable screen geometry only in Win32 virtual
desktop device pixels (`coordinateSpace=device_pixels`). RuneLite's AWT user
coordinates are converted with the proven per-monitor scale anchored at that
monitor's nonzero or negative origin. The display transform must be finite,
positive, axis-aligned, and proven for one containing monitor; missing,
spanning, or invalid transform evidence makes geometry unavailable. Native
projection coordinates retain their separate source-canvas dimensions, while
the published canvas bounds are physical device pixels. Python rejects any
available geometry whose schema or coordinate space is missing or different.

`clientWindowX`, `clientWindowY`, `clientWindowWidth`, and
`clientWindowHeight` are optional only as one all-or-none group. Dimensions must
be positive and the resulting device-pixel window must contain the complete
canvas; partial or contradictory bounds reject the observation. This outer
window is not gameplay transit or activation authority. It is expected geometry
for the pinned RuneLite PID/root HWND. The input boundary independently matches
it to PMv2 `GetWindowRect`, samples the actual Win32 client, and proves the exact
canvas inside that client. RuneLite remains stationary during cursor recovery.
A cursor anywhere outside the canvas but inside the freshly proven PMv2 virtual
desktop may use only the connected Arduino movement-only lane to a neutral
canvas point. That lane retains the exact outer/client/canvas geometry before
and after, requires it unchanged throughout, sends no activation, discards the
old intent, and requires complete lane-specific reobservation before later
input.

For a projected object, `aimPoint` must be inside both the viewport and the
first present authoritative API shape in clickbox -> convex hull -> canvas tile
order. A present stronger shape never falls through to weaker geometry.
`canvasLocation` is retained only when that shape itself contains it; otherwise
RuneLite performs one bounded interior search and marks the projection
non-actionable when no interior point exists. Bounds and point therefore use the
same shape precedence, with fresh hover/menu validation still the final veto.

The canonical adapter requests one neutral `scene_object_census`. The endpoint
does not publish resource/route/service classifications, and Python ignores the
retired filtered-census payload names. Scene rows contain only authoritative
identity, action, location, and projection facts; selected definitions assign
task meaning downstream. Dialogue option and continue evidence is discovered
from pinned RuneLite widget identities rather than task-specific text. Exact
prompt/option meaning remains definition/task-owned. A same-tick request that
needs projection capability
missing from the cached world-model snapshot forces a capability refresh, so
an earlier lower-capability query cannot suppress later geometry. World-model
censuses and tile projections are calculated on the RuneLite
client thread. They are merged only when source tick, session, process, and
`geometryFrameId` match the captured sensor frame. Their distinct capture time
must follow frame completion, precede response assembly (within clock-skew
tolerance), and satisfy the request's source-age limit. That dynamic timestamp
is preserved in the Python observation payload. This rejects cross-tick reuse,
pre-frame scans, and same-tick camera/window drift.

Post-menu-sort evidence remains a separately sampled client-tick fact. Its
source tick, capture time, session, and process are taken from the actual menu
sample, never from a newer generic client-tick envelope. The Python safety gate
requires that evidence to be fresh and bound to the current observation before
pointer, widget, or context-menu input.

## Python boundary

`osrs_bot.observation` is the only JSON adapter. It requires response v2 and
frame v1, validates all core fact metadata, preserves both source and assembly
timestamps, and carries frame/menu provenance into immutable `Observation`.
`Observation.loaded_scene` and `SafetyGate` fail closed when frame coherence,
identity, freshness, or menu binding is absent.

The contract is covered by Java atomic publication and endpoint tests, Python
schema/provenance/safety tests, and
`tests/fixtures/java_snapshot_endpoint.json`. That deterministic fixture is
generated through the real `SensorFrame -> PluginLiveCache ->
PluginSnapshotEndpoint` path with `./gradlew generateJavaSnapshotFixture` (or
`.\gradlew.bat generateJavaSnapshotFixture` on Windows), is
regenerated byte-for-byte in Java tests, carries real serialized fact sizes,
and is parsed into a loaded Observation by Python.

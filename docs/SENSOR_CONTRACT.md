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

## Dynamic geometry and menu evidence

The canonical adapter requests one neutral `scene_object_census`; filtered
resource/route/service censuses remain diagnostic endpoint capabilities, not
task authorization. Scene rows omit candidate/type/skill labels, and scene
projection scheduling uses only explicit request state, factual distance, and
stable object identity. A same-tick request that needs projection capability
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
schema/provenance/safety tests, and a cross-language test that binds the Python
fixture/parser to the production Java schema constants.

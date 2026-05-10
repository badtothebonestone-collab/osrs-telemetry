# Clickable Hull Pipeline

This note traces the read-only clickable hull path used by the optional telemetry debug overlay.

## Current Findings

1. Java does call `TileObject.getClickbox()` in the compact live projection capture path. The clickbox is captured in `TelemetryPlugin.captureSceneObjectProjection(...)` as bounds and polygon data.
2. Java also calls `TileObject.getCanvasTilePoly()` in the same projection capture path and captures the canvas tile polygon when available.
3. Java converts `Shape` and `Polygon` geometry to point arrays with `polygonSnapshot(...)`. `Polygon` uses `xpoints` and `ypoints`; non-polygon `Shape` values are sampled with a `PathIterator` capped at 64 points.
4. Compact live projection packets can carry `clickableHull`, `clickboxPolygon`, `convexHull`, `convexHullPolygon`, and `canvasTilePolygon` when opt-in geometry is enabled. The normal compact live path remains lean unless `compactLiveIncludeClickableHull`, `compactLiveIncludeHeavyGeometry`, or the debug overlay clickable hull mode requests it.
5. `live_target_processor.py` preserves compact packet geometry fields when they arrive, and the overlay debug state writer can emit `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon` for capped overlay targets.
6. `TelemetryDebugOverlay.java` parses the same overlay state field names that Python writes: `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon`. It draws hulls in `CLICKABLE_HULL`, `HULL_AND_BOUNDS`, and `ALL_GEOMETRY_DEBUG` modes, with bounds as fallback.
7. The observed `hulls=0` failure was not an overlay drawing/color problem. The loss was in the Python candidate handoff: world target geometry could contain polygon fields, but candidate records kept only the preferred aim summary and did not preserve polygon payloads into `candidate["geometry"]`, so `overlay_debug_state.json` had only bounds.

## Fixed Data Path

The intended path is now:

1. RuneLite observes read-only clickbox/canvas/convex geometry.
2. `live_projection_packet.v1` emits capped polygon fields only when debug hull geometry is enabled.
3. `live_target_processor.py` normalizes compact refs without dropping polygon fields.
4. `build_world_target_geometry.py` carries `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon` into target geometry.
5. `select_target_candidates.py` ranks using the preferred geometry and now preserves the polygon payload in each candidate geometry record.
6. Before writing overlay state, Python matches any available compact hull refs back onto the ranked candidates using `objectKey`, then stable fallback keys.
7. `overlay_debug_state.json` writes capped overlay targets, and only the first `--overlay-debug-hull-limit` ranked targets may carry compact polygon fields.
8. `TelemetryDebugOverlay.java` draws the hull, then falls back to bounds or aim point if hull data is missing.

Java polygon emission is capped, so the cap must be spent on useful refs. The compact projection writer now prioritizes visible refs with clickbox geometry nearest the player scene tile, then nearest the screen center, before applying `compactLiveGeometryMaxRefs`; this avoids spending the hull cap on arbitrary scene iteration order such as corner/edge refs. The Python overlay state adds `bestHullAvailable`, `nearestHullAvailable`, and `hullRankBuckets` so the diagnostics can tell whether hull coverage is landing on the intended targets.

## Safety Notes

Clickable hulls are read-only visual telemetry. They are not click commands, action instructions, movement instructions, or menu interactions. Geometry emission is opt-in and capped so normal compact live mode does not become a large per-tick geometry dump.

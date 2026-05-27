# Clickable Hull Pipeline

This note traces the read-only clickable hull path used by the optional telemetry debug overlay.

## Current Findings

1. Java does call `TileObject.getClickbox()` in the snapshot/world-model projection capture path. The clickbox is captured in `TelemetryPlugin.captureSceneObjectProjection(...)` as bounds and polygon data.
2. Java also calls `TileObject.getCanvasTilePoly()` in the same projection capture path and captures the canvas tile polygon when available.
3. Java converts `Shape` and `Polygon` geometry to point arrays with `polygonSnapshot(...)`. `Polygon` uses `xpoints` and `ypoints`; non-polygon `Shape` values are sampled with a `PathIterator` capped at 64 points.
4. Snapshot/world-model projection payloads can carry `clickableHull`, `clickboxPolygon`, `convexHull`, `convexHullPolygon`, and `canvasTilePolygon` when developer geometry diagnostics are enabled. The normal query path remains capped and compact unless the debug overlay clickable-hull mode requests geometry.
5. `live_target_processor.py` preserves these geometry fields when they arrive from the snapshot/world-model path, and the overlay debug state writer can emit `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon` for capped overlay targets.
6. `TelemetryDebugOverlay.java` parses the same overlay state field names that Python writes: `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon`. It draws hulls in `CLICKABLE_HULL`, `HULL_AND_BOUNDS`, and `ALL_GEOMETRY_DEBUG` modes, with bounds as fallback.
7. The observed `hulls=0` failure was not an overlay drawing/color problem. The loss was in the Python candidate handoff: world target geometry could contain polygon fields, but candidate records kept only the preferred aim summary and did not preserve polygon payloads into `candidate["geometry"]`, so `overlay_debug_state.json` had only bounds.

## Fixed Data Path

The intended path is now:

1. RuneLite observes read-only clickbox/canvas/convex geometry.
2. The snapshot/world-model projection payload emits capped polygon fields only when debug hull geometry is enabled.
3. `live_target_processor.py` normalizes snapshot refs without dropping polygon fields.
4. `build_world_target_geometry.py` carries `clickableHull`, `clickboxPolygon`, `convexHull`, and `canvasTilePolygon` into target geometry.
5. `select_target_candidates.py` ranks using the preferred geometry and now preserves the polygon payload in each candidate geometry record.
6. Before writing overlay state, Python matches any available hull refs back onto the ranked candidates using `objectKey`, then stable fallback keys.
7. `overlay_debug_state.json` writes capped overlay targets, and only the first `--overlay-debug-hull-limit` ranked targets may carry compact polygon fields.
8. `TelemetryDebugOverlay.java` draws the hull, then falls back to bounds or aim point if hull data is missing.

Java polygon emission is capped, so the cap must be spent on useful refs. The projection capture path now prioritizes visible refs with clickbox geometry nearest the player scene tile, then nearest the screen center, before applying the geometry cap; this avoids spending the hull cap on arbitrary scene iteration order such as corner/edge refs. The Python overlay state adds `bestHullAvailable`, `nearestHullAvailable`, and `hullRankBuckets` so the diagnostics can tell whether hull coverage is landing on the intended targets.

## Read-Only Notes

Clickable hulls are read-only visual telemetry. Geometry emission is opt-in and capped so the snapshot/query path does not become a large per-tick geometry dump.

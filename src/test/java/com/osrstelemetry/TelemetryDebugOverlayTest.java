package com.osrstelemetry;

import java.awt.Color;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Assert;
import org.junit.Test;

public class TelemetryDebugOverlayTest
{
	@Test
	public void reachableAssumedIsGreenAndNotBlocked()
	{
		Color color = TelemetryDebugOverlay.colorFor("reachable", "live_assumed");
		Assert.assertEquals(TelemetryDebugOverlay.reachabilityToken("reachable"), "R");
		Assert.assertEquals(new Color(66, 220, 110), color);
	}

	@Test
	public void blockedAssumedIsRed()
	{
		Color color = TelemetryDebugOverlay.colorFor("blocked", "live_assumed");
		Assert.assertEquals(TelemetryDebugOverlay.reachabilityToken("blocked"), "BLOCK");
		Assert.assertEquals(new Color(240, 85, 85), color);
	}

	@Test
	public void staleOrDepletedIsGray()
	{
		Assert.assertEquals(new Color(165, 165, 165), TelemetryDebugOverlay.colorFor("reachable", "depleted_or_stump"));
		Assert.assertEquals(new Color(165, 165, 165), TelemetryDebugOverlay.colorFor("reachable", "stale"));
	}

	@Test
	public void geometryFallbackPrefersClickableHull()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.bounds = new TelemetryDebugOverlay.Bounds();
		target.canvasTilePolygon = polygon();
		target.convexHull = polygon();
		target.clickboxPolygon = polygon();
		target.clickableHull = polygon();

		Assert.assertEquals("clickableHull", TelemetryDebugOverlay.fallbackGeometrySource(target));
	}

	@Test
	public void geometryFallbackUsesBoundsThenAim()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.bounds = new TelemetryDebugOverlay.Bounds();
		Assert.assertEquals("bounds", TelemetryDebugOverlay.fallbackGeometrySource(target));

		target.bounds = null;
		target.aimPoint = new TelemetryDebugOverlay.AimPoint();
		Assert.assertEquals("aimPoint", TelemetryDebugOverlay.fallbackGeometrySource(target));
	}

	@Test
	public void projectionIdentityAllowsDrawableMarkerWithoutStaleCanvasPoint()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.markerType = "selected_target";
		target.targetType = "sceneObject";
		target.objectKey = "oak-1";
		target.id = 1278.0;
		target.worldX = 3201.0;
		target.worldY = 3202.0;
		target.plane = 0.0;

		Assert.assertTrue(TelemetryDebugOverlay.hasProjectionIdentity(target));
		Assert.assertEquals("live_tile_fallback", TelemetryDebugOverlay.drawableGeometrySource(target));
	}

	@Test
	public void pathTileMarkersPreferLiveTilePolygonGeometry()
	{
		TelemetryDebugOverlay.OverlayTarget destination = new TelemetryDebugOverlay.OverlayTarget();
		destination.markerType = "destination_tile";
		destination.targetType = "tile";
		destination.worldX = 3207.0;
		destination.worldY = 3215.0;
		destination.plane = 0.0;

		TelemetryDebugOverlay.OverlayTarget waypoint = new TelemetryDebugOverlay.OverlayTarget();
		waypoint.markerType = "waypoint";
		waypoint.markerId = "next_waypoint_tile:3206:3215:0";
		waypoint.targetType = "tile";
		waypoint.worldX = 3206.0;
		waypoint.worldY = 3215.0;
		waypoint.plane = 0.0;

		TelemetryDebugOverlay.OverlayTarget predictedPath = new TelemetryDebugOverlay.OverlayTarget();
		predictedPath.markerType = "predicted_path_tile";
		predictedPath.targetType = "tile";
		predictedPath.worldX = 3205.0;
		predictedPath.worldY = 3215.0;
		predictedPath.plane = 0.0;

		Assert.assertEquals("live_tile_polygon", TelemetryDebugOverlay.drawableGeometrySource(destination));
		Assert.assertEquals("live_tile_polygon", TelemetryDebugOverlay.drawableGeometrySource(waypoint));
		Assert.assertEquals("live_tile_polygon", TelemetryDebugOverlay.drawableGeometrySource(predictedPath));
	}

	@Test
	public void pathTileMarkerTypesAreRecognized()
	{
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("destination_tile"));
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("final_approach_tile"));
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("next_waypoint_tile"));
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("predicted_path_tile"));
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("path_blocked"));
		Assert.assertTrue(TelemetryDebugOverlay.isPathTileMarkerType("path_unknown"));
		Assert.assertFalse(TelemetryDebugOverlay.isPathTileMarkerType("selected_target"));
	}

	@Test
	public void drawableGeometryPrefersStoredClickableHullBeforeTileFallback()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.markerType = "selected_target";
		target.targetType = "sceneObject";
		target.objectKey = "oak-1";
		target.id = 1278.0;
		target.worldX = 3201.0;
		target.worldY = 3202.0;
		target.plane = 0.0;
		target.clickableHull = polygon();

		Assert.assertTrue(TelemetryDebugOverlay.hasProjectionIdentity(target));
		Assert.assertEquals("clickableHull", TelemetryDebugOverlay.drawableGeometrySource(target));
	}

	@Test
	public void genericMarkerIdentityFieldsAreParsed()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.markerType = "selected_target";
		target.markerId = "oak-1";
		target.markerVersion = "overlay_intent_marker.v1";
		target.objectKey = "oak-1";
		target.targetType = "sceneObject";
		target.classId = "tree";
		target.id = 1278.0;
		target.hash = 123456.0;
		target.localX = 6400.0;
		target.localY = 6408.0;
		target.worldX = 3201.0;
		target.worldY = 3202.0;
		target.plane = 0.0;
		target.sceneX = 10.0;
		target.sceneY = 11.0;

		Assert.assertEquals("oak-1", target.markerId);
		Assert.assertEquals("overlay_intent_marker.v1", target.markerVersion);
		Assert.assertEquals("oak-1", target.objectKey);
		Assert.assertEquals(Double.valueOf(6400.0), target.localX);
		Assert.assertEquals(Double.valueOf(123456.0), target.hash);
	}

	@Test
	public void malformedPolygonIsIgnored()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.clickableHull = Arrays.asList(Arrays.asList(1.0, 2.0), Arrays.asList(3.0, 4.0));
		target.bounds = new TelemetryDebugOverlay.Bounds();

		Assert.assertEquals("bounds", TelemetryDebugOverlay.fallbackGeometrySource(target));
	}

	@Test
	public void wrappedPolygonPointsAreAccepted()
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		Map<String, Object> hull = new LinkedHashMap<>();
		hull.put("points", polygon());
		target.clickableHull = hull;

		Assert.assertEquals("clickableHull", TelemetryDebugOverlay.fallbackGeometrySource(target));
	}

	@Test
	public void compactGeometryRefCapIsClamped()
	{
		Assert.assertEquals(0, TelemetryPlugin.clampCompactLiveGeometryMaxRefs(-5));
		Assert.assertEquals(50, TelemetryPlugin.clampCompactLiveGeometryMaxRefs(50));
		Assert.assertEquals(200, TelemetryPlugin.clampCompactLiveGeometryMaxRefs(500));
	}

	@Test
	public void polygonPointsPayloadUsesXyPointObjects()
	{
		List<Map<String, Object>> points = TelemetryPlugin.polygonPointsPayload(new int[][]{
				{1, 2},
				{3, 4},
				{5, 6},
		});

		Assert.assertNotNull(points);
		Assert.assertEquals(3, points.size());
		Assert.assertEquals(1, points.get(0).get("x"));
		Assert.assertEquals(2, points.get(0).get("y"));
	}

	@Test
	public void drawableTargetsPreferIntentMarkers()
	{
		TelemetryDebugOverlay.OverlayDebugState state = new TelemetryDebugOverlay.OverlayDebugState();
		TelemetryDebugOverlay.OverlayTarget oldTarget = new TelemetryDebugOverlay.OverlayTarget();
		oldTarget.name = "Old candidate";
		state.targets = List.of(oldTarget);
		state.intentState = new TelemetryDebugOverlay.OverlayIntentState();
		TelemetryDebugOverlay.OverlayTarget marker = new TelemetryDebugOverlay.OverlayTarget();
		marker.markerType = "selected_target";
		marker.label = "Target: Oak tree";
		marker.targetType = "sceneObject";
		state.intentState.markers = List.of(marker);

		List<TelemetryDebugOverlay.OverlayTarget> targets = TelemetryDebugOverlay.drawableTargets(state);

		Assert.assertEquals(1, targets.size());
		Assert.assertEquals("Target: Oak tree", targets.get(0).label);
		Assert.assertEquals("selected_target", targets.get(0).markerType);
	}

	@Test
	public void statusLineReportsIntentSelectedSeparatelyFromLegacyBestNearest()
	{
		TelemetryDebugOverlay.OverlayDebugState state = new TelemetryDebugOverlay.OverlayDebugState();
		state.latestTick = 344.0;
		state.profile = "woodcutting";
		state.summary = new TelemetryDebugOverlay.OverlaySummary();
		state.summary.targetsWritten = 3.0;
		state.summary.clickableHullTargets = 1.0;
		state.summary.hullLimit = 10.0;
		state.summary.compactLiveGeometryMaxRefs = 100.0;
		state.summary.bestHullAvailable = false;
		state.summary.nearestHullAvailable = false;
		state.intentState = new TelemetryDebugOverlay.OverlayIntentState();
		TelemetryDebugOverlay.OverlayTarget selected = new TelemetryDebugOverlay.OverlayTarget();
		selected.markerType = "selected_target";
		selected.selected = true;
		TelemetryDebugOverlay.OverlayTarget backupA = new TelemetryDebugOverlay.OverlayTarget();
		backupA.markerType = "backup_candidate";
		TelemetryDebugOverlay.OverlayTarget backupB = new TelemetryDebugOverlay.OverlayTarget();
		backupB.markerType = "backup_candidate";
		state.intentState.markers = List.of(selected, backupA, backupB);

		String line = TelemetryDebugOverlay.statusLine(state, TelemetryDebugOverlayGeometryMode.CLICKABLE_HULL);

		Assert.assertTrue(line.contains("selected yes"));
		Assert.assertTrue(line.contains("backups 2"));
		Assert.assertTrue(line.contains("legacy best no"));
		Assert.assertTrue(line.contains("legacy nearest no"));
		Assert.assertFalse(line.contains("| best no nearest no"));
	}

	@Test
	public void orderedDrawableTargetsProtectSelectedTargetFromPathCap()
	{
		TelemetryDebugOverlay.OverlayDebugState state = new TelemetryDebugOverlay.OverlayDebugState();
		state.intentState = new TelemetryDebugOverlay.OverlayIntentState();
		TelemetryDebugOverlay.OverlayTarget selected = new TelemetryDebugOverlay.OverlayTarget();
		selected.markerType = "selected_target";
		selected.selected = true;
		selected.clickableHull = polygon();
		TelemetryDebugOverlay.OverlayTarget destination = pathMarker("destination_tile", 3208, 3219);
		TelemetryDebugOverlay.OverlayTarget waypoint = pathMarker("waypoint", 3201, 3200);
		TelemetryDebugOverlay.OverlayTarget backup = new TelemetryDebugOverlay.OverlayTarget();
		backup.markerType = "backup_candidate";
		TelemetryDebugOverlay.OverlayTarget path1 = pathMarker("predicted_path_tile", 3202, 3200);
		TelemetryDebugOverlay.OverlayTarget path2 = pathMarker("predicted_path_tile", 3203, 3200);
		TelemetryDebugOverlay.OverlayTarget path3 = pathMarker("predicted_path_tile", 3204, 3200);
		state.intentState.markers = List.of(selected, destination, waypoint, path1, path2, path3, backup);

		List<TelemetryDebugOverlay.OverlayTarget> ordered = TelemetryDebugOverlay.orderedDrawableTargets(state, 5);

		Assert.assertTrue(ordered.contains(selected));
		Assert.assertFalse(ordered.contains(backup));
		Assert.assertEquals(selected, ordered.get(ordered.size() - 1));
		Assert.assertEquals(5, ordered.size());
	}

	@Test
	public void orderedDrawableTargetsDrawSelectedAfterPathTiles()
	{
		TelemetryDebugOverlay.OverlayDebugState state = new TelemetryDebugOverlay.OverlayDebugState();
		state.intentState = new TelemetryDebugOverlay.OverlayIntentState();
		TelemetryDebugOverlay.OverlayTarget selected = new TelemetryDebugOverlay.OverlayTarget();
		selected.markerType = "selected_target";
		selected.selected = true;
		selected.clickableHull = polygon();
		TelemetryDebugOverlay.OverlayTarget path = pathMarker("predicted_path_tile", 3202, 3200);
		state.intentState.markers = List.of(selected, path);

		List<TelemetryDebugOverlay.OverlayTarget> ordered = TelemetryDebugOverlay.orderedDrawableTargets(state, 25);

		Assert.assertEquals(path, ordered.get(0));
		Assert.assertEquals(selected, ordered.get(1));
	}

	private static java.util.List<java.util.List<Double>> polygon()
	{
		return Arrays.asList(
				Arrays.asList(0.0, 0.0),
				Arrays.asList(4.0, 0.0),
				Arrays.asList(4.0, 4.0),
				Arrays.asList(0.0, 4.0));
	}

	private static TelemetryDebugOverlay.OverlayTarget pathMarker(String markerType, double worldX, double worldY)
	{
		TelemetryDebugOverlay.OverlayTarget target = new TelemetryDebugOverlay.OverlayTarget();
		target.markerType = markerType;
		target.targetType = "tile";
		target.worldX = worldX;
		target.worldY = worldY;
		target.plane = 0.0;
		return target;
	}
}

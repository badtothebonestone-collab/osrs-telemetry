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

	private static java.util.List<java.util.List<Double>> polygon()
	{
		return Arrays.asList(
				Arrays.asList(0.0, 0.0),
				Arrays.asList(4.0, 0.0),
				Arrays.asList(4.0, 4.0),
				Arrays.asList(0.0, 4.0));
	}
}

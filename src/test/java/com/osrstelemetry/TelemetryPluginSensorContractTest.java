package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.awt.Rectangle;
import java.awt.geom.AffineTransform;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class TelemetryPluginSensorContractTest
{
	@Test
	public void sensorFramePublishesDirectlyToLiveCache()
	{
		Gson gson = new Gson();
		PluginLiveCache cache = new PluginLiveCache(gson);
		SensorFrame frame = SensorFrame.builder(
				"test-frame-7",
				7L,
				1L,
				"2026-07-10T12:00:00Z")
				.completedAtUtc("2026-07-10T12:00:00Z")
				.fact(gson, SensorFrame.FACT_BASELINE, 7L,
						"2026-07-10T12:00:00Z", true, java.util.List.of(),
						Map.of("gameState", "LOGGED_IN"))
				.build();

		assertTrue(cache.publish(frame));
		assertEquals(7L, cache.get("live_baseline_packet.v1").tick);
		assertEquals(1L, cache.getUpdates());
	}

	@Test
	public void inputGeometryIncludesTelemetryOwningProcessId()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.tickId = 42L;
		snapshot.inputGeometry = new TickSnapshot.InputGeometrySnapshot();
		snapshot.inputGeometry.geometryAvailable = true;
		snapshot.inputGeometry.isClientFocused = true;
		snapshot.inputGeometry.clientProcessId = 1234L;
		snapshot.inputGeometry.coordinateSpace = "device_pixels";
		snapshot.inputGeometry.sourceCanvasWidth = 765;
		snapshot.inputGeometry.sourceCanvasHeight = 503;
		snapshot.inputGeometry.canvasWidth = 2151;
		snapshot.inputGeometry.canvasHeight = 1519;

		Map<String, Object> payload = TelemetryPlugin.inputGeometryPayload(snapshot);

		assertEquals(1234L, payload.get("clientProcessId"));
		assertEquals(Boolean.TRUE, payload.get("isClientFocused"));
		assertEquals("device_pixels", payload.get("coordinateSpace"));
		assertEquals(765, payload.get("sourceCanvasWidth"));
		assertEquals(2151, payload.get("canvasWidth"));
	}

	@Test
	public void inputGeometryConvertsPrimaryMonitorBoundsToDevicePixels()
	{
		Rectangle logical = new Rectangle(148, 57, 1229, 868);
		AffineTransform scale = AffineTransform.getScaleInstance(1.75, 1.75);
		Rectangle monitor = new Rectangle(0, 0, 2194, 1234);

		Rectangle device = TelemetryPlugin.devicePixelBounds(logical, scale, monitor);

		assertEquals(new Rectangle(259, 100, 2151, 1519), device);
	}

	@Test
	public void inputGeometryAnchorsScaledCoordinatesAtEachMonitorOrigin()
	{
		AffineTransform scale = AffineTransform.getScaleInstance(1.75, 1.75);
		assertEquals(
				new Rectangle(4099, 100, 2151, 1519),
				TelemetryPlugin.devicePixelBounds(
						new Rectangle(3988, 57, 1229, 868),
						scale,
						new Rectangle(3840, 0, 2194, 1234)));
		assertEquals(
				new Rectangle(-1770, -930, 300, 150),
				TelemetryPlugin.devicePixelBounds(
						new Rectangle(-1820, -980, 200, 100),
						AffineTransform.getScaleInstance(1.5, 1.5),
						new Rectangle(-1920, -1080, 1280, 720)));
	}

	@Test
	public void inputGeometryUsesWindowsHalfDownScalingAndRejectsUnprovenTransforms()
	{
		assertEquals(
				new Rectangle(1, 1, 1, 1),
				TelemetryPlugin.devicePixelBounds(
						new Rectangle(1, 1, 1, 1),
						AffineTransform.getScaleInstance(1.5, 1.5),
						new Rectangle(0, 0, 100, 100)));
		assertFalse(TelemetryPlugin.usableDeviceTransform(null, null));
		assertFalse(TelemetryPlugin.usableDeviceTransform(
				new AffineTransform(1.75, 0.1, 0.0, 1.75, 0.0, 0.0),
				new Rectangle(0, 0, 100, 100)));
		assertFalse(TelemetryPlugin.usableDeviceTransform(
				new AffineTransform(Double.NaN, 0.0, 0.0, 1.0, 0.0, 0.0),
				new Rectangle(0, 0, 100, 100)));
		assertTrue(TelemetryPlugin.usableDeviceTransform(
				new AffineTransform(),
				new Rectangle(-100, 0, 100, 100)));
	}

	@Test(expected = IllegalArgumentException.class)
	public void inputGeometryRejectsBoundsSpanningTheProvenMonitor()
	{
		TelemetryPlugin.devicePixelBounds(
				new Rectangle(900, 100, 200, 100),
				AffineTransform.getScaleInstance(1.5, 1.5),
				new Rectangle(0, 0, 1000, 800));
	}

	@Test
	public void loginScreenBaselineStillIdentifiesTelemetryOwningProcess()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.tickId = 1L;
		snapshot.gameState = "LOGIN_SCREEN";

		Map<String, Object> payload = TelemetryPlugin.inputGeometryPayload(snapshot);

		assertEquals(ProcessHandle.current().pid(), payload.get("clientProcessId"));
		assertEquals(Boolean.FALSE, payload.get("geometryAvailable"));
	}

	@Test
	public void failedBankUiCaptureIsExplicitlyUnknown()
	{
		TickSnapshot missing = new TickSnapshot();
		TickSnapshot captured = new TickSnapshot();
		captured.bankUi = new TickSnapshot.BankUiSnapshot();

		assertFalse(TelemetryPlugin.bankUiKnown(missing));
		assertTrue(TelemetryPlugin.bankUiKnown(captured));
	}

	@Test
	public void unknownMenuStateFailsClosedAsOpen()
	{
		assertTrue(TelemetryPlugin.failClosedMenuOpen(null));
		assertTrue(TelemetryPlugin.failClosedMenuOpen(true));
		assertFalse(TelemetryPlugin.failClosedMenuOpen(false));
	}

	@Test
	public void onlyAnObservedOpenMenuGetsClientTickGeometryRefreshes()
	{
		assertTrue(TelemetryPlugin.shouldRefreshOpenMenu(true));
		assertFalse(TelemetryPlugin.shouldRefreshOpenMenu(false));
		assertFalse(TelemetryPlugin.shouldRefreshOpenMenu(null));
	}

	@Test
	public void hotMenuSourceRetainsAtMostSixteenEntries()
	{
		assertEquals(16, TelemetryPlugin.HOT_MENU_ENTRY_LIMIT);
	}

	@Test
	public void welcomeScreenCannotBeClaimedAsPlayableScene()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.gameState = "LOGGED_IN";
		snapshot.localPlayer = new TickSnapshot.LocalPlayer();

		assertFalse(TelemetryPlugin.scenePlayable(snapshot));
		snapshot.welcomeScreenVisible = true;
		assertFalse(TelemetryPlugin.scenePlayable(snapshot));
		snapshot.welcomeScreenVisible = false;
		assertTrue(TelemetryPlugin.scenePlayable(snapshot));
	}

	@Test
	public void baselineAvailabilityRequiresEveryOwnedCaptureSection()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.gameState = "LOGIN_SCREEN";

		assertTrue(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of()));
		assertFalse(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of("gameState")));
		assertFalse(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of("cameraViewport")));
		assertFalse(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of("welcomeScreen")));
		snapshot.gameState = null;
		assertFalse(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of()));
	}

	@Test
	public void tileProjectionRequiresVisibleAimPointInsideObservedPolygon()
	{
		int[][] polygon = new int[][] {{10, 10}, {30, 10}, {30, 30}, {10, 30}};
		TickSnapshot.CanvasPoint aimPoint = new TickSnapshot.CanvasPoint();
		aimPoint.x = 20;
		aimPoint.y = 20;
		Map<String, Object> payload = new LinkedHashMap<>();

		TelemetryPlugin.addTileProjectionReadiness(payload, true, true, true, polygon, aimPoint);

		assertEquals(Boolean.TRUE, payload.get("geometryAvailable"));
		assertEquals(Boolean.TRUE, payload.get("onScreen"));
		assertEquals(Boolean.TRUE, payload.get("visible"));
		assertEquals(Boolean.TRUE, payload.get("actionable"));
		assertEquals(Boolean.TRUE, payload.get("actionableByCanvas"));
		assertTrue(TelemetryPlugin.tileProjectionActionable(true, true, true, polygon, aimPoint));

		aimPoint.x = 40;
		assertFalse(TelemetryPlugin.tileProjectionActionable(true, true, true, polygon, aimPoint));
		assertFalse(TelemetryPlugin.tileProjectionActionable(true, false, true, polygon, aimPoint));
	}
}

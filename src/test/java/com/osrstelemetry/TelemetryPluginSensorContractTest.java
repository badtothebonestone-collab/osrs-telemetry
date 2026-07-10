package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class TelemetryPluginSensorContractTest
{
	@Test
	public void tickPayloadPublishesDirectlyToLiveCache()
	{
		PluginLiveCache cache = new PluginLiveCache(new Gson());
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.tickId = 7L;
		snapshot.timestampUtc = "2026-07-10T12:00:00Z";

		assertTrue(TelemetryPlugin.updateLiveCache(cache, "live_baseline_packet.v1", snapshot, Map.of("gameState", "LOGGED_IN")));
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

		Map<String, Object> payload = TelemetryPlugin.inputGeometryPayload(snapshot);

		assertEquals(1234L, payload.get("clientProcessId"));
		assertEquals(Boolean.TRUE, payload.get("isClientFocused"));
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

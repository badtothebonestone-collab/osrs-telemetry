package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.geom.AffineTransform;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.runelite.api.CollisionDataFlag;
import net.runelite.api.Point;
import net.runelite.api.coords.WorldPoint;
import org.junit.Test;

public class TelemetryPluginSensorContractTest
{
	@Test
	public void baselineCameraViewportIncludesAuthoritativeZoomState()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.cameraYaw = 512;
		snapshot.cameraPitch = 1_024;
		snapshot.zoom3d = 384;

		Map<String, Object> payload = TelemetryPlugin.cameraViewportPayload(snapshot);

		assertEquals(512, payload.get("cameraYaw"));
		assertEquals(1_024, payload.get("cameraPitch"));
		assertEquals(384, payload.get("zoom3d"));
	}

	@Test
	public void baselinePublishesAuthoritativeTextInputState()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.textInputActive = true;

		Map<String, Object> payload = TelemetryPlugin.baselinePayload(snapshot);

		assertEquals(Boolean.TRUE, payload.get("textInputActive"));
		snapshot.textInputActive = false;
		assertEquals(Boolean.FALSE,
				TelemetryPlugin.baselinePayload(snapshot).get("textInputActive"));
		snapshot.textInputActive = null;
		assertNull(TelemetryPlugin.baselinePayload(snapshot).get("textInputActive"));
	}

	@Test
	public void textInputStateMatchesFocusedFieldOrClientInputMode()
	{
		assertFalse(TelemetryPlugin.textInputActive(false, 0));
		assertTrue(TelemetryPlugin.textInputActive(true, 0));
		assertTrue(TelemetryPlugin.textInputActive(false, 1));
		assertTrue(TelemetryPlugin.textInputActive(false, -1));
	}

	@Test
	public void localPlayerCanvasCenterUsesUsableTilePolygon()
	{
		Polygon polygon = new Polygon(
				new int[]{100, 140, 140, 100},
				new int[]{200, 200, 240, 240},
				4);

		assertEquals(new Point(120, 220), TelemetryPlugin.playerCanvasCenter(polygon));
		assertNull(TelemetryPlugin.playerCanvasCenter(null));
		assertNull(TelemetryPlugin.playerCanvasCenter(
				new Polygon(new int[]{10, 10, 10}, new int[]{20, 20, 20}, 3)));
	}

	@Test
	public void baselinePlayerPayloadIncludesOptionalCanvasPointAsOnePair()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.localPlayer = new TickSnapshot.LocalPlayer();
		snapshot.localPlayer.canvasX = 120;
		snapshot.localPlayer.canvasY = 220;

		Map<String, Object> payload = TelemetryPlugin.playerPayload(snapshot);

		assertEquals(120, payload.get("canvasX"));
		assertEquals(220, payload.get("canvasY"));
		snapshot.localPlayer.canvasY = null;
		payload = TelemetryPlugin.playerPayload(snapshot);
		assertFalse(payload.containsKey("canvasX"));
		assertFalse(payload.containsKey("canvasY"));
	}

	@Test
	public void requestedTileCollisionLineAcceptsClearOpenTerrain()
	{
		int[][] flags = new int[8][8];

		assertTrue(TelemetryPlugin.collisionLineClear(flags, 1, 1, 6, 5));
		assertTrue(TelemetryPlugin.collisionLineClear(flags, 6, 5, 1, 1));
	}

	@Test
	public void requestedTileCollisionLineRejectsBlockedTileAndWallShortcut()
	{
		int[][] blockedTile = new int[8][8];
		blockedTile[4][3] = CollisionDataFlag.BLOCK_MOVEMENT_OBJECT;
		assertFalse(TelemetryPlugin.collisionLineClear(blockedTile, 1, 1, 6, 5));

		int[][] blockedWall = new int[8][8];
		blockedWall[2][2] = CollisionDataFlag.BLOCK_MOVEMENT_EAST;
		blockedWall[3][2] = CollisionDataFlag.BLOCK_MOVEMENT_WEST;
		assertFalse(TelemetryPlugin.collisionLineClear(blockedWall, 1, 2, 6, 2));
	}

	@Test
	public void requestedTileCollisionReachabilityCanRouteAroundBlockedShortcut()
	{
		int[][] aroundObject = new int[8][8];
		aroundObject[3][2] = CollisionDataFlag.BLOCK_MOVEMENT_OBJECT;
		assertFalse(TelemetryPlugin.collisionLineClear(aroundObject, 1, 2, 5, 2));
		assertTrue(TelemetryPlugin.collisionPathReachable(aroundObject, 1, 2, 5, 2));

		int[][] sealedWall = new int[8][8];
		for (int y = 0; y < sealedWall[3].length; y++)
		{
			sealedWall[3][y] = CollisionDataFlag.BLOCK_MOVEMENT_FULL;
		}
		assertFalse(TelemetryPlugin.collisionPathReachable(sealedWall, 1, 2, 5, 2));
	}

	@Test
	public void menuSceneFootprintContainmentIsInclusiveAndOrderIndependent()
	{
		assertTrue(TelemetryPlugin.sceneFootprintContains(50, 51, 49, 50, 51, 52));
		assertTrue(TelemetryPlugin.sceneFootprintContains(49, 52, 51, 52, 49, 50));
		assertFalse(TelemetryPlugin.sceneFootprintContains(48, 51, 49, 50, 51, 52));
		assertFalse(TelemetryPlugin.sceneFootprintContains(50, 53, 49, 50, 51, 52));
	}

	@Test
	public void freshOpenMenuTupleRequiresExactCurrentRowEvidence()
	{
		Map<String, Object> entry = new LinkedHashMap<>();
		entry.put("option", "Bank");
		entry.put("target", "<col=ffff>Bank booth");
		entry.put("type", "GAME_OBJECT_FIRST_OPTION");
		entry.put("identifier", 18491);
		entry.put("param0", 54);
		entry.put("param1", 63);
		entry.put("worldViewId", -1);

		Map<String, Object> bounds = new LinkedHashMap<>();
		bounds.put("x", 100);
		bounds.put("y", 200);
		bounds.put("width", 180);
		bounds.put("height", 120);

		Map<String, Object> menu = new LinkedHashMap<>();
		menu.put("menuOpen", true);
		menu.put("gameState", "LOGGED_IN");
		menu.put("sessionId", "plugin-session");
		menu.put("clientProcessId", 4321L);
		menu.put("clientTick", 100L);
		menu.put("wallTimeMillis", 10_000L);
		menu.put("menuBounds", bounds);
		menu.put("entries", List.of(entry));

		assertTrue(TelemetryPlugin.freshOpenMenuTupleMatches(
				menu,
				101L,
				10_020L,
				"plugin-session",
				4321L,
				new Point(150, 250),
				"Bank",
				"<col=ffff>Bank booth",
				"GAME_OBJECT_FIRST_OPTION",
				18491,
				54,
				63,
				-1));

		assertFalse(TelemetryPlugin.freshOpenMenuTupleMatches(
				menu,
				101L,
				10_020L,
				"plugin-session",
				4321L,
				new Point(99, 250),
				"Bank",
				"<col=ffff>Bank booth",
				"GAME_OBJECT_FIRST_OPTION",
				18491,
				54,
				63,
				-1));

		assertFalse(TelemetryPlugin.freshOpenMenuTupleMatches(
				menu,
				103L,
				10_020L,
				"plugin-session",
				4321L,
				new Point(150, 250),
				"Bank",
				"<col=ffff>Bank booth",
				"GAME_OBJECT_FIRST_OPTION",
				18491,
				54,
				63,
				-1));

		assertFalse(TelemetryPlugin.freshOpenMenuTupleMatches(
				menu,
				101L,
				10_020L,
				"plugin-session",
				4321L,
				new Point(150, 250),
				"Top-floor",
				"<col=ffff>Bank booth",
				"GAME_OBJECT_FIRST_OPTION",
				18491,
				54,
				63,
				-1));
	}

	@Test
	public void freshContextMenuRowTakesPrecedenceOverOverlappingObjectGeometry()
	{
		assertEquals("context_menu_row", TelemetryPlugin.tileObjectActivationKind(true, true));
		assertEquals("context_menu_row", TelemetryPlugin.tileObjectActivationKind(false, true));
		assertEquals("object_geometry", TelemetryPlugin.tileObjectActivationKind(true, false));
		assertEquals("unverified", TelemetryPlugin.tileObjectActivationKind(false, false));
	}

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
	public void visibleEmptyInventoryWidgetProvesKnownEmptySlots()
	{
		int[] indexes = inventoryIndexes();
		int[] itemIds = emptyInventoryItemIds();
		TickSnapshot.InventorySlot[] slots =
				TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
						true, 28, indexes, itemIds, new int[28]);

		assertEquals(28, slots.length);
		for (int i = 0; i < slots.length; i++)
		{
			assertEquals(i, slots[i].slot);
			assertEquals(-1, slots[i].itemId);
			assertEquals(0, slots[i].quantity);
		}
	}

	@Test
	public void visibleBlankObjectInventoryWidgetProvesKnownEmptySlots()
	{
		int[] itemIds = new int[28];
		int[] quantities = new int[28];
		java.util.Arrays.fill(itemIds, 6512);
		java.util.Arrays.fill(quantities, 1);
		TickSnapshot.InventorySlot[] slots =
				TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
						true, 28, inventoryIndexes(), itemIds, quantities);

		assertEquals(28, slots.length);
		for (int i = 0; i < slots.length; i++)
		{
			assertEquals(i, slots[i].slot);
			assertEquals(-1, slots[i].itemId);
			assertEquals(0, slots[i].quantity);
		}
	}

	@Test
	public void visibleInventoryWidgetRetainsPositiveItemEvidence()
	{
		int[] indexes = inventoryIndexes();
		int[] itemIds = emptyInventoryItemIds();
		int[] quantities = new int[28];
		itemIds[7] = 1351;
		quantities[7] = 1;
		TickSnapshot.InventorySlot[] slots =
				TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
						true, 28, indexes, itemIds, quantities);

		assertEquals(1351, slots[7].itemId);
		assertEquals(1, slots[7].quantity);
	}

	@Test
	public void hiddenOrIncoherentInventoryWidgetEvidenceRemainsUnavailable()
	{
		int[] indexes = inventoryIndexes();
		int[] itemIds = emptyInventoryItemIds();
		int[] quantities = new int[28];
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				false, 28, indexes, itemIds, quantities));
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 29, indexes, itemIds, quantities));
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 0, new int[0], new int[0], new int[0]));
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 27, new int[27], new int[27], new int[27]));
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 29, new int[29], new int[29], new int[29]));

		int[] zeroItemIds = itemIds.clone();
		zeroItemIds[0] = 0;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, indexes, zeroItemIds, quantities));
		int[] emptyWithQuantity = quantities.clone();
		emptyWithQuantity[0] = 1;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, indexes, itemIds, emptyWithQuantity));
		int[] positiveWithoutQuantity = itemIds.clone();
		positiveWithoutQuantity[0] = 1351;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, indexes, positiveWithoutQuantity, quantities));
		int[] malformedBlankIds = itemIds.clone();
		malformedBlankIds[0] = 6512;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, indexes, malformedBlankIds, quantities));
		int[] malformedBlankQuantities = quantities.clone();
		malformedBlankQuantities[0] = 2;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, indexes, malformedBlankIds, malformedBlankQuantities));

		int[] duplicateIndexes = indexes.clone();
		duplicateIndexes[27] = 0;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, duplicateIndexes, itemIds, quantities));
		int[] outOfRangeIndexes = indexes.clone();
		outOfRangeIndexes[27] = 28;
		assertNull(TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
				true, 28, outOfRangeIndexes, itemIds, quantities));
	}

	@Test
	public void inventoryCaptureSelectionPreservesAuthorityAndEvidenceSource()
	{
		TickSnapshot.InventorySlot[] itemContainer = inventorySlots(10);
		TickSnapshot.InventorySlot[] inventoryWidget = inventorySlots(20);
		TickSnapshot.InventorySlot[] bankSideWidget = inventorySlots(30);
		TickSnapshot.InventorySlot[] depositWidget = inventorySlots(40);

		TelemetryPlugin.InventoryCapture capture =
				TelemetryPlugin.selectInventoryCapture(
						itemContainer,
						inventoryWidget,
						bankSideWidget,
						depositWidget);
		assertEquals("item_container", capture.source);
		assertTrue(capture.slots == itemContainer);

		capture = TelemetryPlugin.selectInventoryCapture(
				null, inventoryWidget, bankSideWidget, depositWidget);
		assertEquals("inventory_widget", capture.source);
		assertTrue(capture.slots == inventoryWidget);

		capture = TelemetryPlugin.selectInventoryCapture(
				null, null, bankSideWidget, depositWidget);
		assertEquals("bank_side_widget", capture.source);
		assertTrue(capture.slots == bankSideWidget);

		capture = TelemetryPlugin.selectInventoryCapture(
				null, null, null, depositWidget);
		assertEquals("deposit_inventory_widget", capture.source);
		assertTrue(capture.slots == depositWidget);

		assertNull(TelemetryPlugin.selectInventoryCapture(
				null, null, null, null));
	}

	@Test
	@SuppressWarnings("unchecked")
	public void inventoryFactEmitsKnownEmptyWidgetSource()
	{
		TickSnapshot snapshot = new TickSnapshot();
		snapshot.inventory =
				TelemetryPlugin.inventorySlotsFromVisibleWidgetEvidence(
						true,
						28,
						inventoryIndexes(),
						emptyInventoryItemIds(),
						new int[28]);
		snapshot.inventoryCaptureSource = "inventory_widget";

		Map<String, Object> fact = TelemetryPlugin.inventoryPayload(snapshot);
		Map<String, Object> inventory =
				(Map<String, Object>) fact.get("inventory");

		assertEquals(Boolean.TRUE, inventory.get("known"));
		assertEquals(28, inventory.get("slotCount"));
		assertEquals(28, inventory.get("freeSlots"));
		assertEquals(0, inventory.get("occupiedSlots"));
		assertEquals("inventory_widget", inventory.get("source"));
	}

	private static TickSnapshot.InventorySlot[] inventorySlots(int itemId)
	{
		TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
		slot.slot = 0;
		slot.itemId = itemId;
		slot.quantity = 1;
		return new TickSnapshot.InventorySlot[]{slot};
	}

	private static int[] inventoryIndexes()
	{
		int[] indexes = new int[28];
		for (int i = 0; i < indexes.length; i++)
		{
			indexes[i] = i;
		}
		return indexes;
	}

	private static int[] emptyInventoryItemIds()
	{
		int[] itemIds = new int[28];
		java.util.Arrays.fill(itemIds, -1);
		return itemIds;
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
		assertTrue(TelemetryPlugin.baselineCaptureAvailable(snapshot, java.util.List.of("textInputState")));
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

	@Test
	public void walkClickPrefersSelectedSceneTileCorrelatedWithMenuParameters()
	{
		WorldPoint menu = new WorldPoint(3200, 3201, 0);
		WorldPoint selected = new WorldPoint(3200, 3201, 0);
		WorldPoint oldDestination = new WorldPoint(3198, 3198, 0);

		Map<String, Object> payload = TelemetryPlugin.walkTargetPayload(
				-1, 50, 51, menu, selected, new Point(50, 51), oldDestination);

		assertEquals("walk_tile", payload.get("actionFamily"));
		assertEquals("resolved", payload.get("resolution"));
		assertEquals("exact", payload.get("confidence"));
		assertEquals("selected_scene_tile_correlated_with_menu_params", payload.get("source"));
		assertEquals(Boolean.TRUE, payload.get("selectedSceneTileMatchesMenuParams"));
		assertEquals(3200, ((Map<?, ?>) payload.get("worldTile")).get("worldX"));
	}

	@Test
	public void walkClickDoesNotMistakeStaleLocalDestinationForExactTarget()
	{
		WorldPoint menu = new WorldPoint(3205, 3206, 0);
		WorldPoint staleSelected = new WorldPoint(3190, 3190, 0);
		WorldPoint staleDestination = new WorldPoint(3191, 3191, 0);

		Map<String, Object> payload = TelemetryPlugin.walkTargetPayload(
				-1, 55, 56, menu, staleSelected, new Point(40, 40), staleDestination);

		assertEquals("high", payload.get("confidence"));
		assertEquals("menu_params", payload.get("source"));
		assertEquals(Boolean.FALSE, payload.get("selectedSceneTileMatchesMenuParams"));
		assertEquals(3205, ((Map<?, ?>) payload.get("worldTile")).get("worldX"));
	}
}

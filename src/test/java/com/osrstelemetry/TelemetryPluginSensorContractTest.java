package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
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

package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.Test;

public class ClientTickHotStateTest
{
	@Test
	public void compactSnapshotExposesLatestSamplesWithoutTail()
	{
		ClientTickHotState state = new ClientTickHotState(2);
		state.recordClientTick(Map.of("clientTick", 7L, "wallTimeMillis", 1000L, "mouseCanvasX", 10, "mouseCanvasY", 20));
		state.recordPostMenuSort(Map.of("clientTick", 7L, "wallTimeMillis", 1010L, "topOption", "Walk here"));
		state.recordPostMenuSort(Map.of("clientTick", 8L, "wallTimeMillis", 1020L, "topOption", "Chop down", "topTarget", "Tree"));
		state.recordMenuOptionClicked(Map.of("clientTick", 8L, "wallTimeMillis", 1030L, "option", "Chop down", "target", "Tree"));

		Map<String, Object> snapshot = state.snapshot(0, 0, 0, true, 5);

		assertEquals("client_tick_hot.v1", snapshot.get("schema"));
		assertEquals(8L, snapshot.get("clientTick"));
		assertEquals("Tree", ((Map<?, ?>) snapshot.get("postMenuSort")).get("topTarget"));
		assertEquals("Chop down", ((Map<?, ?>) snapshot.get("lastMenuOptionClicked")).get("option"));
		assertTrue(snapshot.containsKey("latency"));
		assertFalse(snapshot.containsKey("postMenuSortTail"));
		assertFalse(snapshot.containsKey("clickedTail"));
	}

	@Test
	public void tailSnapshotIsBoundedByRequestAndStateCap()
	{
		ClientTickHotState state = new ClientTickHotState(2);
		state.recordPostMenuSort(Map.of("clientTick", 1L, "topOption", "One"));
		state.recordPostMenuSort(Map.of("clientTick", 2L, "topOption", "Two"));
		state.recordPostMenuSort(Map.of("clientTick", 3L, "topOption", "Three"));
		state.recordMenuOptionClicked(Map.of("clientTick", 2L, "option", "Walk here"));
		state.recordMenuOptionClicked(Map.of("clientTick", 3L, "option", "Chop down"));

		Map<String, Object> snapshot = state.snapshot(0, 5, 5, true, 5);
		List<?> menuTail = (List<?>) snapshot.get("postMenuSortTail");
		List<?> clickedTail = (List<?>) snapshot.get("clickedTail");

		assertEquals(2, menuTail.size());
		assertEquals("Two", ((Map<?, ?>) menuTail.get(0)).get("topOption"));
		assertEquals("Three", ((Map<?, ?>) menuTail.get(1)).get("topOption"));
		assertEquals(2, clickedTail.size());
		assertEquals("Walk here", ((Map<?, ?>) clickedTail.get(0)).get("option"));
		assertEquals("Chop down", ((Map<?, ?>) clickedTail.get(1)).get("option"));
		assertEquals(1L, ((Map<?, ?>) snapshot.get("latency")).get("droppedPostMenuSortSamples"));
	}
}

package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class ClientTickHotStateTest
{
	@Test
	public void compactSnapshotExposesLatestSamplesWithoutTail()
	{
		ClientTickHotState state = new ClientTickHotState(2);
		state.recordClientTick(Map.of("clientTick", 7L, "wallTimeMillis", 1000L, "mouseCanvasX", 10, "mouseCanvasY", 20, "clientProcessId", 1234L));
		state.recordPostMenuSort(Map.of("clientTick", 7L, "wallTimeMillis", 1010L, "topOption", "Walk here", "clientProcessId", 1234L));
		state.recordPostMenuSort(Map.of("clientTick", 8L, "wallTimeMillis", 1020L, "topOption", "Chop down", "topTarget", "Tree", "clientProcessId", 1234L));
		state.recordMenuOptionClicked(Map.of("clientTick", 8L, "wallTimeMillis", 1030L, "option", "Chop down", "target", "Tree", "clientProcessId", 1234L));

		Map<String, Object> snapshot = state.snapshot(0, 0, 0, true, 5);

		assertEquals("client_tick_hot.v1", snapshot.get("schema"));
		assertEquals(8L, snapshot.get("clientTick"));
		assertEquals(1234L, snapshot.get("clientProcessId"));
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
		assertEquals(2L, ((Map<?, ?>) menuTail.get(0)).get("eventSequence"));
		assertEquals(3L, ((Map<?, ?>) menuTail.get(1)).get("eventSequence"));
		assertEquals(4L, ((Map<?, ?>) clickedTail.get(0)).get("eventSequence"));
		assertEquals(5L, ((Map<?, ?>) clickedTail.get(1)).get("eventSequence"));
		assertEquals(1L, ((Map<?, ?>) snapshot.get("latency")).get("droppedPostMenuSortSamples"));
	}

	@Test
	public void eventSequenceIsMonotonicAcrossEveryHotLaneAndCannotBeSpoofed()
	{
		ClientTickHotState state = new ClientTickHotState(8);
		Map<String, Object> callerSample = new LinkedHashMap<>();
		callerSample.put("clientTick", 1L);
		callerSample.put("wallTimeMillis", 1000L);
		callerSample.put("eventSequence", 999L);
		state.recordClientTick(callerSample);
		state.recordPostMenuSort(Map.of("clientTick", 2L, "wallTimeMillis", 1000L));
		state.recordMenuOptionClicked(Map.of("clientTick", 3L, "wallTimeMillis", 1000L));
		state.recordClientTick(Map.of("clientTick", 4L, "wallTimeMillis", 1000L));

		Map<String, Object> snapshot = state.snapshot(8, 8, 8, true, 16);
		List<?> clientTail = (List<?>) snapshot.get("clientTickTail");
		List<?> menuTail = (List<?>) snapshot.get("postMenuSortTail");
		List<?> clickedTail = (List<?>) snapshot.get("clickedTail");

		assertEquals(1L, ((Map<?, ?>) clientTail.get(0)).get("eventSequence"));
		assertEquals(ClientTickHotState.LANE_CLIENT_TICK, ((Map<?, ?>) clientTail.get(0)).get("eventLane"));
		assertEquals(2L, ((Map<?, ?>) menuTail.get(0)).get("eventSequence"));
		assertEquals(ClientTickHotState.LANE_POST_MENU_SORT, ((Map<?, ?>) menuTail.get(0)).get("eventLane"));
		assertEquals(3L, ((Map<?, ?>) clickedTail.get(0)).get("eventSequence"));
		assertEquals(ClientTickHotState.LANE_MENU_OPTION_CLICKED, ((Map<?, ?>) clickedTail.get(0)).get("eventLane"));
		assertEquals(4L, ((Map<?, ?>) clientTail.get(1)).get("eventSequence"));
		assertEquals(4L, snapshot.get("eventSequence"));
		assertEquals(4L, snapshot.get("latestEventSequence"));
		assertEquals(4L, snapshot.get("clientTick"));
		assertEquals(999L, callerSample.get("eventSequence"));
	}

	@Test
	public void menuEntriesAreBoundedToSixteenInLatestAndTailSamples()
	{
		ClientTickHotState state = new ClientTickHotState(4);
		List<Map<String, Object>> entries = new ArrayList<>();
		for (int index = 0; index < 20; index++)
		{
			entries.add(Map.of("option", "Option " + index));
		}
		state.recordPostMenuSort(Map.of("clientTick", 1L, "entries", entries));

		Map<String, Object> snapshot = state.snapshot(0, 1, 0, true, 1000);
		Map<?, ?> latest = (Map<?, ?>) snapshot.get("postMenuSort");
		List<?> tail = (List<?>) snapshot.get("postMenuSortTail");

		assertEquals(16, ClientTickHotState.MAX_MENU_ENTRY_LIMIT);
		assertEquals(16, ((List<?>) latest.get("entries")).size());
		assertEquals(16, ((List<?>) ((Map<?, ?>) tail.get(0)).get("entries")).size());
	}

	@Test
	public void newerGameStateSampleReplacesStaleLoggedInHotState()
	{
		ClientTickHotState state = new ClientTickHotState(4);
		state.recordClientTick(Map.of(
				"sampleSource", "ClientTick",
				"sourceEvent", "ClientTick",
				"clientTick", 10L,
				"wallTimeMillis", 1000L,
				"gameState", "LOGGED_IN"));
		state.recordClientTick(Map.of(
				"sampleSource", "GameStateChanged",
				"sourceEvent", "GameStateChanged",
				"clientTick", 10L,
				"wallTimeMillis", 2000L,
				"gameState", "LOGIN_SCREEN"));

		Map<String, Object> snapshot = state.snapshot(1, 0, 0, true, 5);
		List<?> clientTail = (List<?>) snapshot.get("clientTickTail");

		assertEquals("LOGIN_SCREEN", snapshot.get("gameState"));
		assertEquals("GameStateChanged", snapshot.get("sampleSource"));
		assertEquals("GameStateChanged", snapshot.get("sourceEvent"));
		assertEquals("GameStateChanged", ((Map<?, ?>) clientTail.get(0)).get("sampleSource"));
	}

	@Test
	public void cameraInputHasItsOwnBoundedSequencedLaneAndDropCounters()
	{
		ClientTickHotState state = new ClientTickHotState(2);
		state.recordClientTick(Map.of("clientTick", 1L, "wallTimeMillis", 1000L));
		state.recordCameraInput(Map.of("clientTick", 1L, "wallTimeMillis", 1010L, "control", "W"));
		state.recordCameraInput(Map.of("clientTick", 1L, "wallTimeMillis", 1020L, "control", "A"));
		state.recordCameraInput(Map.of("clientTick", 2L, "wallTimeMillis", 1030L, "control", "D"));

		Map<String, Object> snapshot = state.snapshot(0, 0, 0, 8, true, 5);
		List<?> cameraTail = (List<?>) snapshot.get("cameraInputTail");
		Map<?, ?> latency = (Map<?, ?>) snapshot.get("latency");

		assertEquals(2, cameraTail.size());
		assertEquals("A", ((Map<?, ?>) cameraTail.get(0)).get("control"));
		assertEquals("D", ((Map<?, ?>) cameraTail.get(1)).get("control"));
		assertEquals(ClientTickHotState.LANE_CAMERA_INPUT,
				((Map<?, ?>) cameraTail.get(0)).get("eventLane"));
		assertEquals(3L, ((Map<?, ?>) cameraTail.get(0)).get("eventSequence"));
		assertEquals(4L, ((Map<?, ?>) cameraTail.get(1)).get("eventSequence"));
		assertEquals(1L, latency.get("droppedCameraInputSamples"));
		assertEquals(2, latency.get("cameraInputSamplesBuffered"));
	}

	@Test
	public void startingANewCameraLeaseCanClearOnlyThePriorCameraLane()
	{
		ClientTickHotState state = new ClientTickHotState(2);
		state.recordClientTick(Map.of("clientTick", 1L, "wallTimeMillis", 1000L));
		state.recordCameraInput(Map.of("clientTick", 1L, "wallTimeMillis", 1010L, "control", "W"));
		state.recordCameraInput(Map.of("clientTick", 1L, "wallTimeMillis", 1020L, "control", "A"));
		state.recordCameraInput(Map.of("clientTick", 2L, "wallTimeMillis", 1030L, "control", "D"));

		state.clearCameraInput();
		Map<String, Object> snapshot = state.snapshot(2, 0, 0, 8, true, 5);
		Map<?, ?> latency = (Map<?, ?>) snapshot.get("latency");

		assertEquals(1, ((List<?>) snapshot.get("clientTickTail")).size());
		assertTrue(((List<?>) snapshot.get("cameraInputTail")).isEmpty());
		assertEquals(null, snapshot.get("latestCameraInput"));
		assertEquals(0L, latency.get("droppedCameraInputSamples"));
		assertEquals(0, latency.get("cameraInputSamplesBuffered"));
	}
}

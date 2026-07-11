package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class PluginLiveCacheTest
{
	private final Gson gson = new Gson();

	@Test
	public void storesPayloadSnapshotInsteadOfMutableMapReference()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("gameState", "LOGGED_IN");

		assertTrue(cache.update("live_baseline_packet.v1", 10L, "2026-05-11T00:00:00Z", payload));
		payload.put("laterMutation", true);

		PluginLiveCache.CachedPayload cached = cache.get("live_baseline_packet.v1");
		assertNotNull(cached);
		JsonObject cachedPayload = gson.fromJson(cached.payloadJson, JsonObject.class);
		assertEquals("LOGGED_IN", cachedPayload.get("gameState").getAsString());
		assertFalse(cachedPayload.has("laterMutation"));
	}

	@Test
	public void latestPacketByTypeOverwritesOlderPacket()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_inventory_packet.v1", 1L, "2026-05-11T00:00:00Z", Map.of("freeSlots", 10));
		cache.update("live_inventory_packet.v1", 2L, "2026-05-11T00:00:01Z", Map.of("freeSlots", 9));

		PluginLiveCache.CachedPayload cached = cache.get("live_inventory_packet.v1");
		JsonObject cachedPayload = gson.fromJson(cached.payloadJson, JsonObject.class);
		assertEquals(2L, cached.tick);
		assertEquals(2L, cached.sequence);
		assertEquals(9, cachedPayload.get("freeSlots").getAsInt());
	}

	@Test
	public void healthReportsPayloadTypesAndCounters()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_baseline_packet.v1", 3L, "2026-05-11T00:00:00Z", Map.of("gameState", "LOGGED_IN"));

		Map<String, Object> health = cache.health();
		assertEquals(1L, health.get("liveCacheUpdates"));
		assertEquals(0L, health.get("liveCacheUpdateErrors"));
		assertEquals(3L, health.get("liveCacheLatestTick"));
		assertTrue(((java.util.List<?>) health.get("liveCachePayloadTypes")).contains("live_baseline_packet.v1"));
		assertTrue(((Long) health.get("liveCacheEstimatedBytes")) > 0L);
	}

	@Test
	public void publishesAndReadsOneStableFrameSnapshot()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		SensorFrame firstFrame = completeFrame("frame-10", 10L, "2026-05-11T00:00:00Z");
		SensorFrame secondFrame = completeFrame("frame-11", 11L, "2026-05-11T00:00:01Z");

		assertTrue(cache.publish(firstFrame));
		PluginLiveCache.FrameSnapshot firstPublication = cache.snapshot();
		assertNotNull(firstPublication);
		assertSame(firstFrame, firstPublication.getFrame());
		assertEquals(10L, firstPublication.get("live_inventory_packet.v1").tick);

		assertTrue(cache.publish(secondFrame));
		PluginLiveCache.FrameSnapshot secondPublication = cache.snapshot();
		assertNotNull(secondPublication);
		assertSame(secondFrame, secondPublication.getFrame());
		assertEquals(11L, cache.getLatestTick());
		assertEquals(11L, cache.get("live_inventory_packet.v1").tick);

		// Holding the first reference remains a coherent tick-10 view.
		assertEquals(10L, firstPublication.getFrame().getSourceTick());
		assertEquals(10L, firstPublication.get("live_baseline_packet.v1").tick);
		assertEquals(10L, firstPublication.get("live_dialogue_state_packet.v1").tick);
	}

	@Test
	public void incompleteFrameReplacesCompleteFrameWithoutRetainingFacts()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		assertTrue(cache.publish(completeFrame("complete", 20L, "2026-05-11T00:00:00Z")));

		SensorFrame baselineOnly = SensorFrame.builder(
				"baseline-only",
				20L,
				1L,
				"2026-05-11T00:00:01Z")
				.completedAtUtc("2026-05-11T00:00:01Z")
				.fact(gson,
						SensorFrame.FACT_BASELINE,
						20L,
						"2026-05-11T00:00:01Z",
						true,
						List.of(),
						Map.of("gameState", "LOGIN_SCREEN"))
				.build();

		assertTrue(cache.publish(baselineOnly));
		SensorFrame current = cache.currentFrame();
		assertSame(baselineOnly, current);
		assertEquals(20L, current.getSourceTick());
		assertFalse(current.isComplete());
		assertEquals(List.of(SensorFrame.FACT_BASELINE), current.getAvailableFacts());
		assertTrue(current.getUnavailableFacts().contains(SensorFrame.FACT_INVENTORY));
		assertNull(current.getFact(SensorFrame.FACT_INVENTORY));
		assertNull(cache.get("live_inventory_packet.v1"));
		assertEquals(List.of("live_baseline_packet.v1"), cache.packetTypes());
	}

	@Test
	public void healthAgeUsesFrameCaptureTimeInsteadOfPublicationTime()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		SensorFrame frame = SensorFrame.builder(
				"aged-frame",
				30L,
				1L,
				"2026-05-11T00:00:00Z")
				.completedAtUtc("2026-05-11T00:00:01Z")
				.fact(gson,
						SensorFrame.FACT_BASELINE,
						30L,
						"2026-05-11T00:00:00Z",
						true,
						List.of(),
						Map.of("gameState", "LOGGED_IN"))
				.build();

		assertTrue(cache.publishAt(frame, Instant.parse("2026-05-11T00:10:00Z")));
		Map<String, Object> health = cache.healthAt(Instant.parse("2026-05-11T00:15:00Z"));

		assertEquals(900_000L, health.get("liveCacheFrameAgeMillis"));
		@SuppressWarnings("unchecked")
		Map<String, Long> ageByType = (Map<String, Long>) health.get("liveCacheAgeMillisByType");
		assertEquals(Long.valueOf(900_000L), ageByType.get("live_baseline_packet.v1"));
		assertEquals("2026-05-11T00:10:00Z", health.get("liveCacheFramePublishedAtUtc"));
	}

	@Test
	public void healthIsDerivedFromCurrentFrameMetadata()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		SensorFrame frame = completeFrame("health-frame", 40L, "2026-05-11T00:00:00Z");
		assertTrue(cache.publishAt(frame, Instant.parse("2026-05-11T00:00:02Z")));

		Map<String, Object> health = cache.healthAt(Instant.parse("2026-05-11T00:00:03Z"));
		assertEquals(SensorFrame.SCHEMA, health.get("liveCacheFrameSchema"));
		assertEquals("health-frame", health.get("liveCacheFrameId"));
		assertEquals(40L, health.get("liveCacheFrameSourceTick"));
		assertEquals(true, health.get("liveCacheFrameCoherent"));
		assertEquals(true, health.get("liveCacheFrameComplete"));
		assertEquals(SensorFrame.CORE_FACT_NAMES, health.get("liveCacheFrameAvailableFacts"));
		assertEquals(List.of(), health.get("liveCacheFrameUnavailableFacts"));
	}

	@Test
	public void healthCanBeDerivedFromOnePreviouslyCapturedPublication()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		SensorFrame first = completeFrame("health-first", 50L, "2026-05-11T00:00:00Z");
		SensorFrame second = completeFrame("health-second", 51L, "2026-05-11T00:00:01Z");
		assertTrue(cache.publish(first));
		PluginLiveCache.FrameSnapshot captured = cache.snapshot();
		assertTrue(cache.publish(second));

		Map<String, Object> health = cache.healthAt(
				captured,
				Instant.parse("2026-05-11T00:00:02Z"));

		assertEquals("health-first", health.get("liveCacheFrameId"));
		assertEquals(50L, health.get("liveCacheLatestTick"));
		assertEquals(captured.getSequence(), health.get("liveCacheLatestSequence"));
	}

	private SensorFrame completeFrame(String frameId, long tick, String capturedAtUtc)
	{
		SensorFrame.Builder builder = SensorFrame.builder(frameId, tick, 1L, capturedAtUtc)
				.completedAtUtc(capturedAtUtc)
				.sessionId("session-a")
				.clientProcessId(1234L)
				.geometryFrameId("geometry-" + tick);
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			builder.fact(
					gson,
					factName,
					tick,
					capturedAtUtc,
					true,
					List.of(),
					Map.of("fact", factName, "tick", tick));
		}
		return builder.build();
	}
}

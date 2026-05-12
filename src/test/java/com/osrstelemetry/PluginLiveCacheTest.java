package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.util.LinkedHashMap;
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
}

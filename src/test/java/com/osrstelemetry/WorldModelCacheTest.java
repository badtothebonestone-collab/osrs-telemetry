package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class WorldModelCacheTest
{
	@Test
	public void refreshDecisionIncludesTickGeometryAndExistingTriggers()
	{
		assertFalse(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				false, false, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, true, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, true, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, true, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, 10L, 11L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, 10L, 9L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, 10L, 10L, "geometry-1", "geometry-2"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, 10L, 10L, null, "geometry-1"));
	}

	@Test
	public void queryNeverReusesAnotherTickOrGeometryFrame()
	{
		WorldModelCache cache = new WorldModelCache();
		Map<String, Object> identity = Map.of(
				"sessionId", "session-1",
				"clientProcessId", 4321L,
				"geometryFrameId", "geometry-1");

		long initial = refreshSequence(cache.query(null, List.of(), Map.of(), 10L, 20L, identity));
		assertEquals(initial, refreshSequence(cache.query(null, List.of(), Map.of(), 10L, 20L, identity)));

		long newerTick = refreshSequence(cache.query(null, List.of(), Map.of(), 11L, 21L, identity));
		assertEquals(initial + 1L, newerTick);

		long priorTick = refreshSequence(cache.query(null, List.of(), Map.of(), 10L, 22L, identity));
		assertEquals(newerTick + 1L, priorTick);

		Map<String, Object> nextGeometry = Map.of(
				"sessionId", "session-1",
				"clientProcessId", 4321L,
				"geometryFrameId", "geometry-2");
		long nextFrame = refreshSequence(cache.query(null, List.of(), Map.of(), 10L, 22L, nextGeometry));
		assertEquals(priorTick + 1L, nextFrame);
	}

	@Test
	public void responseQualityAndMetadataExposeCaptureProvenance()
	{
		WorldModelCache cache = new WorldModelCache();
		Map<String, Object> identity = Map.of(
				"sessionId", "session-7",
				"clientProcessId", 9876L,
				"geometryFrameId", "geometry-7");

		Map<String, Object> response = cache.query(
				null,
				List.of("world_model_summary"),
				Map.of(),
				55L,
				89L,
				identity);
		Map<String, Object> quality = map(response.get("quality"));
		Map<String, Object> metadata = map(response.get("metadata"));
		Map<String, Object> payloads = map(response.get("payloads"));
		Map<String, Object> summary = map(payloads.get("world_model_summary"));
		Map<String, Object> summaryMetadata = map(summary.get("metadata"));

		assertProvenance(quality);
		assertProvenance(metadata);
		assertProvenance(summaryMetadata);
		assertEquals(quality.get("capturedAtUtc"), metadata.get("capturedAtUtc"));
		assertEquals(metadata.get("capturedAtUtc"), summaryMetadata.get("capturedAtUtc"));
	}

	private static void assertProvenance(Map<String, Object> provenance)
	{
		assertEquals(55L, provenance.get("sourceTick"));
		assertEquals(89L, provenance.get("clientTick"));
		assertEquals("session-7", provenance.get("sessionId"));
		assertEquals(9876L, provenance.get("clientProcessId"));
		assertEquals("geometry-7", provenance.get("geometryFrameId"));
		Instant.parse(String.valueOf(provenance.get("capturedAtUtc")));
	}

	private static long refreshSequence(Map<String, Object> response)
	{
		return ((Number) map(response.get("quality")).get("refreshSequence")).longValue();
	}

	@SuppressWarnings("unchecked")
	private static Map<String, Object> map(Object value)
	{
		return (Map<String, Object>) value;
	}
}

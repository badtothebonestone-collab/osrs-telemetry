package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class WorldModelCacheTest
{
	@Test
	public void refreshDecisionIncludesTickGeometryAndExistingTriggers()
	{
		assertFalse(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				false, false, false, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, true, false, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, true, false, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, true, false, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, true, 10L, 10L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, 10L, 11L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, 10L, 9L, "geometry-1", "geometry-1"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, 10L, 10L, "geometry-1", "geometry-2"));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, 10L, 10L, null, "geometry-1"));
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

	@Test
	public void sceneCensusRowsExposeFactsWithoutPluginTaskSemantics()
	{
		Map<String, Object> source = new LinkedHashMap<>();
		source.put("objectKey", "object-1");
		source.put("kind", "GAME_OBJECT");
		source.put("source", "world_model_cache");
		source.put("id", 1276);
		source.put("name", "Tree");
		source.put("objectName", "Tree");
		source.put("actions", List.of("Chop down"));
		source.put("worldX", 3196);
		source.put("worldY", 3240);
		source.put("plane", 0);
		source.put("sceneX", 50);
		source.put("sceneY", 50);
		source.put("distanceToPlayer", 3);
		source.put("projection", Map.of("geometryAvailable", true));
		source.put("resourceCandidate", true);
		source.put("resourceType", "basic_tree");
		source.put("routeObjectCandidate", false);
		source.put("routeObjectKind", "route_transition");
		source.put("serviceObjectCandidate", false);
		source.put("serviceObjectType", "bank_booth");
		source.put("requiredSkill", "WOODCUTTING");
		source.put("requiredLevel", 1);
		source.put("playerLevelKnown", true);
		source.put("playerLevel", 99);
		source.put("levelRequirementMet", true);
		source.put("targetTemporarilyLockedReason", "task_hint");
		source.put("visibleButNotExecutable", false);
		source.put("futureEligibleWhenLevelMet", false);

		Map<String, Object> scene = WorldModelCache.compactObjectRow(source, true, false);

		assertEquals(1276, scene.get("id"));
		assertEquals("Tree", scene.get("name"));
		assertEquals(List.of("Chop down"), scene.get("actions"));
		assertEquals(3196, scene.get("worldX"));
		assertTrue(scene.containsKey("projection"));
		for (String semanticKey : List.of(
				"resourceCandidate",
				"resourceType",
				"routeObjectCandidate",
				"routeObjectKind",
				"serviceObjectCandidate",
				"serviceObjectType",
				"requiredSkill",
				"requiredLevel",
				"playerLevelKnown",
				"playerLevel",
				"levelRequirementMet",
				"targetTemporarilyLockedReason",
				"visibleButNotExecutable",
				"futureEligibleWhenLevelMet"))
		{
			assertFalse(semanticKey, scene.containsKey(semanticKey));
		}

		Map<String, Object> legacyFiltered = WorldModelCache.compactObjectRow(source, true, true);
		assertTrue(legacyFiltered.containsKey("resourceCandidate"));
		assertTrue(legacyFiltered.containsKey("requiredSkill"));
	}

	@Test
	public void projectionOrderingAndEligibilityNeverUseCandidateHints()
	{
		Map<String, Object> farResource = Map.of(
				"objectKey", "far-resource",
				"distanceToPlayer", 10,
				"resourceCandidate", true);
		Map<String, Object> nearNeutral = Map.of(
				"objectKey", "near-neutral",
				"distanceToPlayer", 1,
				"resourceCandidate", false);

		List<Map<String, Object>> ordered = new ArrayList<>(List.of(farResource, nearNeutral));
		WorldModelCache.sortProjectionCandidates(
				ordered,
				32,
				object -> ((Number) object.get("distanceToPlayer")).intValue());
		assertEquals("near-neutral", ordered.get(0).get("objectKey"));
		assertFalse(WorldModelCache.shouldProjectRecord(false));
		assertTrue(WorldModelCache.shouldProjectRecord(true));

		assertFalse(WorldModelCache.projectionRefreshRequired(
				false, false, false, 0, 192, null, "player"));
		assertTrue(WorldModelCache.projectionRefreshRequired(
				true, false, false, 0, 192, null, "player"));
		assertFalse(WorldModelCache.projectionRefreshRequired(
				true, true, false, 192, 600, "player", "different"));
		assertFalse(WorldModelCache.projectionRefreshRequired(
				true, true, true, 192, 192, "player", "player"));
		assertTrue(WorldModelCache.projectionRefreshRequired(
				true, true, true, 192, 193, "player", "player"));
		assertTrue(WorldModelCache.projectionRefreshRequired(
				true, true, true, 192, 192, "player", "3200:3200:0"));
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

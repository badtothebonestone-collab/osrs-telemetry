package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.Shape;
import java.awt.geom.Area;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.runelite.api.Point;
import org.junit.Test;

public class WorldModelCacheTest
{
	@Test
	public void aimPointUsesShapeInteriorInsteadOfUnverifiedCanvasLocation()
	{
		Rectangle clickbox = new Rectangle(100, 120, 80, 60);
		Rectangle viewport = new Rectangle(0, 0, 400, 300);

		Point point = WorldModelCache.interiorAimPoint(
				clickbox, viewport, new Point(140, 250));

		assertNotNull(point);
		assertEquals(140, point.getX());
		assertEquals(150, point.getY());
		assertTrue(clickbox.contains(point.getX(), point.getY()));
		assertTrue(viewport.contains(point.getX(), point.getY()));
	}

	@Test
	public void aimPointRetainsOnlyValidPreferredPointAndClipsToViewport()
	{
		Rectangle viewport = new Rectangle(0, 0, 100, 100);
		Rectangle shape = new Rectangle(80, 80, 40, 40);
		Point preferred = new Point(85, 86);

		Point retained = WorldModelCache.interiorAimPoint(shape, viewport, preferred);
		assertNotNull(retained);
		assertEquals(preferred.getX(), retained.getX());
		assertEquals(preferred.getY(), retained.getY());

		Point clipped = WorldModelCache.interiorAimPoint(
				shape, viewport, new Point(110, 110));
		assertNotNull(clipped);
		assertTrue(shape.contains(clipped.getX(), clipped.getY()));
		assertTrue(viewport.contains(clipped.getX(), clipped.getY()));
	}

	@Test
	public void aimPointFindsConcaveVisibleInteriorOrFailsClosed()
	{
		Polygon concave = new Polygon(
				new int[]{10, 90, 90, 30, 30, 10},
				new int[]{10, 10, 30, 30, 90, 90},
				6);
		Rectangle viewport = new Rectangle(0, 0, 100, 100);

		Point point = WorldModelCache.interiorAimPoint(
				concave, viewport, new Point(60, 60));

		assertNotNull(point);
		assertTrue(concave.contains(point.getX(), point.getY()));
		assertTrue(viewport.contains(point.getX(), point.getY()));
		Point repeated = WorldModelCache.interiorAimPoint(
				concave, viewport, new Point(60, 60));
		assertEquals(point.getX(), repeated.getX());
		assertEquals(point.getY(), repeated.getY());
		assertNull(WorldModelCache.interiorAimPoint(
				concave, new Rectangle(200, 200, 20, 20), null));
	}

	@Test
	public void aimPointShapeFailureStaysLocalAndFailsClosed()
	{
		Shape throwing = new Area(new Rectangle(10, 10, 20, 20))
		{
			@Override
			public boolean contains(double x, double y)
			{
				throw new IllegalStateException("shape failure");
			}
		};

		assertNull(WorldModelCache.interiorAimPoint(
				throwing, new Rectangle(0, 0, 100, 100), null));
	}

	@Test
	public void aimPointNeverFallsThroughPresentAuthoritativeShape()
	{
		Rectangle viewport = new Rectangle(0, 0, 100, 100);
		Rectangle outside = new Rectangle(200, 200, 20, 20);
		Rectangle hull = new Rectangle(20, 20, 30, 30);
		Rectangle tile = new Rectangle(60, 60, 20, 20);

		assertNull(WorldModelCache.authoritativeAimPoint(
				null, viewport, outside, hull, tile));
		Point hullPoint = WorldModelCache.authoritativeAimPoint(
				null, viewport, null, hull, tile);
		assertNotNull(hullPoint);
		assertTrue(hull.contains(hullPoint.getX(), hullPoint.getY()));
		assertNull(WorldModelCache.authoritativeAimPoint(
				null, viewport, null, outside, tile));
		Point tilePoint = WorldModelCache.authoritativeAimPoint(
				null, viewport, null, null, tile);
		assertNotNull(tilePoint);
		assertTrue(tile.contains(tilePoint.getX(), tilePoint.getY()));
	}

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

		Map<String, Object> scene = WorldModelCache.compactObjectRow(source, true);

		assertEquals(1276, scene.get("id"));
		assertEquals("Tree", scene.get("name"));
		assertEquals(List.of("Chop down"), scene.get("actions"));
		assertEquals(3196, scene.get("worldX"));
		assertTrue(scene.containsKey("projection"));
		assertEquals(
				List.of(
						"objectKey", "kind", "source", "id", "name", "objectName",
						"actions", "worldX", "worldY", "plane", "sceneX", "sceneY",
						"distanceToPlayer", "projection", "projectionStatus"),
				new ArrayList<>(scene.keySet()));
	}

	@Test
	public void projectionOrderingUsesOnlyDistanceAndStableIdentity()
	{
		Map<String, Object> far = Map.of(
				"objectKey", "far",
				"distanceToPlayer", 10);
		Map<String, Object> near = Map.of(
				"objectKey", "near",
				"distanceToPlayer", 1);

		List<Map<String, Object>> ordered = new ArrayList<>(List.of(far, near));
		WorldModelCache.sortProjectionCandidates(
				ordered,
				32,
				object -> ((Number) object.get("distanceToPlayer")).intValue());
		assertEquals("near", ordered.get(0).get("objectKey"));
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

	@Test
	public void actorCapabilityUpgradeIsExplicitAndBudgetAware()
	{
		assertFalse(WorldModelCache.actorRefreshRequired(
				false, false, true, 0, 64, 12, 12));
		assertTrue(WorldModelCache.actorRefreshRequired(
				true, false, false, 0, 64, 12, 12));
		assertFalse(WorldModelCache.actorRefreshRequired(
				true, true, false, 16, 64, 12, 12));
		assertFalse(WorldModelCache.actorRefreshRequired(
				true, true, true, 16, 16, 12, 12));
		assertTrue(WorldModelCache.actorRefreshRequired(
				true, true, true, 16, 17, 12, 12));
		assertTrue(WorldModelCache.actorRefreshRequired(
				true, true, false, 64, 64, 12, 16));
		assertTrue(WorldModelCache.shouldRefreshSnapshot(
				true, false, false, false, false, true,
				10L, 10L, "geometry-1", "geometry-1"));
	}

	@Test
	public void actorCensusRowsAreBoundedNearbyNpcFactsWithoutNestedPlayerNames()
	{
		Map<String, Object> nearNpc = new LinkedHashMap<>();
		nearNpc.put("type", "NPC");
		nearNpc.put("index", 7);
		nearNpc.put("id", 123);
		nearNpc.put("name", "Guide");
		nearNpc.put("actions", List.of("Talk-to"));
		nearNpc.put("worldX", 3200);
		nearNpc.put("worldY", 3230);
		nearNpc.put("plane", 0);
		nearNpc.put("distanceToPlayer", 2);
		nearNpc.put("interacting", Map.of("name", "Private player name"));
		Map<String, Object> secondNpc = Map.of(
				"type", "NPC",
				"index", 8,
				"id", 124,
				"name", "Banker",
				"actions", List.of("Bank"),
				"distanceToPlayer", 3);
		Map<String, Object> player = Map.of(
				"type", "PLAYER",
				"index", 9,
				"name", "Private player name",
				"distanceToPlayer", 1);
		Map<String, Object> farNpc = Map.of(
				"type", "NPC",
				"index", 10,
				"id", 125,
				"name", "Far guide",
				"distanceToPlayer", 20);

		List<Map<String, Object>> rows = WorldModelCache.boundedNpcActorRows(
				List.of(player, secondNpc, farNpc, nearNpc), 8, 1);

		assertEquals(1, rows.size());
		assertEquals("NPC", rows.get(0).get("type"));
		assertEquals(123, rows.get(0).get("id"));
		assertEquals(List.of("Talk-to"), rows.get(0).get("actions"));
		assertFalse(rows.get(0).containsKey("interacting"));
		assertFalse(rows.get(0).containsValue("Private player name"));
		assertTrue(WorldModelCache.boundedNpcActorRows(
				List.of(nearNpc), 8, 0).isEmpty());
	}

	@Test
	public void actorAndCollisionNeedsProduceBoundedReadOnlyPayloads()
	{
		WorldModelCache cache = new WorldModelCache();
		Map<String, Object> response = cache.query(
				null,
				List.of("actor_census", "collision_window"),
				Map.of("worldModel", Map.of(
						"radiusTiles", 8,
						"maxActors", 4,
						"maxCollisionTiles", 16)),
				55L,
				89L,
				Map.of(
						"sessionId", "session-7",
						"clientProcessId", 9876L,
						"geometryFrameId", "geometry-7"));
		Map<String, Object> payloads = map(response.get("payloads"));
		Map<String, Object> actors = map(payloads.get("actor_census"));
		Map<String, Object> collision = map(payloads.get("collision_window"));

		assertEquals("world_model_actor_census.v1", actors.get("schema"));
		assertEquals(List.of(), actors.get("actors"));
		assertEquals("world_model_collision_window.v1", collision.get("schema"));
		assertFalse((Boolean) collision.get("collisionAvailable"));
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

package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.awt.Polygon;
import java.awt.Rectangle;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import com.google.gson.Gson;
import net.runelite.api.Client;
import net.runelite.api.GameObject;
import net.runelite.api.GameState;
import net.runelite.api.ObjectComposition;
import net.runelite.api.Player;
import net.runelite.api.Point;
import net.runelite.api.Scene;
import net.runelite.api.Tile;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import org.junit.Test;

public class WorldModelDenseSceneTest
{
	private static final Gson GSON = new Gson();

	@Test
	public void denseSceneBoundsRawWorkAndEnrichesOnlyReturnedRows()
	{
		AtomicInteger definitionLookups = new AtomicInteger();
		Client client = denseClient(definitionLookups);
		WorldModelCache cache = new WorldModelCache();
		Map<String, Object> request = Map.of(
				"worldModel", Map.of(
						"radiusTiles", 32,
						"maxObjects", 64,
						"includeProjection", true,
						"includeCollision", false));
		Map<String, Object> identity = Map.of(
				"sessionId", "dense-session",
				"clientProcessId", 9876L,
				"geometryFrameId", "dense-geometry");

		Map<String, Object> first = cache.query(
				client, List.of("scene_object_census"), request, 100L, 200L, identity);
		Map<String, Object> census = census(first);
		Map<String, Object> pipeline = map(first.get("pipeline"));

		assertEquals(4_225, pipeline.get("requestedTileCount"));
		assertEquals(4_225, pipeline.get("scannedTileSlots"));
		assertEquals(4_225, pipeline.get("scannedTiles"));
		assertEquals(4_225, pipeline.get("discoveredObjectCount"));
		assertEquals(4_225, pipeline.get("indexedObjectCount"));
		assertEquals(64, pipeline.get("enrichedObjectCount"));
		assertEquals(64, pipeline.get("projectedObjectCount"));
		assertEquals(64, pipeline.get("returnedObjectCount"));
		assertEquals(64, definitionLookups.get());
		assertTrue((Boolean) census.get("sceneCoverageComplete"));
		assertTrue((Boolean) census.get("censusComplete"));
		assertTrue((Boolean) census.get("responseCapHit"));
		assertFalse((Boolean) census.get("authoritativeAbsenceEligible"));

		Map<String, Object> repeated = cache.query(
				client, List.of("scene_object_census"), request, 100L, 201L, identity);
		Map<String, Object> repeatedPipeline = map(repeated.get("pipeline"));
		assertTrue((Boolean) repeatedPipeline.get("cacheHit"));
		assertEquals(0, repeatedPipeline.get("enrichedObjectCount"));
		assertEquals(64, repeatedPipeline.get("enrichmentCacheHits"));
		assertEquals(0, repeatedPipeline.get("projectedObjectCount"));
		assertEquals(64, repeatedPipeline.get("projectionCacheHits"));
		assertEquals(64, definitionLookups.get());

		cache.markDirty("synthetic_scene_changed");
		Map<String, Object> dirtyRefresh = cache.query(
				client, List.of("scene_object_census"), request, 100L, 202L, identity);
		Map<String, Object> dirtyPipeline = map(dirtyRefresh.get("pipeline"));
		assertFalse((Boolean) dirtyPipeline.get("cacheHit"));
		assertEquals("synthetic_scene_changed", dirtyPipeline.get("refreshReason"));
		assertEquals(128, definitionLookups.get());
	}

	@Test
	public void missingCenterUsesPlayerAndIncoherentExplicitCentersFailClosed()
	{
		Client client = denseClient(new AtomicInteger());
		Map<String, Object> identity = Map.of(
				"sessionId", "anchor-session",
				"clientProcessId", 9876L,
				"geometryFrameId", "anchor-geometry");
		Map<String, Object> playerAnchored = new WorldModelCache().query(
				client,
				List.of("scene_object_census"),
				Map.of("worldModel", Map.of("radiusTiles", 4, "maxObjects", 16)),
				10L,
				20L,
				identity);
		Map<String, Object> playerCensus = census(playerAnchored);
		assertEquals("player", playerCensus.get("anchorSource"));
		assertEquals(81, playerCensus.get("scannedTiles"));
		assertTrue((Boolean) playerCensus.get("sceneCoverageComplete"));

		Map<String, Object> wrongPlane = new WorldModelCache().query(
				client,
				List.of("scene_object_census"),
				Map.of("worldModel", Map.of(
						"radiusTiles", 4,
						"maxObjects", 16,
						"centerWorldLocation", Map.of(
								"worldX", 3_252, "worldY", 3_252, "plane", 1))),
				10L,
				20L,
				identity);
		Map<String, Object> wrongPlaneCensus = census(wrongPlane);
		assertEquals("explicit", wrongPlaneCensus.get("anchorSource"));
		assertEquals(0, wrongPlaneCensus.get("scannedTiles"));
		assertFalse((Boolean) wrongPlaneCensus.get("sceneCoverageComplete"));
		assertFalse((Boolean) wrongPlaneCensus.get("censusComplete"));

		Map<String, Object> malformed = new WorldModelCache().query(
				client,
				List.of("scene_object_census"),
				Map.of("worldModel", Map.of(
						"radiusTiles", 4,
						"maxObjects", 16,
						"centerWorldLocation", Map.of("worldX", 3_252))),
				10L,
				20L,
				identity);
		Map<String, Object> malformedCensus = census(malformed);
		assertEquals("invalid_explicit", malformedCensus.get("anchorSource"));
		assertEquals(0, malformedCensus.get("scannedTiles"));
		assertFalse((Boolean) malformedCensus.get("censusComplete"));

		Map<String, Object> outside = new WorldModelCache().query(
				client,
				List.of("scene_object_census"),
				Map.of("worldModel", Map.of(
						"radiusTiles", 4,
						"maxObjects", 16,
						"centerWorldLocation", Map.of(
								"worldX", 3_000, "worldY", 3_000, "plane", 0))),
				10L,
				20L,
				identity);
		Map<String, Object> outsideCensus = census(outside);
		assertEquals(0, outsideCensus.get("scannedTiles"));
		assertEquals(0, outsideCensus.get("returned"));
		assertFalse((Boolean) outsideCensus.get("sceneCoverageComplete"));
		assertFalse((Boolean) outsideCensus.get("authoritativeAbsenceEligible"));
	}

	@Test
	public void denseSceneBenchmarkReportsRefreshAndExactSourceHitDistribution()
	{
		AtomicInteger definitionLookups = new AtomicInteger();
		Client client = denseClient(definitionLookups);
		WorldModelCache cache = new WorldModelCache();
		Map<String, Object> request = Map.of(
				"worldModel", Map.of(
						"radiusTiles", 32,
						"maxObjects", 64,
						"includeProjection", true,
						"includeCollision", false));
		Map<String, Object> identity = Map.of(
				"sessionId", "dense-benchmark",
				"clientProcessId", 9876L,
				"geometryFrameId", "dense-benchmark-geometry");
		for (int warmup = 0; warmup < 5; warmup++)
		{
			cache.query(client, List.of("scene_object_census"), request,
					warmup + 1L, warmup + 1L, identity);
		}

		List<Double> refreshMillis = new ArrayList<>();
		List<Integer> payloadBytes = new ArrayList<>();
		Map<String, Object> last = Map.of();
		for (int sample = 0; sample < 30; sample++)
		{
			long started = System.nanoTime();
			last = cache.query(client, List.of("scene_object_census"), request,
					100L + sample, 200L + sample, identity);
			refreshMillis.add((System.nanoTime() - started) / 1_000_000.0d);
			payloadBytes.add(GSON.toJson(last).getBytes(StandardCharsets.UTF_8).length);
			assertEquals(1, map(last.get("pipeline")).get("cacheEntries"));
		}

		List<Double> hitMillis = new ArrayList<>();
		for (int sample = 0; sample < 100; sample++)
		{
			long started = System.nanoTime();
			Map<String, Object> hit = cache.query(
					client, List.of("scene_object_census"), request,
					129L, 300L + sample, identity);
			hitMillis.add((System.nanoTime() - started) / 1_000_000.0d);
			assertTrue((Boolean) map(hit.get("pipeline")).get("cacheHit"));
		}

		System.out.printf(
				"DENSE_PIPELINE_BENCHMARK samples=30 refresh_ms_p50=%.3f refresh_ms_p95=%.3f refresh_ms_max=%.3f "
						+ "hit_ms_p50=%.3f hit_ms_p95=%.3f hit_ms_max=%.3f payload_bytes_p50=%d payload_bytes_p95=%d payload_bytes_max=%d "
						+ "scanned=%s discovered=%s enriched=%s projected=%s returned=%s%n",
				percentile(refreshMillis, 0.50), percentile(refreshMillis, 0.95), Collections.max(refreshMillis),
				percentile(hitMillis, 0.50), percentile(hitMillis, 0.95), Collections.max(hitMillis),
				percentileInt(payloadBytes, 0.50), percentileInt(payloadBytes, 0.95), Collections.max(payloadBytes),
				map(last.get("pipeline")).get("scannedTiles"),
				map(last.get("pipeline")).get("discoveredObjectCount"),
				map(last.get("pipeline")).get("enrichedObjectCount"),
				map(last.get("pipeline")).get("projectedObjectCount"),
				map(last.get("pipeline")).get("returnedObjectCount"));
	}

	private static Client denseClient(AtomicInteger definitionLookups)
	{
		int baseX = 3_200;
		int baseY = 3_200;
		Tile[][][] tiles = new Tile[1][104][104];
		for (int sceneX = 20; sceneX <= 84; sceneX++)
		{
			for (int sceneY = 20; sceneY <= 84; sceneY++)
			{
				tiles[0][sceneX][sceneY] = tile(baseX, baseY, sceneX, sceneY);
			}
		}
		Scene scene = proxy(Scene.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getTiles": return tiles;
				case "getBaseX": return baseX;
				case "getBaseY": return baseY;
				default: return defaultValue(method);
			}
		});
		WorldView worldView = proxy(WorldView.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getScene": return scene;
				case "getBaseX": return baseX;
				case "getBaseY": return baseY;
				default: return defaultValue(method);
			}
		});
		Player player = proxy(Player.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getWorldLocation": return new WorldPoint(baseX + 52, baseY + 52, 0);
				case "getLocalLocation": return LocalPoint.fromScene(52, 52);
				default: return defaultValue(method);
			}
		});
		ObjectComposition definition = proxy(ObjectComposition.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getName": return "Dense object";
				case "getActions": return new String[]{"Use", null, null, null, "Examine"};
				default: return defaultValue(method);
			}
		});
		return proxy(Client.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getGameState": return GameState.LOGGED_IN;
				case "getPlane": return 0;
				case "getTopLevelWorldView": return worldView;
				case "getScene": return scene;
				case "getLocalPlayer": return player;
				case "getObjectDefinition":
					definitionLookups.incrementAndGet();
					return definition;
				case "getViewportWidth": return 800;
				case "getViewportHeight": return 600;
				case "getCanvasWidth": return 800;
				case "getCanvasHeight": return 600;
				default: return defaultValue(method);
			}
		});
	}

	private static Tile tile(int baseX, int baseY, int sceneX, int sceneY)
	{
		WorldPoint world = new WorldPoint(baseX + sceneX, baseY + sceneY, 0);
		LocalPoint local = LocalPoint.fromScene(sceneX, sceneY);
		long hash = ((long) sceneX << 32) | (sceneY & 0xffffffffL);
		Polygon tilePolygon = new Polygon(
				new int[]{100, 110, 110, 100},
				new int[]{100, 100, 110, 110},
				4);
		GameObject gameObject = proxy(GameObject.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getId": return 2_000 + ((sceneX * 104 + sceneY) % 1_000);
				case "getHash": return hash;
				case "getWorldLocation": return world;
				case "getLocalLocation": return local;
				case "getPlane": return 0;
				case "getOrientation": return 0;
				case "getCanvasLocation": return new Point(105, 105);
				case "getClickbox": return new Rectangle(100, 100, 10, 10);
				case "getConvexHull": return new Rectangle(100, 100, 10, 10);
				case "getCanvasTilePoly": return tilePolygon;
				default: return defaultValue(method);
			}
		});
		return proxy(Tile.class, (proxy, method, args) -> {
			switch (method.getName())
			{
				case "getWorldLocation": return world;
				case "getSceneLocation": return new Point(sceneX, sceneY);
				case "getPlane": return 0;
				case "getGameObjects": return new GameObject[]{gameObject};
				case "getGroundItems": return List.of();
				default: return defaultValue(method);
			}
		});
	}

	@SuppressWarnings("unchecked")
	private static <T> T proxy(Class<T> type, InvocationHandler handler)
	{
		return (T) Proxy.newProxyInstance(
				type.getClassLoader(), new Class<?>[]{type}, handler);
	}

	private static Object defaultValue(Method method)
	{
		Class<?> type = method.getReturnType();
		if (!type.isPrimitive())
		{
			return null;
		}
		if (type == boolean.class)
		{
			return false;
		}
		if (type == byte.class)
		{
			return (byte) 0;
		}
		if (type == short.class)
		{
			return (short) 0;
		}
		if (type == int.class)
		{
			return 0;
		}
		if (type == long.class)
		{
			return 0L;
		}
		if (type == float.class)
		{
			return 0.0f;
		}
		if (type == double.class)
		{
			return 0.0d;
		}
		if (type == char.class)
		{
			return '\0';
		}
		return null;
	}

	@SuppressWarnings("unchecked")
	private static Map<String, Object> map(Object value)
	{
		return (Map<String, Object>) value;
	}

	private static Map<String, Object> census(Map<String, Object> response)
	{
		return map(map(response.get("payloads")).get("scene_object_census"));
	}

	private static double percentile(List<Double> values, double quantile)
	{
		List<Double> sorted = new ArrayList<>(values);
		Collections.sort(sorted);
		int index = Math.min(sorted.size() - 1,
				Math.max(0, (int) Math.ceil(sorted.size() * quantile) - 1));
		return sorted.get(index);
	}

	private static int percentileInt(List<Integer> values, double quantile)
	{
		List<Integer> sorted = new ArrayList<>(values);
		Collections.sort(sorted);
		int index = Math.min(sorted.size() - 1,
				Math.max(0, (int) Math.ceil(sorted.size() * quantile) - 1));
		return sorted.get(index);
	}
}

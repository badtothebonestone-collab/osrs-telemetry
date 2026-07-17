package com.osrstelemetry;

import java.awt.Rectangle;
import java.awt.Shape;
import java.awt.geom.PathIterator;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.function.ToIntFunction;
import net.runelite.api.Client;
import net.runelite.api.CollisionData;
import net.runelite.api.CollisionDataFlag;
import net.runelite.api.DecorativeObject;
import net.runelite.api.GameObject;
import net.runelite.api.GameState;
import net.runelite.api.GroundObject;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.MenuEntry;
import net.runelite.api.NPC;
import net.runelite.api.NPCComposition;
import net.runelite.api.ObjectComposition;
import net.runelite.api.Perspective;
import net.runelite.api.Player;
import net.runelite.api.Point;
import net.runelite.api.Scene;
import net.runelite.api.Tile;
import net.runelite.api.TileItem;
import net.runelite.api.TileObject;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;

class WorldModelCache
{
	static final String SCHEMA = "world_model_snapshot.v1";
	private static final String QUERY_SCHEMA = "world_model_query_response.v1";
	private static final int DEFAULT_MAX_OBJECTS = 160;
	private static final int HARD_MAX_OBJECTS = 10000;
	private static final int DEFAULT_RADIUS_TILES = 48;
	private static final int HARD_RADIUS_TILES = 96;
	private static final int DEFAULT_MAX_COLLISION_TILES = 4096;
	private static final int HARD_MAX_COLLISION_TILES = 8192;
	private static final int DEFAULT_MAX_GROUND_ITEMS = 250;
	private static final int HARD_MAX_PROJECTION_OBJECTS = 600;
	private static final int HARD_MAX_PRIORITY_OBJECT_IDS = 32;
	private static final int MAX_RAW_SNAPSHOT_CACHE_ENTRIES = 4;
	private static final int MAX_ENRICHED_OBJECT_CACHE_ENTRIES = 256;
	private static final int MAX_PROJECTION_CACHE_ENTRIES = 128;
	private static final int COLLISION_MOVEMENT_MASK = CollisionDataFlag.BLOCK_MOVEMENT_NORTH_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_NORTH
			| CollisionDataFlag.BLOCK_MOVEMENT_NORTH_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_OBJECT
			| CollisionDataFlag.BLOCK_MOVEMENT_FLOOR_DECORATION
			| CollisionDataFlag.BLOCK_MOVEMENT_FLOOR
			| CollisionDataFlag.BLOCK_MOVEMENT_FULL;

	private Snapshot latest;
	private final LinkedHashMap<String, Snapshot> rawSnapshotCache = new LinkedHashMap<>(8, 0.75f, true);
	private boolean dirty = true;
	private String dirtyReason = "startup";
	private long refreshSequence;
	private long dirtySequence;
	private long querySequence;
	private long cacheHits;
	private long cacheMisses;

	synchronized void clear(String reason)
	{
		latest = null;
		rawSnapshotCache.clear();
		dirty = true;
		dirtySequence++;
		dirtyReason = reason == null || reason.isBlank() ? "clear" : reason;
	}

	synchronized void markDirty(String reason)
	{
		dirty = true;
		dirtySequence++;
		rawSnapshotCache.clear();
		dirtyReason = reason == null || reason.isBlank() ? "scene_changed" : reason;
	}

	synchronized Map<String, Object> query(
			Client client,
			List<String> needs,
			Map<String, Object> request,
			long tick,
			long clientTick,
			Map<String, Object> identity)
	{
		long started = System.nanoTime();
		long currentQuerySequence = ++querySequence;
		QueryOptions options = QueryOptions.from(request, needs);
		RefreshResult refresh = refreshIfNeeded(client, options, tick, clientTick, identity);
		Snapshot snapshot = refresh.snapshot;
		QueryWork queryWork = new QueryWork(options.maxProjectionObjects());
		Map<String, Object> response = new LinkedHashMap<>();
		Map<String, Object> payloads = new LinkedHashMap<>();
		List<String> warnings = new ArrayList<>(snapshot.warnings);
		for (String need : needs)
		{
			switch (need)
			{
				case "world_model_summary":
					payloads.put(need, summaryPayload(snapshot));
					break;
				case "scene_object_census":
					payloads.put(need, objectCensusPayload(client, snapshot, options, queryWork));
					break;
				case "actor_census":
					payloads.put(need, actorCensusPayload(snapshot, options));
					break;
				case "collision_window":
					payloads.put(need, collisionWindowPayload(snapshot, options, true));
					break;
				case "pathing_frontier":
					payloads.put(need, pathingFrontierPayload(snapshot, options));
					break;
				case "projection_audit":
					payloads.put(need, projectionAuditPayload(snapshot));
					break;
				case "minimap_projection":
					payloads.put(need, minimapProjectionPayload(snapshot, options));
					break;
				case "full_world_model_debug":
					payloads.put(need, fullDebugPayload(client, snapshot, options, queryWork));
					break;
				default:
					warnings.add("unsupported world model need: " + need);
					break;
			}
		}
		Map<String, Object> sizing = new LinkedHashMap<>();
		sizing.put("objectCount", snapshot.objects.size());
		sizing.put("groundItemCount", snapshot.groundItems.size());
		sizing.put("actorCount", snapshot.actors.size());
		sizing.put("maxObjects", options.maxObjects);
		sizing.put("radiusTiles", options.radiusTiles);
		sizing.put("priorityObjectIdCount", options.priorityObjectIds.size());
		sizing.put("priorityObjectKeyCount", options.priorityObjectKeys.size());
		sizing.put("refreshDurationMillis", snapshot.refreshDurationMillis);
		sizing.put("queryDurationMillis", elapsedMillis(started));
		sizing.put("scannedTiles", snapshot.scannedTiles);
		sizing.put("discoveredObjectCount", snapshot.discoveredObjectCount);
		sizing.put("indexedObjectCount", snapshot.objects.size());
		sizing.put("enrichedObjectCount", queryWork.enrichedObjectCount);
		sizing.put("projectedObjectCount", queryWork.projectedObjectCount);

		response.put("schema", QUERY_SCHEMA);
		response.put("snapshotSchema", SCHEMA);
		response.put("generatedAtUtc", Instant.now().toString());
		response.put("status", snapshot.available ? (warnings.isEmpty() ? "PASS" : "WARN") : "FAIL");
		response.put("needs", List.copyOf(needs));
		response.put("payloads", payloads);
		response.put("metadata", metadataPayload(snapshot));
		response.put("quality", qualityPayload(snapshot));
		response.put("warnings", warnings);
		response.put("sizing", sizing);
		response.put("pipeline", pipelinePayload(
				currentQuerySequence, refresh, snapshot, options, queryWork, elapsedMillis(started)));
		return response;
	}

	private RefreshResult refreshIfNeeded(
			Client client,
			QueryOptions options,
			long tick,
			long clientTick,
			Map<String, Object> identity)
	{
		boolean forced = options.fullDebug || options.forceRefresh;
		Map<String, Object> sourceIdentity = identity == null ? Map.of() : identity;
		Integer livePlane = null;
		Integer liveBaseX = null;
		Integer liveBaseY = null;
		if (client != null)
		{
			try
			{
				livePlane = client.getPlane();
				WorldView view = client.getTopLevelWorldView();
				Scene scene = view == null ? client.getScene() : view.getScene();
				if (scene != null)
				{
					liveBaseX = view == null ? scene.getBaseX() : view.getBaseX();
					liveBaseY = view == null ? scene.getBaseY() : view.getBaseY();
				}
			}
			catch (RuntimeException ignored)
			{
				// The normal snapshot build reports client access failures truthfully.
			}
		}
		boolean sourceChanged = latest != null && (
				latest.sourceTick != tick
						|| !Objects.equals(latest.geometryFrameId, sourceIdentity.get("geometryFrameId"))
						|| !Objects.equals(latest.sessionId, stringValue(sourceIdentity.get("sessionId")))
						|| !Objects.equals(latest.clientProcessId, sourceIdentity.get("clientProcessId"))
						|| (livePlane != null && latest.plane != livePlane)
						|| (liveBaseX != null && !Objects.equals(latest.baseX, liveBaseX))
						|| (liveBaseY != null && !Objects.equals(latest.baseY, liveBaseY)));
		if (sourceChanged)
		{
			rawSnapshotCache.clear();
		}
		String cacheKey = options.rawCacheKey(
				tick, identity, dirtySequence, livePlane, liveBaseX, liveBaseY);
		Snapshot cached = forced || dirty || sourceChanged ? null : rawSnapshotCache.get(cacheKey);
		if (cached != null)
		{
			latest = cached;
			cacheHits++;
			return new RefreshResult(cached, true, "exact_source_identity_hit", cacheKey);
		}
		cacheMisses++;
		String reason;
		if (forced)
		{
			reason = "forced_query";
		}
		else if (dirty)
		{
			reason = dirtyReason;
		}
		else if (latest != null && latest.sourceTick != tick)
		{
			reason = "source_tick_changed";
		}
		else if (latest != null && !Objects.equals(
				latest.geometryFrameId, identity == null ? null : identity.get("geometryFrameId")))
		{
			reason = "geometry_frame_changed";
		}
		else if (sourceChanged)
		{
			reason = "source_identity_changed";
		}
		else if (latest != null)
		{
			reason = "request_shape_changed";
		}
		else
		{
			reason = "cache_miss";
		}
		Snapshot snapshot = buildSnapshot(client, options, tick, clientTick, identity, reason);
		snapshot.rawCacheKey = cacheKey;
		latest = snapshot;
		rawSnapshotCache.put(cacheKey, snapshot);
		while (rawSnapshotCache.size() > MAX_RAW_SNAPSHOT_CACHE_ENTRIES)
		{
			String eldest = rawSnapshotCache.keySet().iterator().next();
			rawSnapshotCache.remove(eldest);
		}
		dirty = false;
		dirtyReason = null;
		return new RefreshResult(snapshot, false, reason, cacheKey);
	}

	static boolean shouldRefreshSnapshot(
			boolean snapshotPresent,
			boolean dirty,
			boolean stale,
			boolean forced,
			boolean projectionUpgrade,
			long cachedSourceTick,
			long requestedSourceTick,
			Object cachedGeometryFrameId,
			Object requestedGeometryFrameId)
	{
		return shouldRefreshSnapshot(
				snapshotPresent,
				dirty,
				stale,
				forced,
				projectionUpgrade,
				false,
				cachedSourceTick,
				requestedSourceTick,
				cachedGeometryFrameId,
				requestedGeometryFrameId);
	}

	static boolean shouldRefreshSnapshot(
			boolean snapshotPresent,
			boolean dirty,
			boolean stale,
			boolean forced,
			boolean projectionUpgrade,
			boolean actorUpgrade,
			long cachedSourceTick,
			long requestedSourceTick,
			Object cachedGeometryFrameId,
			Object requestedGeometryFrameId)
	{
		return !snapshotPresent
				|| dirty
				|| stale
				|| forced
				|| projectionUpgrade
				|| actorUpgrade
				|| cachedSourceTick != requestedSourceTick
				|| !Objects.equals(cachedGeometryFrameId, requestedGeometryFrameId);
	}

	static boolean projectionRefreshRequired(
			boolean projectionRequested,
			boolean cachedProjectionCaptured,
			boolean cachedProjectionCapHit,
			int cachedProjectionBudget,
			int requestedProjectionBudget,
			String cachedProjectionAnchorKey,
			String requestedProjectionAnchorKey)
	{
		if (!projectionRequested)
		{
			return false;
		}
		if (!cachedProjectionCaptured)
		{
			return true;
		}
		return cachedProjectionCapHit
				&& (requestedProjectionBudget > cachedProjectionBudget
				|| !Objects.equals(cachedProjectionAnchorKey, requestedProjectionAnchorKey));
	}

	static boolean actorRefreshRequired(
			boolean actorCensusRequested,
			boolean cachedActorsCaptured,
			boolean cachedActorCapHit,
			int cachedActorBudget,
			int requestedActorBudget,
			int cachedRadiusTiles,
			int requestedRadiusTiles)
	{
		if (!actorCensusRequested)
		{
			return false;
		}
		return !cachedActorsCaptured
				|| cachedRadiusTiles != requestedRadiusTiles
				|| (cachedActorCapHit && requestedActorBudget > cachedActorBudget);
	}

	static boolean projectionPriorityRefreshRequired(
			boolean projectionRequested,
			boolean cachedProjectionCapHit,
			String cachedPriorityKey,
			String requestedPriorityKey)
	{
		return projectionRequested
				&& cachedProjectionCapHit
				&& !Objects.equals(cachedPriorityKey, requestedPriorityKey);
	}

	private Snapshot buildSnapshot(
			Client client,
			QueryOptions options,
			long tick,
			long clientTick,
			Map<String, Object> identity,
			String refreshReason)
	{
		long started = System.nanoTime();
		Snapshot snapshot = new Snapshot();
		Map<String, Object> sourceIdentity = identity == null ? Map.of() : identity;
		Instant capturedAt = Instant.now();
		snapshot.schema = SCHEMA;
		snapshot.refreshSequence = ++refreshSequence;
		snapshot.dirtySequence = dirtySequence;
		snapshot.sourceTick = tick;
		snapshot.clientTick = clientTick;
		snapshot.wallTimeMillis = capturedAt.toEpochMilli();
		snapshot.capturedAtUtc = capturedAt.toString();
		snapshot.generatedAtUtc = snapshot.capturedAtUtc;
		snapshot.refreshReason = refreshReason == null ? "unknown" : refreshReason;
		snapshot.sessionPath = stringValue(sourceIdentity.get("sessionPath"));
		snapshot.sessionId = stringValue(sourceIdentity.get("sessionId"));
		snapshot.clientProcessId = sourceIdentity.get("clientProcessId");
		snapshot.geometryFrameId = sourceIdentity.get("geometryFrameId");
		if (client == null)
		{
			snapshot.available = false;
			snapshot.warnings.add("client unavailable");
			snapshot.refreshDurationMillis = elapsedMillis(started);
			return snapshot;
		}

		try
		{
			GameState state = client.getGameState();
			snapshot.gameState = state == null ? null : state.name();
			snapshot.plane = client.getPlane();
			snapshot.cameraYaw = client.getCameraYaw();
			snapshot.cameraPitch = client.getCameraPitch();
			snapshot.zoom3d = client.get3dZoom();
			snapshot.cameraX = client.getCameraX();
			snapshot.cameraY = client.getCameraY();
			snapshot.cameraZ = client.getCameraZ();
			snapshot.viewportWidth = client.getViewportWidth();
			snapshot.viewportHeight = client.getViewportHeight();
			snapshot.viewportXOffset = client.getViewportXOffset();
			snapshot.viewportYOffset = client.getViewportYOffset();
			snapshot.canvasWidth = client.getCanvasWidth();
			snapshot.canvasHeight = client.getCanvasHeight();
			Player localPlayer = client.getLocalPlayer();
			if (localPlayer != null && localPlayer.getWorldLocation() != null)
			{
				WorldPoint player = localPlayer.getWorldLocation();
				snapshot.playerWorldX = player.getX();
				snapshot.playerWorldY = player.getY();
				snapshot.playerPlane = player.getPlane();
				LocalPoint local = localPlayer.getLocalLocation();
				if (local != null)
				{
					snapshot.playerLocalX = local.getX();
					snapshot.playerLocalY = local.getY();
					snapshot.playerSceneX = local.getSceneX();
					snapshot.playerSceneY = local.getSceneY();
				}
			}
			if (options.includeInventory || options.fullDebug)
			{
				captureInventory(client, snapshot);
			}
			captureScene(client, snapshot, options);
			captureActors(client, snapshot, options);
			captureCollision(client, snapshot, options);
			snapshot.available = true;
		}
		catch (RuntimeException e)
		{
			snapshot.available = false;
			snapshot.warnings.add("world model refresh failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
		}
		snapshot.refreshDurationMillis = elapsedMillis(started);
		return snapshot;
	}

	private void captureScene(Client client, Snapshot snapshot, QueryOptions options)
	{
		WorldView worldView = client.getTopLevelWorldView();
		Scene scene = worldView == null ? client.getScene() : worldView.getScene();
		if (scene == null || scene.getTiles() == null)
		{
			snapshot.warnings.add("scene unavailable");
			return;
		}
		Tile[][][] tiles = scene.getTiles();
		int plane = snapshot.playerPlane == null ? snapshot.plane : snapshot.playerPlane;
		if (plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			snapshot.warnings.add("current scene plane unavailable");
			return;
		}
		snapshot.baseX = worldView == null ? scene.getBaseX() : worldView.getBaseX();
		snapshot.baseY = worldView == null ? scene.getBaseY() : worldView.getBaseY();
		Tile[][] planeTiles = tiles[plane];
		snapshot.sceneMinX = 0;
		snapshot.sceneMinY = 0;
		snapshot.sceneMaxX = planeTiles.length - 1;
		snapshot.sceneMaxY = maxColumnHeight(planeTiles) - 1;
		if (options.centerInvalid)
		{
			snapshot.queryAnchorSource = "invalid_explicit";
			snapshot.queryRadiusTiles = options.radiusTiles;
			snapshot.warnings.add("scene query centerWorldLocation is incomplete or malformed");
			return;
		}
		WorldTile anchor = options.center == null ? snapshot.playerTile() : options.center;
		snapshot.queryAnchorSource = options.center == null ? "player" : "explicit";
		snapshot.queryRadiusTiles = options.radiusTiles;
		if (anchor == null)
		{
			snapshot.warnings.add("scene query anchor unavailable");
			return;
		}
		snapshot.queryCenterWorldX = anchor.worldX;
		snapshot.queryCenterWorldY = anchor.worldY;
		snapshot.queryCenterPlane = anchor.plane;
		if (anchor.plane != plane
				|| (options.plane != null && options.plane != plane)
				|| snapshot.baseX == null
				|| snapshot.baseY == null)
		{
			snapshot.warnings.add("scene query anchor is incoherent with the loaded plane");
			return;
		}
		int centerSceneX = options.center == null && snapshot.playerSceneX != null
				? snapshot.playerSceneX
				: anchor.worldX - snapshot.baseX;
		int centerSceneY = options.center == null && snapshot.playerSceneY != null
				? snapshot.playerSceneY
				: anchor.worldY - snapshot.baseY;
		ScanWindow scanWindow = boundedScanWindow(
				centerSceneX, centerSceneY, options.radiusTiles,
				planeTiles.length, snapshot.sceneMaxY + 1);
		snapshot.requestedTileCount = scanWindow.requestedTileCount;
		snapshot.scanMinSceneX = scanWindow.minX;
		snapshot.scanMaxSceneX = scanWindow.maxX;
		snapshot.scanMinSceneY = scanWindow.minY;
		snapshot.scanMaxSceneY = scanWindow.maxY;
		if (!scanWindow.valid)
		{
			snapshot.warnings.add("scene query anchor is outside the loaded scene");
			return;
		}
		for (int sceneX = snapshot.scanMinSceneX; sceneX <= snapshot.scanMaxSceneX; sceneX++)
		{
			Tile[] column = planeTiles[sceneX];
			if (column == null)
			{
				int missingColumnSlots = snapshot.scanMaxSceneY - snapshot.scanMinSceneY + 1;
				snapshot.scannedTileSlots += missingColumnSlots;
				snapshot.missingTileCount += missingColumnSlots;
				continue;
			}
			for (int sceneY = snapshot.scanMinSceneY; sceneY <= snapshot.scanMaxSceneY; sceneY++)
			{
				snapshot.scannedTileSlots++;
				if (sceneY < 0 || sceneY >= column.length)
				{
					snapshot.missingTileCount++;
					continue;
				}
				Tile tile = column[sceneY];
				if (tile == null)
				{
					snapshot.missingTileCount++;
					continue;
				}
				snapshot.scannedTiles++;
				WorldPoint tileWorld = tile.getWorldLocation();
				if (tileWorld != null)
				{
					snapshot.loadedMinWorldX = minNullable(snapshot.loadedMinWorldX, tileWorld.getX());
					snapshot.loadedMaxWorldX = maxNullable(snapshot.loadedMaxWorldX, tileWorld.getX());
					snapshot.loadedMinWorldY = minNullable(snapshot.loadedMinWorldY, tileWorld.getY());
					snapshot.loadedMaxWorldY = maxNullable(snapshot.loadedMaxWorldY, tileWorld.getY());
				}
				addTileObjects(snapshot, tile);
				if (options.includeGroundItems || options.fullDebug)
				{
					addGroundItems(client, snapshot, tile, options.maxGroundItems);
				}
			}
		}
		snapshot.sceneCoverageComplete = !scanWindow.clipped
				&& snapshot.missingTileCount == 0
				&& !snapshot.objectCensusCapHit;
	}

	private void addTileObjects(
			Snapshot snapshot,
			Tile tile)
	{
		WallObject wall = tile.getWallObject();
		if (wall != null)
		{
			addObject(snapshot, "WALL_OBJECT", wall, wall.getOrientationA());
		}
		GroundObject ground = tile.getGroundObject();
		if (ground != null)
		{
			addObject(snapshot, "GROUND_OBJECT", ground, -1);
		}
		DecorativeObject decorative = tile.getDecorativeObject();
		if (decorative != null)
		{
			addObject(snapshot, "DECORATIVE_OBJECT", decorative, -1);
		}
		GameObject[] gameObjects = tile.getGameObjects();
		if (gameObjects == null)
		{
			return;
		}
		for (GameObject gameObject : gameObjects)
		{
			if (gameObject != null)
			{
				addObject(snapshot, "GAME_OBJECT", gameObject, gameObject.getOrientation());
			}
		}
	}

	private void addObject(
			Snapshot snapshot,
			String kind,
			TileObject object,
			int orientation)
	{
		snapshot.discoveredObjectCount++;
		if (snapshot.objects.size() >= HARD_MAX_OBJECTS)
		{
			snapshot.objectCensusCapHit = true;
			return;
		}
		Map<String, Object> record = rawObjectRecord(snapshot, kind, object, orientation);
		Object key = record.get("objectKey");
		if (key != null)
		{
			String objectKey = String.valueOf(key);
			if (snapshot.quarantinedObjectKeys.containsKey(objectKey))
			{
				snapshot.contradictoryDuplicateCount++;
				return;
			}
			String identity = rawIdentitySignature(record);
			String priorIdentity = snapshot.objectIdentityByKey.get(objectKey);
			if (priorIdentity != null)
			{
				if (priorIdentity.equals(identity))
				{
					snapshot.duplicateObjectCount++;
					return;
				}
				snapshot.contradictoryDuplicateCount++;
				snapshot.quarantinedObjectKeys.put(objectKey, true);
				snapshot.objectIdentityByKey.remove(objectKey);
				snapshot.objectRefs.remove(objectKey);
				snapshot.objects.removeIf(row -> objectKey.equals(String.valueOf(row.get("objectKey"))));
				return;
			}
			snapshot.objectIdentityByKey.put(objectKey, identity);
			snapshot.objectRefs.put(objectKey, object);
		}
		snapshot.objects.add(record);
	}

	private Map<String, Object> enrichObject(
			Client client,
			Snapshot snapshot,
			Map<String, Object> raw,
			QueryWork queryWork)
	{
		String key = String.valueOf(raw.get("objectKey"));
		Map<String, Object> cached = snapshot.enrichedObjectCache.get(key);
		if (cached != null)
		{
			queryWork.enrichmentCacheHits++;
			return cached;
		}
		Map<String, Object> record = new LinkedHashMap<>(raw);
		int id = intValue(raw.get("id"), -1);
		ObjectComposition definition = objectDefinition(client, id);
		String name = definitionName(definition);
		record.put("name", name);
		record.put("objectName", name);
		record.put("actions", definitionActions(definition));
		queryWork.enrichedObjectCount++;
		snapshot.totalEnrichedObjectCount++;
		putBounded(snapshot.enrichedObjectCache, key, record, MAX_ENRICHED_OBJECT_CACHE_ENTRIES);
		return record;
	}

	private Map<String, Object> projectObject(
			Client client,
			Snapshot snapshot,
			Map<String, Object> enriched,
			QueryWork queryWork)
	{
		Map<String, Object> record = new LinkedHashMap<>(enriched);
		String key = String.valueOf(record.get("objectKey"));
		Map<String, Object> projection = snapshot.projectionCache.get(key);
		if (projection != null)
		{
			queryWork.projectionCacheHits++;
			record.put("projection", projection);
			return record;
		}
		if (!queryWork.projectionBudget.tryConsume())
		{
			snapshot.projectionCapHit = true;
			record.put("projection", projectionUnavailable("projection_cap_hit"));
			return record;
		}
		TileObject object = snapshot.objectRefs.get(key);
		projection = object == null
				? projectionUnavailable("object_ref_missing")
				: projectionPayload(client, object, snapshot);
		putBounded(snapshot.projectionCache, key, projection, MAX_PROJECTION_CACHE_ENTRIES);
		queryWork.projectedObjectCount++;
		snapshot.totalProjectedObjectCount++;
		snapshot.projectionCaptured = true;
		snapshot.projectionBudget = queryWork.projectionBudget.max;
		record.put("projection", projection);
		return record;
	}

	private static String rawIdentitySignature(Map<String, Object> record)
	{
		return List.of(
				String.valueOf(record.get("kind")),
				String.valueOf(record.get("id")),
				String.valueOf(record.get("hash")),
				String.valueOf(record.get("orientation")),
				String.valueOf(record.get("plane")),
				String.valueOf(record.get("worldX")),
				String.valueOf(record.get("worldY")),
				String.valueOf(record.get("sceneX")),
				String.valueOf(record.get("sceneY"))).toString();
	}

	private static <V> void putBounded(
			LinkedHashMap<String, V> cache,
			String key,
			V value,
			int maxEntries)
	{
		cache.put(key, value);
		while (cache.size() > maxEntries)
		{
			String eldest = cache.keySet().iterator().next();
			cache.remove(eldest);
		}
	}

	static int squareTileCount(int radiusTiles)
	{
		long side = Math.max(0L, radiusTiles) * 2L + 1L;
		return (int) Math.min(Integer.MAX_VALUE, side * side);
	}

	static ScanWindow boundedScanWindow(
			int centerX,
			int centerY,
			int radiusTiles,
			int width,
			int height)
	{
		int radius = Math.max(0, radiusTiles);
		int requestedMinX = centerX - radius;
		int requestedMaxX = centerX + radius;
		int requestedMinY = centerY - radius;
		int requestedMaxY = centerY + radius;
		int minX = Math.max(0, requestedMinX);
		int maxX = Math.min(width - 1, requestedMaxX);
		int minY = Math.max(0, requestedMinY);
		int maxY = Math.min(height - 1, requestedMaxY);
		boolean valid = width > 0 && height > 0 && minX <= maxX && minY <= maxY;
		boolean clipped = !valid
				|| minX != requestedMinX
				|| maxX != requestedMaxX
				|| minY != requestedMinY
				|| maxY != requestedMaxY;
		return new ScanWindow(
				minX, maxX, minY, maxY,
				squareTileCount(radius), valid, clipped);
	}

	private boolean shouldProjectRecord(QueryOptions options)
	{
		return shouldProjectRecord(options.includeProjection || options.fullDebug);
	}

	static boolean shouldProjectRecord(boolean projectionRequested)
	{
		return projectionRequested;
	}

	static void sortProjectionCandidates(
			List<Map<String, Object>> candidates,
			int radiusTiles,
			ToIntFunction<Map<String, Object>> distance,
			List<Integer> priorityObjectIds)
	{
		candidates.sort(Comparator
				.comparingInt((Map<String, Object> object) -> projectionPriority(
						object, radiusTiles, distance, priorityObjectIds))
				.thenComparingInt(object -> priorityObjectRank(object, priorityObjectIds))
				.thenComparingInt(distance)
				.thenComparing(object -> String.valueOf(object.get("objectKey"))));
	}

	private static int projectionPriority(
			Map<String, Object> record,
			int radiusTiles,
			ToIntFunction<Map<String, Object>> distance,
			List<Integer> priorityObjectIds)
	{
		if (priorityObjectRank(record, priorityObjectIds) < priorityObjectIds.size())
		{
			return 0;
		}
		if (distance.applyAsInt(record) <= radiusTiles)
		{
			return 10;
		}
		return 100;
	}

	static void sortObjectCensusCandidates(
			List<Map<String, Object>> candidates,
			List<Integer> priorityObjectIds)
	{
		sortObjectCensusCandidates(candidates, List.of(), priorityObjectIds);
	}

	static void sortObjectCensusCandidates(
			List<Map<String, Object>> candidates,
			List<String> priorityObjectKeys,
			List<Integer> priorityObjectIds)
	{
		candidates.sort(Comparator
				.comparingInt((Map<String, Object> object) -> priorityObjectKeyRank(
						object, priorityObjectKeys))
				.thenComparingInt(object -> priorityObjectRank(
						object, priorityObjectIds))
				.thenComparingInt(object -> intValue(object.get("distanceToPlayer"), 9999))
				.thenComparing(object -> String.valueOf(object.get("objectKey"))));
	}

	private static int priorityObjectKeyRank(
			Map<String, Object> object,
			List<String> priorityObjectKeys)
	{
		int rank = priorityObjectKeys.indexOf(String.valueOf(object.get("objectKey")));
		return rank < 0 ? priorityObjectKeys.size() : rank;
	}

	private static int priorityObjectRank(
			Map<String, Object> object,
			List<Integer> priorityObjectIds)
	{
		int rank = priorityObjectIds.indexOf(intValue(object.get("id"), -1));
		return rank < 0 ? priorityObjectIds.size() : rank;
	}

	private int distanceToProjectionAnchor(Map<String, Object> record, QueryOptions options, Snapshot snapshot)
	{
		WorldTile anchor = projectionAnchor(options, snapshot);
		if (anchor == null)
		{
			return intValue(record.get("distanceToPlayer"), 9999);
		}
		int plane = intValue(record.get("plane"), Integer.MIN_VALUE);
		int worldX = intValue(record.get("worldX"), Integer.MIN_VALUE);
		int worldY = intValue(record.get("worldY"), Integer.MIN_VALUE);
		if (worldX == Integer.MIN_VALUE || worldY == Integer.MIN_VALUE || (plane != Integer.MIN_VALUE && plane != anchor.plane))
		{
			return 9999;
		}
		return Math.max(Math.abs(worldX - anchor.worldX), Math.abs(worldY - anchor.worldY));
	}

	private WorldTile projectionAnchor(QueryOptions options, Snapshot snapshot)
	{
		if (options.center != null)
		{
			return options.center;
		}
		return snapshot.playerTile();
	}

	private Map<String, Object> rawObjectRecord(Snapshot snapshot, String kind, TileObject object, int orientation)
	{
		Map<String, Object> record = new LinkedHashMap<>();
		int id = object.getId();
		WorldPoint world = object.getWorldLocation();
		LocalPoint local = object.getLocalLocation();
		int plane = world == null ? object.getPlane() : world.getPlane();
		int worldX = world == null ? -1 : world.getX();
		int worldY = world == null ? -1 : world.getY();
		int sceneX = world == null || snapshot.baseX == null ? -1 : worldX - snapshot.baseX;
		int sceneY = world == null || snapshot.baseY == null ? -1 : worldY - snapshot.baseY;
		String objectKey = objectKey(kind, id, objectHash(object), orientation, plane, worldX, worldY, sceneX, sceneY);
		record.put("objectKey", objectKey);
		record.put("kind", kind);
		record.put("source", "world_model_cache");
		record.put("id", id);
		record.put("hash", objectHash(object));
		record.put("orientation", orientation);
		record.put("worldX", worldX >= 0 ? worldX : null);
		record.put("worldY", worldY >= 0 ? worldY : null);
		record.put("plane", plane >= 0 ? plane : null);
		record.put("sceneX", sceneX >= 0 ? sceneX : null);
		record.put("sceneY", sceneY >= 0 ? sceneY : null);
		record.put("localX", local == null ? null : local.getX());
		record.put("localY", local == null ? null : local.getY());
		record.put("distanceToPlayer", distanceToPlayer(snapshot, worldX, worldY, plane));
		return record;
	}

	private void addGroundItems(Client client, Snapshot snapshot, Tile tile, int maxGroundItems)
	{
		List<TileItem> items = tile.getGroundItems();
		if (items == null || items.isEmpty())
		{
			return;
		}
		if (snapshot.groundItems.size() >= maxGroundItems)
		{
			snapshot.groundItemCapHit = true;
			return;
		}
		WorldPoint world = tile.getWorldLocation();
		Point scene = tile.getSceneLocation();
		for (TileItem item : items)
		{
			if (item == null)
			{
				continue;
			}
			if (snapshot.groundItems.size() >= maxGroundItems)
			{
				snapshot.groundItemCapHit = true;
				return;
			}
			Map<String, Object> record = new LinkedHashMap<>();
			record.put("id", item.getId());
			record.put("name", itemName(client, item.getId()));
			record.put("quantity", item.getQuantity());
			record.put("worldX", world == null ? null : world.getX());
			record.put("worldY", world == null ? null : world.getY());
			record.put("plane", world == null ? tile.getPlane() : world.getPlane());
			record.put("sceneX", scene == null ? null : scene.getX());
			record.put("sceneY", scene == null ? null : scene.getY());
			snapshot.groundItems.add(record);
		}
	}

	private void captureActors(Client client, Snapshot snapshot, QueryOptions options)
	{
		if (!options.includeActors && !options.fullDebug)
		{
			return;
		}
		snapshot.actorsCaptured = true;
		snapshot.actorBudget = options.maxActors;
		snapshot.actorRadiusTiles = options.radiusTiles;
		List<Map<String, Object>> npcActors = new ArrayList<>();
		List<NPC> npcs = client.getNpcs();
		for (NPC npc : npcs == null ? List.<NPC>of() : npcs)
		{
			if (npc == null)
			{
				continue;
			}
			npcActors.add(npcActorPayload(npc, snapshot));
		}
		npcActors.sort(Comparator
				.comparingInt((Map<String, Object> actor) -> intValue(actor.get("distanceToPlayer"), 9999))
				.thenComparingInt(actor -> intValue(actor.get("index"), Integer.MAX_VALUE)));
		snapshot.npcWithinRadiusCount = 0;
		for (Map<String, Object> actor : npcActors)
		{
			if (intValue(actor.get("distanceToPlayer"), 9999) <= options.radiusTiles)
			{
				snapshot.npcWithinRadiusCount++;
			}
		}
		int npcLimit = Math.min(Math.max(0, options.maxActors), npcActors.size());
		snapshot.actorCensusCapHit = snapshot.npcWithinRadiusCount > npcLimit;
		snapshot.actors.addAll(npcActors.subList(0, npcLimit));
		if (!options.fullDebug)
		{
			return;
		}
		int playerIndex = 0;
		List<Player> players = client.getPlayers();
		for (Player player : players == null ? List.<Player>of() : players)
		{
			int currentIndex = playerIndex++;
			if (player == null || player == client.getLocalPlayer() || snapshot.actors.size() >= options.maxActors)
			{
				continue;
			}
			Map<String, Object> actor = new LinkedHashMap<>();
			actor.put("type", "PLAYER");
			actor.put("index", currentIndex);
			actor.put("combatLevel", player.getCombatLevel());
			WorldPoint world = player.getWorldLocation();
			actor.put("worldX", world == null ? null : world.getX());
			actor.put("worldY", world == null ? null : world.getY());
			actor.put("plane", world == null ? null : world.getPlane());
			actor.put("animation", player.getAnimation());
			actor.put("interacting", actorRef(player.getInteracting()));
			snapshot.actors.add(actor);
		}
	}

	private Map<String, Object> npcActorPayload(NPC npc, Snapshot snapshot)
	{
		Map<String, Object> actor = new LinkedHashMap<>();
		NPCComposition composition = npc.getTransformedComposition();
		if (composition == null)
		{
			composition = npc.getComposition();
		}
		WorldPoint world = npc.getWorldLocation();
		LocalPoint local = npc.getLocalLocation();
		int worldX = world == null ? -1 : world.getX();
		int worldY = world == null ? -1 : world.getY();
		int plane = world == null ? -1 : world.getPlane();
		actor.put("type", "NPC");
		actor.put("index", npc.getIndex());
		actor.put("id", npc.getId());
		actor.put("name", npc.getName());
		actor.put("actions", actorActions(composition));
		actor.put("worldX", world == null ? null : worldX);
		actor.put("worldY", world == null ? null : worldY);
		actor.put("plane", world == null ? null : plane);
		actor.put("sceneX", local == null ? null : local.getSceneX());
		actor.put("sceneY", local == null ? null : local.getSceneY());
		actor.put("localX", local == null ? null : local.getX());
		actor.put("localY", local == null ? null : local.getY());
		actor.put("distanceToPlayer", distanceToPlayer(snapshot, worldX, worldY, plane));
		actor.put("animation", npc.getAnimation());
		actor.put("interacting", actorRef(npc.getInteracting()));
		return actor;
	}

	private List<String> actorActions(NPCComposition composition)
	{
		List<String> actions = new ArrayList<>();
		if (composition == null || composition.getActions() == null)
		{
			return actions;
		}
		for (String action : composition.getActions())
		{
			if (action != null && !action.isBlank())
			{
				actions.add(action);
			}
		}
		return actions;
	}

	private Map<String, Object> actorRef(net.runelite.api.Actor actor)
	{
		if (actor == null)
		{
			return null;
		}
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("name", actor.getName());
		WorldPoint world = actor.getWorldLocation();
		payload.put("worldX", world == null ? null : world.getX());
		payload.put("worldY", world == null ? null : world.getY());
		payload.put("plane", world == null ? null : world.getPlane());
		return payload;
	}

	private void captureInventory(Client client, Snapshot snapshot)
	{
		ItemContainer container = client.getItemContainer(InventoryID.INVENTORY);
		if (container == null || container.getItems() == null)
		{
			return;
		}
		List<Map<String, Object>> slots = new ArrayList<>();
		int occupied = 0;
		Item[] items = container.getItems();
		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];
			int id = item == null ? -1 : item.getId();
			int quantity = item == null ? 0 : item.getQuantity();
			if (id > 0 && quantity > 0)
			{
				occupied++;
				Map<String, Object> slot = new LinkedHashMap<>();
				slot.put("slot", i);
				slot.put("itemId", id);
				slot.put("name", itemName(client, id));
				slot.put("quantity", quantity);
				slots.add(slot);
			}
		}
		snapshot.inventory.put("schema", "world_model_inventory_summary.v1");
		snapshot.inventory.put("occupiedSlots", occupied);
		snapshot.inventory.put("freeSlots", Math.max(0, 28 - occupied));
		snapshot.inventory.put("slots", slots);
	}

	private void captureCollision(Client client, Snapshot snapshot, QueryOptions options)
	{
		if (!options.includeCollision && !options.fullDebug)
		{
			return;
		}
		CollisionData data = collisionDataForPlane(client, snapshot.plane);
		int[][] flags = data == null ? null : data.getFlags();
		snapshot.collisionAvailable = flags != null;
		if (flags == null)
		{
			return;
		}
		snapshot.collisionFlags = flags;
		int width = flags.length;
		int height = collisionHeight(flags);
		snapshot.collisionMapWidth = width;
		snapshot.collisionMapHeight = height;
		long hash = 1125899906842597L;
		for (int x = 0; x < width; x++)
		{
			int[] column = flags[x];
			if (column == null)
			{
				continue;
			}
			for (int y = 0; y < column.length; y++)
			{
				int value = column[y];
				if ((value & COLLISION_MOVEMENT_MASK) != 0)
				{
					snapshot.blockedMovementTileCount++;
				}
				hash = (hash * 31L) ^ value;
			}
		}
		snapshot.collisionHash = Long.toUnsignedString(hash, 16);
	}

	private Map<String, Object> projectionPayload(Client client, TileObject object, Snapshot snapshot)
	{
		Map<String, Object> projection = new LinkedHashMap<>();
		List<String> warnings = new ArrayList<>();
		Point canvasLocation = null;
		Shape clickboxShape = null;
		Shape convexHullShape = null;
		Shape tileShape = null;
		Rectangle clickbox = null;
		Rectangle convexHull = null;
		Rectangle tileBounds = null;
		try
		{
			canvasLocation = object.getCanvasLocation();
		}
		catch (RuntimeException e)
		{
			warnings.add("canvas_location_failed");
		}
		try
		{
			Shape capturedClickbox = clickboxShape(object);
			Rectangle capturedBounds = capturedClickbox == null ? null : capturedClickbox.getBounds();
			clickboxShape = capturedClickbox;
			clickbox = capturedBounds;
		}
		catch (RuntimeException e)
		{
			warnings.add("clickbox_failed");
		}
		try
		{
			Shape capturedHull = convexHullShape(object);
			Rectangle capturedBounds = capturedHull == null ? null : capturedHull.getBounds();
			convexHullShape = capturedHull;
			convexHull = capturedBounds;
		}
		catch (RuntimeException e)
		{
			warnings.add("convex_hull_failed");
		}
		try
		{
			LocalPoint local = object.getLocalLocation();
			if (local != null)
			{
				java.awt.Polygon polygon = object.getCanvasTilePoly();
				if (polygon == null)
				{
					polygon = Perspective.getCanvasTilePoly(client, local);
				}
				tileShape = polygon;
				tileBounds = polygon == null ? null : polygon.getBounds();
			}
		}
		catch (RuntimeException e)
		{
			warnings.add("canvas_tile_poly_failed");
		}
		Rectangle bestBounds = firstNonNull(clickbox, convexHull, tileBounds);
		boolean geometry = canvasLocation != null || bestBounds != null;
		boolean visible = geometry && intersectsViewport(snapshot, canvasLocation, clickbox, convexHull, tileBounds);
		Map<String, Object> aimPoint = aimPoint(
				canvasLocation,
				viewportRect(snapshot),
				clickboxShape,
				convexHullShape,
				tileShape);
		String authoritativeGeometrySource = authoritativeGeometrySource(
				clickboxShape, convexHullShape, tileShape);
		List<Map<String, Object>> authoritativePolygon = authoritativePolygon(
				clickboxShape, convexHullShape, tileShape);
		boolean authoritativeGeometryComplete = authoritativeGeometrySource == null
				|| authoritativePolygon != null;
		projection.put("schema", "world_model_projection.v1");
		projection.put("geometryAvailable", geometry);
		projection.put("onScreen", visible);
		projection.put("visible", visible);
		projection.put("actionableByCanvas", visible && aimPoint != null
				&& authoritativeGeometryComplete);
		projection.put("canvasLocation", pointPayload(canvasLocation));
		projection.put("clickboxBounds", boundsPayload(clickbox));
		projection.put("convexHullBounds", boundsPayload(convexHull));
		projection.put("canvasTileBounds", boundsPayload(tileBounds));
		projection.put("authoritativeGeometrySource", authoritativeGeometrySource);
		projection.put("authoritativePolygon", authoritativePolygon);
		projection.put("authoritativeGeometryComplete", authoritativeGeometryComplete);
		projection.put("aimPoint", aimPoint);
		projection.put("visibleAreaRatio", visibleAreaRatio(snapshot, firstNonNull(bestBounds, clickbox, convexHull, tileBounds)));
		projection.put("edgeDistancePx", edgeDistance(snapshot, aimPoint));
		projection.put("classification", !geometry ? "no_projection" : (visible ? "visible" : "offscreen"));
		if (!warnings.isEmpty())
		{
			projection.put("warnings", warnings);
		}
		return projection;
	}

	private Map<String, Object> projectionUnavailable(String reason)
	{
		Map<String, Object> projection = new LinkedHashMap<>();
		projection.put("schema", "world_model_projection.v1");
		projection.put("geometryAvailable", false);
		projection.put("onScreen", false);
		projection.put("visible", false);
		projection.put("actionableByCanvas", false);
		projection.put("classification", "unavailable");
		projection.put("reason", reason);
		return projection;
	}

	private Map<String, Object> summaryPayload(Snapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "world_model_summary.v1");
		payload.put("metadata", metadataPayload(snapshot));
		payload.put("scene", scenePayload(snapshot));
		payload.put("objects", objectSummaryPayload(snapshot));
		payload.put("actors", Map.of("count", snapshot.actors.size()));
		payload.put("ui", Map.of("inventory", snapshot.inventory));
		payload.put("interactionHot", Map.of("clientTick", snapshot.clientTick));
		payload.put("projection", projectionAuditPayload(snapshot));
		payload.put("quality", qualityPayload(snapshot));
		return payload;
	}

	private Map<String, Object> objectCensusPayload(
			Client client,
			Snapshot snapshot,
			QueryOptions options,
			QueryWork queryWork)
	{
		List<Map<String, Object>> filtered = new ArrayList<>();
		for (Map<String, Object> object : snapshot.objects)
		{
			if (!matchesQueryFilters(object, options, snapshot))
			{
				continue;
			}
			filtered.add(object);
		}
		sortObjectCensusCandidates(filtered, options.priorityObjectKeys, options.priorityObjectIds);
		int limit = Math.max(0, Math.min(options.maxObjects, filtered.size()));
		List<Map<String, Object>> items = new ArrayList<>();
		for (Map<String, Object> item : filtered.subList(0, limit))
		{
			Map<String, Object> enriched = enrichObject(client, snapshot, item, queryWork);
			if (options.projectionRequested())
			{
				enriched = projectObject(client, snapshot, enriched, queryWork);
			}
			items.add(compactObject(enriched, options));
		}
		queryWork.filteredObjectCount = Math.max(queryWork.filteredObjectCount, filtered.size());
		queryWork.returnedObjectCount = Math.max(queryWork.returnedObjectCount, items.size());
		boolean responseCapHit = filtered.size() > items.size();
		boolean censusComplete = snapshot.sceneCoverageComplete && !snapshot.objectCensusCapHit;
		List<String> contradictoryObjectKeys = new ArrayList<>(snapshot.quarantinedObjectKeys.keySet());
		if (contradictoryObjectKeys.size() > HARD_MAX_PRIORITY_OBJECT_IDS)
		{
			contradictoryObjectKeys = new ArrayList<>(
					contradictoryObjectKeys.subList(0, HARD_MAX_PRIORITY_OBJECT_IDS));
		}
		boolean priorityIdentityConflict = options.priorityObjectKeys.stream()
				.anyMatch(snapshot.quarantinedObjectKeys::containsKey);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "scene_object_census.v1");
		payload.put("sourceSchema", SCHEMA);
		payload.put("tick", snapshot.sourceTick);
		payload.put("clientTick", snapshot.clientTick);
		payload.put("filter", "scene");
		payload.put("queryPurpose", options.purpose);
		payload.put("centerWorldLocation", snapshot.queryCenterTile() == null ? null : snapshot.queryCenterTile().toMap());
		payload.put("anchorSource", snapshot.queryAnchorSource);
		payload.put("radiusTiles", snapshot.queryRadiusTiles);
		payload.put("requestedTileCount", snapshot.requestedTileCount);
		payload.put("scannedTileSlots", snapshot.scannedTileSlots);
		payload.put("scannedTiles", snapshot.scannedTiles);
		payload.put("missingTileCount", snapshot.missingTileCount);
		payload.put("discoveredObjectCount", snapshot.discoveredObjectCount);
		payload.put("duplicateObjectCount", snapshot.duplicateObjectCount);
		payload.put("contradictoryDuplicateCount", snapshot.contradictoryDuplicateCount);
		payload.put("contradictoryObjectKeys", List.copyOf(contradictoryObjectKeys));
		payload.put("indexedObjectCount", snapshot.objects.size());
		payload.put("enrichedObjectCount", queryWork.enrichedObjectCount);
		payload.put("projectedObjectCount", queryWork.projectedObjectCount);
		payload.put("count", filtered.size());
		payload.put("returned", items.size());
		payload.put("capHit", responseCapHit);
		payload.put("responseCapHit", responseCapHit);
		payload.put("objectCensusCapHit", snapshot.objectCensusCapHit);
		payload.put("sceneCoverageComplete", snapshot.sceneCoverageComplete);
		payload.put("censusComplete", censusComplete);
		payload.put("authoritativeAbsenceEligible",
				censusComplete && !responseCapHit && contradictoryObjectKeys.isEmpty());
		payload.put("priorityAbsenceEligible", censusComplete && !priorityIdentityConflict);
		payload.put("priorityObjectIds", options.priorityObjectIds);
		payload.put("priorityObjectKeys", options.priorityObjectKeys);
		List<Integer> returnedPriorityIds = new ArrayList<>();
		for (Integer priorityObjectId : options.priorityObjectIds)
		{
			if (items.stream().anyMatch(item -> intValue(item.get("id"), -1) == priorityObjectId))
			{
				returnedPriorityIds.add(priorityObjectId);
			}
		}
		payload.put("returnedPriorityObjectIds", List.copyOf(returnedPriorityIds));
		List<String> returnedPriorityKeys = new ArrayList<>();
		for (String priorityObjectKey : options.priorityObjectKeys)
		{
			if (items.stream().anyMatch(item -> priorityObjectKey.equals(String.valueOf(item.get("objectKey")))))
			{
				returnedPriorityKeys.add(priorityObjectKey);
			}
		}
		payload.put("returnedPriorityObjectKeys", List.copyOf(returnedPriorityKeys));
		payload.put("priorityObjectsComplete",
				returnedPriorityIds.size() == options.priorityObjectIds.size()
						&& returnedPriorityKeys.size() == options.priorityObjectKeys.size());
		payload.put("objects", items);
		payload.put("source", "java_world_model_cache");
		return payload;
	}

	private Map<String, Object> actorCensusPayload(Snapshot snapshot, QueryOptions options)
	{
		List<Map<String, Object>> actors = boundedNpcActorRows(
				snapshot.actors,
				options.radiusTiles,
				options.maxActors);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "world_model_actor_census.v1");
		payload.put("sourceSchema", SCHEMA);
		payload.put("tick", snapshot.sourceTick);
		payload.put("clientTick", snapshot.clientTick);
		payload.put("radiusTiles", options.radiusTiles);
		payload.put("count", snapshot.npcWithinRadiusCount);
		payload.put("returned", actors.size());
		payload.put("capHit", snapshot.npcWithinRadiusCount > actors.size());
		payload.put("actors", actors);
		payload.put("source", "java_world_model_cache");
		return payload;
	}

	static List<Map<String, Object>> boundedNpcActorRows(
			List<Map<String, Object>> source,
			int radiusTiles,
			int maxActors)
	{
		List<Map<String, Object>> candidates = new ArrayList<>();
		for (Map<String, Object> actor : source == null ? List.<Map<String, Object>>of() : source)
		{
			if (actor == null
					|| !"NPC".equals(actor.get("type"))
					|| intValue(actor.get("distanceToPlayer"), 9999) > Math.max(0, radiusTiles))
			{
				continue;
			}
			candidates.add(compactNpcActorRow(actor));
		}
		candidates.sort(Comparator
				.comparingInt((Map<String, Object> actor) -> intValue(actor.get("distanceToPlayer"), 9999))
				.thenComparingInt(actor -> intValue(actor.get("index"), Integer.MAX_VALUE)));
		int limit = Math.min(Math.max(0, maxActors), candidates.size());
		return new ArrayList<>(candidates.subList(0, limit));
	}

	private static Map<String, Object> compactNpcActorRow(Map<String, Object> source)
	{
		Map<String, Object> actor = new LinkedHashMap<>();
		for (String key : List.of(
				"type",
				"index",
				"id",
				"name",
				"actions",
				"worldX",
				"worldY",
				"plane",
				"sceneX",
				"sceneY",
				"localX",
				"localY",
				"distanceToPlayer",
				"animation"))
		{
			if (source.containsKey(key))
			{
				actor.put(key, source.get(key));
			}
		}
		return actor;
	}

	private Map<String, Object> compactObject(Map<String, Object> source, QueryOptions options)
	{
		return compactObjectRow(
				source,
				options.includeProjection
						|| options.fullDebug
						|| source.get("projection") instanceof Map);
	}

	static Map<String, Object> compactObjectRow(
			Map<String, Object> source,
			boolean includeProjection)
	{
		Map<String, Object> object = new LinkedHashMap<>();
		for (String key : List.of(
				"objectKey",
				"kind",
				"source",
				"id",
				"hash",
				"name",
				"objectName",
				"actions",
				"worldX",
				"worldY",
				"plane",
				"sceneX",
				"sceneY",
				"localX",
				"localY",
				"distanceToPlayer"))
		{
			if (source.containsKey(key))
			{
				object.put(key, source.get(key));
			}
		}
		if (includeProjection)
		{
			object.put("projection", source.get("projection"));
			object.put("projectionStatus", source.get("projection"));
		}
		return object;
	}

	private Map<String, Object> pathingFrontierPayload(Snapshot snapshot, QueryOptions options)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "pathing_frontier.v1");
		payload.put("sourceSchema", SCHEMA);
		payload.put("tick", snapshot.sourceTick);
		payload.put("collisionAvailable", snapshot.collisionAvailable);
		Map<String, Object> collision = collisionWindowPayload(snapshot, options, false);
		payload.put("collisionWindow", collision);
		payload.put("frontier", frontier(snapshot, options));
		return payload;
	}

	private Map<String, Object> collisionWindowPayload(Snapshot snapshot, QueryOptions options, boolean includeCells)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "world_model_collision_window.v1");
		payload.put("collisionAvailable", snapshot.collisionAvailable);
		payload.put("plane", snapshot.plane);
		payload.put("mapWidth", snapshot.collisionMapWidth);
		payload.put("mapHeight", snapshot.collisionMapHeight);
		payload.put("blockedMovementTileCount", snapshot.blockedMovementTileCount);
		payload.put("collisionHash", snapshot.collisionHash);
		if (!snapshot.collisionAvailable)
		{
			return payload;
		}
		WorldTile center = options.center != null ? options.center : snapshot.playerTile();
		payload.put("centerWorldLocation", center == null ? null : center.toMap());
		payload.put("radiusTiles", options.radiusTiles);
		if (!includeCells && !options.includeCollision && !options.fullDebug)
		{
			return payload;
		}
		List<Map<String, Object>> cells = collisionCells(snapshot, center, options);
		payload.put("cells", cells);
		payload.put("cellCount", cells.size());
		payload.put("cellCapHit", cells.size() >= options.maxCollisionTiles);
		return payload;
	}

	private List<Map<String, Object>> collisionCells(Snapshot snapshot, WorldTile center, QueryOptions options)
	{
		List<Map<String, Object>> cells = new ArrayList<>();
		if (center == null || snapshot.collisionFlags == null || snapshot.baseX == null || snapshot.baseY == null)
		{
			return cells;
		}
		int centerSceneX = center.worldX - snapshot.baseX;
		int centerSceneY = center.worldY - snapshot.baseY;
		for (int sceneX = Math.max(0, centerSceneX - options.radiusTiles); sceneX <= Math.min(snapshot.collisionMapWidth - 1, centerSceneX + options.radiusTiles); sceneX++)
		{
			int[] column = snapshot.collisionFlags[sceneX];
			if (column == null)
			{
				continue;
			}
			for (int sceneY = Math.max(0, centerSceneY - options.radiusTiles); sceneY <= Math.min(column.length - 1, centerSceneY + options.radiusTiles); sceneY++)
			{
				if (cells.size() >= options.maxCollisionTiles)
				{
					return cells;
				}
				int flags = column[sceneY];
				Map<String, Object> cell = new LinkedHashMap<>();
				cell.put("sceneX", sceneX);
				cell.put("sceneY", sceneY);
				cell.put("worldX", snapshot.baseX + sceneX);
				cell.put("worldY", snapshot.baseY + sceneY);
				cell.put("plane", snapshot.plane);
				cell.put("flags", flags);
				cell.put("blockedMovement", (flags & COLLISION_MOVEMENT_MASK) != 0);
				cells.add(cell);
			}
		}
		return cells;
	}

	private Map<String, Object> frontier(Snapshot snapshot, QueryOptions options)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<Map<String, Object>> candidates = new ArrayList<>();
		if (!snapshot.collisionAvailable || snapshot.collisionFlags == null || snapshot.baseX == null || snapshot.baseY == null)
		{
			payload.put("status", "WARN");
			payload.put("reason", "collision_unavailable");
			payload.put("candidates", candidates);
			return payload;
		}
		WorldTile start = snapshot.playerTile();
		if (start == null)
		{
			payload.put("status", "WARN");
			payload.put("reason", "player_location_unavailable");
			payload.put("candidates", candidates);
			return payload;
		}
		int startX = start.worldX - snapshot.baseX;
		int startY = start.worldY - snapshot.baseY;
		int width = snapshot.collisionMapWidth;
		int height = snapshot.collisionMapHeight;
		if (!inside(width, height, startX, startY) || blocked(snapshot.collisionFlags, startX, startY))
		{
			payload.put("status", "WARN");
			payload.put("reason", "player_collision_tile_blocked_or_outside");
			payload.put("candidates", candidates);
			return payload;
		}
		boolean[][] visited = new boolean[width][height];
		ArrayDeque<int[]> queue = new ArrayDeque<>();
		queue.add(new int[]{startX, startY});
		visited[startX][startY] = true;
		int reached = 0;
		int radius = options.radiusTiles;
		while (!queue.isEmpty())
		{
			int[] tile = queue.removeFirst();
			reached++;
			int dx = tile[0] - startX;
			int dy = tile[1] - startY;
			if (Math.max(Math.abs(dx), Math.abs(dy)) >= Math.max(1, radius - 1))
			{
				candidates.add(frontierCandidate(snapshot, tile[0], tile[1], options.destination));
			}
			for (int[] dir : new int[][]{{1, 0}, {-1, 0}, {0, 1}, {0, -1}})
			{
				int nx = tile[0] + dir[0];
				int ny = tile[1] + dir[1];
				if (!inside(width, height, nx, ny) || visited[nx][ny] || blocked(snapshot.collisionFlags, nx, ny))
				{
					continue;
				}
				if (Math.max(Math.abs(nx - startX), Math.abs(ny - startY)) > radius)
				{
					continue;
				}
				visited[nx][ny] = true;
				queue.addLast(new int[]{nx, ny});
			}
		}
		candidates.sort(Comparator.comparingDouble(candidate -> doubleValue(candidate.get("distanceToDestination"), 999999.0)));
		if (candidates.size() > 32)
		{
			candidates = new ArrayList<>(candidates.subList(0, 32));
		}
		payload.put("status", "PASS");
		payload.put("reachableTileCount", reached);
		payload.put("candidateCount", candidates.size());
		payload.put("candidates", candidates);
		payload.put("destination", options.destination == null ? null : options.destination.toMap());
		return payload;
	}

	private Map<String, Object> frontierCandidate(Snapshot snapshot, int sceneX, int sceneY, WorldTile destination)
	{
		Map<String, Object> candidate = new LinkedHashMap<>();
		int worldX = snapshot.baseX + sceneX;
		int worldY = snapshot.baseY + sceneY;
		candidate.put("worldX", worldX);
		candidate.put("worldY", worldY);
		candidate.put("plane", snapshot.plane);
		candidate.put("sceneX", sceneX);
		candidate.put("sceneY", sceneY);
		if (destination != null)
		{
			candidate.put("distanceToDestination", Math.max(Math.abs(worldX - destination.worldX), Math.abs(worldY - destination.worldY)));
		}
		return candidate;
	}

	private Map<String, Object> projectionAuditPayload(Snapshot snapshot)
	{
		int projected = 0;
		int visible = 0;
		int actionable = 0;
		int missing = 0;
		int offscreen = 0;
		int edge = 0;
		for (Map<String, Object> projection : snapshot.projectionCache.values())
		{
			if (projection.isEmpty() || !booleanValue(projection.get("geometryAvailable")))
			{
				missing++;
				continue;
			}
			projected++;
			if (booleanValue(projection.get("visible")) || booleanValue(projection.get("onScreen")))
			{
				visible++;
			}
			else
			{
				offscreen++;
			}
			if (booleanValue(projection.get("actionableByCanvas")))
			{
				actionable++;
			}
			double edgeDistance = doubleValue(projection.get("edgeDistancePx"), 999999.0);
			if (edgeDistance < 12.0)
			{
				edge++;
			}
		}
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "projection_audit.v1");
		payload.put("sourceSchema", SCHEMA);
		payload.put("objectCount", snapshot.objects.size());
		payload.put("projectionObjectsProjected", projected);
		payload.put("onScreenObjectCount", visible);
		payload.put("actionableObjectCount", actionable);
		payload.put("missingGeometryCount", missing);
		payload.put("offscreenObjectCount", offscreen);
		payload.put("edgeClippedObjectCount", edge);
		payload.put("projectionCapHit", snapshot.projectionCapHit);
		payload.put("projectionAuditAvailable", projected > 0 || snapshot.objects.isEmpty());
		return payload;
	}

	private Map<String, Object> minimapProjectionPayload(Snapshot snapshot, QueryOptions options)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "minimap_projection.v1");
		payload.put("status", "WARN");
		payload.put("reason", "minimap projection helper not implemented; use world/local/collision frontier fields");
		payload.put("playerWorldLocation", snapshot.playerTile() == null ? null : snapshot.playerTile().toMap());
		payload.put("destination", options.destination == null ? null : options.destination.toMap());
		return payload;
	}

	private Map<String, Object> fullDebugPayload(
			Client client,
			Snapshot snapshot,
			QueryOptions options,
			QueryWork queryWork)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", SCHEMA);
		payload.put("metadata", metadataPayload(snapshot));
		payload.put("scene", scenePayload(snapshot));
		payload.put("objectCensus", objectCensusPayload(client, snapshot, options, queryWork));
		payload.put("objects", ((Map<?, ?>) payload.get("objectCensus")).get("objects"));
		payload.put("groundItems", snapshot.groundItems.subList(0, Math.min(snapshot.groundItems.size(), options.maxGroundItems)));
		payload.put("actors", snapshot.actors);
		payload.put("inventory", snapshot.inventory);
		payload.put("collisionWindow", collisionWindowPayload(snapshot, options, true));
		payload.put("pathingFrontier", frontier(snapshot, options));
		payload.put("projectionAudit", projectionAuditPayload(snapshot));
		payload.put("quality", qualityPayload(snapshot));
		return payload;
	}

	private List<Map<String, Object>> compactObjects(List<Map<String, Object>> objects, int limit)
	{
		List<Map<String, Object>> compact = new ArrayList<>();
		int max = Math.min(Math.max(0, limit), objects.size());
		for (Map<String, Object> object : objects.subList(0, max))
		{
			compact.add(compactObject(object, QueryOptions.full()));
		}
		return compact;
	}

	private Map<String, Object> metadataPayload(Snapshot snapshot)
	{
		Map<String, Object> metadata = new LinkedHashMap<>();
		metadata.put("schema", SCHEMA);
		metadata.put("sessionPath", snapshot.sessionPath);
		metadata.put("sessionId", snapshot.sessionId);
		metadata.put("clientProcessId", snapshot.clientProcessId);
		metadata.put("geometryFrameId", snapshot.geometryFrameId);
		metadata.put("gameState", snapshot.gameState);
		metadata.put("tick", snapshot.sourceTick);
		metadata.put("sourceTick", snapshot.sourceTick);
		metadata.put("clientTick", snapshot.clientTick);
		metadata.put("wallTimeMillis", snapshot.wallTimeMillis);
		metadata.put("capturedAtUtc", snapshot.capturedAtUtc);
		metadata.put("generatedAtUtc", snapshot.generatedAtUtc);
		metadata.put("plane", snapshot.plane);
		metadata.put("baseX", snapshot.baseX);
		metadata.put("baseY", snapshot.baseY);
		metadata.put("playerWorldLocation", snapshot.playerTile() == null ? null : snapshot.playerTile().toMap());
		metadata.put("cameraYaw", snapshot.cameraYaw);
		metadata.put("cameraPitch", snapshot.cameraPitch);
		metadata.put("zoom3d", snapshot.zoom3d);
		metadata.put("viewport", viewportPayload(snapshot));
		metadata.put("sourceFreshness", qualityPayload(snapshot));
		return metadata;
	}

	private Map<String, Object> scenePayload(Snapshot snapshot)
	{
		Map<String, Object> scene = new LinkedHashMap<>();
		Map<String, Object> bounds = new LinkedHashMap<>();
		bounds.put("minSceneX", snapshot.sceneMinX);
		bounds.put("maxSceneX", snapshot.sceneMaxX);
		bounds.put("minSceneY", snapshot.sceneMinY);
		bounds.put("maxSceneY", snapshot.sceneMaxY);
		bounds.put("minWorldX", snapshot.loadedMinWorldX);
		bounds.put("maxWorldX", snapshot.loadedMaxWorldX);
		bounds.put("minWorldY", snapshot.loadedMinWorldY);
		bounds.put("maxWorldY", snapshot.loadedMaxWorldY);
		scene.put("loadedSceneBounds", bounds);
		Map<String, Object> scanWindow = new LinkedHashMap<>();
		scanWindow.put("minSceneX", snapshot.scanMinSceneX);
		scanWindow.put("maxSceneX", snapshot.scanMaxSceneX);
		scanWindow.put("minSceneY", snapshot.scanMinSceneY);
		scanWindow.put("maxSceneY", snapshot.scanMaxSceneY);
		scanWindow.put("centerWorldLocation", snapshot.queryCenterTile() == null ? null : snapshot.queryCenterTile().toMap());
		scanWindow.put("anchorSource", snapshot.queryAnchorSource);
		scanWindow.put("radiusTiles", snapshot.queryRadiusTiles);
		scene.put("scanWindow", scanWindow);
		scene.put("requestedTileCount", snapshot.requestedTileCount);
		scene.put("scannedTileSlots", snapshot.scannedTileSlots);
		scene.put("scannedTiles", snapshot.scannedTiles);
		scene.put("missingTileCount", snapshot.missingTileCount);
		scene.put("sceneCoverageComplete", snapshot.sceneCoverageComplete);
		scene.put("collisionAvailable", snapshot.collisionAvailable);
		scene.put("blockedMovementTileCount", snapshot.blockedMovementTileCount);
		scene.put("collisionHash", snapshot.collisionHash);
		scene.put("fullWorldLoaded", false);
		scene.put("loadedSceneOnly", true);
		return scene;
	}

	private Map<String, Object> objectSummaryPayload(Snapshot snapshot)
	{
		Map<String, Integer> byKind = new LinkedHashMap<>();
		for (Map<String, Object> object : snapshot.objects)
		{
			String kind = stringValue(object.get("kind"));
			byKind.put(kind, byKind.getOrDefault(kind, 0) + 1);
		}
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("total", snapshot.objects.size());
		payload.put("byKind", byKind);
		payload.put("objectCensusCapHit", snapshot.objectCensusCapHit);
		payload.put("groundItemCount", snapshot.groundItems.size());
		payload.put("groundItemCapHit", snapshot.groundItemCapHit);
		return payload;
	}

	private Map<String, Object> qualityPayload(Snapshot snapshot)
	{
		Map<String, Object> quality = new LinkedHashMap<>();
		quality.put("worldModelAvailable", snapshot.available);
		quality.put("worldModelAgeMs", Math.max(0L, System.currentTimeMillis() - snapshot.wallTimeMillis));
		quality.put("capturedAtUtc", snapshot.capturedAtUtc);
		quality.put("sourceTick", snapshot.sourceTick);
		quality.put("clientTick", snapshot.clientTick);
		quality.put("sessionId", snapshot.sessionId);
		quality.put("clientProcessId", snapshot.clientProcessId);
		quality.put("geometryFrameId", snapshot.geometryFrameId);
		quality.put("refreshSequence", snapshot.refreshSequence);
		quality.put("dirtySequence", snapshot.dirtySequence);
		quality.put("refreshReason", snapshot.refreshReason);
		quality.put("objectCensusCapHit", snapshot.objectCensusCapHit);
		quality.put("sceneCoverageComplete", snapshot.sceneCoverageComplete);
		quality.put("censusComplete", snapshot.sceneCoverageComplete && !snapshot.objectCensusCapHit);
		quality.put("collisionAvailable", snapshot.collisionAvailable);
		quality.put("projectionAuditAvailable", snapshot.projectionCaptured);
		quality.put("projectionCapHit", snapshot.projectionCapHit);
		quality.put("actorsCaptured", snapshot.actorsCaptured);
		quality.put("actorCensusCapHit", snapshot.actorCensusCapHit);
		quality.put("loadedSceneOnly", true);
		quality.put("fullWorldLoaded", false);
		return quality;
	}

	private Map<String, Object> pipelinePayload(
			long currentQuerySequence,
			RefreshResult refresh,
			Snapshot snapshot,
			QueryOptions options,
			QueryWork queryWork,
			long queryDurationMillis)
	{
		Map<String, Object> pipeline = new LinkedHashMap<>();
		pipeline.put("schema", "world_model_pipeline.v1");
		pipeline.put("querySequence", currentQuerySequence);
		pipeline.put("queryPurpose", options.purpose);
		pipeline.put("sourceTick", snapshot.sourceTick);
		pipeline.put("clientTick", snapshot.clientTick);
		pipeline.put("sessionId", snapshot.sessionId);
		pipeline.put("clientProcessId", snapshot.clientProcessId);
		pipeline.put("geometryFrameId", snapshot.geometryFrameId);
		pipeline.put("rawCacheKey", refresh.cacheKey);
		pipeline.put("cacheHit", refresh.cacheHit);
		pipeline.put("cacheMiss", !refresh.cacheHit);
		pipeline.put("cacheEntries", rawSnapshotCache.size());
		pipeline.put("cacheHits", cacheHits);
		pipeline.put("cacheMisses", cacheMisses);
		pipeline.put("refreshSequence", snapshot.refreshSequence);
		pipeline.put("dirtySequence", snapshot.dirtySequence);
		pipeline.put("refreshReason", refresh.reason);
		pipeline.put("refreshDurationMillis", refresh.cacheHit ? 0L : snapshot.refreshDurationMillis);
		pipeline.put("queryDurationMillis", queryDurationMillis);
		pipeline.put("requestedTileCount", snapshot.requestedTileCount);
		pipeline.put("scannedTileSlots", snapshot.scannedTileSlots);
		pipeline.put("scannedTiles", snapshot.scannedTiles);
		pipeline.put("missingTileCount", snapshot.missingTileCount);
		pipeline.put("discoveredObjectCount", snapshot.discoveredObjectCount);
		pipeline.put("duplicateObjectCount", snapshot.duplicateObjectCount);
		pipeline.put("contradictoryDuplicateCount", snapshot.contradictoryDuplicateCount);
		pipeline.put("indexedObjectCount", snapshot.objects.size());
		pipeline.put("filteredObjectCount", queryWork.filteredObjectCount);
		pipeline.put("enrichedObjectCount", queryWork.enrichedObjectCount);
		pipeline.put("enrichmentCacheHits", queryWork.enrichmentCacheHits);
		pipeline.put("projectedObjectCount", queryWork.projectedObjectCount);
		pipeline.put("projectionCacheHits", queryWork.projectionCacheHits);
		pipeline.put("returnedObjectCount", queryWork.returnedObjectCount);
		pipeline.put("totalEnrichedObjectCount", snapshot.totalEnrichedObjectCount);
		pipeline.put("totalProjectedObjectCount", snapshot.totalProjectedObjectCount);
		pipeline.put("sceneCoverageComplete", snapshot.sceneCoverageComplete);
		pipeline.put("objectCensusCapHit", snapshot.objectCensusCapHit);
		return pipeline;
	}

	private Map<String, Object> viewportPayload(Snapshot snapshot)
	{
		Map<String, Object> viewport = new LinkedHashMap<>();
		viewport.put("viewportWidth", snapshot.viewportWidth);
		viewport.put("viewportHeight", snapshot.viewportHeight);
		viewport.put("viewportXOffset", snapshot.viewportXOffset);
		viewport.put("viewportYOffset", snapshot.viewportYOffset);
		viewport.put("canvasWidth", snapshot.canvasWidth);
		viewport.put("canvasHeight", snapshot.canvasHeight);
		return viewport;
	}

	private boolean matchesQueryFilters(
			Map<String, Object> object,
			QueryOptions options,
			Snapshot snapshot)
	{
		if (options.plane != null && intValue(object.get("plane"), -1) != options.plane)
		{
			return false;
		}
		WorldTile center = options.center == null ? snapshot.queryCenterTile() : options.center;
		if (center != null)
		{
			int worldX = intValue(object.get("worldX"), Integer.MIN_VALUE);
			int worldY = intValue(object.get("worldY"), Integer.MIN_VALUE);
			int plane = intValue(object.get("plane"), Integer.MIN_VALUE);
			if (worldX == Integer.MIN_VALUE || worldY == Integer.MIN_VALUE || plane != center.plane)
			{
				return false;
			}
			if (Math.max(Math.abs(worldX - center.worldX), Math.abs(worldY - center.worldY)) > options.radiusTiles)
			{
				return false;
			}
		}
		return true;
	}

	private CollisionData collisionDataForPlane(Client client, int plane)
	{
		if (client == null || plane < 0)
		{
			return null;
		}
		WorldView worldView = client.getTopLevelWorldView();
		CollisionData[] collisionMaps = worldView == null ? client.getCollisionMaps() : worldView.getCollisionMaps();
		if (collisionMaps == null || plane >= collisionMaps.length)
		{
			return null;
		}
		return collisionMaps[plane];
	}

	private Shape clickboxShape(TileObject object)
	{
		return object == null ? null : object.getClickbox();
	}

	private Shape convexHullShape(TileObject object)
	{
		if (object instanceof GameObject)
		{
			return ((GameObject) object).getConvexHull();
		}
		if (object instanceof WallObject)
		{
			return ((WallObject) object).getConvexHull();
		}
		if (object instanceof DecorativeObject)
		{
			return ((DecorativeObject) object).getConvexHull();
		}
		if (object instanceof GroundObject)
		{
			return ((GroundObject) object).getConvexHull();
		}
		return null;
	}

	private ObjectComposition objectDefinition(Client client, int id)
	{
		try
		{
			return client.getObjectDefinition(id);
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private String definitionName(ObjectComposition definition)
	{
		if (definition == null || definition.getName() == null || definition.getName().isBlank() || "null".equalsIgnoreCase(definition.getName()))
		{
			return null;
		}
		return definition.getName();
	}

	private List<String> definitionActions(ObjectComposition definition)
	{
		List<String> actions = new ArrayList<>();
		if (definition == null || definition.getActions() == null)
		{
			return actions;
		}
		for (String action : definition.getActions())
		{
			if (action != null && !action.isBlank())
			{
				actions.add(action);
			}
		}
		return actions;
	}

	private String itemName(Client client, int id)
	{
		try
		{
			ItemComposition definition = client.getItemDefinition(id);
			String name = definition == null ? null : definition.getName();
			return name == null || name.isBlank() || "null".equalsIgnoreCase(name) ? null : name;
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private Long objectHash(TileObject object)
	{
		try
		{
			return object == null ? null : object.getHash();
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private String objectKey(String kind, int id, Long hash, int orientation, int plane, int worldX, int worldY, int sceneX, int sceneY)
	{
		return plane + ":" + worldX + ":" + worldY + ":" + sceneX + ":" + sceneY + ":" + kind + ":" + id + ":" + (hash == null ? "nohash" : hash) + ":" + orientation;
	}

	private int distanceToPlayer(Snapshot snapshot, int worldX, int worldY, int plane)
	{
		if (snapshot.playerWorldX == null || snapshot.playerWorldY == null || snapshot.playerPlane == null || plane != snapshot.playerPlane || worldX < 0 || worldY < 0)
		{
			return 9999;
		}
		return Math.max(Math.abs(worldX - snapshot.playerWorldX), Math.abs(worldY - snapshot.playerWorldY));
	}

	private boolean intersectsViewport(Snapshot snapshot, Point point, Rectangle... bounds)
	{
		Rectangle viewport = viewportRect(snapshot);
		if (viewport == null)
		{
			return false;
		}
		if (point != null && viewport.contains(point.getX(), point.getY()))
		{
			return true;
		}
		for (Rectangle bound : bounds)
		{
			if (bound != null && viewport.intersects(bound))
			{
				return true;
			}
		}
		return false;
	}

	private Rectangle viewportRect(Snapshot snapshot)
	{
		if (snapshot.viewportWidth == null || snapshot.viewportHeight == null)
		{
			return null;
		}
		return new Rectangle(
				Math.max(0, snapshot.viewportXOffset == null ? 0 : snapshot.viewportXOffset),
				Math.max(0, snapshot.viewportYOffset == null ? 0 : snapshot.viewportYOffset),
				Math.max(0, snapshot.viewportWidth),
				Math.max(0, snapshot.viewportHeight));
	}

	private Map<String, Object> aimPoint(
			Point canvasLocation,
			Rectangle viewport,
			Shape clickbox,
			Shape convexHull,
			Shape canvasTile)
	{
		String source = clickbox != null
				? "clickboxInterior"
				: (convexHull != null ? "convexHullInterior" : "canvasTileInterior");
		return aimPointPayload(authoritativeAimPoint(
				canvasLocation, viewport, clickbox, convexHull, canvasTile), source);
	}

	private Map<String, Object> aimPointPayload(Point point, String source)
	{
		return point == null ? null : Map.of(
				"canvasX", point.getX(),
				"canvasY", point.getY(),
				"source", source);
	}

	static Point authoritativeAimPoint(
			Point canvasLocation,
			Rectangle viewport,
			Shape clickbox,
			Shape convexHull,
			Shape canvasTile)
	{
		// Do not fall through from a present interaction shape to weaker
		// geometry. Downstream bounds use the same clickbox -> hull -> tile
		// precedence, so this also keeps the point and bounds paired.
		Shape authoritative = clickbox != null
				? clickbox
				: (convexHull != null ? convexHull : canvasTile);
		return interiorAimPoint(authoritative, viewport, canvasLocation);
	}

	static String authoritativeGeometrySource(
			Shape clickbox,
			Shape convexHull,
			Shape canvasTile)
	{
		return clickbox != null
				? "clickbox"
				: (convexHull != null ? "convex_hull" : (canvasTile != null ? "canvas_tile" : null));
	}

	static List<Map<String, Object>> authoritativePolygon(
			Shape clickbox,
			Shape convexHull,
			Shape canvasTile)
	{
		Shape authoritative = clickbox != null
				? clickbox
				: (convexHull != null ? convexHull : canvasTile);
		return polygonPayload(authoritative);
	}

	private static List<Map<String, Object>> polygonPayload(Shape shape)
	{
		if (shape == null)
		{
			return null;
		}
		try
		{
			List<Map<String, Object>> points = new ArrayList<>();
			PathIterator iterator = shape.getPathIterator(null, 1.0);
			double[] coordinates = new double[6];
			int contours = 0;
			while (!iterator.isDone())
			{
				int segment = iterator.currentSegment(coordinates);
				if (segment == PathIterator.SEG_MOVETO)
				{
					contours++;
					if (contours > 1)
					{
						return null;
					}
				}
				if (segment == PathIterator.SEG_MOVETO || segment == PathIterator.SEG_LINETO)
				{
					Map<String, Object> point = Map.of(
							"x", (int) Math.round(coordinates[0]),
							"y", (int) Math.round(coordinates[1]));
					if (points.isEmpty() || !points.get(points.size() - 1).equals(point))
					{
						if (points.size() >= 256)
						{
							return null;
						}
						points.add(point);
					}
				}
				iterator.next();
			}
			if (points.size() > 1 && points.get(0).equals(points.get(points.size() - 1)))
			{
				points.remove(points.size() - 1);
			}
			return validPolygon(points) ? List.copyOf(points) : null;
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private static boolean validPolygon(List<Map<String, Object>> points)
	{
		if (points.size() < 3)
		{
			return false;
		}
		long twiceArea = 0L;
		for (int i = 0; i < points.size(); i++)
		{
			Map<String, Object> current = points.get(i);
			Map<String, Object> next = points.get((i + 1) % points.size());
			long currentX = ((Number) current.get("x")).longValue();
			long currentY = ((Number) current.get("y")).longValue();
			long nextX = ((Number) next.get("x")).longValue();
			long nextY = ((Number) next.get("y")).longValue();
			twiceArea += currentX * nextY - nextX * currentY;
		}
		return twiceArea != 0L;
	}

	static Point interiorAimPoint(Shape shape, Rectangle viewport, Point preferred)
	{
		try
		{
			return interiorAimPointUnchecked(shape, viewport, preferred);
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private static Point interiorAimPointUnchecked(
			Shape shape, Rectangle viewport, Point preferred)
	{
		if (shape == null || viewport == null || viewport.isEmpty())
		{
			return null;
		}
		Rectangle visible = shape.getBounds().intersection(viewport);
		if (visible.isEmpty())
		{
			return null;
		}

		if (preferred != null && containsAimPoint(
				shape, viewport, preferred.getX(), preferred.getY()))
		{
			return preferred;
		}
		int centerX = visible.x + visible.width / 2;
		int centerY = visible.y + visible.height / 2;
		if (containsAimPoint(shape, viewport, centerX, centerY))
		{
			return new Point(centerX, centerY);
		}

		// Bound work per projected object while covering the visible shape from
		// its center outward. Exact hover/menu validation remains authoritative.
		final int samplesPerAxis = 17;
		Point best = null;
		long bestDistance = Long.MAX_VALUE;
		for (int row = 0; row < samplesPerAxis; row++)
		{
			int y = visible.y + Math.min(
					visible.height - 1,
					(int) (((long) (2 * row + 1) * visible.height) / (2 * samplesPerAxis)));
			for (int column = 0; column < samplesPerAxis; column++)
			{
				int x = visible.x + Math.min(
						visible.width - 1,
						(int) (((long) (2 * column + 1) * visible.width) / (2 * samplesPerAxis)));
				if (!containsAimPoint(shape, viewport, x, y))
				{
					continue;
				}
				long dx = x - centerX;
				long dy = y - centerY;
				long distance = dx * dx + dy * dy;
				if (best == null || distance < bestDistance)
				{
					best = new Point(x, y);
					bestDistance = distance;
				}
			}
		}
		return best;
	}

	private static boolean containsAimPoint(Shape shape, Rectangle viewport, int x, int y)
	{
		return viewport.contains(x, y) && shape.contains(x, y);
	}

	private Map<String, Object> pointPayload(Point point)
	{
		if (point == null)
		{
			return null;
		}
		return Map.of("x", point.getX(), "y", point.getY());
	}

	private Map<String, Object> boundsPayload(Rectangle bounds)
	{
		if (bounds == null)
		{
			return null;
		}
		return Map.of("x", bounds.x, "y", bounds.y, "w", bounds.width, "h", bounds.height);
	}

	private double visibleAreaRatio(Snapshot snapshot, Rectangle bounds)
	{
		Rectangle viewport = viewportRect(snapshot);
		if (viewport == null || bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			return 0.0;
		}
		Rectangle intersection = viewport.intersection(bounds);
		double visibleArea = Math.max(0, intersection.width) * Math.max(0, intersection.height);
		double area = Math.max(1, bounds.width * bounds.height);
		return Math.max(0.0, Math.min(1.0, visibleArea / area));
	}

	private double edgeDistance(Snapshot snapshot, Map<String, Object> aimPoint)
	{
		Rectangle viewport = viewportRect(snapshot);
		if (viewport == null || aimPoint == null)
		{
			return 0.0;
		}
		double x = doubleValue(aimPoint.get("canvasX"), -1.0);
		double y = doubleValue(aimPoint.get("canvasY"), -1.0);
		if (x < 0 || y < 0)
		{
			return 0.0;
		}
		double left = x - viewport.x;
		double right = viewport.x + viewport.width - x;
		double top = y - viewport.y;
		double bottom = viewport.y + viewport.height - y;
		return Math.max(0.0, Math.min(Math.min(left, right), Math.min(top, bottom)));
	}

	private Rectangle firstNonNull(Rectangle... values)
	{
		for (Rectangle value : values)
		{
			if (value != null)
			{
				return value;
			}
		}
		return null;
	}

	private int maxColumnHeight(Tile[][] planeTiles)
	{
		int max = 0;
		for (Tile[] column : planeTiles)
		{
			max = Math.max(max, column == null ? 0 : column.length);
		}
		return max;
	}

	private int collisionHeight(int[][] flags)
	{
		int height = 0;
		for (int[] column : flags)
		{
			height = Math.max(height, column == null ? 0 : column.length);
		}
		return height;
	}

	private boolean inside(int width, int height, int x, int y)
	{
		return x >= 0 && y >= 0 && x < width && y < height;
	}

	private boolean blocked(int[][] flags, int x, int y)
	{
		if (flags == null || x < 0 || x >= flags.length || flags[x] == null || y < 0 || y >= flags[x].length)
		{
			return true;
		}
		return (flags[x][y] & COLLISION_MOVEMENT_MASK) != 0;
	}

	private String lower(Object value)
	{
		return stringValue(value).toLowerCase(Locale.ROOT);
	}

	private String stringValue(Object value)
	{
		return value == null ? "" : String.valueOf(value);
	}

	private static int intValue(Object value, int fallback)
	{
		if (value instanceof Number)
		{
			return ((Number) value).intValue();
		}
		if (value instanceof String)
		{
			try
			{
				return Integer.parseInt(((String) value).trim());
			}
			catch (NumberFormatException e)
			{
				return fallback;
			}
		}
		return fallback;
	}

	private double doubleValue(Object value, double fallback)
	{
		if (value instanceof Number)
		{
			return ((Number) value).doubleValue();
		}
		if (value instanceof String)
		{
			try
			{
				return Double.parseDouble(((String) value).trim());
			}
			catch (NumberFormatException e)
			{
				return fallback;
			}
		}
		return fallback;
	}

	private boolean booleanValue(Object value)
	{
		return value instanceof Boolean && (Boolean) value;
	}

	@SuppressWarnings("unchecked")
	private Map<String, Object> mapValue(Object value)
	{
		return value instanceof Map ? (Map<String, Object>) value : Map.of();
	}

	@SuppressWarnings("unchecked")
	private List<String> stringList(Object value)
	{
		List<String> strings = new ArrayList<>();
		if (value instanceof List)
		{
			for (Object item : (List<Object>) value)
			{
				if (item != null)
				{
					strings.add(String.valueOf(item));
				}
			}
		}
		return strings;
	}

	private Integer minNullable(Integer current, int value)
	{
		return current == null ? value : Math.min(current, value);
	}

	private Integer maxNullable(Integer current, int value)
	{
		return current == null ? value : Math.max(current, value);
	}

	private long elapsedMillis(long startNanos)
	{
		return Math.max(0L, (System.nanoTime() - startNanos) / 1_000_000L);
	}

	static final class ScanWindow
	{
		final int minX;
		final int maxX;
		final int minY;
		final int maxY;
		final int requestedTileCount;
		final boolean valid;
		final boolean clipped;

		private ScanWindow(
				int minX,
				int maxX,
				int minY,
				int maxY,
				int requestedTileCount,
				boolean valid,
				boolean clipped)
		{
			this.minX = minX;
			this.maxX = maxX;
			this.minY = minY;
			this.maxY = maxY;
			this.requestedTileCount = requestedTileCount;
			this.valid = valid;
			this.clipped = clipped;
		}

		int boundedTileSlots()
		{
			return valid ? (maxX - minX + 1) * (maxY - minY + 1) : 0;
		}
	}

	private static class ProjectionBudget
	{
		private final int max;
		private int used;
		private boolean capHit;

		private ProjectionBudget(int max)
		{
			this.max = Math.max(0, max);
		}

		private boolean tryConsume()
		{
			if (used >= max)
			{
				capHit = true;
				return false;
			}
			used++;
			return true;
		}
	}

	private static class QueryWork
	{
		private final ProjectionBudget projectionBudget;
		private int filteredObjectCount;
		private int enrichedObjectCount;
		private int enrichmentCacheHits;
		private int projectedObjectCount;
		private int projectionCacheHits;
		private int returnedObjectCount;

		private QueryWork(int maxProjectionObjects)
		{
			projectionBudget = new ProjectionBudget(maxProjectionObjects);
		}
	}

	private static class RefreshResult
	{
		private final Snapshot snapshot;
		private final boolean cacheHit;
		private final String reason;
		private final String cacheKey;

		private RefreshResult(Snapshot snapshot, boolean cacheHit, String reason, String cacheKey)
		{
			this.snapshot = snapshot;
			this.cacheHit = cacheHit;
			this.reason = reason;
			this.cacheKey = cacheKey;
		}
	}

	private static class Snapshot
	{
		private String schema;
		private boolean available;
		private long refreshSequence;
		private long dirtySequence;
		private long sourceTick;
		private long clientTick;
		private long wallTimeMillis;
		private String capturedAtUtc;
		private String generatedAtUtc;
		private String sessionPath;
		private String sessionId;
		private Object clientProcessId;
		private Object geometryFrameId;
		private String rawCacheKey;
		private String gameState;
		private int plane = -1;
		private Integer baseX;
		private Integer baseY;
		private Integer playerWorldX;
		private Integer playerWorldY;
		private Integer playerPlane;
		private Integer playerLocalX;
		private Integer playerLocalY;
		private Integer playerSceneX;
		private Integer playerSceneY;
		private Integer cameraYaw;
		private Integer cameraPitch;
		private Integer zoom3d;
		private Integer cameraX;
		private Integer cameraY;
		private Integer cameraZ;
		private Integer viewportWidth;
		private Integer viewportHeight;
		private Integer viewportXOffset;
		private Integer viewportYOffset;
		private Integer canvasWidth;
		private Integer canvasHeight;
		private int sceneMinX;
		private int sceneMaxX;
		private int sceneMinY;
		private int sceneMaxY;
		private int scanMinSceneX;
		private int scanMaxSceneX = -1;
		private int scanMinSceneY;
		private int scanMaxSceneY = -1;
		private Integer queryCenterWorldX;
		private Integer queryCenterWorldY;
		private Integer queryCenterPlane;
		private String queryAnchorSource;
		private int queryRadiusTiles;
		private int requestedTileCount;
		private int scannedTileSlots;
		private Integer loadedMinWorldX;
		private Integer loadedMaxWorldX;
		private Integer loadedMinWorldY;
		private Integer loadedMaxWorldY;
		private int scannedTiles;
		private int missingTileCount;
		private boolean sceneCoverageComplete;
		private int discoveredObjectCount;
		private int duplicateObjectCount;
		private int contradictoryDuplicateCount;
		private int totalEnrichedObjectCount;
		private int totalProjectedObjectCount;
		private int[][] collisionFlags;
		private boolean collisionAvailable;
		private int collisionMapWidth;
		private int collisionMapHeight;
		private int blockedMovementTileCount;
		private String collisionHash;
		private boolean objectCensusCapHit;
		private boolean groundItemCapHit;
		private boolean projectionCaptured;
		private int projectionBudget;
		private String projectionAnchorKey;
		private String projectionPriorityKey;
		private boolean projectionCapHit;
		private boolean actorsCaptured;
		private int actorBudget;
		private int actorRadiusTiles;
		private int npcWithinRadiusCount;
		private boolean actorCensusCapHit;
		private String refreshReason;
		private long refreshDurationMillis;
		private final List<Map<String, Object>> objects = new ArrayList<>();
		private final Map<String, TileObject> objectRefs = new LinkedHashMap<>();
		private final Map<String, String> objectIdentityByKey = new LinkedHashMap<>();
		private final Map<String, Boolean> quarantinedObjectKeys = new LinkedHashMap<>();
		private final LinkedHashMap<String, Map<String, Object>> enrichedObjectCache = new LinkedHashMap<>(32, 0.75f, true);
		private final LinkedHashMap<String, Map<String, Object>> projectionCache = new LinkedHashMap<>(32, 0.75f, true);
		private final List<Map<String, Object>> groundItems = new ArrayList<>();
		private final List<Map<String, Object>> actors = new ArrayList<>();
		private final Map<String, Object> inventory = new LinkedHashMap<>();
		private final List<String> warnings = new ArrayList<>();

		private WorldTile playerTile()
		{
			if (playerWorldX == null || playerWorldY == null || playerPlane == null)
			{
				return null;
			}
			return new WorldTile(playerWorldX, playerWorldY, playerPlane);
		}

		private WorldTile queryCenterTile()
		{
			if (queryCenterWorldX == null || queryCenterWorldY == null || queryCenterPlane == null)
			{
				return null;
			}
			return new WorldTile(queryCenterWorldX, queryCenterWorldY, queryCenterPlane);
		}
	}

	private static class WorldTile
	{
		private final int worldX;
		private final int worldY;
		private final int plane;

		private WorldTile(int worldX, int worldY, int plane)
		{
			this.worldX = worldX;
			this.worldY = worldY;
			this.plane = plane;
		}

		private Map<String, Object> toMap()
		{
			return Map.of("worldX", worldX, "worldY", worldY, "plane", plane);
		}
	}

	private static class QueryOptions
	{
		private int maxObjects = DEFAULT_MAX_OBJECTS;
		private int radiusTiles = DEFAULT_RADIUS_TILES;
		private int maxCollisionTiles = DEFAULT_MAX_COLLISION_TILES;
		private int maxGroundItems = DEFAULT_MAX_GROUND_ITEMS;
		private int maxActors = 64;
		private int maxProjectionObjects = -1;
		private Integer plane;
		private WorldTile center;
		private boolean centerInvalid;
		private WorldTile destination;
		private boolean includeProjection;
		private boolean includeCollision;
		private boolean includeActors;
		private boolean includeInventory;
		private boolean includeGroundItems;
		private boolean fullDebug;
		private boolean forceRefresh;
		private boolean needsProjectionAudit;
		private String purpose = "unspecified";
		private List<Integer> priorityObjectIds = List.of();
		private List<String> priorityObjectKeys = List.of();

		private static QueryOptions from(Map<String, Object> request, List<String> needs)
		{
			QueryOptions options = new QueryOptions();
			Map<String, Object> worldModel = mapFrom(request == null ? null : request.get("worldModel"));
			options.maxObjects = boundedInt(first(worldModel.get("maxObjects"), requestValue(request, "maxObjects")), DEFAULT_MAX_OBJECTS, 0, HARD_MAX_OBJECTS);
			options.radiusTiles = boundedInt(first(worldModel.get("radiusTiles"), requestValue(request, "radiusTiles")), DEFAULT_RADIUS_TILES, 1, HARD_RADIUS_TILES);
			options.maxCollisionTiles = boundedInt(worldModel.get("maxCollisionTiles"), DEFAULT_MAX_COLLISION_TILES, 0, HARD_MAX_COLLISION_TILES);
			options.maxGroundItems = boundedInt(worldModel.get("maxGroundItems"), DEFAULT_MAX_GROUND_ITEMS, 0, DEFAULT_MAX_GROUND_ITEMS);
			options.maxActors = boundedInt(worldModel.get("maxActors"), 64, 0, 256);
			options.maxProjectionObjects = boundedInt(
					worldModel.get("maxProjectionObjects"), options.maxObjects, 0,
					Math.min(HARD_MAX_PROJECTION_OBJECTS, options.maxObjects));
			options.priorityObjectIds = boundedPositiveIntegerList(
					worldModel.get("priorityObjectIds"), HARD_MAX_PRIORITY_OBJECT_IDS);
			options.priorityObjectKeys = boundedStringList(
					worldModel.get("priorityObjectKeys"), HARD_MAX_PRIORITY_OBJECT_IDS, 256);
			options.purpose = boundedPurpose(worldModel.get("purpose"));
			options.plane = integerValue(first(worldModel.get("plane"), requestValue(request, "plane")));
			Object centerValue = first(
					worldModel.get("centerWorldLocation"),
					requestValue(request, "centerWorldLocation"));
			options.center = tileFrom(centerValue);
			options.centerInvalid = centerValue != null && options.center == null;
			options.destination = tileFrom(first(worldModel.get("destinationWorldLocation"), requestValue(request, "destinationWorldLocation")));
			options.includeProjection = booleanValueStatic(first(worldModel.get("includeProjection"), requestValue(request, "includeProjection")));
			options.includeCollision = booleanValueStatic(first(worldModel.get("includeCollision"), requestValue(request, "includeCollision")))
					|| (needs != null && needs.contains("collision_window"));
			options.includeActors = booleanValueStatic(first(worldModel.get("includeActors"), requestValue(request, "includeActors")))
					|| (needs != null && needs.contains("actor_census"));
			options.includeInventory = needs != null && needs.contains("world_model_summary");
			options.includeGroundItems = booleanValueStatic(worldModel.get("includeGroundItems"));
			options.forceRefresh = booleanValueStatic(worldModel.get("forceRefresh"));
			options.fullDebug = needs != null && needs.contains("full_world_model_debug");
			options.needsProjectionAudit = needs != null && needs.contains("projection_audit");
			if (options.fullDebug)
			{
				options.includeProjection = true;
				options.includeCollision = true;
			}
			else if (options.needsProjectionAudit)
			{
				options.includeProjection = true;
			}
			return options;
		}

		private static QueryOptions full()
		{
			QueryOptions options = new QueryOptions();
			options.includeProjection = true;
			options.includeCollision = true;
			options.includeActors = true;
			options.fullDebug = true;
			return options;
		}

		private int maxProjectionObjects()
		{
			return projectionRequested()
					? Math.min(Math.min(HARD_MAX_PROJECTION_OBJECTS, maxObjects), Math.max(0, maxProjectionObjects))
					: 0;
		}

		private boolean actorCensusRequested()
		{
			return includeActors || fullDebug;
		}

		private boolean projectionRequested()
		{
			return includeProjection || fullDebug;
		}

		private String projectionAnchorKey()
		{
			if (centerInvalid)
			{
				return "invalid_explicit";
			}
			if (center == null)
			{
				return "player";
			}
			return center.worldX + ":" + center.worldY + ":" + center.plane;
		}

		private String priorityObjectKey()
		{
			return priorityObjectKeys + ":" + priorityObjectIds;
		}

		private String rawCacheKey(
				long tick,
				Map<String, Object> identity,
				long dirtySequence,
				Integer livePlane,
				Integer liveBaseX,
				Integer liveBaseY)
		{
			Map<String, Object> source = identity == null ? Map.of() : identity;
			return List.of(
					"tick=" + tick,
					"session=" + String.valueOf(source.get("sessionId")),
					"process=" + String.valueOf(source.get("clientProcessId")),
					"geometry=" + String.valueOf(source.get("geometryFrameId")),
					"livePlane=" + String.valueOf(livePlane),
					"baseX=" + String.valueOf(first(liveBaseX, first(source.get("sceneBaseX"), source.get("baseX")))),
					"baseY=" + String.valueOf(first(liveBaseY, first(source.get("sceneBaseY"), source.get("baseY")))),
					"dirty=" + dirtySequence,
					"plane=" + String.valueOf(plane),
					"anchor=" + projectionAnchorKey(),
					"radius=" + radiusTiles,
					"actors=" + includeActors + ":" + maxActors,
					"collision=" + includeCollision,
					"inventory=" + includeInventory,
					"ground=" + includeGroundItems + ":" + maxGroundItems,
					"full=" + fullDebug).toString();
		}

		@SuppressWarnings("unchecked")
		private static Map<String, Object> mapFrom(Object value)
		{
			return value instanceof Map ? (Map<String, Object>) value : Map.of();
		}

		private static Object requestValue(Map<String, Object> request, String key)
		{
			return request == null ? null : request.get(key);
		}

		private static Object first(Object first, Object second)
		{
			return first != null ? first : second;
		}

		private static int boundedInt(Object value, int fallback, int min, int max)
		{
			Integer parsed = integerValue(value);
			return Math.max(min, Math.min(max, parsed == null ? fallback : parsed));
		}

		private static List<Integer> boundedPositiveIntegerList(Object value, int limit)
		{
			if (!(value instanceof List))
			{
				return List.of();
			}
			List<Integer> result = new ArrayList<>();
			for (Object item : (List<?>) value)
			{
				Integer parsed = integerValue(item);
				if (parsed == null || parsed <= 0 || result.contains(parsed))
				{
					continue;
				}
				result.add(parsed);
				if (result.size() >= limit)
				{
					break;
				}
			}
			return List.copyOf(result);
		}

		private static List<String> boundedStringList(Object value, int limit, int maxLength)
		{
			if (!(value instanceof List))
			{
				return List.of();
			}
			List<String> result = new ArrayList<>();
			for (Object item : (List<?>) value)
			{
				String parsed = item == null ? "" : String.valueOf(item).trim();
				if (parsed.isEmpty() || parsed.length() > maxLength || result.contains(parsed))
				{
					continue;
				}
				result.add(parsed);
				if (result.size() >= limit)
				{
					break;
				}
			}
			return List.copyOf(result);
		}

		private static String boundedPurpose(Object value)
		{
			if (value == null)
			{
				return "unspecified";
			}
			String purpose = String.valueOf(value).trim();
			if (purpose.isEmpty() || purpose.length() > 64 || !purpose.matches("[a-z0-9_]+"))
			{
				return "invalid";
			}
			return purpose;
		}

		private static Integer integerValue(Object value)
		{
			if (value instanceof Number)
			{
				return ((Number) value).intValue();
			}
			if (value instanceof String)
			{
				try
				{
					return Integer.parseInt(((String) value).trim());
				}
				catch (NumberFormatException e)
				{
					return null;
				}
			}
			return null;
		}

		private static WorldTile tileFrom(Object value)
		{
			Map<String, Object> tile = mapFrom(value);
			Integer worldX = integerValue(tile.get("worldX"));
			Integer worldY = integerValue(tile.get("worldY"));
			Integer plane = integerValue(tile.get("plane"));
			if (worldX == null || worldY == null || plane == null)
			{
				return null;
			}
			return new WorldTile(worldX, worldY, plane);
		}

		private static boolean booleanValueStatic(Object value)
		{
			return value instanceof Boolean && (Boolean) value;
		}

	}
}

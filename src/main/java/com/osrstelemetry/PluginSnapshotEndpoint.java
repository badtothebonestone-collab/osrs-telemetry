package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Supplier;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class PluginSnapshotEndpoint implements Closeable
{
	static final String HEALTH_SCHEMA = "plugin_snapshot_health.v1";
	static final String REQUEST_SCHEMA = "plugin_snapshot_request.v1";
	static final String RESPONSE_SCHEMA = "plugin_snapshot_response.v2";
	static final int MAX_REQUEST_BODY_BYTES = 16 * 1024;
	static final int MAX_TILE_PROJECTION_REQUESTS = 16;
	static final long CACHE_FRESHNESS_THRESHOLD_MILLIS = 5_000L;
	static final String STALE_REASON_ALL_PACKETS = "plugin_all_packets_stale";
	private static final String CONTENT_TYPE_JSON = "application/json; charset=utf-8";
	private static final List<String> SUPPORTED_NEEDS = Arrays.asList(
			"baseline",
			"inventory",
			"activity",
			"bank_ui",
			"dialogue_state",
			"interaction_hot",
			"route_object_census",
			"resource_object_census",
			"service_object_census");
	private static final List<String> WORLD_MODEL_NEEDS = Arrays.asList(
			"route_object_census",
			"resource_object_census",
			"service_object_census");
	private static final Map<String, String> NEED_TO_PACKET_TYPE = createNeedMap();

	private final PluginLiveCache liveCache;
	private final Gson gson;
	private final String host;
	private final int port;
	private final String authToken;
	private final int maxProjectionRefs;
	private final int maxResponseBytes;
	private final boolean allowNonLocalHost;
	private final Supplier<Map<String, Object>> hoverMenuSupplier;
	private final Supplier<Map<String, Object>> lastMenuOptionClickedSupplier;
	private final ClientTickHotState clientTickHotState;
	private final TileProjectionProvider tileProjectionProvider;
	private final WorldModelQueryProvider worldModelQueryProvider;
	private HttpServer server;
	private ExecutorService executor;
	private int boundPort;

	@FunctionalInterface
	interface TileProjectionProvider
	{
		Map<String, Object> projectTiles(List<Map<String, Object>> requests);
	}

	@FunctionalInterface
	interface WorldModelQueryProvider
	{
		Map<String, Object> queryWorldModel(List<String> needs, Map<String, Object> request);
	}

	public PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost)
	{
		this(
				liveCache,
				gson,
				host,
				port,
				authToken,
				maxProjectionRefs,
				maxResponseBytes,
				allowNonLocalHost,
				null,
				null,
				null,
				null,
				null);
	}

	public PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost,
			Supplier<Map<String, Object>> hoverMenuSupplier,
			Supplier<Map<String, Object>> lastMenuOptionClickedSupplier)
	{
		this(
				liveCache,
				gson,
				host,
				port,
				authToken,
				maxProjectionRefs,
				maxResponseBytes,
				allowNonLocalHost,
				hoverMenuSupplier,
				lastMenuOptionClickedSupplier,
				null,
				null,
				null);
	}

	public PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost,
			ClientTickHotState clientTickHotState)
	{
		this(
				liveCache,
				gson,
				host,
				port,
				authToken,
				maxProjectionRefs,
				maxResponseBytes,
				allowNonLocalHost,
				null,
				null,
				clientTickHotState,
				null,
				null);
	}

	public PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost,
			ClientTickHotState clientTickHotState,
			TileProjectionProvider tileProjectionProvider)
	{
		this(
				liveCache,
				gson,
				host,
				port,
				authToken,
				maxProjectionRefs,
				maxResponseBytes,
				allowNonLocalHost,
				null,
				null,
				clientTickHotState,
				tileProjectionProvider,
				null);
	}

	public PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost,
			ClientTickHotState clientTickHotState,
			TileProjectionProvider tileProjectionProvider,
			WorldModelQueryProvider worldModelQueryProvider)
	{
		this(
				liveCache,
				gson,
				host,
				port,
				authToken,
				maxProjectionRefs,
				maxResponseBytes,
				allowNonLocalHost,
				null,
				null,
				clientTickHotState,
				tileProjectionProvider,
				worldModelQueryProvider);
	}

	private PluginSnapshotEndpoint(
			PluginLiveCache liveCache,
			Gson gson,
			String host,
			int port,
			String authToken,
			int maxProjectionRefs,
			int maxResponseBytes,
			boolean allowNonLocalHost,
			Supplier<Map<String, Object>> hoverMenuSupplier,
			Supplier<Map<String, Object>> lastMenuOptionClickedSupplier,
			ClientTickHotState clientTickHotState,
			TileProjectionProvider tileProjectionProvider,
			WorldModelQueryProvider worldModelQueryProvider)
	{
		this.liveCache = liveCache;
		this.gson = gson;
		this.host = normalizeHost(host);
		this.port = Math.max(0, Math.min(65535, port));
		this.authToken = authToken == null ? "" : authToken.trim();
		this.maxProjectionRefs = Math.max(0, maxProjectionRefs);
		this.maxResponseBytes = Math.max(8 * 1024, maxResponseBytes);
		this.allowNonLocalHost = allowNonLocalHost;
		this.hoverMenuSupplier = hoverMenuSupplier;
		this.lastMenuOptionClickedSupplier = lastMenuOptionClickedSupplier;
		this.clientTickHotState = clientTickHotState;
		this.tileProjectionProvider = tileProjectionProvider;
		this.worldModelQueryProvider = worldModelQueryProvider;
	}

	public void start() throws IOException
	{
		InetAddress bindAddress = bindAddress();
		server = HttpServer.create(new InetSocketAddress(bindAddress, port), 0);
		boundPort = server.getAddress().getPort();
		executor = Executors.newSingleThreadExecutor(runnable ->
		{
			Thread thread = new Thread(runnable, "telemetry-plugin-snapshot-endpoint");
			thread.setDaemon(true);
			return thread;
		});
		server.setExecutor(executor);
		server.createContext("/health", this::handleHealth);
		server.createContext("/schema", this::handleSchema);
		server.createContext("/snapshot", this::handleSnapshot);
		server.start();
		log.info("Plugin snapshot endpoint started on {}:{}", host, boundPort);
	}

	public int getBoundPort()
	{
		return boundPort;
	}

	public String getHost()
	{
		return host;
	}

	public int getConfiguredPort()
	{
		return port;
	}

	Map<String, Object> healthPayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		PluginLiveCache.FrameSnapshot publication = liveCache == null ? null : liveCache.snapshot();
		SensorFrame frame = publication == null ? null : publication.getFrame();
		Map<String, Object> cacheHealth = liveCache == null
				? Map.of()
				: liveCache.health(publication);
		CacheFreshness cacheFreshness = cacheFreshness(cacheHealth);
		List<String> warnings = endpointWarnings();
		warnings.addAll(cacheFreshness.healthWarnings);
		long frameAgeMillis = frame == null ? -1L : frame.ageMillis();
		boolean frameFresh = frame != null
				&& frameAgeMillis >= -2_000L
				&& frameAgeMillis <= CACHE_FRESHNESS_THRESHOLD_MILLIS;
		boolean frameHealthy = frame != null
				&& frame.isCoherent()
				&& frame.isComplete()
				&& frameFresh;
		if (!frameHealthy)
		{
			warnings.add("sensor_frame_incomplete_incoherent_or_stale");
		}
		payload.put("schema", HEALTH_SCHEMA);
		payload.put("enabled", true);
		payload.put("status", liveCache == null ? "FAIL" : (frameHealthy ? "PASS" : "WARN"));
		payload.put("latestTick", frame == null ? -1L : frame.getSourceTick());
		payload.put("latestSequence", publication == null ? -1L : publication.getSequence());
		payload.put("cachedPacketTypes", publication == null ? List.of() : publication.packetTypes());
		payload.put("cacheAgeMillisByType", cacheHealth.get("liveCacheAgeMillisByType"));
		payload.put("cacheWallClockFresh", cacheFreshness.anyFreshPacket);
		payload.put("sourceCaptureFresh", frameFresh);
		payload.put("sensorFrame", sensorFrameMetadata(frame, Instant.now()));
		payload.put("allCachedPacketsStale", cacheFreshness.allPacketsStale);
		payload.put("cacheFreshnessThresholdMillis", CACHE_FRESHNESS_THRESHOLD_MILLIS);
		payload.put("maxCacheAgeMillis", cacheFreshness.maxCacheAgeMillis);
		payload.put("freshPacketTypes", cacheFreshness.freshPacketTypes);
		payload.put("stalePacketTypes", cacheFreshness.stalePacketTypes);
		payload.put("staleReasons", cacheFreshness.staleReasons);
		payload.put("cacheHealth", cacheHealth);
		payload.put("endpointHost", host);
		payload.put("endpointPort", boundPort == 0 ? port : boundPort);
		payload.put("warnings", warnings);
		return payload;
	}

	Map<String, Object> schemaPayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "plugin_snapshot_schema.v1");
		payload.put("supportedSchemas", List.of(HEALTH_SCHEMA, REQUEST_SCHEMA, RESPONSE_SCHEMA));
		payload.put("sensorFrameSchema", SensorFrame.SCHEMA);
		payload.put("supportedNeeds", SUPPORTED_NEEDS);
		payload.put("endpoints", List.of("GET /health", "GET /schema", "POST /snapshot"));
		payload.put("snapshotTiers", List.of("hot"));
		payload.put("configLimits", Map.of(
				"maxProjectionRefs", maxProjectionRefs,
				"maxResponseBytes", maxResponseBytes,
				"maxRequestBodyBytes", MAX_REQUEST_BODY_BYTES,
				"maxTileProjectionRequests", MAX_TILE_PROJECTION_REQUESTS));
		payload.put("projectionFieldModes", List.of("compact"));
		payload.put("hotSamples", List.of("clientTickHot", "hoverMenu"));
		payload.put("clientTickHotSchema", ClientTickHotState.SCHEMA);
		payload.put("requestControls", List.of("tileProjectionRequests", "maxSourceAgeMillis"));
		payload.put("tileProjectionSchema", "tile_projection_response.v1");
		payload.put("worldModelSchema", WorldModelCache.SCHEMA);
		payload.put("worldModelQueryControls", List.of(
				"worldModel.maxObjects",
				"worldModel.radiusTiles",
				"worldModel.includeProjection"));
		payload.put("readOnlyStatement", "Returns cached telemetry observations only. It has no configuration, game input, command, or game-state mutation endpoints.");
		return payload;
	}

	Map<String, Object> snapshotPayload(JsonObject request)
	{
		long startNanos = System.nanoTime();
		Instant assemblyStartedAt = Instant.now();
		String requestId = stringValue(request, "requestId", null);
		int requestedProjectionRefs = intValue(request, "maxProjectionRefs", maxProjectionRefs);
		int effectiveProjectionRefs = Math.max(0, Math.min(maxProjectionRefs, requestedProjectionRefs));
		long maxAgeTicks = longValue(request, "maxAgeTicks", 0L);
		long requestedSourceAgeMillis = longValue(
				request,
				"maxSourceAgeMillis",
				CACHE_FRESHNESS_THRESHOLD_MILLIS);
		long maxSourceAgeMillis = Math.max(
				0L,
				Math.min(CACHE_FRESHNESS_THRESHOLD_MILLIS, requestedSourceAgeMillis));
		boolean includeGeometry = booleanValue(request, "includeGeometry", false);
		boolean includeCollisionWindow = booleanValue(request, "includeCollisionWindow", true);
		boolean includeWatchValues = booleanValue(request, "includeWatchValues", false);
		String responseMode = normalizeMode(stringValue(request, "responseMode", "compact"));
		String projectionFieldMode = normalizeMode(stringValue(request, "projectionFieldMode", responseMode));
		String snapshotTier = normalizeSnapshotTier(stringValue(request, "snapshotTier", "hot"));
		SnapshotHints snapshotHints = snapshotHints(request);
		List<String> needs = requestedNeeds(request, includeCollisionWindow, includeWatchValues);
		List<String> hotNeeds = requestedHotNeeds(request);
		List<String> worldModelNeeds = requestedWorldModelNeeds(request);
		PluginLiveCache.FrameSnapshot publication = liveCache == null ? null : liveCache.snapshot();
		SensorFrame frame = publication == null ? null : publication.getFrame();
		Map<String, Object> clientTickHot = enrichClientTickHot(
				clientTickHotSnapshot(request, false));
		List<Map<String, Object>> tileProjectionRequests = tileProjectionRequests(request);
		Map<String, Object> response = new LinkedHashMap<>();
		Map<String, JsonElement> payloads = new LinkedHashMap<>();
		Map<String, Object> freshness = new LinkedHashMap<>();
		List<String> missingCapabilities = new ArrayList<>();
		List<String> warnings = new ArrayList<>();
		Map<String, Object> responseSizing = new LinkedHashMap<>();
		long latestTick = frame == null ? -1L : frame.getSourceTick();
		long sourceAgeMillis = -1L;
		boolean sourceCaptureFresh = false;
		boolean frameCoherent = frame != null && frame.isCoherent() && frame.isComplete();
		boolean stale = false;
		PriorityContext priorityContext = projectionPriorityContext(snapshotHints, frame);

		responseSizing.put("maxResponseBytes", maxResponseBytes);
		responseSizing.put("requestedProjectionRefs", requestedProjectionRefs);
		responseSizing.put("effectiveProjectionRefs", effectiveProjectionRefs);
		responseSizing.put("projectionFieldMode", projectionFieldMode);
		responseSizing.put("responseMode", responseMode);
		responseSizing.put("includeGeometry", includeGeometry);
		responseSizing.put("snapshotTier", snapshotTier);
		responseSizing.put("snapshotHints", snapshotHints.toMap());
		responseSizing.put("tileProjectionRequestCount", tileProjectionRequests.size());

		if (frame == null)
		{
			warnings.add("sensor_frame_unavailable");
		}
		else
		{
			for (String need : needs)
			{
				if (!SensorFrame.isCoreFact(need))
				{
					missingCapabilities.add(need);
					warnings.add("unsupported need: " + need);
					continue;
				}
				SensorFrame.Fact fact = frame.getFact(need);
				if (fact == null || !fact.isAvailable())
				{
					missingCapabilities.add(need);
					warnings.add("sensor_fact_unavailable:" + need);
					continue;
				}
				if (fact.getSourceTick() != frame.getSourceTick())
				{
					stale = true;
					missingCapabilities.add(need);
					warnings.add("sensor_fact_tick_mismatch:" + need);
					continue;
				}
				JsonElement payload = parsePayload(fact.getPayloadJson());
				payload = compactPayloadForResponse(
						need,
						payload,
						includeGeometry,
						effectiveProjectionRefs,
						projectionFieldMode,
						priorityContext,
						warnings,
						responseSizing);
				payloads.put(need, payload);
			}
		}
		boolean menuFresh = false;
		if (hotNeeds.contains("interaction_hot"))
		{
			payloads.put("interaction_hot", gson.toJsonTree(clientTickHot));
		}
		if (hotNeeds.contains("client_tick_tail"))
		{
			payloads.put("client_tick_tail", gson.toJsonTree(clientTickHotSnapshot(request, true)));
		}
		Map<String, Object> tileProjections = tileProjectionPayload(
				tileProjectionRequests,
				frame,
				maxSourceAgeMillis,
				warnings,
				missingCapabilities);
		if (tileProjections != null)
		{
			payloads.put("tile_projection", gson.toJsonTree(tileProjections));
		}
		Map<String, Object> worldModel = worldModelPayload(
				worldModelNeeds,
				request,
				frame,
				maxSourceAgeMillis,
				warnings,
				missingCapabilities,
				responseSizing);
		if (worldModel != null)
		{
			@SuppressWarnings("unchecked")
			Map<String, Object> worldPayloads = worldModel.get("payloads") instanceof Map
					? (Map<String, Object>) worldModel.get("payloads")
					: Map.of();
			@SuppressWarnings("unchecked")
			Map<String, Object> worldMetadata = worldModel.get("metadata") instanceof Map
					? (Map<String, Object>) worldModel.get("metadata")
					: Map.of();
			String dynamicCapturedAtUtc = String.valueOf(
					worldMetadata.getOrDefault("capturedAtUtc", ""));
			for (String need : worldModelNeeds)
			{
				Object value = worldPayloads.get(need);
				if (value != null)
				{
					JsonElement stamped = stampDynamicPayload(
							value,
							frame,
							dynamicCapturedAtUtc);
					payloads.put(need, stamped);
				}
				else
				{
					missingCapabilities.add(need);
					warnings.add("world_model_payload_unavailable:" + need);
				}
			}
		}

		Instant assembledAt = Instant.now();
		sourceAgeMillis = frame == null
				? -1L
				: Duration.between(Instant.parse(frame.getCapturedAtUtc()), assembledAt).toMillis();
		sourceCaptureFresh = frame != null
				&& sourceAgeMillis >= -2_000L
				&& sourceAgeMillis <= maxSourceAgeMillis;
		if (!frameCoherent)
		{
			stale = true;
			warnings.add("sensor_frame_incomplete_or_incoherent");
		}
		if (!sourceCaptureFresh)
		{
			stale = true;
			warnings.add(sourceAgeMillis < -2_000L
					? "sensor_frame_timestamp_future"
					: "sensor_frame_source_stale");
		}
		menuFresh = menuEvidenceMatchesFrame(
				clientTickHot,
				frame,
				assembledAt,
				maxSourceAgeMillis);
		if (hotNeeds.contains("interaction_hot") && !menuFresh)
		{
			missingCapabilities.add("interaction_hot");
			warnings.add("menu_evidence_provenance_mismatch_or_stale");
		}

		freshness.put("latestTick", latestTick);
		freshness.put("maxAgeTicks", maxAgeTicks);
		freshness.put("maxSourceAgeMillis", maxSourceAgeMillis);
		freshness.put("sourceAgeMillis", sourceAgeMillis);
		freshness.put("sourceCaptureFresh", sourceCaptureFresh);
		freshness.put("cacheWallClockFresh", sourceCaptureFresh);
		freshness.put("frameCoherent", frameCoherent);
		freshness.put("menuFresh", menuFresh);
		freshness.put("fresh", !stale && frameCoherent);

		String status = "PASS";
		if (frame == null || payloads.isEmpty())
		{
			status = "FAIL";
		}
		else if (!missingCapabilities.isEmpty() || !warnings.isEmpty() || stale)
		{
			status = "WARN";
		}

		response.put("schema", RESPONSE_SCHEMA);
		response.put("requestId", requestId);
		response.put("assembledAtUtc", assembledAt.toString());
		response.put("assemblyStartedAtUtc", assemblyStartedAt.toString());
		response.put("latestTick", latestTick);
		response.put("sensorFrame", sensorFrameMetadata(frame, assembledAt));
		response.put("publication", publication == null
				? Map.of()
				: Map.of(
						"sequence", publication.getSequence(),
						"publishedAtUtc", publication.getPublishedAtUtc()));
		response.put("snapshotTier", snapshotTier);
		response.put("status", status);
		response.put("freshness", freshness);
		response.put("payloads", payloads);
		if (tileProjections != null)
		{
			response.put("tileProjections", tileProjections);
		}
		if (worldModel != null)
		{
			response.put("worldModel", compactWorldModelEnvelope(worldModel));
			Object quality = worldModel.get("quality");
			if (quality instanceof Map)
			{
				response.put("worldModelQuality", quality);
			}
		}
		response.put("clientTickHot", clientTickHot);
		response.put("hoverMenu", clientTickHot == null ? hotSample(hoverMenuSupplier) : clientTickHot.get("postMenuSort"));
		response.put("lastMenuOptionClicked", clientTickHot == null ? hotSample(lastMenuOptionClickedSupplier) : clientTickHot.get("lastMenuOptionClicked"));
		response.put("missingCapabilities", missingCapabilities);
		response.put("warnings", warnings);
		response.put("serviceTimingMillis", elapsedMillis(startNanos));
		response.put("responseSizing", responseSizing);
		return response;
	}

	private Map<String, Object> clientTickHotSnapshot(JsonObject request, boolean includeTail)
	{
		boolean includeMenuEntries = booleanValue(request, "includeMenuEntries", true);
		int menuEntryLimit = intValue(request, "menuEntryLimit", 5);
		if (clientTickHotState != null)
		{
			return clientTickHotState.snapshot(
					includeTail ? intValue(request, "maxClientTickSamples", 0) : 0,
					includeTail ? intValue(request, "maxMenuSamples", 0) : 0,
					includeTail ? intValue(request, "maxClickedSamples", 0) : 0,
					includeMenuEntries,
					menuEntryLimit);
		}
		Map<String, Object> hoverMenu = hotSample(hoverMenuSupplier);
		Map<String, Object> lastClicked = hotSample(lastMenuOptionClickedSupplier);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", ClientTickHotState.SCHEMA);
		payload.put("clientTick", hoverMenu == null ? null : hoverMenu.get("clientTick"));
		payload.put("wallTimeMillis", hoverMenu == null ? null : hoverMenu.get("wallTimeMillis"));
		payload.put("gameTickAtSample", hoverMenu == null ? null : hoverMenu.get("gameTickAtSample"));
		payload.put("gameState", hoverMenu == null ? null : hoverMenu.get("gameState"));
		payload.put("sessionId", hoverMenu == null ? null : hoverMenu.get("sessionId"));
		payload.put("clientProcessId", hoverMenu == null ? null : hoverMenu.get("clientProcessId"));
		Map<String, Object> mouse = new LinkedHashMap<>();
		mouse.put("canvasX", hoverMenu == null ? null : hoverMenu.get("mouseCanvasX"));
		mouse.put("canvasY", hoverMenu == null ? null : hoverMenu.get("mouseCanvasY"));
		mouse.put("isInCanvas", hoverMenu == null ? null : hoverMenu.get("isInCanvas"));
		payload.put("mouse", mouse);
		payload.put("postMenuSort", hoverMenu);
		payload.put("hoverMenu", hoverMenu);
		payload.put("lastMenuOptionClicked", lastClicked);
		payload.put("latency", Map.of("samplesBuffered", hoverMenu == null && lastClicked == null ? 0 : 1));
		return payload;
	}

	private Map<String, Object> enrichClientTickHot(Map<String, Object> source)
	{
		Map<String, Object> result = source == null
				? new LinkedHashMap<>()
				: new LinkedHashMap<>(source);
		Map<?, ?> menu = result.get("postMenuSort") instanceof Map
				? (Map<?, ?>) result.get("postMenuSort")
				: Map.of();
		// Action safety is bound to the actual post-menu-sort sample, not to a
		// newer generic client-tick sample that happens to share this envelope.
		Object sourceTick = menu.get("gameTickAtSample");
		Object wallTimeMillis = menu.get("wallTimeMillis");
		Object sessionId = menu.get("sessionId");
		Object clientProcessId = menu.get("clientProcessId");
		if (sourceTick instanceof Number)
		{
			result.put("sourceTick", ((Number) sourceTick).longValue());
		}
		if (wallTimeMillis instanceof Number)
		{
			result.put(
					"capturedAtUtc",
					Instant.ofEpochMilli(((Number) wallTimeMillis).longValue()).toString());
		}
		if (sessionId instanceof String && !((String) sessionId).isBlank())
		{
			result.put("sessionId", sessionId);
		}
		if (clientProcessId instanceof Number)
		{
			result.put("clientProcessId", ((Number) clientProcessId).longValue());
		}
		return result;
	}

	private boolean menuEvidenceMatchesFrame(
			Map<String, Object> hot,
			SensorFrame frame,
			Instant assembledAt,
			long maxSourceAgeMillis)
	{
		Object tick = hot == null ? null : hot.get("sourceTick");
		Object processId = hot == null ? null : hot.get("clientProcessId");
		if (frame == null
				|| !(tick instanceof Number)
				|| ((Number) tick).longValue() != frame.getSourceTick()
				|| !java.util.Objects.equals(hot.get("sessionId"), frame.getSessionId())
				|| !(processId instanceof Number)
				|| frame.getClientProcessId() == null
				|| ((Number) processId).longValue() != frame.getClientProcessId())
		{
			return false;
		}
		Object capturedAtUtc = hot.get("capturedAtUtc");
		if (!(capturedAtUtc instanceof String))
		{
			return false;
		}
		try
		{
			long ageMillis = Duration.between(
					Instant.parse((String) capturedAtUtc),
					assembledAt).toMillis();
			return ageMillis >= -2_000L && ageMillis <= maxSourceAgeMillis;
		}
		catch (RuntimeException e)
		{
			return false;
		}
	}

	private boolean sourceIdentityMatches(
			Map<?, ?> source,
			SensorFrame frame,
			Instant validationAt,
			long maxSourceAgeMillis)
	{
		if (source == null || frame == null)
		{
			return false;
		}
		Object tick = source.get("sourceTick");
		Object processId = source.get("clientProcessId");
		Object capturedAtUtc = source.get("capturedAtUtc");
		boolean captureFresh = false;
		if (capturedAtUtc instanceof String)
		{
			try
			{
				Instant capturedAt = Instant.parse((String) capturedAtUtc);
				long ageMillis = Duration.between(
						capturedAt,
						validationAt).toMillis();
				captureFresh = ageMillis >= -2_000L
						&& ageMillis <= maxSourceAgeMillis
						&& !capturedAt.isBefore(Instant.parse(frame.getCompletedAtUtc()));
			}
			catch (RuntimeException ignored)
			{
				captureFresh = false;
			}
		}
		return captureFresh
				&& tick instanceof Number
				&& ((Number) tick).longValue() == frame.getSourceTick()
				&& java.util.Objects.equals(source.get("sessionId"), frame.getSessionId())
				&& processId instanceof Number
				&& frame.getClientProcessId() != null
				&& ((Number) processId).longValue() == frame.getClientProcessId()
				&& java.util.Objects.equals(source.get("geometryFrameId"), frame.getGeometryFrameId());
	}

	private void stampSourceIdentity(Map<String, Object> target, SensorFrame frame)
	{
		if (target == null || frame == null)
		{
			return;
		}
		target.put("sourceTick", frame.getSourceTick());
		target.put("sessionId", frame.getSessionId());
		target.put("clientProcessId", frame.getClientProcessId());
		target.put("geometryFrameId", frame.getGeometryFrameId());
	}

	private JsonElement stampDynamicPayload(
			Object value,
			SensorFrame frame,
			String capturedAtUtc)
	{
		JsonElement result = gson.toJsonTree(value);
		if (result != null && result.isJsonObject() && frame != null)
		{
			JsonObject object = result.getAsJsonObject();
			object.addProperty("sourceTick", frame.getSourceTick());
			object.addProperty("sessionId", frame.getSessionId());
			if (frame.getClientProcessId() != null)
			{
				object.addProperty("clientProcessId", frame.getClientProcessId());
			}
			object.addProperty("geometryFrameId", frame.getGeometryFrameId());
			object.addProperty("capturedAtUtc", capturedAtUtc);
		}
		return result;
	}

	private Map<String, Object> sensorFrameMetadata(SensorFrame frame, Instant assembledAt)
	{
		Instant stamp = assembledAt == null ? Instant.now() : assembledAt;
		Map<String, Object> metadata = frame == null
				? new LinkedHashMap<>()
				: new LinkedHashMap<>(frame.metadata());
		metadata.putIfAbsent("schema", SensorFrame.SCHEMA);
		metadata.putIfAbsent("frameId", "sensor-frame-unavailable");
		metadata.putIfAbsent("sourceTick", -1L);
		metadata.putIfAbsent("captureStartedMonotonicNanos", -1L);
		metadata.putIfAbsent("capturedAtUtc", stamp.toString());
		metadata.putIfAbsent("completedAtUtc", stamp.toString());
		metadata.putIfAbsent("captureDurationMillis", 0L);
		metadata.putIfAbsent("sessionId", null);
		metadata.putIfAbsent("clientProcessId", null);
		metadata.putIfAbsent("geometryFrameId", null);
		metadata.putIfAbsent("coherent", false);
		metadata.putIfAbsent("complete", false);
		metadata.putIfAbsent("availableFacts", List.of());
		metadata.putIfAbsent("unavailableFacts", SensorFrame.CORE_FACT_NAMES);
		Map<String, Object> facts = new LinkedHashMap<>();
		Object existingFacts = metadata.get("facts");
		if (existingFacts instanceof Map)
		{
			for (Map.Entry<?, ?> entry : ((Map<?, ?>) existingFacts).entrySet())
			{
				facts.put(String.valueOf(entry.getKey()), entry.getValue());
			}
		}
		long sourceTick = frame == null ? -1L : frame.getSourceTick();
		String capturedAtUtc = frame == null ? stamp.toString() : frame.getCapturedAtUtc();
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			facts.putIfAbsent(
					factName,
					Map.of(
							"sourceTick", sourceTick,
							"capturedAtUtc", capturedAtUtc,
							"available", false,
							"errors", List.of("not_captured"),
							"sizeBytes", 0L));
		}
		metadata.put("facts", facts);
		return metadata;
	}

	private List<Map<String, Object>> tileProjectionRequests(JsonObject request)
	{
		if (request == null || !request.has("tileProjectionRequests") || !request.get("tileProjectionRequests").isJsonArray())
		{
			return List.of();
		}
		JsonArray rawRequests = request.getAsJsonArray("tileProjectionRequests");
		List<Map<String, Object>> requests = new ArrayList<>();
		int cap = Math.min(rawRequests.size(), MAX_TILE_PROJECTION_REQUESTS);
		for (int i = 0; i < cap; i++)
		{
			JsonElement element = rawRequests.get(i);
			if (element == null || !element.isJsonObject())
			{
				continue;
			}
			JsonObject raw = element.getAsJsonObject();
			Map<String, Object> sanitized = new LinkedHashMap<>();
			copyStringField(raw, sanitized, "label");
			copyStringField(raw, sanitized, "source");
			copyIntField(raw, sanitized, "worldX");
			copyIntField(raw, sanitized, "worldY");
			copyIntField(raw, sanitized, "plane");
			copyIntField(raw, sanitized, "id");
			requests.add(sanitized);
		}
		return requests;
	}

	private Map<String, Object> tileProjectionPayload(
			List<Map<String, Object>> requests,
			SensorFrame frame,
			long maxSourceAgeMillis,
			List<String> warnings,
			List<String> missingCapabilities)
	{
		if (requests == null || requests.isEmpty())
		{
			return null;
		}
		if (tileProjectionProvider == null)
		{
			missingCapabilities.add("tile_projection");
			warnings.add("tile projection provider unavailable");
			Map<String, Object> unavailable = new LinkedHashMap<>();
			unavailable.put("schema", "tile_projection_response.v1");
			unavailable.put("status", "WARN");
			unavailable.put("tiles", List.of());
			unavailable.put("warnings", List.of("tile projection provider unavailable"));
			return unavailable;
		}
		try
		{
			Map<String, Object> payload = tileProjectionProvider.projectTiles(List.copyOf(requests));
			if (payload == null)
			{
				missingCapabilities.add("tile_projection");
				warnings.add("tile projection provider returned no payload");
				return Map.of(
						"schema", "tile_projection_response.v1",
						"status", "WARN",
						"tiles", List.of(),
						"warnings", List.of("tile projection provider returned no payload"));
			}
			Map<String, Object> normalized = new LinkedHashMap<>(payload);
			normalized.putIfAbsent("schema", "tile_projection_response.v1");
			normalized.putIfAbsent("status", "PASS");
			normalized.putIfAbsent("tiles", List.of());
			if (!sourceIdentityMatches(
					normalized,
					frame,
					Instant.now(),
					maxSourceAgeMillis))
			{
				missingCapabilities.add("tile_projection");
				warnings.add("tile_projection_provenance_mismatch");
				return null;
			}
			stampSourceIdentity(normalized, frame);
			return normalized;
		}
		catch (RuntimeException e)
		{
			missingCapabilities.add("tile_projection");
			warnings.add("tile projection provider failed: " + e.getClass().getSimpleName());
			return Map.of(
					"schema", "tile_projection_response.v1",
					"status", "WARN",
					"tiles", List.of(),
					"warnings", List.of("tile projection provider failed: " + e.getClass().getSimpleName()));
		}
	}

	private Map<String, Object> worldModelPayload(
			List<String> needs,
			JsonObject request,
			SensorFrame frame,
			long maxSourceAgeMillis,
			List<String> warnings,
			List<String> missingCapabilities,
			Map<String, Object> responseSizing)
	{
		if (needs == null || needs.isEmpty())
		{
			return null;
		}
		if (worldModelQueryProvider == null)
		{
			missingCapabilities.add("world_model");
			warnings.add("world model query provider unavailable");
			return null;
		}
		try
		{
			@SuppressWarnings("unchecked")
			Map<String, Object> requestMap = gson.fromJson(request == null ? new JsonObject() : request, Map.class);
			Map<String, Object> payload = worldModelQueryProvider.queryWorldModel(List.copyOf(needs), requestMap == null ? Map.of() : requestMap);
			if (payload == null)
			{
				missingCapabilities.add("world_model");
				warnings.add("world model query returned no payload");
				return null;
			}
			Object metadata = payload.get("metadata");
			if (!(metadata instanceof Map)
					|| !sourceIdentityMatches(
							(Map<?, ?>) metadata,
							frame,
							Instant.now(),
							maxSourceAgeMillis))
			{
				for (String need : needs)
				{
					if (!missingCapabilities.contains(need))
					{
						missingCapabilities.add(need);
					}
				}
				warnings.add("world_model_provenance_mismatch");
				return null;
			}
			responseSizing.put("worldModelNeeds", List.copyOf(needs));
			Object sizing = payload.get("sizing");
			if (sizing instanceof Map)
			{
				responseSizing.put("worldModelSizing", sizing);
			}
			Object payloadWarnings = payload.get("warnings");
			if (payloadWarnings instanceof List)
			{
				for (Object warning : (List<?>) payloadWarnings)
				{
					if (warning != null)
					{
						warnings.add(String.valueOf(warning));
					}
				}
			}
			return payload;
		}
		catch (RuntimeException e)
		{
			missingCapabilities.add("world_model");
			warnings.add("world model query failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
			return null;
		}
	}

	private Map<String, Object> compactWorldModelEnvelope(Map<String, Object> worldModel)
	{
		Map<String, Object> envelope = new LinkedHashMap<>();
		for (String key : List.of("schema", "snapshotSchema", "generatedAtUtc", "status", "needs", "quality", "warnings", "sizing"))
		{
			if (worldModel.containsKey(key))
			{
				envelope.put(key, worldModel.get(key));
			}
		}
		Object payloads = worldModel.get("payloads");
		if (payloads instanceof Map)
		{
			envelope.put("payloadKeys", new ArrayList<>(((Map<?, ?>) payloads).keySet()));
			envelope.put("payloadsMirroredInTopLevel", true);
		}
		return envelope;
	}

	private void copyStringField(JsonObject source, Map<String, Object> target, String key)
	{
		if (source.has(key) && source.get(key).isJsonPrimitive())
		{
			target.put(key, source.get(key).getAsString());
		}
	}

	private void copyIntField(JsonObject source, Map<String, Object> target, String key)
	{
		if (source.has(key) && source.get(key).isJsonPrimitive())
		{
			try
			{
				target.put(key, source.get(key).getAsInt());
			}
			catch (RuntimeException e)
			{
				log.debug("Ignoring non-integer tile projection request field {}", key);
			}
		}
	}

	private Map<String, Object> hotSample(Supplier<Map<String, Object>> supplier)
	{
		if (supplier == null)
		{
			return null;
		}
		try
		{
			Map<String, Object> sample = supplier.get();
			return sample == null ? null : new LinkedHashMap<>(sample);
		}
		catch (RuntimeException e)
		{
			log.debug("Failed to read plugin snapshot hot sample", e);
			return null;
		}
	}

	private void handleHealth(HttpExchange exchange) throws IOException
	{
		if (!requireMethod(exchange, "GET") || !checkAuth(exchange))
		{
			return;
		}
		writeJson(exchange, 200, healthPayload());
	}

	private void handleSchema(HttpExchange exchange) throws IOException
	{
		if (!requireMethod(exchange, "GET") || !checkAuth(exchange))
		{
			return;
		}
		writeJson(exchange, 200, schemaPayload());
	}

	private void handleSnapshot(HttpExchange exchange) throws IOException
	{
		if (!requireMethod(exchange, "POST") || !checkAuth(exchange))
		{
			return;
		}

		JsonObject request;
		try
		{
			String body = readBody(exchange);
			request = body.isBlank() ? new JsonObject() : gson.fromJson(body, JsonObject.class);
			if (request == null)
			{
				request = new JsonObject();
			}
		}
		catch (IllegalArgumentException e)
		{
			writeJson(exchange, 400, errorPayload("bad_request", e.getMessage()));
			return;
		}

		Map<String, Object> response = boundedSnapshotPayload(request);
		writeJson(exchange, httpStatusFor(response), response);
	}


	private JsonElement compactPayloadForResponse(
			String need,
			JsonElement payload,
			boolean includeGeometry,
			int effectiveProjectionRefs,
			String projectionFieldMode,
			PriorityContext priorityContext,
			List<String> warnings,
			Map<String, Object> responseSizing)
	{
		JsonElement compactPayload = payload == null ? null : payload.deepCopy();
		if ("projection".equals(need) && compactPayload != null && compactPayload.isJsonObject())
		{
			JsonObject projection = compactPayload.getAsJsonObject();
			compactProjectionPayloadForResponse(
					projection,
					includeGeometry,
					effectiveProjectionRefs,
					projectionFieldMode,
					priorityContext,
					warnings,
					responseSizing);
		}
		return compactPayload == null ? JsonNull.INSTANCE : compactPayload;
	}

	private void compactProjectionPayloadForResponse(
			JsonObject projection,
			boolean includeGeometry,
			int effectiveProjectionRefs,
			String projectionFieldMode,
			PriorityContext priorityContext,
			List<String> warnings)
	{
		compactProjectionPayloadForResponse(projection, includeGeometry, effectiveProjectionRefs, projectionFieldMode, priorityContext, warnings, new LinkedHashMap<>());
	}

	private void compactProjectionPayloadForResponse(
			JsonObject projection,
			boolean includeGeometry,
			int effectiveProjectionRefs,
			String projectionFieldMode,
			PriorityContext priorityContext,
			List<String> warnings,
			Map<String, Object> responseSizing)
	{
		JsonArray refs = projectionRefs(projection);
		if (refs != null)
		{
			responseSizing.put("projectionRefsBeforeCap", refs.size());
			prioritizeProjectionRefs(refs, priorityContext, responseSizing);
			capProjectionRefs(projection, effectiveProjectionRefs, warnings, responseSizing);
		}
		if (!includeGeometry)
		{
			removeProjectionGeometry(projection);
			responseSizing.put("omittedHeavyGeometry", true);
		}
		else
		{
			responseSizing.put("omittedHeavyGeometry", false);
		}
		if ("compact".equals(projectionFieldMode))
		{
			compactProjectionFields(projection, includeGeometry);
			responseSizing.put("projectionCompactFieldsApplied", true);
		}
		else
		{
			responseSizing.put("projectionCompactFieldsApplied", false);
		}
		JsonArray afterRefs = projectionRefs(projection);
		if (afterRefs != null)
		{
			responseSizing.put("projectionRefsAfterCap", afterRefs.size());
			Object beforeValue = responseSizing.get("projectionRefsBeforeCap");
			int before = beforeValue instanceof Number ? ((Number) beforeValue).intValue() : afterRefs.size();
			responseSizing.put("trimmedProjectionRefs", Math.max(0, before - afterRefs.size()));
		}
	}

	private JsonArray projectionRefs(JsonObject projection)
	{
		if (projection == null)
		{
			return null;
		}
		JsonElement refsElement = projection.get("visibleObjectRefs");
		return refsElement != null && refsElement.isJsonArray() ? refsElement.getAsJsonArray() : null;
	}

	private void prioritizeProjectionRefs(
			JsonArray refs,
			PriorityContext priorityContext,
			Map<String, Object> responseSizing)
	{
		List<JsonElement> ordered = new ArrayList<>();
		for (JsonElement element : refs)
		{
			ordered.add(element);
		}
		ordered.sort(Comparator.comparingDouble(element -> projectionRefPriority(element, priorityContext)));
		JsonArray sorted = new JsonArray();
		for (JsonElement element : ordered)
		{
			sorted.add(element);
		}
		while (refs.size() > 0)
		{
			refs.remove(0);
		}
		for (JsonElement element : sorted)
		{
			refs.add(element);
		}
		responseSizing.put("projectionPriorityApplied", true);
		responseSizing.put("projectionPriorityReason", priorityContext != null && priorityContext.hints.hasHints()
				? "request hints, onScreen scene objects with geometry, stable ids/locations, and player-near refs first"
				: "onScreen scene objects with geometry and stable ids/locations first");
		if (priorityContext != null)
		{
			responseSizing.put("projectionHintsApplied", priorityContext.hints.toMap());
		}
	}

	private double projectionRefPriority(JsonElement element, PriorityContext priorityContext)
	{
		if (element == null || !element.isJsonObject())
		{
			return 9_000_000_000.0;
		}

		JsonObject ref = element.getAsJsonObject();
		double priority = 0.0;
		if (priorityContext != null)
		{
			priority += hintPriority(ref, priorityContext.hints);
		}
		if (!booleanValue(ref, "onScreen", false))
		{
			priority += 1_000_000_000.0;
		}
		if (!"sceneObject".equalsIgnoreCase(stringValue(ref, "targetType", "")))
		{
			priority += 500_000_000.0;
		}
		if (!booleanValue(ref, "geometryAvailable", false))
		{
			priority += 250_000_000.0;
		}
		if (!hasStableIdentityAndLocation(ref))
		{
			priority += 125_000_000.0;
		}
		priority += playerDistancePriority(ref, priorityContext) * 1_000.0;
		return priority;
	}

	private double hintPriority(JsonObject ref, SnapshotHints hints)
	{
		if (hints == null || !hints.hasHints())
		{
			return 0.0;
		}
		double priority = 0.0;
		if (!hints.targetTypeHint.isBlank() && !hints.targetTypeHint.equalsIgnoreCase(stringValue(ref, "targetType", "")))
		{
			priority += 20_000_000.0;
		}
		if (hints.requireOnScreen && !booleanValue(ref, "onScreen", false))
		{
			priority += 10_000_000.0;
		}
		if (hints.requireGeometryAvailable && !booleanValue(ref, "geometryAvailable", false))
		{
			priority += 5_000_000.0;
		}
		if (!hints.desiredClasses.isEmpty() && !matchesAnyDesiredClass(ref, hints.desiredClasses))
		{
			priority += 2_500_000.0;
		}
		else if (!hints.classHint.isBlank() && !matchesClassHint(ref, hints.classHint))
		{
			priority += 2_500_000.0;
		}
		return priority;
	}

	private boolean matchesAnyDesiredClass(JsonObject ref, List<String> classes)
	{
		for (String className : classes)
		{
			if (matchesClassHint(ref, className))
			{
				return true;
			}
		}
		return false;
	}

	private boolean matchesClassHint(JsonObject ref, String classHint)
	{
		String hint = classHint == null ? "" : classHint.trim().toLowerCase(Locale.ROOT);
		if (hint.isBlank())
		{
			return false;
		}
		for (String key : List.of("name", "objectName", "targetName", "objectKey", "kind", "layer", "targetType"))
		{
			String value = stringValue(ref, key, "");
			if (!value.isBlank() && value.toLowerCase(Locale.ROOT).contains(hint))
			{
				return true;
			}
		}
		return false;
	}

	private boolean hasStableIdentityAndLocation(JsonObject ref)
	{
		return (ref.has("objectKey") || ref.has("id") || ref.has("rawId") || ref.has("hash"))
				&& ref.has("worldX")
				&& ref.has("worldY")
				&& ref.has("plane");
	}

	private double playerDistancePriority(JsonObject ref, PriorityContext priorityContext)
	{
		if (priorityContext == null
				|| priorityContext.sceneX == null
				|| priorityContext.sceneY == null
				|| priorityContext.plane == null
				|| !ref.has("sceneX")
				|| !ref.has("sceneY")
				|| !ref.has("plane"))
		{
			return 1_000_000.0;
		}
		try
		{
			double refPlane = ref.get("plane").getAsDouble();
			if (Math.round(refPlane) != priorityContext.plane)
			{
				return 1_000_000.0;
			}
			double dx = ref.get("sceneX").getAsDouble() - priorityContext.sceneX;
			double dy = ref.get("sceneY").getAsDouble() - priorityContext.sceneY;
			return dx * dx + dy * dy;
		}
		catch (RuntimeException e)
		{
			return 1_000_000.0;
		}
	}

	private void capProjectionRefs(
			JsonObject projection,
			int effectiveProjectionRefs,
			List<String> warnings,
			Map<String, Object> responseSizing)
	{
		JsonArray refs = projectionRefs(projection);
		if (refs == null)
		{
			return;
		}

		responseSizing.put("projectionCapApplied", false);
		responseSizing.put("projectionRefsCapped", false);
		if (refs.size() <= effectiveProjectionRefs)
		{
			responseSizing.put("projectionRefsAfterCap", refs.size());
			return;
		}

		JsonArray capped = new JsonArray();
		for (int i = 0; i < effectiveProjectionRefs; i++)
		{
			capped.add(refs.get(i));
		}
		projection.add("visibleObjectRefs", capped);
		warnings.add("projection refs capped");
		responseSizing.put("projectionCapApplied", true);
		responseSizing.put("projectionRefsCapped", true);
		responseSizing.put("projectionRefsAfterCap", capped.size());
	}

	private void removeProjectionGeometry(JsonObject projection)
	{
		JsonArray refs = projectionRefs(projection);
		if (refs == null)
		{
			return;
		}

		for (JsonElement element : refs)
		{
			if (!element.isJsonObject())
			{
				continue;
			}
			JsonObject ref = element.getAsJsonObject();
			ref.remove("clickableHull");
			ref.remove("clickboxPolygon");
			ref.remove("canvasTilePolygon");
			ref.remove("convexHull");
			ref.remove("convexHullPolygon");
			ref.remove("geometryEmission");
		}
	}

	private void compactProjectionFields(JsonObject projection, boolean includeGeometry)
	{
		JsonObject compactProjection = new JsonObject();
		copyIfPresent(projection, compactProjection, "sceneProjectionSummary");
		copyIfPresent(projection, compactProjection, "projectionStateHash");
		copyIfPresent(projection, compactProjection, "refreshMode");
		copyIfPresent(projection, compactProjection, "serviceSceneObjects");
		copyIfPresent(projection, compactProjection, "serviceSceneObjectCount");
		copyIfPresent(projection, compactProjection, "serviceSceneObjectCap");
		copyIfPresent(projection, compactProjection, "serviceSceneObjectRadius");
		copyIfPresent(projection, compactProjection, "serviceSceneObjectCapHit");

		JsonArray compactRefs = new JsonArray();
		JsonArray refs = projectionRefs(projection);
		if (refs != null)
		{
			for (JsonElement element : refs)
			{
				if (element == null || !element.isJsonObject())
				{
					continue;
				}
				compactRefs.add(compactProjectionRef(element.getAsJsonObject(), includeGeometry));
			}
		}
		compactProjection.add("visibleObjectRefs", compactRefs);

		for (String key : new ArrayList<>(projection.keySet()))
		{
			projection.remove(key);
		}
		for (Map.Entry<String, JsonElement> entry : compactProjection.entrySet())
		{
			projection.add(entry.getKey(), entry.getValue());
		}
	}

	private JsonObject compactProjectionRef(JsonObject ref, boolean includeGeometry)
	{
		JsonObject compact = new JsonObject();
		for (String key : compactProjectionRefKeys(includeGeometry))
		{
			copyIfPresent(ref, compact, key);
		}
		return compact;
	}

	private List<String> compactProjectionRefKeys(boolean includeGeometry)
	{
		List<String> keys = new ArrayList<>(Arrays.asList(
				"objectKey",
				"targetType",
				"id",
				"rawId",
				"hash",
				"name",
				"objectName",
				"nameSource",
				"objectNameSource",
				"kind",
				"layer",
				"worldX",
				"worldY",
				"plane",
				"sceneX",
				"sceneY",
				"onScreen",
				"geometryAvailable",
				"aimPoint",
				"bounds",
				"geometrySource",
				"actions",
				"menuActions",
				"actionNames",
				"present"));
		if (includeGeometry)
		{
			keys.add("canvasTilePolygon");
			keys.add("convexHull");
			keys.add("convexHullPolygon");
		}
		return keys;
	}

	private void copyIfPresent(JsonObject source, JsonObject target, String key)
	{
		if (source != null && target != null && source.has(key))
		{
			target.add(key, source.get(key));
		}
	}

	Map<String, Object> boundedSnapshotPayload(JsonObject request)
	{
		Map<String, Object> response = snapshotPayload(request);
		int estimatedBytes = estimatedResponseBytes(response);
		attachResponseSize(response, estimatedBytes);
		estimatedBytes = estimatedResponseBytes(response);
		attachResponseSize(response, estimatedBytes);
		if (estimatedBytes <= maxResponseBytes)
		{
			return response;
		}
		return responseTooLargePayload(response, estimatedBytes);
	}

	private int httpStatusFor(Map<String, Object> response)
	{
		Object errorCode = response == null ? null : response.get("errorCode");
		if ("response_too_large".equals(errorCode))
		{
			return 413;
		}
		if ("bad_request".equals(errorCode))
		{
			return 400;
		}
		if ("unauthorized".equals(errorCode))
		{
			return 401;
		}
		return 200;
	}

	private int estimatedResponseBytes(Map<String, Object> response)
	{
		return gson.toJson(response).getBytes(StandardCharsets.UTF_8).length;
	}

	@SuppressWarnings("unchecked")
	private void attachResponseSize(Map<String, Object> response, int estimatedBytes)
	{
		if (response == null)
		{
			return;
		}
		Object sizingValue = response.get("responseSizing");
		Map<String, Object> sizing;
		if (sizingValue instanceof Map)
		{
			sizing = (Map<String, Object>) sizingValue;
		}
		else
		{
			sizing = new LinkedHashMap<>();
			response.put("responseSizing", sizing);
		}
		sizing.put("estimatedResponseBytes", estimatedBytes);
		sizing.put("maxResponseBytes", maxResponseBytes);
		response.put("estimatedResponseBytes", estimatedBytes);
		response.put("maxResponseBytes", maxResponseBytes);
	}

	private Map<String, Object> responseTooLargePayload(Map<String, Object> response, int estimatedBytes)
	{
		Map<String, Object> tooLarge = errorPayload("response_too_large", "snapshot response exceeded pluginSnapshotMaxResponseBytes");
		tooLarge.put("assembledAtUtc", Instant.now().toString());
		tooLarge.put("status", "FAIL");
		tooLarge.put("warnings", List.of("responseTooLarge"));
		tooLarge.put("maxResponseBytes", maxResponseBytes);
		tooLarge.put("estimatedResponseBytes", estimatedBytes);
		Object sizing = response == null ? null : response.get("responseSizing");
		if (sizing instanceof Map)
		{
			@SuppressWarnings("unchecked")
			Map<String, Object> copiedSizing = new LinkedHashMap<>((Map<String, Object>) sizing);
			copiedSizing.put("estimatedResponseBytes", estimatedBytes);
			copiedSizing.put("maxResponseBytes", maxResponseBytes);
			tooLarge.put("responseSizing", copiedSizing);
		}
		tooLarge.put("cacheHealth", cacheHealth());
		return tooLarge;
	}

	private List<String> requestedNeeds(JsonObject request, boolean includeCollisionWindow, boolean includeWatchValues)
	{
		JsonElement needsElement = request == null ? null : request.get("needs");
		List<String> needs = new ArrayList<>();
		boolean explicitNeeds = needsElement != null && needsElement.isJsonArray();

		if (explicitNeeds)
		{
			for (JsonElement element : needsElement.getAsJsonArray())
			{
				if (element.isJsonPrimitive())
				{
					String need = normalizeNeed(element.getAsString());
					if (NEED_TO_PACKET_TYPE.containsKey(need) && !needs.contains(need))
					{
						needs.add(need);
					}
				}
			}
		}

		if (needs.isEmpty() && !explicitNeeds)
		{
			needs.add("baseline");
			needs.add("inventory");
			needs.add("activity");
			needs.add("bank_ui");
			needs.add("dialogue_state");
		}

		if (!includeCollisionWindow)
		{
			needs.remove("collision_window");
		}
		if (!includeWatchValues)
		{
			needs.remove("watch_values");
		}

		return needs;
	}

	private List<String> requestedHotNeeds(JsonObject request)
	{
		JsonElement needsElement = request == null ? null : request.get("needs");
		List<String> needs = new ArrayList<>();
		if (needsElement != null && needsElement.isJsonArray())
		{
			for (JsonElement element : needsElement.getAsJsonArray())
			{
				if (element.isJsonPrimitive())
				{
					String need = normalizeNeed(element.getAsString());
					if ("interaction_hot".equals(need) && !needs.contains(need))
					{
						needs.add(need);
					}
				}
			}
		}
		return needs;
	}

	private List<String> requestedWorldModelNeeds(JsonObject request)
	{
		JsonElement needsElement = request == null ? null : request.get("needs");
		List<String> needs = new ArrayList<>();
		if (needsElement != null && needsElement.isJsonArray())
		{
			for (JsonElement element : needsElement.getAsJsonArray())
			{
				if (!element.isJsonPrimitive())
				{
					continue;
				}
				String need = normalizeNeed(element.getAsString());
				if (WORLD_MODEL_NEEDS.contains(need) && !needs.contains(need))
				{
					needs.add(need);
				}
			}
		}
		return needs;
	}

	private String normalizeNeed(String need)
	{
		if (need == null)
		{
			return "";
		}
		return need.trim()
				.replace('-', '_')
				.replace("sceneDelta", "scene_delta")
				.replace("inventoryDelta", "inventory_delta")
				.replace("collisionWindow", "collision_window")
				.replace("bankUi", "bank_ui")
				.replace("dialogueState", "dialogue_state")
				.replace("worldModelSummary", "world_model_summary")
				.replace("sceneObjectCensus", "scene_object_census")
				.replace("routeObjectCensus", "route_object_census")
				.replace("resourceObjectCensus", "resource_object_census")
				.replace("serviceObjectCensus", "service_object_census")
				.replace("pathingFrontier", "pathing_frontier")
				.replace("projectionAudit", "projection_audit")
				.replace("minimapProjection", "minimap_projection")
				.replace("viewQualityInputs", "view_quality_inputs")
				.replace("fullWorldModelDebug", "full_world_model_debug")
				.replace("interactionHot", "interaction_hot")
				.replace("clientTickTail", "client_tick_tail")
				.replace("writerHealth", "writer_health")
				.replace("watchValues", "watch_values")
				.toLowerCase(Locale.ROOT);
	}

	private String normalizeMode(String value)
	{
		String normalized = value == null ? "compact" : value.trim().toLowerCase(Locale.ROOT);
		return "normal".equals(normalized) || "full".equals(normalized) ? normalized : "compact";
	}

	private String normalizeSnapshotTier(String value)
	{
		String normalized = value == null ? "hot" : value.trim().toLowerCase(Locale.ROOT);
		if ("expanded".equals(normalized) || "audit".equals(normalized))
		{
			return normalized;
		}
		return "hot";
	}

	private SnapshotHints snapshotHints(JsonObject request)
	{
		if (request == null)
		{
			return SnapshotHints.EMPTY;
		}
		String profileHint = stringValue(request, "profileHint", "");
		String taskHint = stringValue(request, "taskHint", "");
		String classHint = stringValue(request, "classHint", "");
		String targetTypeHint = stringValue(request, "targetTypeHint", "");
		boolean requireOnScreen = booleanValue(request, "requireOnScreen", false);
		boolean requireGeometryAvailable = booleanValue(request, "requireGeometryAvailable", false);
		int maxCandidatesHint = intValue(request, "maxCandidatesHint", 0);
		List<String> desiredClasses = stringListValue(request.get("desiredClasses"));
		return new SnapshotHints(
				profileHint,
				taskHint,
				classHint,
				targetTypeHint,
				requireOnScreen,
				requireGeometryAvailable,
				Math.max(0, maxCandidatesHint),
				desiredClasses);
	}

	private List<String> stringListValue(JsonElement element)
	{
		List<String> values = new ArrayList<>();
		if (element == null || !element.isJsonArray())
		{
			return values;
		}
		for (JsonElement child : element.getAsJsonArray())
		{
			if (child != null && child.isJsonPrimitive())
			{
				String value = child.getAsString();
				if (value != null && !value.isBlank())
				{
					values.add(value.trim());
				}
			}
		}
		return values;
	}

	private JsonElement parsePayload(String payloadJson)
	{
		if (payloadJson == null || payloadJson.isBlank())
		{
			return JsonNull.INSTANCE;
		}
		JsonElement parsed = gson.fromJson(payloadJson, JsonElement.class);
		return parsed == null ? JsonNull.INSTANCE : parsed;
	}

	private PriorityContext projectionPriorityContext(SnapshotHints hints, SensorFrame frame)
	{
		if (frame == null)
		{
			return new PriorityContext(null, null, null, hints);
		}
		SensorFrame.Fact baselineFact = frame.getFact(SensorFrame.FACT_BASELINE);
		if (baselineFact == null || !baselineFact.isAvailable())
		{
			return new PriorityContext(null, null, null, hints);
		}
		try
		{
			JsonElement parsed = parsePayload(baselineFact.getPayloadJson());
			if (!parsed.isJsonObject())
			{
				return new PriorityContext(null, null, null, hints);
			}
			JsonObject baseline = parsed.getAsJsonObject();
			JsonObject player = baseline.has("player") && baseline.get("player").isJsonObject()
					? baseline.getAsJsonObject("player")
					: null;
			if (player == null)
			{
				return new PriorityContext(null, null, null, hints);
			}
			Double sceneX = doubleValue(player, "sceneX");
			Double sceneY = doubleValue(player, "sceneY");
			Integer plane = integerValue(player, "plane");
			return new PriorityContext(sceneX, sceneY, plane, hints);
		}
		catch (RuntimeException e)
		{
			return new PriorityContext(null, null, null, hints);
		}
	}

	private Double doubleValue(JsonObject object, String key)
	{
		JsonElement element = object == null ? null : object.get(key);
		try
		{
			return element != null && element.isJsonPrimitive() ? element.getAsDouble() : null;
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private Integer integerValue(JsonObject object, String key)
	{
		JsonElement element = object == null ? null : object.get(key);
		try
		{
			return element != null && element.isJsonPrimitive() ? element.getAsInt() : null;
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private CacheFreshness cacheFreshness(Map<String, Object> cacheHealth)
	{
		Map<String, Long> ageMillisByType = longMap(cacheHealth == null ? null : cacheHealth.get("liveCacheAgeMillisByType"));
		List<String> packetTypes = stringList(cacheHealth == null ? null : cacheHealth.get("liveCachePayloadTypes"));
		List<String> freshPacketTypes = new ArrayList<>();
		List<String> stalePacketTypes = new ArrayList<>();
		List<String> staleReasons = new ArrayList<>();
		List<String> healthWarnings = new ArrayList<>();
		long maxCacheAgeMillis = -1L;

		for (String packetType : packetTypes)
		{
			Long ageMillis = ageMillisByType.get(packetType);
			if (ageMillis == null || ageMillis < 0L)
			{
				stalePacketTypes.add(packetType);
				continue;
			}
			maxCacheAgeMillis = Math.max(maxCacheAgeMillis, ageMillis);
			if (ageMillis <= CACHE_FRESHNESS_THRESHOLD_MILLIS)
			{
				freshPacketTypes.add(packetType);
			}
			else
			{
				stalePacketTypes.add(packetType);
			}
		}

		boolean hasPacketTypes = !packetTypes.isEmpty();
		boolean anyFreshPacket = !freshPacketTypes.isEmpty();
		boolean allPacketsStale = hasPacketTypes && !anyFreshPacket;
		boolean healthWarn = !hasPacketTypes || allPacketsStale;
		if (!hasPacketTypes)
		{
			staleReasons.add("plugin_cache_empty");
			healthWarnings.add("plugin live cache has no payloads");
		}
		else if (allPacketsStale)
		{
			staleReasons.add(STALE_REASON_ALL_PACKETS);
			healthWarnings.add(STALE_REASON_ALL_PACKETS);
		}
		else if (!stalePacketTypes.isEmpty())
		{
			staleReasons.add("plugin_some_packets_stale");
		}

		return new CacheFreshness(
				ageMillisByType,
				freshPacketTypes,
				stalePacketTypes,
				staleReasons,
				healthWarnings,
				maxCacheAgeMillis,
				hasPacketTypes,
				anyFreshPacket,
				allPacketsStale,
				healthWarn);
	}

	private Map<String, Long> longMap(Object value)
	{
		Map<String, Long> result = new LinkedHashMap<>();
		if (!(value instanceof Map))
		{
			return result;
		}
		for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet())
		{
			if (entry.getKey() == null || !(entry.getValue() instanceof Number))
			{
				continue;
			}
			result.put(String.valueOf(entry.getKey()), ((Number) entry.getValue()).longValue());
		}
		return result;
	}

	private List<String> stringList(Object value)
	{
		List<String> result = new ArrayList<>();
		if (!(value instanceof List))
		{
			return result;
		}
		for (Object item : (List<?>) value)
		{
			if (item != null)
			{
				result.add(String.valueOf(item));
			}
		}
		return result;
	}

	private Map<String, Object> cacheHealth()
	{
		return liveCache == null ? Map.of() : liveCache.health();
	}

	private static final class CacheFreshness
	{
		private final Map<String, Long> ageMillisByType;
		private final List<String> freshPacketTypes;
		private final List<String> stalePacketTypes;
		private final List<String> staleReasons;
		private final List<String> healthWarnings;
		private final long maxCacheAgeMillis;
		private final boolean hasPacketTypes;
		private final boolean anyFreshPacket;
		private final boolean allPacketsStale;
		private final boolean healthWarn;

		private CacheFreshness(
				Map<String, Long> ageMillisByType,
				List<String> freshPacketTypes,
				List<String> stalePacketTypes,
				List<String> staleReasons,
				List<String> healthWarnings,
				long maxCacheAgeMillis,
				boolean hasPacketTypes,
				boolean anyFreshPacket,
				boolean allPacketsStale,
				boolean healthWarn)
		{
			this.ageMillisByType = ageMillisByType;
			this.freshPacketTypes = freshPacketTypes;
			this.stalePacketTypes = stalePacketTypes;
			this.staleReasons = staleReasons;
			this.healthWarnings = healthWarnings;
			this.maxCacheAgeMillis = maxCacheAgeMillis;
			this.hasPacketTypes = hasPacketTypes;
			this.anyFreshPacket = anyFreshPacket;
			this.allPacketsStale = allPacketsStale;
			this.healthWarn = healthWarn;
		}
	}

	private List<String> endpointWarnings()
	{
		List<String> warnings = new ArrayList<>();
		if (!isLoopbackHost(host))
		{
			warnings.add("endpoint host is not loopback");
		}
		if (authToken.isBlank())
		{
			warnings.add("auth token not configured; endpoint must remain localhost-only");
		}
		return warnings;
	}

	private boolean requireMethod(HttpExchange exchange, String method) throws IOException
	{
		if (method.equalsIgnoreCase(exchange.getRequestMethod()))
		{
			return true;
		}
		writeJson(exchange, 405, errorPayload("method_not_allowed", "Expected " + method));
		return false;
	}

	private boolean checkAuth(HttpExchange exchange) throws IOException
	{
		if (authToken.isBlank())
		{
			return true;
		}

		String token = exchange.getRequestHeaders().getFirst("X-Plugin-Snapshot-Token");
		if (authToken.equals(token))
		{
			return true;
		}

		writeJson(exchange, 401, errorPayload("unauthorized", "Invalid snapshot token"));
		return false;
	}

	private String readBody(HttpExchange exchange) throws IOException
	{
		try (InputStream input = exchange.getRequestBody();
				ByteArrayOutputStream output = new ByteArrayOutputStream())
		{
			byte[] buffer = new byte[2048];
			int total = 0;
			int read;
			while ((read = input.read(buffer)) != -1)
			{
				total += read;
				if (total > MAX_REQUEST_BODY_BYTES)
				{
					throw new IllegalArgumentException("request body exceeds max size");
				}
				output.write(buffer, 0, read);
			}
			return output.toString(StandardCharsets.UTF_8.name());
		}
	}

	private void writeJson(HttpExchange exchange, int statusCode, Object payload) throws IOException
	{
		byte[] response = gson.toJson(payload).getBytes(StandardCharsets.UTF_8);
		exchange.getResponseHeaders().set("Content-Type", CONTENT_TYPE_JSON);
		exchange.sendResponseHeaders(statusCode, response.length);
		try (OutputStream output = exchange.getResponseBody())
		{
			output.write(response);
		}
	}

	private Map<String, Object> errorPayload(String code, String message)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "plugin_snapshot_error.v1");
		payload.put("status", "FAIL");
		payload.put("errorCode", code);
		payload.put("message", message);
		return payload;
	}

	private InetAddress bindAddress() throws IOException
	{
		if (!allowNonLocalHost && !isLoopbackHost(host))
		{
			throw new IOException("plugin snapshot endpoint only allows loopback hosts by default");
		}

		InetAddress address = InetAddress.getByName(host);
		if (!allowNonLocalHost && !address.isLoopbackAddress())
		{
			throw new IOException("plugin snapshot endpoint refused non-loopback host: " + host);
		}
		return address;
	}

	private boolean isLoopbackHost(String value)
	{
		String normalized = normalizeHost(value);
		return "127.0.0.1".equals(normalized)
				|| "localhost".equals(normalized)
				|| "::1".equals(normalized)
				|| "0:0:0:0:0:0:0:1".equals(normalized);
	}

	private String normalizeHost(String value)
	{
		return value == null || value.isBlank() ? "127.0.0.1" : value.trim();
	}

	private String stringValue(JsonObject object, String key, String defaultValue)
	{
		JsonElement element = object == null ? null : object.get(key);
		return element != null && element.isJsonPrimitive() ? element.getAsString() : defaultValue;
	}

	private int intValue(JsonObject object, String key, int defaultValue)
	{
		JsonElement element = object == null ? null : object.get(key);
		try
		{
			return element != null && element.isJsonPrimitive() ? element.getAsInt() : defaultValue;
		}
		catch (RuntimeException e)
		{
			return defaultValue;
		}
	}

	private long longValue(JsonObject object, String key, long defaultValue)
	{
		JsonElement element = object == null ? null : object.get(key);
		try
		{
			return element != null && element.isJsonPrimitive() ? element.getAsLong() : defaultValue;
		}
		catch (RuntimeException e)
		{
			return defaultValue;
		}
	}

	private boolean booleanValue(JsonObject object, String key, boolean defaultValue)
	{
		JsonElement element = object == null ? null : object.get(key);
		try
		{
			return element != null && element.isJsonPrimitive() ? element.getAsBoolean() : defaultValue;
		}
		catch (RuntimeException e)
		{
			return defaultValue;
		}
	}

	private double elapsedMillis(long startNanos)
	{
		return Duration.ofNanos(System.nanoTime() - startNanos).toNanos() / 1_000_000.0;
	}

	private static final class PriorityContext
	{
		private final Double sceneX;
		private final Double sceneY;
		private final Integer plane;
		private final SnapshotHints hints;

		private PriorityContext(Double sceneX, Double sceneY, Integer plane, SnapshotHints hints)
		{
			this.sceneX = sceneX;
			this.sceneY = sceneY;
			this.plane = plane;
			this.hints = hints == null ? SnapshotHints.EMPTY : hints;
		}
	}

	private static final class SnapshotHints
	{
		private static final SnapshotHints EMPTY = new SnapshotHints("", "", "", "", false, false, 0, List.of());

		private final String profileHint;
		private final String taskHint;
		private final String classHint;
		private final String targetTypeHint;
		private final boolean requireOnScreen;
		private final boolean requireGeometryAvailable;
		private final int maxCandidatesHint;
		private final List<String> desiredClasses;

		private SnapshotHints(
				String profileHint,
				String taskHint,
				String classHint,
				String targetTypeHint,
				boolean requireOnScreen,
				boolean requireGeometryAvailable,
				int maxCandidatesHint,
				List<String> desiredClasses)
		{
			this.profileHint = normalize(profileHint);
			this.taskHint = normalize(taskHint);
			this.classHint = normalize(classHint);
			this.targetTypeHint = normalize(targetTypeHint);
			this.requireOnScreen = requireOnScreen;
			this.requireGeometryAvailable = requireGeometryAvailable;
			this.maxCandidatesHint = maxCandidatesHint;
			this.desiredClasses = desiredClasses == null ? List.of() : List.copyOf(desiredClasses);
		}

		private boolean hasHints()
		{
			return !profileHint.isBlank()
					|| !taskHint.isBlank()
					|| !classHint.isBlank()
					|| !targetTypeHint.isBlank()
					|| requireOnScreen
					|| requireGeometryAvailable
					|| maxCandidatesHint > 0
					|| !desiredClasses.isEmpty();
		}

		private Map<String, Object> toMap()
		{
			Map<String, Object> map = new LinkedHashMap<>();
			if (!profileHint.isBlank())
			{
				map.put("profileHint", profileHint);
			}
			if (!taskHint.isBlank())
			{
				map.put("taskHint", taskHint);
			}
			if (!classHint.isBlank())
			{
				map.put("classHint", classHint);
			}
			if (!targetTypeHint.isBlank())
			{
				map.put("targetTypeHint", targetTypeHint);
			}
			if (!desiredClasses.isEmpty())
			{
				map.put("desiredClasses", desiredClasses);
			}
			if (requireOnScreen)
			{
				map.put("requireOnScreen", true);
			}
			if (requireGeometryAvailable)
			{
				map.put("requireGeometryAvailable", true);
			}
			if (maxCandidatesHint > 0)
			{
				map.put("maxCandidatesHint", maxCandidatesHint);
			}
			return map;
		}

		private static String normalize(String value)
		{
			return value == null ? "" : value.trim();
		}
	}

	private static Map<String, String> createNeedMap()
	{
		Map<String, String> map = new LinkedHashMap<>();
		map.put("baseline", "live_baseline_packet.v1");
		map.put("inventory", "live_inventory_packet.v1");
		map.put("activity", "live_activity_packet.v1");
		map.put("bank_ui", "live_bank_ui_packet.v1");
		map.put("dialogue_state", "live_dialogue_state_packet.v1");
		return map;
	}

	@Override
	public void close()
	{
		if (server != null)
		{
			server.stop(0);
			server = null;
		}
		if (executor != null)
		{
			executor.shutdownNow();
			executor = null;
		}
	}
}

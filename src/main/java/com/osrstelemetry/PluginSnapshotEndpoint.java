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
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class PluginSnapshotEndpoint implements Closeable
{
	static final String HEALTH_SCHEMA = "plugin_snapshot_health.v1";
	static final String REQUEST_SCHEMA = "plugin_snapshot_request.v1";
	static final String RESPONSE_SCHEMA = "plugin_snapshot_response.v1";
	static final String PRESET_REQUEST_SCHEMA = "telemetry_preset_request.v1";
	static final String PRESET_RESPONSE_SCHEMA = "telemetry_preset_response.v1";
	static final int MAX_REQUEST_BODY_BYTES = 16 * 1024;
	private static final String CONTENT_TYPE_JSON = "application/json; charset=utf-8";
	private static final List<String> SUPPORTED_NEEDS = Arrays.asList(
			"baseline",
			"scene_delta",
			"projection",
			"inventory",
			"inventory_delta",
			"activity",
			"navigation",
			"collision_window",
			"writer_health",
			"watch_values");
	private static final Map<String, String> NEED_TO_PACKET_TYPE = createNeedMap();

	private final PluginLiveCache liveCache;
	private final Gson gson;
	private final String host;
	private final int port;
	private final String authToken;
	private final int maxProjectionRefs;
	private final int maxResponseBytes;
	private final boolean allowNonLocalHost;
	private final TelemetryPresetApplier presetApplier;
	private HttpServer server;
	private ExecutorService executor;
	private int boundPort;

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
		this(liveCache, gson, host, port, authToken, maxProjectionRefs, maxResponseBytes, allowNonLocalHost, null);
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
			TelemetryPresetApplier presetApplier)
	{
		this.liveCache = liveCache;
		this.gson = gson;
		this.host = normalizeHost(host);
		this.port = Math.max(0, Math.min(65535, port));
		this.authToken = authToken == null ? "" : authToken.trim();
		this.maxProjectionRefs = Math.max(0, maxProjectionRefs);
		this.maxResponseBytes = Math.max(8 * 1024, maxResponseBytes);
		this.allowNonLocalHost = allowNonLocalHost;
		this.presetApplier = presetApplier;
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
		server.createContext("/presets", this::handlePresets);
		server.createContext("/preset/preview", this::handlePresetPreview);
		server.createContext("/preset/apply", this::handlePresetApply);
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
		Map<String, Object> cacheHealth = cacheHealth();
		payload.put("schema", HEALTH_SCHEMA);
		payload.put("enabled", true);
		payload.put("status", liveCache == null ? "FAIL" : "PASS");
		payload.put("latestTick", liveCache == null ? -1L : liveCache.getLatestTick());
		payload.put("latestSequence", liveCache == null ? -1L : liveCache.getLatestSequence());
		payload.put("cachedPacketTypes", liveCache == null ? List.of() : liveCache.packetTypes());
		payload.put("cacheAgeMillisByType", cacheHealth.get("liveCacheAgeMillisByType"));
		payload.put("cacheHealth", cacheHealth);
		payload.put("endpointHost", host);
		payload.put("endpointPort", boundPort == 0 ? port : boundPort);
		payload.put("warnings", endpointWarnings());
		return payload;
	}

	Map<String, Object> schemaPayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "plugin_snapshot_schema.v1");
		payload.put("supportedSchemas", List.of(HEALTH_SCHEMA, REQUEST_SCHEMA, RESPONSE_SCHEMA, PRESET_REQUEST_SCHEMA, PRESET_RESPONSE_SCHEMA));
		payload.put("supportedNeeds", SUPPORTED_NEEDS);
		payload.put("presetEndpointAvailable", presetApplier != null);
		payload.put("supportedPresets", TelemetryPresetApplier.PRESET_NAMES);
		payload.put("presetEndpoints", List.of("GET /presets", "POST /preset/preview", "POST /preset/apply"));
		payload.put("snapshotTiers", List.of("hot", "expanded", "audit"));
		payload.put("configLimits", Map.of(
				"maxProjectionRefs", maxProjectionRefs,
				"maxResponseBytes", maxResponseBytes,
				"maxRequestBodyBytes", MAX_REQUEST_BODY_BYTES));
		payload.put("projectionFieldModes", List.of("compact", "normal", "full"));
		payload.put("readOnlyStatement", "Returns cached telemetry observations and can apply fixed whitelisted telemetry config presets. It has no game input, command, or game-state mutation endpoints.");
		return payload;
	}

	Map<String, Object> presetsPayload()
	{
		return presetApplier == null ? presetUnavailablePayload() : presetApplier.presetsPayload();
	}

	Map<String, Object> presetPayload(JsonObject request, boolean preview)
	{
		if (presetApplier == null)
		{
			return presetUnavailablePayload();
		}
		return presetApplier.apply(stringValue(request, "preset", ""), preview);
	}

	Map<String, Object> snapshotPayload(JsonObject request)
	{
		long startNanos = System.nanoTime();
		String requestId = stringValue(request, "requestId", null);
		int requestedProjectionRefs = intValue(request, "maxProjectionRefs", maxProjectionRefs);
		int effectiveProjectionRefs = Math.max(0, Math.min(maxProjectionRefs, requestedProjectionRefs));
		long maxAgeTicks = longValue(request, "maxAgeTicks", -1L);
		boolean includeGeometry = booleanValue(request, "includeGeometry", false);
		boolean includeCollisionWindow = booleanValue(request, "includeCollisionWindow", true);
		boolean includeWatchValues = booleanValue(request, "includeWatchValues", false);
		String responseMode = normalizeMode(stringValue(request, "responseMode", "compact"));
		String projectionFieldMode = normalizeMode(stringValue(request, "projectionFieldMode", responseMode));
		String snapshotTier = normalizeSnapshotTier(stringValue(request, "snapshotTier", "hot"));
		SnapshotHints snapshotHints = snapshotHints(request);
		List<String> needs = requestedNeeds(request, includeCollisionWindow, includeWatchValues);
		Map<String, Object> response = new LinkedHashMap<>();
		Map<String, JsonElement> payloads = new LinkedHashMap<>();
		Map<String, Object> freshness = new LinkedHashMap<>();
		Map<String, Long> ageTicksByNeed = new LinkedHashMap<>();
		List<String> missingCapabilities = new ArrayList<>();
		List<String> warnings = new ArrayList<>();
		Map<String, Object> responseSizing = new LinkedHashMap<>();
		long latestTick = liveCache == null ? -1L : liveCache.getLatestTick();
		boolean stale = false;
		PriorityContext priorityContext = projectionPriorityContext(snapshotHints);

		responseSizing.put("maxResponseBytes", maxResponseBytes);
		responseSizing.put("requestedProjectionRefs", requestedProjectionRefs);
		responseSizing.put("effectiveProjectionRefs", effectiveProjectionRefs);
		responseSizing.put("projectionFieldMode", projectionFieldMode);
		responseSizing.put("responseMode", responseMode);
		responseSizing.put("includeGeometry", includeGeometry);
		responseSizing.put("snapshotTier", snapshotTier);
		responseSizing.put("snapshotHints", snapshotHints.toMap());

		if (liveCache == null)
		{
			warnings.add("plugin live cache unavailable");
		}
		else
		{
			for (String need : needs)
			{
				String packetType = NEED_TO_PACKET_TYPE.get(need);
				if (packetType == null)
				{
					missingCapabilities.add(need);
					warnings.add("unsupported need: " + need);
					continue;
				}

				PluginLiveCache.CachedPayload cached = liveCache.get(packetType);
				if (cached == null)
				{
					missingCapabilities.add(need);
					continue;
				}
				if ("projection".equals(need))
				{
					responseSizing.put("cachedProjectionBytes", cached.sizeBytes);
				}

				long ageTicks = latestTick >= 0L ? Math.max(0L, latestTick - cached.tick) : -1L;
				ageTicksByNeed.put(need, ageTicks);
				if (maxAgeTicks >= 0L && ageTicks > maxAgeTicks)
				{
					stale = true;
					warnings.add(need + " cache age exceeded maxAgeTicks");
				}

				JsonElement payload = parsePayload(cached.payloadJson);
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

		freshness.put("latestTick", latestTick);
		freshness.put("maxAgeTicks", maxAgeTicks);
		freshness.put("fresh", !stale);
		freshness.put("ageTicksByNeed", ageTicksByNeed);

		String status = "PASS";
		if (liveCache == null || payloads.isEmpty())
		{
			status = "FAIL";
		}
		else if (!missingCapabilities.isEmpty() || !warnings.isEmpty() || stale)
		{
			status = "WARN";
		}

		response.put("schema", RESPONSE_SCHEMA);
		response.put("requestId", requestId);
		response.put("generatedAtUtc", Instant.now().toString());
		response.put("latestTick", latestTick);
		response.put("snapshotTier", snapshotTier);
		response.put("status", status);
		response.put("freshness", freshness);
		response.put("payloads", payloads);
		response.put("missingCapabilities", missingCapabilities);
		response.put("warnings", warnings);
		response.put("serviceTimingMillis", elapsedMillis(startNanos));
		response.put("responseSizing", responseSizing);
		response.put("cacheHealth", cacheHealth());
		return response;
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

	private void handlePresets(HttpExchange exchange) throws IOException
	{
		if (!requireMethod(exchange, "GET") || !checkAuth(exchange))
		{
			return;
		}
		if (presetApplier == null)
		{
			writeJson(exchange, 503, presetUnavailablePayload());
			return;
		}
		writeJson(exchange, 200, presetsPayload());
	}

	private void handlePresetPreview(HttpExchange exchange) throws IOException
	{
		handlePresetRequest(exchange, true);
	}

	private void handlePresetApply(HttpExchange exchange) throws IOException
	{
		handlePresetRequest(exchange, false);
	}

	private void handlePresetRequest(HttpExchange exchange, boolean preview) throws IOException
	{
		if (!requireMethod(exchange, "POST") || !checkAuth(exchange))
		{
			return;
		}
		if (presetApplier == null)
		{
			writeJson(exchange, 503, presetUnavailablePayload());
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

		Map<String, Object> response = presetPayload(request, preview);
		writeJson(exchange, "FAIL".equals(response.get("status")) ? 400 : 200, response);
	}

	private Map<String, Object> presetUnavailablePayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", PRESET_RESPONSE_SCHEMA);
		payload.put("status", "FAIL");
		payload.put("errorCode", "preset_endpoint_unavailable");
		payload.put("message", "preset endpoint is unavailable");
		payload.put("readOnlyGameState", true);
		return payload;
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
		tooLarge.put("schema", RESPONSE_SCHEMA);
		tooLarge.put("generatedAtUtc", Instant.now().toString());
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

		if (needsElement != null && needsElement.isJsonArray())
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

		if (needs.isEmpty())
		{
			needs.add("baseline");
			needs.add("projection");
			needs.add("inventory");
			needs.add("activity");
			needs.add("navigation");
			needs.add("writer_health");
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

	private PriorityContext projectionPriorityContext(SnapshotHints hints)
	{
		if (liveCache == null)
		{
			return new PriorityContext(null, null, null, hints);
		}
		PluginLiveCache.CachedPayload cached = liveCache.get("live_baseline_packet.v1");
		if (cached == null)
		{
			return new PriorityContext(null, null, null, hints);
		}
		try
		{
			JsonElement parsed = parsePayload(cached.payloadJson);
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

	private Map<String, Object> cacheHealth()
	{
		return liveCache == null ? Map.of() : liveCache.health();
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
		map.put("scene_delta", "live_scene_delta_packet.v1");
		map.put("projection", "live_projection_packet.v1");
		map.put("inventory", "live_inventory_packet.v1");
		map.put("inventory_delta", "live_inventory_delta_packet.v1");
		map.put("activity", "live_activity_packet.v1");
		map.put("navigation", "live_navigation_packet.v1");
		map.put("collision_window", "live_collision_window_packet.v1");
		map.put("writer_health", "live_writer_health_packet.v1");
		map.put("watch_values", "live_watch_values_packet.v1");
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

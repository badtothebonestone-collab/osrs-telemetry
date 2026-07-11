package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class PluginSnapshotEndpointTest
{
	private static final long SOURCE_TICK = 8L;
	private static final String SESSION_ID = "fixture-session";
	private static final long CLIENT_PROCESS_ID = 1234L;
	private static final String GEOMETRY_FRAME_ID = "fixture-geometry-8";

	private final Gson gson = new Gson();

	@Test
	public void configDefaultsEnableLoopbackSnapshotEndpoint()
	{
		TelemetryConfig config = new TelemetryConfig() { };
		assertTrue(config.enabled());
		assertTrue(config.enablePluginSnapshotEndpoint());
		assertEquals("127.0.0.1", config.pluginSnapshotHost());
		assertEquals(8893, config.pluginSnapshotPort());
		assertFalse(config.pluginSnapshotAllowNonLocalHost());
	}

	@Test
	public void schemaAdvertisesOnlyObservationContract()
	{
		Map<String, Object> schema = endpoint(new PluginLiveCache(gson)).schemaPayload();
		assertEquals(
				List.of("baseline", "inventory", "activity", "bank_ui", "dialogue_state", "interaction_hot",
						"route_object_census", "resource_object_census", "service_object_census"),
				schema.get("supportedNeeds"));
		assertEquals(List.of("GET /health", "GET /schema", "POST /snapshot"), schema.get("endpoints"));
		assertEquals(List.of("hot"), schema.get("snapshotTiers"));
		assertTrue(((List<?>) schema.get("supportedSchemas")).contains(PluginSnapshotEndpoint.RESPONSE_SCHEMA));
		assertTrue(String.valueOf(schema.get("readOnlyStatement")).contains("no configuration"));
	}

	@Test
	public void snapshotV2ReturnsOneAtomicCanonicalFrameWithProvenance()
	{
		Map<String, Object> response = endpoint(canonicalCache()).snapshotPayload(request(
				"baseline", "inventory", "activity", "bank_ui", "dialogue_state"));

		assertEquals(PluginSnapshotEndpoint.RESPONSE_SCHEMA, response.get("schema"));
		assertEquals("PASS", response.get("status"));
		JsonObject payloads = jsonObject(response.get("payloads"));
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			assertTrue(payloads.has(factName));
		}

		JsonObject frame = jsonObject(response.get("sensorFrame"));
		assertEquals(SensorFrame.SCHEMA, frame.get("schema").getAsString());
		assertEquals(SOURCE_TICK, frame.get("sourceTick").getAsLong());
		assertEquals(SESSION_ID, frame.get("sessionId").getAsString());
		assertEquals(CLIENT_PROCESS_ID, frame.get("clientProcessId").getAsLong());
		assertEquals(GEOMETRY_FRAME_ID, frame.get("geometryFrameId").getAsString());
		assertTrue(frame.get("coherent").getAsBoolean());
		assertTrue(frame.get("complete").getAsBoolean());
		Instant.parse(frame.get("capturedAtUtc").getAsString());
		JsonObject factMetadata = frame.getAsJsonObject("facts");
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			assertEquals(SOURCE_TICK, factMetadata.getAsJsonObject(factName).get("sourceTick").getAsLong());
		}
		JsonObject freshness = jsonObject(response.get("freshness"));
		assertTrue(freshness.get("sourceCaptureFresh").getAsBoolean());
		assertTrue(freshness.get("frameCoherent").getAsBoolean());
		assertTrue(freshness.get("fresh").getAsBoolean());
	}

	@Test
	public void incompleteUnavailableFrameFailsClosed()
	{
		Map<String, Object> response = endpoint(cacheWithFrame(incompleteFrame())).snapshotPayload(
				request("baseline", "inventory"));

		assertEquals("WARN", response.get("status"));
		JsonObject payloads = jsonObject(response.get("payloads"));
		assertTrue(payloads.has("baseline"));
		assertFalse(payloads.has("inventory"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("inventory"));
		assertTrue(((List<?>) response.get("warnings")).contains("sensor_fact_unavailable:inventory"));
		assertTrue(((List<?>) response.get("warnings")).contains("sensor_frame_incomplete_or_incoherent"));
		assertFalse(jsonObject(response.get("sensorFrame")).get("complete").getAsBoolean());
	}

	@Test
	public void loginScreenFrameRoundTripsOwningProcessWithoutLoadedScene()
	{
		SensorFrame frame = completeFrame(
				"fixture-login-frame",
				0L,
				"fixture-login-session",
				4321L,
				"fixture-login-geometry",
				Map.of(
						"gameState", "LOGIN_SCREEN",
						"player", Map.of(),
						"inputGeometry", Map.of(
								"geometryAvailable", false,
								"reason", "canvas_not_showing",
								"clientProcessId", 4321L)));

		Map<String, Object> response = endpoint(cacheWithFrame(frame)).snapshotPayload(request("baseline"));
		JsonObject baseline = jsonObject(response.get("payloads")).getAsJsonObject("baseline");

		assertEquals("LOGIN_SCREEN", baseline.get("gameState").getAsString());
		assertEquals(4321L, baseline.getAsJsonObject("inputGeometry").get("clientProcessId").getAsLong());
		assertEquals("fixture-login-session", jsonObject(response.get("sensorFrame")).get("sessionId").getAsString());
	}

	@Test
	public void interactionHotAndTileProjectionUseMatchingFrameProvenance()
	{
		ClientTickHotState hot = new ClientTickHotState(4);
		hot.recordPostMenuSort(Map.of(
				"clientTick", 10L,
				"gameTickAtSample", SOURCE_TICK,
				"wallTimeMillis", System.currentTimeMillis(),
				"gameState", "LOGGED_IN",
				"sessionId", SESSION_ID,
				"clientProcessId", CLIENT_PROCESS_ID,
				"topOption", "Walk here",
				"topTarget", ""));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false, hot,
				requests -> tileProjectionResponse(SOURCE_TICK, GEOMETRY_FRAME_ID),
				null);
		JsonObject request = request("baseline", "interaction_hot");
		request.add("tileProjectionRequests", tileProjectionRequests());

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject payloads = jsonObject(response.get("payloads"));
		JsonObject interaction = payloads.getAsJsonObject("interaction_hot");
		JsonObject projection = payloads.getAsJsonObject("tile_projection");

		assertEquals("PASS", response.get("status"));
		assertEquals(SOURCE_TICK, interaction.get("sourceTick").getAsLong());
		assertEquals(SESSION_ID, interaction.get("sessionId").getAsString());
		assertEquals(CLIENT_PROCESS_ID, interaction.get("clientProcessId").getAsLong());
		assertEquals(10L, interaction.getAsJsonObject("postMenuSort").get("clientTick").getAsLong());
		Instant.parse(interaction.get("capturedAtUtc").getAsString());
		assertEquals(SOURCE_TICK, projection.get("sourceTick").getAsLong());
		assertEquals(GEOMETRY_FRAME_ID, projection.get("geometryFrameId").getAsString());
	}

	@Test
	public void worldModelProviderIsLimitedToThreeObjectCensuses()
	{
		Map<String, Object> censuses = Map.of(
				"resource_object_census", Map.of("objects", List.of()),
				"route_object_census", Map.of("objects", List.of()),
				"service_object_census", Map.of("objects", List.of()));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(SOURCE_TICK, GEOMETRY_FRAME_ID, censuses));

		Map<String, Object> response = endpoint.snapshotPayload(request(
				"resource_object_census", "route_object_census", "service_object_census"));
		JsonObject payloads = jsonObject(response.get("payloads"));

		assertEquals("PASS", response.get("status"));
		assertTrue(payloads.has("resource_object_census"));
		assertTrue(payloads.has("route_object_census"));
		assertTrue(payloads.has("service_object_census"));
		assertEquals(SOURCE_TICK, payloads.getAsJsonObject("route_object_census").get("sourceTick").getAsLong());
		assertEquals(GEOMETRY_FRAME_ID, payloads.getAsJsonObject("route_object_census").get("geometryFrameId").getAsString());
		Instant.parse(payloads.getAsJsonObject("route_object_census").get("capturedAtUtc").getAsString());
	}

	@Test
	public void worldModelCapturedBeforeSensorFrameIsRejected()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> Map.of(
						"schema", WorldModelCache.SCHEMA,
						"status", "PASS",
						"metadata", sourceMetadata(
								SOURCE_TICK,
								GEOMETRY_FRAME_ID,
								"2026-05-11T00:00:00Z"),
						"payloads", Map.of("route_object_census", Map.of("objects", List.of())),
						"quality", Map.of("worldModelAvailable", true),
						"warnings", List.of(),
						"sizing", Map.of()));

		Map<String, Object> response = endpoint.snapshotPayload(request("route_object_census"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(jsonObject(response.get("payloads")).has("route_object_census"));
		assertTrue(((List<?>) response.get("warnings")).contains("world_model_provenance_mismatch"));
	}

	@Test
	public void worldModelSourceTickMismatchIsRejected()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(
						SOURCE_TICK - 1L,
						GEOMETRY_FRAME_ID,
						Map.of("route_object_census", Map.of("objects", List.of()))));

		Map<String, Object> response = endpoint.snapshotPayload(request("route_object_census"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(jsonObject(response.get("payloads")).has("route_object_census"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("route_object_census"));
		assertTrue(((List<?>) response.get("warnings")).contains("world_model_provenance_mismatch"));
		assertFalse(response.containsKey("worldModel"));
	}

	@Test
	public void tileProjectionSameTickGeometryMismatchIsRejected()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4),
				requests -> tileProjectionResponse(SOURCE_TICK, "fixture-geometry-stale"),
				null);
		JsonObject request = request("baseline");
		request.add("tileProjectionRequests", tileProjectionRequests());

		Map<String, Object> response = endpoint.snapshotPayload(request);

		assertEquals("WARN", response.get("status"));
		JsonObject payloads = jsonObject(response.get("payloads"));
		assertTrue(payloads.has("baseline"));
		assertFalse(payloads.has("tile_projection"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("tile_projection"));
		assertTrue(((List<?>) response.get("warnings")).contains("tile_projection_provenance_mismatch"));
		assertFalse(response.containsKey("tileProjections"));
	}

	@Test
	public void startedEndpointHasNoMutationRoutes() throws Exception
	{
		PluginSnapshotEndpoint endpoint = endpoint(canonicalCache());
		endpoint.start();
		try
		{
			assertEquals(200, httpStatus(endpoint, "GET", "/health", null));
			assertEquals(200, httpStatus(endpoint, "POST", "/snapshot", "{\"needs\":[\"baseline\"]}"));
			assertEquals(404, httpStatus(endpoint, "POST", "/preset/apply", "{}"));
		}
		finally
		{
			endpoint.close();
		}
	}

	private SensorFrame canonicalFrame()
	{
		return completeFrame(
				"fixture-frame-8",
				SOURCE_TICK,
				SESSION_ID,
				CLIENT_PROCESS_ID,
				GEOMETRY_FRAME_ID,
				Map.of(
						"gameState", "LOGGED_IN",
						"player", Map.of(),
						"inputGeometry", Map.of(
								"geometryAvailable", true,
								"clientProcessId", CLIENT_PROCESS_ID)));
	}

	private SensorFrame incompleteFrame()
	{
		String capturedAtUtc = Instant.now().toString();
		return frameBuilder(
				"fixture-incomplete-frame",
				SOURCE_TICK,
				SESSION_ID,
				CLIENT_PROCESS_ID,
				GEOMETRY_FRAME_ID,
				capturedAtUtc)
				.fact(gson, SensorFrame.FACT_BASELINE, SOURCE_TICK, capturedAtUtc, true, List.of(),
						Map.of("gameState", "LOGGED_IN"))
				.fact(gson, SensorFrame.FACT_INVENTORY, SOURCE_TICK, capturedAtUtc, false,
						List.of("inventory_unavailable"), Map.of("inventory", Map.of("known", false)))
				.build();
	}

	private SensorFrame completeFrame(
			String frameId,
			long sourceTick,
			String sessionId,
			long clientProcessId,
			String geometryFrameId,
			Map<String, Object> baseline)
	{
		String capturedAtUtc = Instant.now().toString();
		return frameBuilder(
				frameId,
				sourceTick,
				sessionId,
				clientProcessId,
				geometryFrameId,
				capturedAtUtc)
				.fact(gson, SensorFrame.FACT_BASELINE, sourceTick, capturedAtUtc, true, List.of(), baseline)
				.fact(gson, SensorFrame.FACT_INVENTORY, sourceTick, capturedAtUtc, true, List.of(),
						Map.of("inventory", Map.of("known", true)))
				.fact(gson, SensorFrame.FACT_ACTIVITY, sourceTick, capturedAtUtc, true, List.of(),
						Map.of("animation", -1))
				.fact(gson, SensorFrame.FACT_BANK_UI, sourceTick, capturedAtUtc, true, List.of(),
						Map.of("known", true, "bankOpen", false))
				.fact(gson, SensorFrame.FACT_DIALOGUE_STATE, sourceTick, capturedAtUtc, true, List.of(),
						Map.of("active", false))
				.build();
	}

	private SensorFrame.Builder frameBuilder(
			String frameId,
			long sourceTick,
			String sessionId,
			long clientProcessId,
			String geometryFrameId,
			String capturedAtUtc)
	{
		return SensorFrame.builder(frameId, sourceTick, System.nanoTime(), capturedAtUtc)
				.completedAtUtc(capturedAtUtc)
				.sessionId(sessionId)
				.clientProcessId(clientProcessId)
				.geometryFrameId(geometryFrameId);
	}

	private PluginLiveCache canonicalCache()
	{
		return cacheWithFrame(canonicalFrame());
	}

	private PluginLiveCache cacheWithFrame(SensorFrame frame)
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		assertTrue(cache.publish(frame));
		return cache;
	}

	private Map<String, Object> tileProjectionResponse(long sourceTick, String geometryFrameId)
	{
		return Map.of(
				"schema", "tile_projection_response.v1",
				"status", "PASS",
				"capturedAtUtc", Instant.now().toString(),
				"sourceTick", sourceTick,
				"sessionId", SESSION_ID,
				"clientProcessId", CLIENT_PROCESS_ID,
				"geometryFrameId", geometryFrameId,
				"tiles", List.of(Map.of(
						"label", "route",
						"worldX", 3200,
						"worldY", 3230,
						"plane", 0,
						"geometryAvailable", true,
						"onScreen", true,
						"visible", true,
						"actionable", true)));
	}

	private Map<String, Object> worldModelResponse(
			long sourceTick,
			String geometryFrameId,
			Map<String, Object> payloads)
	{
		return Map.of(
				"schema", WorldModelCache.SCHEMA,
				"status", "PASS",
				"metadata", sourceMetadata(sourceTick, geometryFrameId),
				"payloads", payloads,
				"quality", Map.of("worldModelAvailable", true),
				"warnings", List.of(),
				"sizing", Map.of());
	}

	private Map<String, Object> sourceMetadata(long sourceTick, String geometryFrameId)
	{
		return sourceMetadata(sourceTick, geometryFrameId, Instant.now().toString());
	}

	private Map<String, Object> sourceMetadata(
			long sourceTick,
			String geometryFrameId,
			String capturedAtUtc)
	{
		return Map.of(
				"capturedAtUtc", capturedAtUtc,
				"sourceTick", sourceTick,
				"sessionId", SESSION_ID,
				"clientProcessId", CLIENT_PROCESS_ID,
				"geometryFrameId", geometryFrameId);
	}

	private PluginSnapshotEndpoint endpoint(PluginLiveCache cache)
	{
		return new PluginSnapshotEndpoint(cache, gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false);
	}

	private JsonArray tileProjectionRequests()
	{
		JsonArray tiles = new JsonArray();
		JsonObject tile = new JsonObject();
		tile.addProperty("label", "route");
		tile.addProperty("worldX", 3200);
		tile.addProperty("worldY", 3230);
		tile.addProperty("plane", 0);
		tiles.add(tile);
		return tiles;
	}

	private JsonObject request(String... needs)
	{
		JsonObject request = new JsonObject();
		JsonArray values = new JsonArray();
		for (String need : needs)
		{
			values.add(need);
		}
		request.add("needs", values);
		request.addProperty("maxAgeTicks", 2);
		request.addProperty("maxSourceAgeMillis", 5_000);
		return request;
	}

	private JsonObject jsonObject(Object value)
	{
		return gson.toJsonTree(value).getAsJsonObject();
	}

	private int httpStatus(PluginSnapshotEndpoint endpoint, String method, String path, String body) throws Exception
	{
		HttpURLConnection connection = (HttpURLConnection) new URL(
				"http://127.0.0.1:" + endpoint.getBoundPort() + path).openConnection();
		connection.setRequestMethod(method);
		connection.setConnectTimeout(2_000);
		connection.setReadTimeout(2_000);
		if (body != null)
		{
			byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
			connection.setDoOutput(true);
			connection.setRequestProperty("Content-Type", "application/json");
			connection.setFixedLengthStreamingMode(bytes.length);
			connection.getOutputStream().write(bytes);
		}
		try
		{
			return connection.getResponseCode();
		}
		finally
		{
			connection.disconnect();
		}
	}
}

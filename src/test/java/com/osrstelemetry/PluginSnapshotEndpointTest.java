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
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
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
						"client_tick_tail", "scene_object_census", "actor_census", "collision_window"),
				schema.get("supportedNeeds"));
		assertEquals(List.of("GET /health", "GET /schema", "POST /snapshot"), schema.get("endpoints"));
		assertEquals(List.of("hot"), schema.get("snapshotTiers"));
		assertTrue(((List<?>) schema.get("supportedSchemas")).contains(PluginSnapshotEndpoint.RESPONSE_SCHEMA));
		assertTrue(((List<?>) schema.get("supportedSchemas")).contains(
				ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA));
		assertTrue(((List<?>) schema.get("supportedSchemas")).contains(
				PluginSnapshotEndpoint.ENDPOINT_QUEUE_DIAGNOSTICS_SCHEMA));
		assertTrue(((List<?>) schema.get("requestControls")).contains("maxClickedSamples"));
		assertTrue(((List<?>) schema.get("requestControls")).contains("disableCameraInputCapture"));
		assertEquals(
				CameraInputCapture.CAPTURE_LEASE_MILLIS,
				((Map<?, ?>) schema.get("configLimits")).get("cameraInputCaptureLeaseMillis"));
		assertEquals(
				PluginSnapshotEndpoint.ENDPOINT_WORKER_LIMIT,
				((Map<?, ?>) schema.get("configLimits")).get("endpointWorkerLimit"));
		assertEquals(
				PluginSnapshotEndpoint.ENDPOINT_PENDING_CAPACITY,
				((Map<?, ?>) schema.get("configLimits")).get("endpointPendingCapacity"));
		assertTrue(((List<?>) schema.get("worldModelQueryControls")).contains("worldModel.maxActors"));
		assertTrue(((List<?>) schema.get("worldModelQueryControls")).contains("worldModel.priorityObjectIds"));
		assertEquals(
				ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA,
				schema.get("clientThreadQueryDiagnosticsSchema"));
		assertTrue(String.valueOf(schema.get("readOnlyStatement")).contains("no configuration"));
	}

	@Test
	public void boundedSnapshotEncodesTwiceAndReusesExactFinalBytes()
	{
		PluginSnapshotEndpoint.EncodedResponse encoded = endpoint(canonicalCache())
				.encodedSnapshotResponse(request("baseline"));
		Map<String, Object> payload = encoded.payload();
		Map<?, ?> sizing = (Map<?, ?>) payload.get("responseSizing");
		JsonObject decoded = gson.fromJson(
				new String(encoded.body(), StandardCharsets.UTF_8),
				JsonObject.class);

		assertEquals(2, encoded.serializationPasses());
		assertEquals(200, encoded.httpStatus());
		assertEquals(encoded.body().length, ((Number) payload.get("estimatedResponseBytes")).intValue());
		assertEquals(encoded.body().length, ((Number) sizing.get("estimatedResponseBytes")).intValue());
		assertEquals(2, sizing.get("serializationPasses"));
		assertEquals(true, sizing.get("serializedBytesReusedForWrite"));
		assertEquals(gson.toJsonTree(payload), decoded);
	}

	@Test
	public void oversizedSnapshotStillUsesOnlyTwoBoundedSerializationPasses()
	{
		SensorFrame largeFrame = completeFrame(
				"large-frame",
				SOURCE_TICK,
				SESSION_ID,
				CLIENT_PROCESS_ID,
				GEOMETRY_FRAME_ID,
				Map.of("gameState", "LOGGED_IN", "denseEvidence", "x".repeat(20_000)));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				cacheWithFrame(largeFrame), gson, "127.0.0.1", 0, "", 50, 8 * 1024, false);

		PluginSnapshotEndpoint.EncodedResponse encoded = endpoint.encodedSnapshotResponse(request("baseline"));
		Map<?, ?> sizing = (Map<?, ?>) encoded.payload().get("responseSizing");

		assertEquals(413, encoded.httpStatus());
		assertEquals(2, encoded.serializationPasses());
		assertEquals("response_too_large", encoded.payload().get("errorCode"));
		assertTrue(((Number) encoded.payload().get("estimatedResponseBytes")).intValue() > 8 * 1024);
		assertEquals(2, sizing.get("serializationPasses"));
		assertEquals(true, sizing.get("serializedBytesReusedForWrite"));
	}

	@Test
	public void retiredSemanticCensusNeedsFailExplicitly()
	{
		List<String> retired = List.of(
				"resource_object_census",
				"route_object_census",
				"service_object_census");
		Map<String, Object> response = endpoint(canonicalCache()).snapshotPayload(
				request(retired.toArray(new String[0])));

		assertEquals("FAIL", response.get("status"));
		assertTrue(jsonObject(response.get("payloads")).keySet().isEmpty());
		assertEquals(retired, response.get("missingCapabilities"));
		assertEquals(
				List.of(
						"unsupported need: resource_object_census",
						"unsupported need: route_object_census",
						"unsupported need: service_object_census"),
				response.get("warnings"));
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
	public void newlyAssembledResponseRejectsAnOldSourceFrame()
	{
		String staleCapturedAtUtc = Instant.now().minusSeconds(60L).toString();
		SensorFrame staleFrame = completeFrame(
				"fixture-stale-frame",
				SOURCE_TICK,
				SESSION_ID,
				CLIENT_PROCESS_ID,
				GEOMETRY_FRAME_ID,
				Map.of(
						"gameState", "LOGGED_IN",
						"player", Map.of(),
						"inputGeometry", Map.of(
								"geometryAvailable", true,
								"clientProcessId", CLIENT_PROCESS_ID)),
				staleCapturedAtUtc);

		Map<String, Object> response = endpoint(cacheWithFrame(staleFrame)).snapshotPayload(
				request("baseline", "inventory", "activity", "bank_ui", "dialogue_state"));

		assertEquals("WARN", response.get("status"));
		assertTrue(Instant.parse(String.valueOf(response.get("assembledAtUtc")))
				.isAfter(Instant.parse(staleCapturedAtUtc)));
		JsonObject freshness = jsonObject(response.get("freshness"));
		assertFalse(freshness.get("sourceCaptureFresh").getAsBoolean());
		assertFalse(freshness.get("cacheWallClockFresh").getAsBoolean());
		assertFalse(freshness.get("fresh").getAsBoolean());
		assertTrue(((List<?>) response.get("warnings")).contains("sensor_frame_source_stale"));
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
	public void clientTickTailNeedReturnsAllFourBoundedLanesWithDropEvidence()
	{
		ClientTickHotState hot = new ClientTickHotState(2);
		for (long tick = 1L; tick <= 3L; tick++)
		{
			hot.recordClientTick(Map.of("clientTick", tick, "gameTickAtSample", SOURCE_TICK));
			hot.recordPostMenuSort(Map.of("clientTick", tick, "gameTickAtSample", SOURCE_TICK, "topOption", "Option " + tick));
			hot.recordMenuOptionClicked(Map.of("clientTick", tick, "gameTickAtSample", SOURCE_TICK, "option", "Click " + tick));
			hot.recordCameraInput(Map.of("clientTick", tick, "gameTickAtSample", SOURCE_TICK, "control", "W"));
		}
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false, hot);
		JsonObject request = request("clientTickTail");
		request.addProperty("maxClientTickSamples", 99);
		request.addProperty("maxMenuSamples", 99);
		request.addProperty("maxClickedSamples", 99);
		request.addProperty("maxCameraInputSamples", 99);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject tail = jsonObject(response.get("payloads")).getAsJsonObject("client_tick_tail");

		assertEquals("PASS", response.get("status"));
		assertEquals(2, tail.getAsJsonArray("clientTickTail").size());
		assertEquals(2, tail.getAsJsonArray("postMenuSortTail").size());
		assertEquals(2, tail.getAsJsonArray("clickedTail").size());
		assertEquals(2, tail.getAsJsonArray("cameraInputTail").size());
		assertEquals(5L, tail.getAsJsonArray("clientTickTail").get(0).getAsJsonObject().get("eventSequence").getAsLong());
		assertEquals(6L, tail.getAsJsonArray("postMenuSortTail").get(0).getAsJsonObject().get("eventSequence").getAsLong());
		assertEquals(7L, tail.getAsJsonArray("clickedTail").get(0).getAsJsonObject().get("eventSequence").getAsLong());
		assertEquals(8L, tail.getAsJsonArray("cameraInputTail").get(0).getAsJsonObject().get("eventSequence").getAsLong());
		JsonObject latency = tail.getAsJsonObject("latency");
		assertEquals(4L, latency.get("droppedSamples").getAsLong());
		assertEquals(1L, latency.get("droppedClientTickSamples").getAsLong());
		assertEquals(1L, latency.get("droppedPostMenuSortSamples").getAsLong());
		assertEquals(1L, latency.get("droppedClickedSamples").getAsLong());
		assertEquals(1L, latency.get("droppedCameraInputSamples").getAsLong());
	}

	@Test
	public void onlyExplicitCameraSampleRequestsRenewAndExplicitDisableWins()
	{
		AtomicInteger renewals = new AtomicInteger();
		AtomicInteger disables = new AtomicInteger();
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4));
		endpoint.setCameraInputCaptureLeaseControls(
				renewals::incrementAndGet,
				disables::incrementAndGet);

		endpoint.snapshotPayload(request("baseline"));
		JsonObject zero = request("client_tick_tail");
		zero.addProperty("maxClientTickSamples", 4);
		zero.addProperty("maxCameraInputSamples", 0);
		endpoint.snapshotPayload(zero);
		JsonObject stringValue = request("client_tick_tail");
		stringValue.addProperty("maxCameraInputSamples", "4");
		endpoint.snapshotPayload(stringValue);
		assertEquals(0, renewals.get());
		assertEquals(0, disables.get());

		JsonObject enabled = request("client_tick_tail");
		enabled.addProperty("maxCameraInputSamples", 4);
		endpoint.snapshotPayload(enabled);
		endpoint.snapshotPayload(enabled);
		assertEquals(2, renewals.get());

		JsonObject disabled = request("client_tick_tail");
		disabled.addProperty("maxCameraInputSamples", 4);
		disabled.addProperty("disableCameraInputCapture", true);
		endpoint.snapshotPayload(disabled);
		assertEquals(2, renewals.get());
		assertEquals(1, disables.get());

		JsonObject stringDisable = request("baseline");
		stringDisable.addProperty("disableCameraInputCapture", "true");
		endpoint.snapshotPayload(stringDisable);
		assertEquals(1, disables.get());
	}

	@Test
	public void worldModelProviderServesNeutralObjectCensusWithFrameProvenance()
	{
		Map<String, Object> censuses = Map.of(
				"scene_object_census", Map.of("objects", List.of()));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(SOURCE_TICK, GEOMETRY_FRAME_ID, censuses));

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));
		JsonObject payloads = jsonObject(response.get("payloads"));

		assertEquals("PASS", response.get("status"));
		assertTrue(payloads.has("scene_object_census"));
		JsonObject sceneCensus = payloads.getAsJsonObject("scene_object_census");
		assertEquals(SOURCE_TICK, sceneCensus.get("sourceTick").getAsLong());
		assertEquals(SESSION_ID, sceneCensus.get("sessionId").getAsString());
		assertEquals(CLIENT_PROCESS_ID, sceneCensus.get("clientProcessId").getAsLong());
		assertEquals(GEOMETRY_FRAME_ID, sceneCensus.get("geometryFrameId").getAsString());
		Instant.parse(sceneCensus.get("capturedAtUtc").getAsString());
	}

	@Test
	public void compactWorldModelEnvelopeRetainsClientThreadQueryDiagnostics()
	{
		PluginLiveCache cache = canonicalCache();
		Map<String, Object> providerResponse = new java.util.LinkedHashMap<>(worldModelResponse(
				SOURCE_TICK,
				GEOMETRY_FRAME_ID,
				Map.of("scene_object_census", Map.of("objects", List.of()))));
		providerResponse.put("queryDiagnostics", Map.of(
				"schema", ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA,
				"requestStatus", "SUCCESS",
				"activeRequestCount", 0,
				"pendingRequestCount", 0));
		providerResponse.put("pipeline", Map.of(
				"scannedTiles", 81,
				"discoveredObjects", 14,
				"enrichedObjects", 6));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				cache, gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null, (needs, request) -> providerResponse);

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));
		JsonObject worldModel = jsonObject(response.get("worldModel"));
		JsonObject diagnostics = worldModel.getAsJsonObject("queryDiagnostics");
		JsonObject pipeline = worldModel.getAsJsonObject("pipeline");

		assertEquals(ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA, diagnostics.get("schema").getAsString());
		assertEquals("SUCCESS", diagnostics.get("requestStatus").getAsString());
		assertEquals(0, diagnostics.get("activeRequestCount").getAsInt());
		assertEquals(0, diagnostics.get("pendingRequestCount").getAsInt());
		assertEquals(81, pipeline.get("scannedTiles").getAsInt());
		assertEquals(14, pipeline.get("discoveredObjects").getAsInt());
		assertEquals(6, pipeline.get("enrichedObjects").getAsInt());
	}

	@Test
	public void queryDiagnosticsSurviveWorldModelProvenanceRejection()
	{
		Map<String, Object> providerResponse = new java.util.LinkedHashMap<>(worldModelResponse(
				SOURCE_TICK - 1L,
				GEOMETRY_FRAME_ID,
				Map.of("scene_object_census", Map.of("objects", List.of()))));
		providerResponse.put("queryDiagnostics", Map.of(
				"schema", ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA,
				"requestStatus", "LATE",
				"lateResultCount", 1L));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null, (needs, request) -> providerResponse);

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));
		Map<?, ?> sizing = (Map<?, ?>) response.get("responseSizing");
		Map<?, ?> diagnostics = (Map<?, ?>) sizing.get("worldModelQueryDiagnostics");

		assertFalse(response.containsKey("worldModel"));
		assertEquals(ClientThreadQueryScheduler.DIAGNOSTICS_SCHEMA, diagnostics.get("schema"));
		assertEquals("LATE", diagnostics.get("requestStatus"));
		assertEquals(1L, diagnostics.get("lateResultCount"));
	}

	@Test
	public void actorAndCollisionNeedsAreNormalizedBoundedAndStampedToTheAtomicFrame()
	{
		AtomicReference<List<String>> requestedNeeds = new AtomicReference<>();
		Map<String, Object> actor = Map.of(
				"type", "NPC",
				"index", 12,
				"id", 123,
				"name", "Guide",
				"actions", List.of("Talk-to"),
				"distanceToPlayer", 2);
		Map<String, Object> actorCensus = Map.of(
				"schema", "world_model_actor_census.v1",
				"count", 1,
				"returned", 1,
				"capHit", false,
				"actors", List.of(actor));
		Map<String, Object> collisionWindow = Map.of(
				"schema", "world_model_collision_window.v1",
				"collisionAvailable", true,
				"cellCount", 1,
				"cellCapHit", false,
				"cells", List.of(Map.of(
						"worldX", 3200,
						"worldY", 3230,
						"plane", 0,
						"sceneX", 50,
						"sceneY", 50,
						"flags", 0,
						"blockedMovement", false)));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) ->
				{
					requestedNeeds.set(List.copyOf(needs));
					return worldModelResponse(
							SOURCE_TICK,
							GEOMETRY_FRAME_ID,
							Map.of(
									"actor_census", actorCensus,
									"collision_window", collisionWindow));
				});

		Map<String, Object> response = endpoint.snapshotPayload(request("actorCensus", "collisionWindow"));
		JsonObject payloads = jsonObject(response.get("payloads"));
		JsonObject actors = payloads.getAsJsonObject("actor_census");
		JsonObject collision = payloads.getAsJsonObject("collision_window");

		assertEquals("PASS", response.get("status"));
		assertEquals(List.of("actor_census", "collision_window"), requestedNeeds.get());
		for (JsonObject payload : List.of(actors, collision))
		{
			assertEquals(SOURCE_TICK, payload.get("sourceTick").getAsLong());
			assertEquals(SESSION_ID, payload.get("sessionId").getAsString());
			assertEquals(CLIENT_PROCESS_ID, payload.get("clientProcessId").getAsLong());
			assertEquals(GEOMETRY_FRAME_ID, payload.get("geometryFrameId").getAsString());
			Instant.parse(payload.get("capturedAtUtc").getAsString());
		}
		assertEquals("NPC", actors.getAsJsonArray("actors").get(0).getAsJsonObject().get("type").getAsString());
		assertEquals(1, collision.getAsJsonArray("cells").size());
	}

	@Test
	public void actorAndCollisionEvidenceFromAnotherTickIsRejectedTogether()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(
						SOURCE_TICK - 1L,
						GEOMETRY_FRAME_ID,
						Map.of(
								"actor_census", Map.of("actors", List.of()),
								"collision_window", Map.of("cells", List.of()))));

		Map<String, Object> response = endpoint.snapshotPayload(request("actor_census", "collision_window"));
		JsonObject payloads = jsonObject(response.get("payloads"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(payloads.has("actor_census"));
		assertFalse(payloads.has("collision_window"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("actor_census"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("collision_window"));
		assertTrue(((List<?>) response.get("warnings")).contains("world_model_provenance_mismatch"));
	}

	@Test
	public void missingSceneObjectCensusFailsClosed()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(SOURCE_TICK, GEOMETRY_FRAME_ID, Map.of()));

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(jsonObject(response.get("payloads")).has("scene_object_census"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("scene_object_census"));
		assertTrue(((List<?>) response.get("warnings")).contains(
				"world_model_payload_unavailable:scene_object_census"));
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
						"payloads", Map.of("scene_object_census", Map.of("objects", List.of())),
						"quality", Map.of("worldModelAvailable", true),
						"warnings", List.of(),
						"sizing", Map.of()));

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(jsonObject(response.get("payloads")).has("scene_object_census"));
		assertTrue(((List<?>) response.get("warnings")).contains("world_model_provenance_mismatch"));
	}

	@Test
	public void sceneObjectCensusSourceTickMismatchIsRejected()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> worldModelResponse(
						SOURCE_TICK - 1L,
						GEOMETRY_FRAME_ID,
						Map.of("scene_object_census", Map.of("objects", List.of()))));

		Map<String, Object> response = endpoint.snapshotPayload(request("scene_object_census"));

		assertEquals("FAIL", response.get("status"));
		assertFalse(jsonObject(response.get("payloads")).has("scene_object_census"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("scene_object_census"));
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

	@Test
	public void concurrentSnapshotOverloadFailsFastWithBoundedQueueDiagnostics() throws Exception
	{
		CountDownLatch providerEntered = new CountDownLatch(1);
		CountDownLatch releaseProvider = new CountDownLatch(1);
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) ->
				{
					providerEntered.countDown();
					try
					{
						if (!releaseProvider.await(2, TimeUnit.SECONDS))
						{
							throw new IllegalStateException("test provider release timed out");
						}
					}
					catch (InterruptedException e)
					{
						Thread.currentThread().interrupt();
						throw new IllegalStateException("test provider interrupted", e);
					}
					return worldModelResponse(
							SOURCE_TICK,
							GEOMETRY_FRAME_ID,
							Map.of("scene_object_census", Map.of("objects", List.of())));
				});
		ExecutorService clients = Executors.newSingleThreadExecutor();
		endpoint.start();
		try
		{
			Future<Integer> first = clients.submit(() -> httpStatus(
					endpoint,
					"POST",
					"/snapshot",
					"{\"needs\":[\"scene_object_census\"]}"));
			assertTrue(providerEntered.await(2, TimeUnit.SECONDS));

			assertEquals(503, httpStatus(
					endpoint,
					"POST",
					"/snapshot",
					"{\"needs\":[\"scene_object_census\"]}"));
			Map<String, Object> diagnostics = endpoint.endpointQueueDiagnostics();
			assertEquals(PluginSnapshotEndpoint.ENDPOINT_QUEUE_DIAGNOSTICS_SCHEMA, diagnostics.get("schema"));
			assertEquals(PluginSnapshotEndpoint.ENDPOINT_WORKER_LIMIT, diagnostics.get("workerLimit"));
			assertEquals(PluginSnapshotEndpoint.ENDPOINT_PENDING_CAPACITY, diagnostics.get("pendingCapacity"));
			assertEquals(1L, diagnostics.get("snapshotBusyRejectionCount"));
			assertTrue((Boolean) diagnostics.get("snapshotRequestActive"));

			releaseProvider.countDown();
			assertEquals(200, (int) first.get(2, TimeUnit.SECONDS));
		}
		finally
		{
			releaseProvider.countDown();
			clients.shutdownNow();
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
		return completeFrame(
				frameId,
				sourceTick,
				sessionId,
				clientProcessId,
				geometryFrameId,
				baseline,
				Instant.now().toString());
	}

	private SensorFrame completeFrame(
			String frameId,
			long sourceTick,
			String sessionId,
			long clientProcessId,
			String geometryFrameId,
			Map<String, Object> baseline,
			String capturedAtUtc)
	{
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

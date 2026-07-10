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
		assertTrue(String.valueOf(schema.get("readOnlyStatement")).contains("no configuration"));
	}

	@Test
	public void snapshotReturnsCanonicalCachedObservationPayloads()
	{
		PluginLiveCache cache = canonicalCache();
		Map<String, Object> response = endpoint(cache).snapshotPayload(request(
				"baseline", "inventory", "activity", "bank_ui", "dialogue_state"));

		assertEquals("PASS", response.get("status"));
		JsonObject payloads = gson.toJsonTree(response.get("payloads")).getAsJsonObject();
		assertTrue(payloads.has("baseline"));
		assertTrue(payloads.has("inventory"));
		assertTrue(payloads.has("activity"));
		assertTrue(payloads.has("bank_ui"));
		assertTrue(payloads.has("dialogue_state"));
	}

	@Test
	public void missingCanonicalPayloadFailsClosed()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_baseline_packet.v1", 3L, Instant.now().toString(), Map.of("gameState", "LOGIN_SCREEN"));

		Map<String, Object> response = endpoint(cache).snapshotPayload(request("baseline", "inventory"));

		assertEquals("WARN", response.get("status"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("inventory"));
	}

	@Test
	public void loginScreenBaselineRoundTripsOwningProcessWithoutLoadedScene()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_baseline_packet.v1", 0L, Instant.now().toString(), Map.of(
				"gameState", "LOGIN_SCREEN",
				"player", Map.of(),
				"inputGeometry", Map.of(
						"geometryAvailable", false,
						"reason", "canvas_not_showing",
						"clientProcessId", 4321L)));

		Map<String, Object> response = endpoint(cache).snapshotPayload(request("baseline"));
		JsonObject baseline = gson.toJsonTree(response.get("payloads")).getAsJsonObject().getAsJsonObject("baseline");

		assertEquals("LOGIN_SCREEN", baseline.get("gameState").getAsString());
		assertEquals(4321L, baseline.getAsJsonObject("inputGeometry").get("clientProcessId").getAsLong());
	}

	@Test
	public void interactionHotAndTileProjectionShareSnapshotResponse()
	{
		PluginLiveCache cache = canonicalCache();
		ClientTickHotState hot = new ClientTickHotState(4);
		hot.recordClientTick(Map.of("clientTick", 10L, "gameState", "LOGGED_IN", "clientProcessId", 1234L));
		hot.recordPostMenuSort(Map.of("clientTick", 10L, "topOption", "Walk here", "topTarget", ""));
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				cache, gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false, hot,
				requests -> Map.of("schema", "tile_projection_response.v1", "status", "PASS", "tiles", List.of(Map.of(
						"label", "route", "worldX", 3200, "worldY", 3230, "plane", 0,
						"geometryAvailable", true, "onScreen", true, "visible", true, "actionable", true))),
				null);
		JsonObject request = request("baseline", "interaction_hot");
		JsonArray tiles = new JsonArray();
		JsonObject tile = new JsonObject();
		tile.addProperty("label", "route");
		tile.addProperty("worldX", 3200);
		tile.addProperty("worldY", 3230);
		tile.addProperty("plane", 0);
		tiles.add(tile);
		request.add("tileProjectionRequests", tiles);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject payloads = gson.toJsonTree(response.get("payloads")).getAsJsonObject();
		assertTrue(payloads.has("interaction_hot"));
		assertTrue(payloads.has("tile_projection"));
	}

	@Test
	public void worldModelProviderIsLimitedToThreeObjectCensuses()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				canonicalCache(), gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false,
				new ClientTickHotState(4), null,
				(needs, request) -> Map.of(
						"schema", WorldModelCache.SCHEMA,
						"status", "PASS",
						"payloads", Map.of(
								"resource_object_census", Map.of("objects", List.of()),
								"route_object_census", Map.of("objects", List.of()),
								"service_object_census", Map.of("objects", List.of())),
						"quality", Map.of("worldModelAvailable", true)));

		Map<String, Object> response = endpoint.snapshotPayload(request(
				"resource_object_census", "route_object_census", "service_object_census"));
		JsonObject payloads = gson.toJsonTree(response.get("payloads")).getAsJsonObject();
		assertTrue(payloads.has("resource_object_census"));
		assertTrue(payloads.has("route_object_census"));
		assertTrue(payloads.has("service_object_census"));
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

	private PluginLiveCache canonicalCache()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		String now = Instant.now().toString();
		cache.update("live_baseline_packet.v1", 8L, now, Map.of("gameState", "LOGGED_IN"));
		cache.update("live_inventory_packet.v1", 8L, now, Map.of("inventory", Map.of("known", true)));
		cache.update("live_activity_packet.v1", 8L, now, Map.of("animation", -1));
		cache.update("live_bank_ui_packet.v1", 8L, now, Map.of("known", true, "bankOpen", false));
		cache.update("live_dialogue_state_packet.v1", 8L, now, Map.of("active", false));
		return cache;
	}

	private PluginSnapshotEndpoint endpoint(PluginLiveCache cache)
	{
		return new PluginSnapshotEndpoint(cache, gson, "127.0.0.1", 0, "", 50, 1024 * 1024, false);
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
		return request;
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

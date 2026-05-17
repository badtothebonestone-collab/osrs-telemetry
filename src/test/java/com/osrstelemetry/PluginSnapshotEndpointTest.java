package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class PluginSnapshotEndpointTest
{
	private final Gson gson = new Gson();

	@Test
	public void configDefaultsKeepSnapshotEndpointDisabled()
	{
		TelemetryConfig config = new TelemetryConfig()
		{
		};

		assertFalse(config.enablePluginSnapshotEndpoint());
		assertEquals("127.0.0.1", config.pluginSnapshotHost());
		assertEquals(8893, config.pluginSnapshotPort());
		assertFalse(config.pluginSnapshotAllowNonLocalHost());
		assertFalse(config.pluginSnapshotEnabledInNormalLive());
		assertEquals(TelemetryWorkflowPreset.DAILY_LIVE, config.workflowPreset());
		assertFalse(config.applyWorkflowPreset());
		assertFalse(config.presetPreviewOnly());
	}

	@Test
	public void schemaReportsSupportedNeedsAndReadOnlyLimits()
	{
		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				new PluginLiveCache(gson),
				gson,
				"127.0.0.1",
				8893,
				"",
				100,
				1024 * 1024,
				false);

		Map<String, Object> schema = endpoint.schemaPayload();
		assertEquals("plugin_snapshot_schema.v1", schema.get("schema"));
		assertTrue(((List<?>) schema.get("supportedNeeds")).contains("projection"));
		assertTrue(schema.containsKey("configLimits"));
		assertTrue(((List<?>) schema.get("supportedSchemas")).contains("telemetry_preset_request.v1"));
		assertTrue(((List<?>) schema.get("supportedPresets")).contains("DAILY_LIVE"));
		assertTrue(((List<?>) schema.get("presetEndpoints")).contains("POST /preset/apply"));
	}

	@Test
	public void presetsEndpointPayloadListsFixedPresets()
	{
		PluginSnapshotEndpoint endpoint = endpoint(new PluginLiveCache(gson), 50, 1024 * 1024, new TelemetryPresetApplier(new FakeConfigStore()));

		Map<String, Object> response = endpoint.presetsPayload();

		assertEquals("telemetry_presets.v1", response.get("schema"));
		assertTrue(((List<?>) response.get("presets")).contains("DAILY_LIVE"));
		assertEquals(Boolean.TRUE, response.get("readOnlyGameState"));
	}

	@Test
	public void presetPreviewAndApplyUseWhitelistedPresetOnly()
	{
		FakeConfigStore store = new FakeConfigStore();
		PluginSnapshotEndpoint endpoint = endpoint(new PluginLiveCache(gson), 50, 1024 * 1024, new TelemetryPresetApplier(store));
		JsonObject request = new JsonObject();
		request.addProperty("schema", "telemetry_preset_request.v1");
		request.addProperty("preset", "DAILY_LIVE");
		request.addProperty("arbitraryKey", "ignored");

		Map<String, Object> preview = endpoint.presetPayload(request, true);
		assertEquals("PASS", preview.get("status"));
		assertTrue(store.values.isEmpty());

		Map<String, Object> apply = endpoint.presetPayload(request, false);
		assertEquals("PASS", apply.get("status"));
		assertEquals("LIVE_COMPACT_ONLY", store.values.get("telemetryRecordingMode"));
		assertEquals("false", store.values.get("emitCompactLiveStream"));
		assertFalse(store.values.containsKey("arbitraryKey"));
	}

	@Test
	public void unknownPresetRequestIsRejected()
	{
		PluginSnapshotEndpoint endpoint = endpoint(new PluginLiveCache(gson), 50, 1024 * 1024, new TelemetryPresetApplier(new FakeConfigStore()));
		JsonObject request = new JsonObject();
		request.addProperty("preset", "MUTATE_ANYTHING");

		Map<String, Object> response = endpoint.presetPayload(request, false);

		assertEquals("FAIL", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("unknown preset"));
	}

	@Test
	public void snapshotReturnsRequestedCachedPayloads()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_baseline_packet.v1", 4L, "2026-05-11T00:00:00Z", Map.of("gameState", "LOGGED_IN"));
		cache.update("live_inventory_packet.v1", 4L, "2026-05-11T00:00:00Z", Map.of("freeSlots", 12));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 50, 1024 * 1024);
		JsonObject request = new JsonObject();
		JsonArray needs = new JsonArray();
		needs.add("baseline");
		needs.add("inventory");
		request.add("needs", needs);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		Map<String, JsonElement> payloads = payloads(response);

		assertEquals("PASS", response.get("status"));
		assertTrue(payloads.containsKey("baseline"));
		assertTrue(payloads.containsKey("inventory"));
		assertEquals(4L, response.get("latestTick"));
	}

	@Test
	public void missingPayloadReturnsMissingCapabilities()
	{
		PluginSnapshotEndpoint endpoint = endpoint(new PluginLiveCache(gson), 50, 1024 * 1024);
		JsonObject request = new JsonObject();
		JsonArray needs = new JsonArray();
		needs.add("navigation");
		request.add("needs", needs);

		Map<String, Object> response = endpoint.snapshotPayload(request);

		assertEquals("FAIL", response.get("status"));
		assertTrue(((List<?>) response.get("missingCapabilities")).contains("navigation"));
	}

	@Test
	public void projectionRefsAreCappedAndCompacted()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", projectionPayload(3));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 2, 1024 * 1024);
		JsonObject request = new JsonObject();
		JsonArray needs = new JsonArray();
		needs.add("projection");
		request.add("needs", needs);
		request.addProperty("maxProjectionRefs", 1);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject projection = payloads(response).get("projection").getAsJsonObject();
		JsonArray refs = projection.getAsJsonArray("visibleObjectRefs");
		JsonObject firstRef = refs.get(0).getAsJsonObject();

		assertEquals("WARN", response.get("status"));
		assertEquals(1, refs.size());
		assertTrue(((List<?>) response.get("warnings")).contains("projection refs capped"));
		assertTrue(firstRef.has("actions"));
		assertFalse(firstRef.has("clickableHull"));
		assertFalse(firstRef.has("clickboxPolygon"));
		assertFalse(firstRef.has("canvasTilePolygon"));
		assertTrue(firstRef.has("bounds"));
	}

	@Test
	public void cappedCompactProjectionCanFitEvenWhenCachedProjectionIsLarge()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", heavyProjectionPayload(80, 250));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 80, 8192);
		JsonObject request = projectionRequest(1);

		Map<String, Object> response = endpoint.boundedSnapshotPayload(request);
		JsonObject projection = payloads(response).get("projection").getAsJsonObject();
		JsonArray refs = projection.getAsJsonArray("visibleObjectRefs");
		@SuppressWarnings("unchecked")
		Map<String, Object> sizing = (Map<String, Object>) response.get("responseSizing");

		assertEquals("WARN", response.get("status"));
		assertEquals(1, refs.size());
		assertTrue(((Number) sizing.get("cachedProjectionBytes")).longValue() > ((Number) sizing.get("estimatedResponseBytes")).longValue());
		assertEquals(Boolean.TRUE, sizing.get("projectionCapApplied"));
		assertFalse("response_too_large".equals(response.get("errorCode")));
	}

	@Test
	public void compactProjectionOmitHeavyFields()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", heavyProjectionPayload(1, 200));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 10, 1024 * 1024);
		JsonObject request = projectionRequest(1);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject ref = payloads(response)
				.get("projection")
				.getAsJsonObject()
				.getAsJsonArray("visibleObjectRefs")
				.get(0)
				.getAsJsonObject();

		assertTrue(ref.has("objectKey"));
		assertTrue(ref.has("id"));
		assertTrue(ref.has("bounds"));
		assertFalse(ref.has("geometrySummary"));
		assertFalse(ref.has("source"));
		assertFalse(ref.has("firstSeenTick"));
		assertTrue(ref.has("actions"));
		assertFalse(ref.has("clickableHull"));
	}

	@Test
	public void projectionRefsArePrioritizedBeforeCapping()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		List<Map<String, Object>> refs = new java.util.ArrayList<>();
		refs.add(projectionRef("offscreen", false, "tile", false, false));
		refs.add(projectionRef("candidate", true, "sceneObject", true, true));
		refs.add(projectionRef("other", true, "sceneObject", false, true));
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", projectionPayloadFromRefs(refs));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 3, 1024 * 1024);
		JsonObject request = projectionRequest(1);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject ref = payloads(response)
				.get("projection")
				.getAsJsonObject()
				.getAsJsonArray("visibleObjectRefs")
				.get(0)
				.getAsJsonObject();
		@SuppressWarnings("unchecked")
		Map<String, Object> sizing = (Map<String, Object>) response.get("responseSizing");

		assertEquals("candidate", ref.get("objectKey").getAsString());
		assertEquals(Boolean.TRUE, sizing.get("projectionPriorityApplied"));
	}

	@Test
	public void projectionHintsPrioritizeMatchingClassBeforeCapping()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		List<Map<String, Object>> refs = new java.util.ArrayList<>();
		refs.add(projectionRef("ore-rock", true, "sceneObject", true, true));
		refs.get(0).put("name", "Copper rock");
		refs.add(projectionRef("oak-tree", true, "sceneObject", true, true));
		refs.get(1).put("name", "Oak tree");
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", projectionPayloadFromRefs(refs));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 2, 1024 * 1024);
		JsonObject request = projectionRequest(1);
		request.addProperty("snapshotTier", "hot");
		request.addProperty("profileHint", "woodcutting");
		request.addProperty("classHint", "tree");
		request.addProperty("targetTypeHint", "sceneObject");
		request.addProperty("requireOnScreen", true);
		request.addProperty("requireGeometryAvailable", true);
		JsonArray desired = new JsonArray();
		desired.add("tree");
		request.add("desiredClasses", desired);

		Map<String, Object> response = endpoint.snapshotPayload(request);
		JsonObject ref = payloads(response)
				.get("projection")
				.getAsJsonObject()
				.getAsJsonArray("visibleObjectRefs")
				.get(0)
				.getAsJsonObject();
		@SuppressWarnings("unchecked")
		Map<String, Object> sizing = (Map<String, Object>) response.get("responseSizing");

		assertEquals("oak-tree", ref.get("objectKey").getAsString());
		assertEquals("hot", response.get("snapshotTier"));
		assertTrue(((Map<?, ?>) sizing.get("projectionHintsApplied")).containsKey("classHint"));
	}

	@Test
	public void unknownProjectionHintsAreIgnoredSafely()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", projectionPayload(1));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 1, 1024 * 1024);
		JsonObject request = projectionRequest(1);
		request.addProperty("unknownHint", "ignored");

		Map<String, Object> response = endpoint.snapshotPayload(request);

		assertEquals("PASS", response.get("status"));
		assertEquals("hot", response.get("snapshotTier"));
	}

	@Test
	public void cappedResponseStillFailsWhenCompactResponseExceedsLimit()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_projection_packet.v1", 5L, "2026-05-11T00:00:00Z", heavyProjectionPayload(50, 1000));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 50, 8192);
		JsonObject request = projectionRequest(50);

		Map<String, Object> response = endpoint.boundedSnapshotPayload(request);

		assertEquals("plugin_snapshot_response.v1", response.get("schema"));
		assertEquals("FAIL", response.get("status"));
		assertEquals("response_too_large", response.get("errorCode"));
		assertTrue(response.containsKey("responseSizing"));
		assertTrue(((List<?>) response.get("warnings")).contains("responseTooLarge"));
	}

	@Test
	public void stalePayloadProducesWarnStatus()
	{
		PluginLiveCache cache = new PluginLiveCache(gson);
		cache.update("live_baseline_packet.v1", 10L, "2026-05-11T00:00:00Z", Map.of("gameState", "LOGGED_IN"));
		cache.update("live_inventory_packet.v1", 15L, "2026-05-11T00:00:01Z", Map.of("freeSlots", 1));
		PluginSnapshotEndpoint endpoint = endpoint(cache, 50, 1024 * 1024);
		JsonObject request = new JsonObject();
		JsonArray needs = new JsonArray();
		needs.add("baseline");
		request.add("needs", needs);
		request.addProperty("maxAgeTicks", 1);

		Map<String, Object> response = endpoint.snapshotPayload(request);

		assertEquals("WARN", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("baseline cache age exceeded maxAgeTicks"));
	}

	private PluginSnapshotEndpoint endpoint(PluginLiveCache cache, int maxProjectionRefs, int maxResponseBytes)
	{
		return endpoint(cache, maxProjectionRefs, maxResponseBytes, null);
	}

	private PluginSnapshotEndpoint endpoint(
			PluginLiveCache cache,
			int maxProjectionRefs,
			int maxResponseBytes,
			TelemetryPresetApplier presetApplier)
	{
		return new PluginSnapshotEndpoint(
				cache,
				gson,
				"127.0.0.1",
				8893,
				"",
				maxProjectionRefs,
				maxResponseBytes,
				false,
				presetApplier);
	}

	@SuppressWarnings("unchecked")
	private Map<String, JsonElement> payloads(Map<String, Object> response)
	{
		return (Map<String, JsonElement>) response.get("payloads");
	}

	private Map<String, Object> projectionPayload(int count)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<Map<String, Object>> refs = new java.util.ArrayList<>();
		for (int i = 0; i < count; i++)
		{
			Map<String, Object> ref = new LinkedHashMap<>();
			ref.put("objectKey", "tree:" + i);
			ref.put("targetType", "sceneObject");
			ref.put("id", 1276 + i);
			ref.put("hash", 1000 + i);
			ref.put("worldX", 3200 + i);
			ref.put("worldY", 3200);
			ref.put("plane", 0);
			ref.put("sceneX", 20 + i);
			ref.put("sceneY", 20);
			ref.put("onScreen", true);
			ref.put("geometryAvailable", true);
			ref.put("aimPoint", Map.of("x", 100 + i, "y", 200));
			ref.put("actions", List.of("Chop down"));
			ref.put("clickableHull", Map.of("points", List.of(Map.of("x", 1, "y", 2))));
			ref.put("clickboxPolygon", Map.of("points", List.of(Map.of("x", 1, "y", 2))));
			ref.put("canvasTilePolygon", Map.of("points", List.of(Map.of("x", 3, "y", 4))));
			ref.put("bounds", Map.of("x", 10 + i, "y", 20, "width", 12, "height", 14));
			refs.add(ref);
		}
		payload.put("visibleObjectRefs", refs);
		return payload;
	}

	private JsonObject projectionRequest(int maxRefs)
	{
		JsonObject request = new JsonObject();
		JsonArray needs = new JsonArray();
		needs.add("projection");
		request.add("needs", needs);
		request.addProperty("maxProjectionRefs", maxRefs);
		request.addProperty("responseMode", "compact");
		request.addProperty("projectionFieldMode", "compact");
		return request;
	}

	private Map<String, Object> heavyProjectionPayload(int count, int nameChars)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<Map<String, Object>> refs = new java.util.ArrayList<>();
		for (int i = 0; i < count; i++)
		{
			Map<String, Object> ref = projectionRef("heavy:" + i, true, "sceneObject", true, true);
			ref.put("name", "Oak tree " + "x".repeat(nameChars));
			ref.put("actions", List.of("Chop down"));
			ref.put("source", "debug-source-" + "y".repeat(nameChars));
			ref.put("firstSeenTick", i);
			ref.put("geometrySummary", Map.of(
					"debug", "z".repeat(nameChars),
					"clickboxBounds", Map.of("x", 1, "y", 2, "width", 3, "height", 4)));
			ref.put("clickableHull", Map.of("points", List.of(Map.of("x", 1, "y", 2))));
			refs.add(ref);
		}
		payload.put("visibleObjectRefs", refs);
		return payload;
	}

	private Map<String, Object> projectionPayloadFromRefs(List<Map<String, Object>> refs)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("visibleObjectRefs", refs);
		return payload;
	}

	private Map<String, Object> projectionRef(
			String objectKey,
			boolean onScreen,
			String targetType,
			boolean geometryAvailable,
			boolean stable)
	{
		Map<String, Object> ref = new LinkedHashMap<>();
		ref.put("objectKey", objectKey);
		ref.put("targetType", targetType);
		if (stable)
		{
			ref.put("id", 10820);
			ref.put("hash", objectKey.hashCode());
			ref.put("worldX", 3200);
			ref.put("worldY", 3200);
			ref.put("plane", 0);
			ref.put("sceneX", 20);
			ref.put("sceneY", 20);
		}
		ref.put("name", "Oak tree");
		ref.put("onScreen", onScreen);
		ref.put("geometryAvailable", geometryAvailable);
		ref.put("aimPoint", Map.of("x", 100, "y", 200));
		ref.put("bounds", Map.of("x", 90, "y", 190, "width", 20, "height", 20));
		ref.put("geometrySource", "bounds");
		ref.put("present", true);
		return ref;
	}

	private static final class FakeConfigStore implements TelemetryPresetApplier.ConfigStore
	{
		private final Map<String, String> values = new LinkedHashMap<>();

		@Override
		public String get(String group, String key)
		{
			return values.get(key);
		}

		@Override
		public void set(String group, String key, Object value)
		{
			values.put(key, value instanceof Enum<?> ? ((Enum<?>) value).name() : String.valueOf(value));
		}
	}
}

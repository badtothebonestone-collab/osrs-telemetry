package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Regenerates the deterministic Java-to-Python snapshot contract fixture. */
public final class JavaSnapshotFixture
{
	static final Path DEFAULT_OUTPUT = Path.of(
			"tests",
			"fixtures",
			"java_snapshot_endpoint.json");
	static final long SOURCE_TICK = 4_242L;
	static final String FRAME_CAPTURED_AT = "2025-01-01T00:00:00Z";
	static final String FRAME_COMPLETED_AT = "2025-01-01T00:00:00.010Z";
	static final Instant ASSEMBLED_AT = Instant.parse("2025-01-01T00:00:00.100Z");

	private static final Gson GSON = new Gson();
	private static final Gson PRETTY_GSON = new GsonBuilder().setPrettyPrinting().create();

	private JavaSnapshotFixture()
	{
	}

	public static void main(String[] args) throws IOException
	{
		Path output = args.length == 0 ? DEFAULT_OUTPUT : Path.of(args[0]);
		Path parent = output.toAbsolutePath().getParent();
		if (parent != null)
		{
			Files.createDirectories(parent);
		}
		Files.writeString(
				output,
				generatedFixtureText(),
				StandardCharsets.UTF_8);
	}

	static String generatedFixtureText()
	{
		return PRETTY_GSON.toJson(generatedFixture()) + "\n";
	}

	static JsonObject generatedFixture()
	{
		PluginLiveCache cache = new PluginLiveCache(GSON);
		if (!cache.publishAt(sensorFrame(), Instant.parse(FRAME_COMPLETED_AT)))
		{
			throw new IllegalStateException("fixture SensorFrame publication failed");
		}

		PluginSnapshotEndpoint endpoint = new PluginSnapshotEndpoint(
				cache,
				GSON,
				"127.0.0.1",
				8893,
				"",
				16,
				1024 * 1024,
				false,
				null,
				null,
				null,
				null,
				null,
				() -> ASSEMBLED_AT);

		JsonObject request = new JsonObject();
		request.addProperty("schema", PluginSnapshotEndpoint.REQUEST_SCHEMA);
		request.addProperty("requestId", "java-snapshot-fixture-v1");
		request.add("needs", GSON.toJsonTree(SensorFrame.CORE_FACT_NAMES));
		request.addProperty("includeGeometry", true);
		request.addProperty("includeCollisionWindow", false);
		request.addProperty("maxSourceAgeMillis", 2_000L);

		JsonObject fixture = GSON.toJsonTree(endpoint.snapshotPayload(request)).getAsJsonObject();
		// The endpoint measures this with System.nanoTime(); zero only that duration.
		fixture.addProperty("serviceTimingMillis", 0L);
		return fixture;
	}

	private static SensorFrame sensorFrame()
	{
		Map<String, Object> baseline = ordered(
				"tick", SOURCE_TICK,
				"gameState", "LOGGED_IN",
				"scenePlayable", true,
				"player", ordered(
						"worldX", 3_192,
						"worldY", 3_244,
						"plane", 0,
						"animation", -1,
						"poseAnimation", 808,
						"runEnergyPercent", 100.0,
						"interacting", ordered("type", "UNKNOWN", "id", -1)),
				"cameraViewport", ordered(
						"canvasWidth", 800,
						"canvasHeight", 600,
						"cameraYaw", 512,
						"cameraPitch", 256,
						"zoom3d", 384),
				"inputGeometry", ordered(
						"schema", "input_geometry.v1",
						"sourceTick", SOURCE_TICK,
						"geometryAvailable", true,
						"canvasWidth", 800,
						"canvasHeight", 600,
						"sourceCanvasWidth", 800,
						"sourceCanvasHeight", 600,
						"coordinateSpace", "device_pixels",
						"canvasScreenX", 100,
						"canvasScreenY", 200,
						"isCanvasShowing", true,
						"isClientFocused", true,
						"clientProcessId", 1_234L));
		Map<String, Object> inventory = ordered(
				"inventory", ordered(
						"slotCount", 28,
						"known", true,
						"freeSlots", 28,
						"filledSlots", 0,
						"occupiedSlots", 0,
						"items", List.of()));
		Map<String, Object> activity = ordered(
				"animation", -1,
				"poseAnimation", 808,
				"runEnergyPercent", 100.0,
				"interacting", ordered("type", "UNKNOWN", "id", -1));
		Map<String, Object> bankUi = ordered(
				"known", true,
				"bankOpen", false,
				"bankPinOpen", false,
				"bankContainerVisible", false,
				"depositInventoryButtonVisible", false,
				"closeButtonVisible", false,
				"bankReadable", false,
				"keyboardClosePossible", false);
		Map<String, Object> dialogue = ordered(
				"active", false,
				"type", "none",
				"promptText", "",
				"options", List.of(),
				"canUseNumberKeys", false);

		SensorFrame.Builder builder = SensorFrame.builder(
				"java-fixture-session:4242",
				SOURCE_TICK,
				123_456_789L,
				FRAME_CAPTURED_AT)
				.completedAtUtc(FRAME_COMPLETED_AT)
				.captureDurationMillis(10L)
				.sessionId("java-fixture-session")
				.clientProcessId(1_234L)
				.geometryFrameId("java-fixture-geometry-4242");
		List<Map<String, Object>> payloads = List.of(
				baseline,
				inventory,
				activity,
				bankUi,
				dialogue);
		for (int index = 0; index < SensorFrame.CORE_FACT_NAMES.size(); index++)
		{
			builder.fact(
					GSON,
					SensorFrame.CORE_FACT_NAMES.get(index),
					SOURCE_TICK,
					FRAME_COMPLETED_AT,
					true,
					List.of(),
					payloads.get(index));
		}
		return builder.build();
	}

	private static Map<String, Object> ordered(Object... entries)
	{
		if (entries.length % 2 != 0)
		{
			throw new IllegalArgumentException("ordered map entries must be key/value pairs");
		}
		Map<String, Object> result = new LinkedHashMap<>();
		for (int index = 0; index < entries.length; index += 2)
		{
			result.put(String.valueOf(entries[index]), entries[index + 1]);
		}
		return result;
	}
}

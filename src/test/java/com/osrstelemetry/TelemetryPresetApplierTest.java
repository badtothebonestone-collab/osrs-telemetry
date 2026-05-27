package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class TelemetryPresetApplierTest
{
	@Test
	public void previewReturnsExpectedDailyDiffWithoutWriting()
	{
		FakeConfigStore store = new FakeConfigStore();
		store.values.put("enablePluginSnapshotEndpoint", "true");
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		Map<String, Object> response = applier.preview("DAILY_LIVE");

		assertEquals("PASS", response.get("status"));
		assertTrue(changeFor(response, "enablePluginSnapshotEndpoint").contains("newValue=false"));
		assertEquals("true", store.values.get("enablePluginSnapshotEndpoint"));
	}

	@Test
	public void dailyPresetUsesLiveCacheWithoutPacketArchiveSettings()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("DAILY_LIVE");

		assertFalse(store.values.containsKey("telemetryRecordingMode"));
		assertFalse(store.values.containsKey("emitCompactLivePackets"));
		assertFalse(store.values.containsKey("emitCompactLiveStream"));
		assertFalse(store.values.containsKey("debugRecordRawTicks"));
		assertFalse(store.values.containsKey("debugRecordRawEvents"));
		assertFalse(store.values.containsKey("debugRecordFrames"));
		assertEquals("32", store.values.get("telemetryDebugOverlayMaxTargets"));
	}

	@Test
	public void visualQaPresetSetsOverlayFriendlyValues()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("VISUAL_QA");

		assertFalse(store.values.containsKey("telemetryRecordingMode"));
		assertEquals("true", store.values.get("telemetryDebugOverlayEnabled"));
		assertEquals("32", store.values.get("telemetryDebugOverlayMaxTargets"));
		assertEquals("CLICKABLE_HULL", store.values.get("telemetryDebugOverlayGeometryMode"));
		assertEquals("true", store.values.get("compactLiveIncludeClickableHull"));
		assertFalse(store.values.containsKey("emitCompactLiveStream"));
	}

	@Test
	public void debugAuditPresetIsRetired()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		Map<String, Object> response = applier.apply("DEBUG_AUDIT");

		assertEquals("FAIL", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("unknown preset"));
		assertTrue(store.values.isEmpty());
	}

	@Test
	public void pluginSnapshotExperimentalPresetIsRetired()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		Map<String, Object> response = applier.apply("PLUGIN_SNAPSHOT_EXPERIMENTAL");

		assertEquals("FAIL", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("unknown preset"));
		assertTrue(store.values.isEmpty());
	}

	@Test
	public void dailySnapshotNoFilePresetEnablesEndpointAndAvoidsLivePacketArchive()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("DAILY_SNAPSHOT_NO_FILE");

		assertFalse(store.values.containsKey("telemetryRecordingMode"));
		assertFalse(store.values.containsKey("emitCompactLivePackets"));
		assertFalse(store.values.containsKey("compactLivePacketsRequiredForLive"));
		assertFalse(store.values.containsKey("emitCompactLiveStream"));
		assertEquals("true", store.values.get("emitCompactNavigationPackets"));
		assertEquals("true", store.values.get("compactNavigationEmitCollisionWindow"));
		assertEquals("all", store.values.get("compactLivePacketTypes"));
		assertEquals("true", store.values.get("enablePluginSnapshotEndpoint"));
		assertFalse(store.values.containsKey("pluginSnapshotEnabledInNormalLive"));
		assertEquals("127.0.0.1", store.values.get("pluginSnapshotHost"));
		assertEquals("8893", store.values.get("pluginSnapshotPort"));
		assertFalse(store.values.containsKey("debugRecordRawTicks"));
		assertFalse(store.values.containsKey("debugRecordRawEvents"));
		assertFalse(store.values.containsKey("debugRecordFrames"));
		assertFalse(store.values.containsKey("captureScreenshots"));
		assertEquals("32", store.values.get("telemetryDebugOverlayMaxTargets"));
	}

	@Test
	public void unknownPresetIsRejected()
	{
		TelemetryPresetApplier applier = new TelemetryPresetApplier(new FakeConfigStore());

		Map<String, Object> response = applier.apply("SET_RANDOM_CONFIG");

		assertEquals("FAIL", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("unknown preset"));
	}

	@Test
	public void customPresetDoesNotChangeSettings()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		Map<String, Object> response = applier.apply("CUSTOM");

		assertEquals("WARN", response.get("status"));
		assertTrue(((List<?>) response.get("warnings")).contains("custom preset does not change settings"));
		assertTrue(store.values.isEmpty());
	}

	@Test
	public void presetApplierOnlyChangesWhitelistedKeys()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("DAILY_LIVE");

		for (String key : store.values.keySet())
		{
			assertFalse(key.contains("random"));
			assertFalse(key.contains("client"));
			assertFalse(key.contains("gameState"));
		}
		assertNull(store.values.get("arbitraryKey"));
	}

	private String changeFor(Map<String, Object> response, String key)
	{
		for (Object value : (List<?>) response.get("changes"))
		{
			@SuppressWarnings("unchecked")
			Map<String, Object> change = (Map<String, Object>) value;
			if (key.equals(change.get("key")))
			{
				return change.toString();
			}
		}
		return "";
	}

	private static final class FakeConfigStore implements TelemetryPresetApplier.ConfigStore
	{
		private final Map<String, String> values = new LinkedHashMap<>();

		@Override
		public String get(String group, String key)
		{
			assertEquals(TelemetryPresetApplier.CONFIG_GROUP, group);
			return values.get(key);
		}

		@Override
		public void set(String group, String key, Object value)
		{
			assertEquals(TelemetryPresetApplier.CONFIG_GROUP, group);
			values.put(key, value instanceof Enum<?> ? ((Enum<?>) value).name() : String.valueOf(value));
		}
	}
}

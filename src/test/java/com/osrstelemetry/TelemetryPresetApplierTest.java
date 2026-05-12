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
		store.values.put("emitCompactLiveStream", "true");
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		Map<String, Object> response = applier.preview("DAILY_LIVE");

		assertEquals("PASS", response.get("status"));
		assertTrue(changeFor(response, "emitCompactLiveStream").contains("newValue=false"));
		assertEquals("true", store.values.get("emitCompactLiveStream"));
	}

	@Test
	public void dailyPresetChangesCompactStreamOffAndCompactPacketsOn()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("DAILY_LIVE");

		assertEquals("LIVE_COMPACT_ONLY", store.values.get("telemetryRecordingMode"));
		assertEquals("true", store.values.get("emitCompactLivePackets"));
		assertEquals("false", store.values.get("emitCompactLiveStream"));
		assertEquals("false", store.values.get("debugRecordRawTicks"));
		assertEquals("false", store.values.get("debugRecordRawEvents"));
		assertEquals("false", store.values.get("debugRecordFrames"));
		assertEquals("10", store.values.get("telemetryDebugOverlayMaxTargets"));
	}

	@Test
	public void visualQaPresetSetsOverlayFriendlyValues()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("VISUAL_QA");

		assertEquals("LIVE_COMPACT_ONLY", store.values.get("telemetryRecordingMode"));
		assertEquals("true", store.values.get("telemetryDebugOverlayEnabled"));
		assertEquals("25", store.values.get("telemetryDebugOverlayMaxTargets"));
		assertEquals("CLICKABLE_HULL", store.values.get("telemetryDebugOverlayGeometryMode"));
		assertEquals("true", store.values.get("compactLiveIncludeClickableHull"));
		assertEquals("false", store.values.get("emitCompactLiveStream"));
	}

	@Test
	public void debugAuditPresetEnablesDebugRecording()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("DEBUG_AUDIT");

		assertEquals("DEBUG_RECORDING", store.values.get("telemetryRecordingMode"));
		assertEquals("true", store.values.get("debugRecordRawTicks"));
		assertEquals("true", store.values.get("debugRecordRawEvents"));
		assertEquals("true", store.values.get("debugRecordFrames"));
		assertEquals("true", store.values.get("captureScreenshots"));
	}

	@Test
	public void pluginSnapshotPresetEnablesEndpointButNotStream()
	{
		FakeConfigStore store = new FakeConfigStore();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(store);

		applier.apply("PLUGIN_SNAPSHOT_EXPERIMENTAL");

		assertEquals("LIVE_COMPACT_ONLY", store.values.get("telemetryRecordingMode"));
		assertEquals("true", store.values.get("emitCompactLivePackets"));
		assertEquals("false", store.values.get("emitCompactLiveStream"));
		assertEquals("true", store.values.get("enablePluginSnapshotEndpoint"));
		assertEquals("127.0.0.1", store.values.get("pluginSnapshotHost"));
		assertEquals("8893", store.values.get("pluginSnapshotPort"));
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

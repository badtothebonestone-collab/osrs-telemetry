package com.osrstelemetry;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import net.runelite.client.config.ConfigManager;

public class TelemetryPresetApplier
{
	static final String CONFIG_GROUP = "osrs-telemetry";
	static final String RESPONSE_SCHEMA = "telemetry_preset_response.v1";
	static final List<String> PRESET_NAMES = Arrays.asList(
			"DAILY_LIVE",
			"DAILY_SNAPSHOT_NO_FILE",
			"VISUAL_QA",
			"DEBUG_AUDIT",
			"PLUGIN_SNAPSHOT_EXPERIMENTAL",
			"CUSTOM");

	private final ConfigStore store;

	public TelemetryPresetApplier(ConfigManager configManager)
	{
		this(new ConfigManagerStore(configManager));
	}

	TelemetryPresetApplier(ConfigStore store)
	{
		this.store = store;
	}

	public Map<String, Object> presetsPayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "telemetry_presets.v1");
		payload.put("presets", PRESET_NAMES);
		payload.put("readOnlyGameState", true);
		payload.put("description", "Applies fixed whitelisted telemetry configuration presets only. No game input or action endpoints.");
		return payload;
	}

	public Map<String, Object> preview(String presetName)
	{
		return apply(presetName, true);
	}

	public Map<String, Object> apply(String presetName)
	{
		return apply(presetName, false);
	}

	public Map<String, Object> apply(String presetName, boolean preview)
	{
		TelemetryWorkflowPreset preset = parsePreset(presetName);
		Map<String, Object> response = new LinkedHashMap<>();
		response.put("schema", RESPONSE_SCHEMA);
		response.put("preset", presetName == null ? null : presetName);
		response.put("preview", preview);
		response.put("readOnlyGameState", true);
		response.put("warnings", new ArrayList<String>());
		response.put("changes", new ArrayList<Map<String, Object>>());

		if (preset == null)
		{
			response.put("status", "FAIL");
			@SuppressWarnings("unchecked")
			List<String> warnings = (List<String>) response.get("warnings");
			warnings.add("unknown preset");
			return response;
		}

		response.put("preset", preset.name());
		List<PresetValue> values = presetValues(preset);
		@SuppressWarnings("unchecked")
		List<Map<String, Object>> changes = (List<Map<String, Object>>) response.get("changes");
		@SuppressWarnings("unchecked")
		List<String> warnings = (List<String>) response.get("warnings");

		if (preset == TelemetryWorkflowPreset.CUSTOM)
		{
			warnings.add("custom preset does not change settings");
		}

		for (PresetValue value : values)
		{
			String oldValue = store.get(CONFIG_GROUP, value.key);
			boolean changed = !stringValue(value.value).equals(oldValue);
			Map<String, Object> change = new LinkedHashMap<>();
			change.put("key", value.key);
			change.put("oldValue", oldValue);
			change.put("newValue", stringValue(value.value));
			change.put("changed", changed);
			changes.add(change);
			if (!preview && changed)
			{
				store.set(CONFIG_GROUP, value.key, value.value);
			}
		}

		response.put("status", warnings.isEmpty() ? "PASS" : "WARN");
		return response;
	}

	private TelemetryWorkflowPreset parsePreset(String value)
	{
		if (value == null)
		{
			return null;
		}
		String normalized = value.trim().replace('-', '_').replace(' ', '_').toUpperCase(Locale.ROOT);
		for (TelemetryWorkflowPreset preset : TelemetryWorkflowPreset.values())
		{
			if (preset.name().equals(normalized))
			{
				return preset;
			}
		}
		return null;
	}

	private List<PresetValue> presetValues(TelemetryWorkflowPreset preset)
	{
		switch (preset)
		{
			case DAILY_LIVE:
				return dailyLivePreset();
			case DAILY_SNAPSHOT_NO_FILE:
				return dailySnapshotNoFilePreset();
			case VISUAL_QA:
				return visualQaPreset();
			case DEBUG_AUDIT:
				return debugAuditPreset();
			case PLUGIN_SNAPSHOT_EXPERIMENTAL:
				return pluginSnapshotExperimentalPreset();
			case CUSTOM:
			default:
				return List.of();
		}
	}

	private List<PresetValue> dailyLivePreset()
	{
		List<PresetValue> values = baseCompactLivePreset();
		values.add(v("debugRecordFrames", false));
		values.add(v("captureScreenshots", false));
		values.add(v("emitCompactLiveStream", false));
		values.add(v("compactLiveStreamAlsoWriteFiles", true));
		values.add(v("enablePluginSnapshotEndpoint", false));
		values.add(v("pluginSnapshotEnabledInNormalLive", false));
		values.add(v("telemetryDebugOverlayMaxTargets", 10));
		values.add(v("telemetryDebugOverlayMode", TelemetryDebugOverlayMode.CANDIDATES));
		values.add(v("telemetryDebugOverlayShowLabels", true));
		values.add(v("telemetryDebugOverlayShowCollisionWindow", false));
		values.add(v("compactLiveIncludeHeavyGeometry", false));
		values.add(v("compactLiveIncludeClickableHull", false));
		values.add(v("compactLiveIncludeCanvasTilePolygon", false));
		values.add(v("compactLiveIncludeConvexHull", false));
		values.add(v("retentionEnabled", true));
		values.add(v("compactNavigationIncludeFullCollisionGrid", false));
		values.add(v("compactNavigationGridIntervalTicks", 0));
		values.add(v("compactNavigationFullGridIntervalTicks", 0));
		values.add(v("compactNavigationHashOnly", true));
		return values;
	}

	private List<PresetValue> dailySnapshotNoFilePreset()
	{
		List<PresetValue> values = baseSnapshotLivePreset();
		values.add(v("debugRecordFrames", false));
		values.add(v("captureScreenshots", false));
		values.add(v("emitCompactLiveStream", false));
		values.add(v("compactLiveStreamAlsoWriteFiles", false));
		values.add(v("enablePluginSnapshotEndpoint", true));
		values.add(v("pluginSnapshotHost", "127.0.0.1"));
		values.add(v("pluginSnapshotPort", 8893));
		values.add(v("pluginSnapshotMaxProjectionRefs", 100));
		values.add(v("pluginSnapshotMaxResponseBytes", 1048576));
		values.add(v("pluginSnapshotAllowNonLocalHost", false));
		values.add(v("pluginSnapshotEnabledInNormalLive", true));
		values.add(v("telemetryDebugOverlayMaxTargets", 10));
		values.add(v("telemetryDebugOverlayMode", TelemetryDebugOverlayMode.CANDIDATES));
		values.add(v("telemetryDebugOverlayShowLabels", true));
		values.add(v("telemetryDebugOverlayShowCollisionWindow", false));
		values.add(v("compactLiveIncludeHeavyGeometry", false));
		values.add(v("compactLiveIncludeClickableHull", false));
		values.add(v("compactLiveIncludeCanvasTilePolygon", false));
		values.add(v("compactLiveIncludeConvexHull", false));
		values.add(v("retentionEnabled", true));
		values.add(v("compactNavigationIncludeFullCollisionGrid", false));
		values.add(v("compactNavigationGridIntervalTicks", 0));
		values.add(v("compactNavigationFullGridIntervalTicks", 0));
		values.add(v("compactNavigationHashOnly", true));
		return values;
	}

	private List<PresetValue> visualQaPreset()
	{
		List<PresetValue> values = baseCompactLivePreset();
		values.add(v("debugRecordFrames", false));
		values.add(v("captureScreenshots", false));
		values.add(v("emitCompactLiveStream", false));
		values.add(v("compactLiveStreamAlsoWriteFiles", true));
		values.add(v("enablePluginSnapshotEndpoint", false));
		values.add(v("pluginSnapshotEnabledInNormalLive", false));
		values.add(v("telemetryDebugOverlayEnabled", true));
		values.add(v("telemetryDebugOverlayMode", TelemetryDebugOverlayMode.CANDIDATES));
		values.add(v("telemetryDebugOverlayMaxTargets", 25));
		values.add(v("telemetryDebugOverlayShowLabels", true));
		values.add(v("telemetryDebugOverlayShowAimPoints", true));
		values.add(v("telemetryDebugOverlayShowReachability", true));
		values.add(v("telemetryDebugOverlayGeometryMode", TelemetryDebugOverlayGeometryMode.CLICKABLE_HULL));
		values.add(v("telemetryDebugOverlayShowClickableHull", true));
		values.add(v("telemetryDebugOverlayShowBounds", true));
		values.add(v("telemetryDebugOverlayShowCanvasTilePolygon", false));
		values.add(v("telemetryDebugOverlayShowCollisionWindow", true));
		values.add(v("compactLiveIncludeClickableHull", true));
		values.add(v("compactLiveGeometryMaxRefs", 50));
		values.add(v("compactLiveIncludeHeavyGeometry", false));
		values.add(v("compactLiveIncludeCanvasTilePolygon", false));
		values.add(v("compactLiveIncludeConvexHull", false));
		return values;
	}

	private List<PresetValue> debugAuditPreset()
	{
		List<PresetValue> values = new ArrayList<>();
		values.add(v("telemetryRecordingMode", TelemetryRecordingMode.DEBUG_RECORDING));
		values.add(v("emitCompactLivePackets", true));
		values.add(v("compactLivePacketsRequiredForLive", true));
		values.add(v("debugRecordRawTicks", true));
		values.add(v("debugRecordRawEvents", true));
		values.add(v("debugRecordFrames", true));
		values.add(v("captureScreenshots", true));
		values.add(v("screenshotEveryTicks", 1));
		values.add(v("emitCompactLiveStream", false));
		values.add(v("compactLiveStreamAlsoWriteFiles", true));
		values.add(v("enablePluginSnapshotEndpoint", false));
		values.add(v("pluginSnapshotEnabledInNormalLive", false));
		values.add(v("retentionEnabled", true));
		return values;
	}

	private List<PresetValue> pluginSnapshotExperimentalPreset()
	{
		List<PresetValue> values = baseCompactLivePreset();
		values.add(v("debugRecordFrames", false));
		values.add(v("captureScreenshots", false));
		values.add(v("emitCompactLiveStream", false));
		values.add(v("compactLiveStreamAlsoWriteFiles", true));
		values.add(v("enablePluginSnapshotEndpoint", true));
		values.add(v("pluginSnapshotHost", "127.0.0.1"));
		values.add(v("pluginSnapshotPort", 8893));
		values.add(v("pluginSnapshotMaxProjectionRefs", 500));
		values.add(v("pluginSnapshotMaxResponseBytes", 1048576));
		values.add(v("pluginSnapshotAllowNonLocalHost", false));
		values.add(v("pluginSnapshotEnabledInNormalLive", true));
		values.add(v("compactLiveIncludeHeavyGeometry", false));
		values.add(v("compactLiveIncludeClickableHull", false));
		values.add(v("compactLiveIncludeCanvasTilePolygon", false));
		values.add(v("compactLiveIncludeConvexHull", false));
		return values;
	}

	private List<PresetValue> baseCompactLivePreset()
	{
		List<PresetValue> values = new ArrayList<>();
		values.add(v("telemetryRecordingMode", TelemetryRecordingMode.LIVE_COMPACT_ONLY));
		values.add(v("emitCompactLivePackets", true));
		values.add(v("compactLivePacketsRequiredForLive", true));
		values.add(v("debugRecordRawTicks", false));
		values.add(v("debugRecordRawEvents", false));
		values.add(v("emitCompactNavigationPackets", true));
		values.add(v("compactNavigationEmitCollisionWindow", true));
		values.add(v("compactLivePacketTypes", "all"));
		return values;
	}

	private List<PresetValue> baseSnapshotLivePreset()
	{
		List<PresetValue> values = new ArrayList<>();
		values.add(v("telemetryRecordingMode", TelemetryRecordingMode.LIVE_COMPACT_ONLY));
		values.add(v("emitCompactLivePackets", false));
		values.add(v("compactLivePacketsRequiredForLive", false));
		values.add(v("debugRecordRawTicks", false));
		values.add(v("debugRecordRawEvents", false));
		values.add(v("emitCompactNavigationPackets", true));
		values.add(v("compactNavigationEmitCollisionWindow", true));
		values.add(v("compactLivePacketTypes", "all"));
		return values;
	}

	private PresetValue v(String key, Object value)
	{
		return new PresetValue(key, value);
	}

	private String stringValue(Object value)
	{
		return value instanceof Enum<?> ? ((Enum<?>) value).name() : String.valueOf(value);
	}

	interface ConfigStore
	{
		String get(String group, String key);

		void set(String group, String key, Object value);
	}

	static final class ConfigManagerStore implements ConfigStore
	{
		private final ConfigManager configManager;

		ConfigManagerStore(ConfigManager configManager)
		{
			this.configManager = configManager;
		}

		@Override
		public String get(String group, String key)
		{
			return configManager == null ? null : configManager.getConfiguration(group, key);
		}

		@Override
		public void set(String group, String key, Object value)
		{
			if (configManager != null)
			{
				configManager.setConfiguration(group, key, value);
			}
		}
	}

	private static final class PresetValue
	{
		private final String key;
		private final Object value;

		private PresetValue(String key, Object value)
		{
			this.key = key;
			this.value = value;
		}
	}
}

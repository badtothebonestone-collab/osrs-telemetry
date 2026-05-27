package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.lang.reflect.Method;
import java.util.LinkedHashSet;
import java.util.Set;
import net.runelite.client.config.ConfigItem;
import org.junit.Test;

public class TelemetryConfigKeysTest
{
	@Test
	public void exposesOnlyCurrentPipelineKeysInNormalUi()
	{
		assertEquals(
				new LinkedHashSet<>(TelemetryConfigKeys.EXPOSED_KEYS),
				exposedConfigKeys());
	}

	@Test
	public void hidesDeveloperAndRetiredKeysFromNormalUi()
	{
		Set<String> exposed = exposedConfigKeys();
		for (String key : TelemetryConfigKeys.DEVELOPER_KEYS)
		{
			assertFalse("developer key should be hidden: " + key, exposed.contains(key));
		}
		for (String key : TelemetryConfigKeys.RETIRED_KEYS)
		{
			assertFalse("retired key should be hidden or absent: " + key, exposed.contains(key));
		}
	}

	@Test
	public void retiredKeysIncludeOldPacketArchiveAndRecordingControls()
	{
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("pluginSnapshotEnabledInNormalLive"));
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("debugRecordRawTicks"));
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("debugRecordRawEvents"));
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("captureScreenshots"));
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("emitCompactLivePackets"));
		assertTrue(TelemetryConfigKeys.RETIRED_KEYS.contains("livePacketArchiveEnabled"));
	}

	private Set<String> exposedConfigKeys()
	{
		Set<String> keys = new LinkedHashSet<>();
		for (Method method : TelemetryConfig.class.getDeclaredMethods())
		{
			ConfigItem item = method.getAnnotation(ConfigItem.class);
			if (item != null && !item.hidden())
			{
				keys.add(item.keyName());
			}
		}
		return keys;
	}
}

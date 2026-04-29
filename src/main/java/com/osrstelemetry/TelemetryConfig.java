package com.osrstelemetry;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;

@ConfigGroup("osrs-telemetry")
public interface TelemetryConfig extends Config
{
	@ConfigItem(
			keyName = "enabled",
			name = "Enable telemetry",
			description = "Write read-only telemetry snapshots to disk"
	)
	default boolean enabled()
	{
		return true;
	}

	@ConfigItem(
			keyName = "outputDirectory",
			name = "Output directory",
			description = "Folder where telemetry sessions are written"
	)
	default String outputDirectory()
	{
		return System.getProperty("user.home") + "/.osrs-telemetry";
	}
}

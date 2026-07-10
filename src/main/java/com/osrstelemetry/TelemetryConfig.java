package com.osrstelemetry;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;

@ConfigGroup("osrs-telemetry")
public interface TelemetryConfig extends Config
{
	@ConfigSection(
			name = "Snapshot Endpoint",
			description = "Read-only localhost snapshot bridge.",
			position = 0
	)
	String snapshotSection = "snapshot";

	@ConfigItem(
			section = snapshotSection,
			keyName = "enabled",
			name = "Enable telemetry",
			description = "Keep the read-only telemetry sensor active.",
			position = 0
	)
	default boolean enabled()
	{
		return true;
	}

	@ConfigItem(
			section = snapshotSection,
			keyName = "enablePluginSnapshotEndpoint",
			name = "Enable snapshot endpoint",
			description = "Serve current telemetry observations on localhost.",
			position = 1
	)
	default boolean enablePluginSnapshotEndpoint()
	{
		return true;
	}

	@ConfigItem(
			section = snapshotSection,
			keyName = "pluginSnapshotHost",
			name = "Snapshot host",
			description = "Bind host. Keep 127.0.0.1 unless deliberately testing otherwise.",
			position = 2
	)
	default String pluginSnapshotHost()
	{
		return "127.0.0.1";
	}

	@ConfigItem(
			section = snapshotSection,
			keyName = "pluginSnapshotPort",
			name = "Snapshot port",
			description = "Local port for the read-only snapshot endpoint.",
			position = 3
	)
	default int pluginSnapshotPort()
	{
		return 8893;
	}

	@ConfigItem(
			section = snapshotSection,
			keyName = "pluginSnapshotAuthToken",
			name = "Snapshot auth token",
			description = "Optional token sent in X-Plugin-Snapshot-Token.",
			position = 4
	)
	default String pluginSnapshotAuthToken()
	{
		return "";
	}

	@ConfigItem(keyName = "pluginSnapshotMaxProjectionRefs", name = "Max projection refs", description = "Maximum projected object refs per response.", hidden = true)
	default int pluginSnapshotMaxProjectionRefs()
	{
		return 200;
	}

	@ConfigItem(keyName = "pluginSnapshotMaxResponseBytes", name = "Max response bytes", description = "Maximum snapshot response size.", hidden = true)
	default int pluginSnapshotMaxResponseBytes()
	{
		return 1048576;
	}

	@ConfigItem(keyName = "pluginSnapshotAllowNonLocalHost", name = "Allow non-local host", description = "Permit a non-loopback bind host.", hidden = true)
	default boolean pluginSnapshotAllowNonLocalHost()
	{
		return false;
	}
}

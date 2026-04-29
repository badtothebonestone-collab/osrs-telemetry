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
			description = "Sessions root where telemetry session folders are written"
	)
	default String outputDirectory()
	{
		return System.getProperty("user.home") + "/.osrs-telemetry/sessions";
	}

	@ConfigItem(
			keyName = "retentionEnabled",
			name = "Enable retention cleanup",
			description = "Delete old closed telemetry segments and sessions when the size cap is exceeded"
	)
	default boolean retentionEnabled()
	{
		return true;
	}

	@ConfigItem(
			keyName = "maxTelemetryGb",
			name = "Max telemetry GB",
			description = "Maximum total telemetry size under the sessions directory"
	)
	default int maxTelemetryGb()
	{
		return 2;
	}

	@ConfigItem(
			keyName = "maxSegmentMb",
			name = "Max segment MB",
			description = "Approximate maximum size of each tick or event segment"
	)
	default int maxSegmentMb()
	{
		return 128;
	}

	@ConfigItem(
			keyName = "cleanupIntervalSeconds",
			name = "Cleanup interval seconds",
			description = "How often retention cleanup checks total telemetry size"
	)
	default int cleanupIntervalSeconds()
	{
		return 60;
	}

	@ConfigItem(
			keyName = "preservePinnedSessions",
			name = "Preserve pinned sessions",
			description = "Do not delete sessions containing pinned.flag during retention cleanup"
	)
	default boolean preservePinnedSessions()
	{
		return true;
	}

	@ConfigItem(
			keyName = "allowDeletingClosedSegmentsFromActiveSession",
			name = "Delete closed active segments",
			description = "Allow retention cleanup to delete old closed segments from the current active session"
	)
	default boolean allowDeletingClosedSegmentsFromActiveSession()
	{
		return true;
	}
}

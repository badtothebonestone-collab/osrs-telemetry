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

	@ConfigItem(
			keyName = "captureScreenshots",
			name = "Capture screenshots",
			description = "Capture one read-only canvas frame per configured game tick"
	)
	default boolean captureScreenshots()
	{
		return true;
	}

	@ConfigItem(
			keyName = "screenshotEveryTicks",
			name = "Screenshot tick interval",
			description = "Capture a frame every N game ticks"
	)
	default int screenshotEveryTicks()
	{
		return 1;
	}

	@ConfigItem(
			keyName = "screenshotFormat",
			name = "Screenshot format",
			description = "Frame image format: jpg or png"
	)
	default String screenshotFormat()
	{
		return "jpg";
	}

	@ConfigItem(
			keyName = "jpegQuality",
			name = "JPEG quality",
			description = "JPEG frame quality from 0.0 to 1.0"
	)
	default double jpegQuality()
	{
		return 0.75;
	}

	@ConfigItem(
			keyName = "maxFrameWidth",
			name = "Max frame width",
			description = "Scale captured frames down to this width; 0 keeps the original size"
	)
	default int maxFrameWidth()
	{
		return 0;
	}

	@ConfigItem(
			keyName = "includeFramePathInTicks",
			name = "Include frame path in ticks",
			description = "Write the relative frame path into each captured tick record"
	)
	default boolean includeFramePathInTicks()
	{
		return true;
	}

	@ConfigItem(
			keyName = "maxFrameStorageMb",
			name = "Max frame storage MB",
			description = "Maximum storage for frame files in the active session"
	)
	default int maxFrameStorageMb()
	{
		return 1024;
	}

	@ConfigItem(
			keyName = "deleteOldFrames",
			name = "Delete old frames",
			description = "Delete oldest frame files when the frame storage cap is exceeded"
	)
	default boolean deleteOldFrames()
	{
		return true;
	}

	@ConfigItem(
			keyName = "maxFrameQueueSize",
			name = "Max frame queue size",
			description = "Maximum pending frame writes before new frames are dropped"
	)
	default int maxFrameQueueSize()
	{
		return 250;
	}
}

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
			keyName = "telemetryRecordingMode",
			name = "Recording mode",
			description = "LIVE_COMPACT_ONLY is normal live mode; DEBUG_RECORDING preserves full raw tick/event/frame capture for audit and batch tools."
	)
	default TelemetryRecordingMode telemetryRecordingMode()
	{
		return TelemetryRecordingMode.LIVE_COMPACT_ONLY;
	}

	@ConfigItem(
			keyName = "debugRecordRawTicks",
			name = "Debug record raw ticks",
			description = "Write full raw tick JSONL outside DEBUG_RECORDING mode. Leave disabled for normal compact live use."
	)
	default boolean debugRecordRawTicks()
	{
		return false;
	}

	@ConfigItem(
			keyName = "debugRecordRawEvents",
			name = "Debug record raw events",
			description = "Write raw event JSONL outside DEBUG_RECORDING mode. Leave disabled for normal compact live use."
	)
	default boolean debugRecordRawEvents()
	{
		return false;
	}

	@ConfigItem(
			keyName = "debugRecordFrames",
			name = "Record frames in recording modes",
			description = "Allow frame capture in LIVE_COMPACT_WITH_FRAMES, HYBRID_DEBUG, and DEBUG_RECORDING when screenshot capture is enabled."
	)
	default boolean debugRecordFrames()
	{
		return true;
	}

	@ConfigItem(
			keyName = "debugFrameIntervalTicks",
			name = "Live frame interval ticks",
			description = "Frame interval used by LIVE_COMPACT_WITH_FRAMES. DEBUG_RECORDING keeps using Screenshot tick interval."
	)
	default int debugFrameIntervalTicks()
	{
		return 5;
	}

	@ConfigItem(
			keyName = "compactLivePacketsRequiredForLive",
			name = "Require compact packets for live",
			description = "Force compact packet emission for live recording modes so normal live mode does not depend on raw tick JSONL."
	)
	default boolean compactLivePacketsRequiredForLive()
	{
		return true;
	}

	@ConfigItem(
			keyName = "rawSnapshotSampleIntervalTicks",
			name = "Raw snapshot sample interval",
			description = "Future hybrid debug hook. 0 disables sampled raw snapshots."
	)
	default int rawSnapshotSampleIntervalTicks()
	{
		return 0;
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
			keyName = "frameCleanupIntervalSeconds",
			name = "Frame cleanup interval seconds",
			description = "How often old frame cleanup checks the active frames folder"
	)
	default int frameCleanupIntervalSeconds()
	{
		return 10;
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

	@ConfigItem(
			keyName = "frameCaptureMode",
			name = "Frame capture mode",
			description = "Preferred frame capture mode: RUNELITE_ONLY"
	)
	default String frameCaptureMode()
	{
		return "RUNELITE_ONLY";
	}

	@ConfigItem(
			keyName = "allowScreenRectangleFallback",
			name = "Allow screen rectangle fallback",
			description = "Allow opt-in Robot screen rectangle capture if RuneLite-only capture is unavailable"
	)
	default boolean allowScreenRectangleFallback()
	{
		return false;
	}

	@ConfigItem(
			keyName = "includeClientFrame",
			name = "Include client frame",
			description = "Include RuneLite client chrome around the game frame when supported"
	)
	default boolean includeClientFrame()
	{
		return false;
	}

	@ConfigItem(
			keyName = "sceneCaptureMode",
			name = "Scene capture mode",
			description = "Diagnostic scene object coverage. LOCAL_DEFAULT preserves radius 12/max 250; wide/full are raw-force modes; static index mode reduces repeated unchanged scenery."
	)
	default SceneCaptureMode sceneCaptureMode()
	{
		return SceneCaptureMode.LOCAL_DEFAULT;
	}

	@ConfigItem(
			keyName = "sceneIndexRescanIntervalTicks",
			name = "Scene index rescan interval",
			description = "Diagnostic static scene index full-resync interval in ticks. 0 disables periodic resync."
	)
	default int sceneIndexRescanIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			keyName = "keepDespawnedSceneObjectsInIndex",
			name = "Keep despawned scene objects",
			description = "Keep despawned scene object records in the diagnostic static scene index."
	)
	default boolean keepDespawnedSceneObjectsInIndex()
	{
		return true;
	}

	@ConfigItem(
			keyName = "maxSceneIndexObjects",
			name = "Max scene index objects",
			description = "Safety cap for diagnostic static scene index object count."
	)
	default int maxSceneIndexObjects()
	{
		return 50000;
	}

	@ConfigItem(
			keyName = "sceneProjectionRefreshMode",
			name = "Scene projection refresh mode",
			description = "Projection subset for static scene index diagnostic mode."
	)
	default SceneProjectionRefreshMode sceneProjectionRefreshMode()
	{
		return SceneProjectionRefreshMode.VISIBLE_AND_NEARBY;
	}

	@ConfigItem(
			keyName = "emitCompactLivePackets",
			name = "Emit compact live packets",
			description = "Write bounded read-only compact NDJSON live packets alongside raw telemetry sessions. Recommended for normal live mode."
	)
	default boolean emitCompactLivePackets()
	{
		return true;
	}

	@ConfigItem(
			keyName = "compactLiveSegmentMb",
			name = "Compact live segment MB",
			description = "Approximate maximum size of each compact live packet segment."
	)
	default int compactLiveSegmentMb()
	{
		return 64;
	}

	@ConfigItem(
			keyName = "compactLiveRetentionTicks",
			name = "Compact live retention ticks",
			description = "Approximate tick window to retain for compact live packet segments. 0 disables tick retention."
	)
	default int compactLiveRetentionTicks()
	{
		return 5000;
	}

	@ConfigItem(
			keyName = "compactLiveRetentionMb",
			name = "Compact live retention MB",
			description = "Approximate maximum compact live packet storage per session. 0 disables byte retention."
	)
	default int compactLiveRetentionMb()
	{
		return 512;
	}

	@ConfigItem(
			keyName = "compactLiveRetentionSegments",
			name = "Compact live retention segments",
			description = "Maximum compact live packet segments to keep per session. 0 disables segment-count retention."
	)
	default int compactLiveRetentionSegments()
	{
		return 16;
	}

	@ConfigItem(
			keyName = "compactLiveQueueSize",
			name = "Compact live queue size",
			description = "Maximum pending compact live packets before new live packets are dropped instead of blocking the client thread."
	)
	default int compactLiveQueueSize()
	{
		return 5000;
	}

	@ConfigItem(
			keyName = "compactLiveIncludeHeavyGeometry",
			name = "Compact live heavy geometry",
			description = "Include debug polygon geometry in compact live projection packets. Disabled keeps live packets small."
	)
	default boolean compactLiveIncludeHeavyGeometry()
	{
		return false;
	}

	@ConfigItem(
			keyName = "emitCompactNavigationPackets",
			name = "Emit compact navigation packets",
			description = "Include read-only collision/navigation summary packets in the compact live stream."
	)
	default boolean emitCompactNavigationPackets()
	{
		return true;
	}

	@ConfigItem(
			keyName = "compactNavigationEmitCollisionWindow",
			name = "Compact navigation collision window",
			description = "Emit a bounded read-only local collision window around the player for lightweight reachability QA."
	)
	default boolean compactNavigationEmitCollisionWindow()
	{
		return true;
	}

	@ConfigItem(
			keyName = "compactNavigationCollisionWindowRadius",
			name = "Compact navigation window radius",
			description = "Scene-tile radius for compact collision window packets. Values are clamped between 8 and 52."
	)
	default int compactNavigationCollisionWindowRadius()
	{
		return 24;
	}

	@ConfigItem(
			keyName = "compactNavigationIncludeFullCollisionGrid",
			name = "Compact navigation full collision grid",
			description = "Debug only: include full collision flag grids in compact live packets when the interval is enabled."
	)
	default boolean compactNavigationIncludeFullCollisionGrid()
	{
		return false;
	}

	@ConfigItem(
			keyName = "compactNavigationGridIntervalTicks",
			name = "Compact navigation grid interval ticks",
			description = "Debug-only full collision grid packet interval. 0 disables full grid packets."
	)
	default int compactNavigationGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			keyName = "compactNavigationFullGridIntervalTicks",
			name = "Compact navigation full grid interval",
			description = "Debug-only full collision grid packet interval. 0 disables full grid packets."
	)
	default int compactNavigationFullGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			keyName = "compactNavigationHashOnly",
			name = "Compact navigation hash only",
			description = "Keep normal compact navigation packets to collision summary/hash fields instead of full grid data."
	)
	default boolean compactNavigationHashOnly()
	{
		return true;
	}

	@ConfigItem(
			keyName = "compactLivePacketTypes",
			name = "Compact live packet types",
			description = "Comma-separated compact packet groups to emit: baseline,sceneDelta,projection,inventory,inventoryDelta,activity,navigation,collisionWindow,collisionGrid,writerHealth, or all."
	)
	default String compactLivePacketTypes()
	{
		return "all";
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayEnabled",
			name = "Telemetry debug overlay",
			description = "Draw a read-only telemetry QA overlay from overlay_debug_state.json. Disabled by default."
	)
	default boolean telemetryDebugOverlayEnabled()
	{
		return false;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayMode",
			name = "Debug overlay mode",
			description = "Which read-only telemetry details to draw."
	)
	default TelemetryDebugOverlayMode telemetryDebugOverlayMode()
	{
		return TelemetryDebugOverlayMode.CANDIDATES;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayMaxTargets",
			name = "Debug overlay max targets",
			description = "Maximum candidates to draw. The overlay clamps this between 0 and 200."
	)
	default int telemetryDebugOverlayMaxTargets()
	{
		return 25;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayShowLabels",
			name = "Debug overlay labels",
			description = "Show compact candidate labels."
	)
	default boolean telemetryDebugOverlayShowLabels()
	{
		return true;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayShowAimPoints",
			name = "Debug overlay aim points",
			description = "Draw read-only candidate aim point markers."
	)
	default boolean telemetryDebugOverlayShowAimPoints()
	{
		return true;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayShowReachability",
			name = "Debug overlay reachability",
			description = "Color and label candidates by read-only reachability when available."
	)
	default boolean telemetryDebugOverlayShowReachability()
	{
		return true;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayShowCollisionWindow",
			name = "Debug overlay collision window",
			description = "Show collision window summary in the overlay status panel."
	)
	default boolean telemetryDebugOverlayShowCollisionWindow()
	{
		return false;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayShowLatestEvent",
			name = "Debug overlay latest event",
			description = "Show one compact read-only live event summary in the overlay status panel."
	)
	default boolean telemetryDebugOverlayShowLatestEvent()
	{
		return false;
	}

	@ConfigItem(
			keyName = "telemetryDebugOverlayStatePath",
			name = "Debug overlay state path",
			description = "Optional explicit path to overlay_debug_state.json. Leave blank to use the current telemetry session."
	)
	default String telemetryDebugOverlayStatePath()
	{
		return "";
	}
}

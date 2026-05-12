package com.osrstelemetry;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;

@ConfigGroup("osrs-telemetry")
public interface TelemetryConfig extends Config
{
	@ConfigSection(
			name = "Workflow Presets",
			description = "Apply safe telemetry-only workflow presets.",
			position = -1
	)
	String workflowPresetsSection = "workflowPresets";

	@ConfigSection(
			name = "Normal Live",
			description = "Everyday compact live telemetry settings.",
			position = 0
	)
	String normalLiveSection = "normalLive";

	@ConfigSection(
			name = "Visual QA Overlay",
			description = "Optional read-only overlay settings for visual QA.",
			position = 1
	)
	String visualQaOverlaySection = "visualQaOverlay";

	@ConfigSection(
			name = "Frames / Visual Capture",
			description = "Read-only screenshot and frame capture settings.",
			position = 2
	)
	String framesSection = "frames";

	@ConfigSection(
			name = "Debug / Audit Recording",
			description = "Disk-heavy raw recording settings for audit, replay, and batch tools.",
			position = 3
	)
	String debugAuditSection = "debugAudit";

	@ConfigSection(
			name = "Retention / Storage",
			description = "Storage limits and cleanup behavior.",
			position = 4
	)
	String retentionStorageSection = "retentionStorage";

	@ConfigSection(
			name = "Advanced / Experimental",
			description = "Developer-oriented capture, projection, and packet details.",
			position = 5
	)
	String advancedSection = "advanced";

	@ConfigSection(
			name = "Plugin Snapshot Bridge",
			description = "Optional read-only localhost bridge that serves cached compact telemetry only.",
			position = 6
	)
	String pluginSnapshotSection = "pluginSnapshot";

	@ConfigItem(
			section = workflowPresetsSection,
			keyName = "workflowPreset",
			name = "Workflow preset",
			description = "Telemetry settings preset to preview/apply. Presets only change whitelisted telemetry config keys."
	)
	default TelemetryWorkflowPreset workflowPreset()
	{
		return TelemetryWorkflowPreset.DAILY_LIVE;
	}

	@ConfigItem(
			section = workflowPresetsSection,
			keyName = "presetPreviewOnly",
			name = "Preview preset only",
			description = "When enabled, Apply workflow preset logs the changes that would be made without saving them."
	)
	default boolean presetPreviewOnly()
	{
		return false;
	}

	@ConfigItem(
			section = workflowPresetsSection,
			keyName = "applyWorkflowPreset",
			name = "Apply workflow preset",
			description = "Toggle on to apply the selected telemetry preset to whitelisted telemetry settings only. It does not click, type, invoke menus, or change game state."
	)
	default boolean applyWorkflowPreset()
	{
		return false;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "enabled",
			name = "Enable telemetry",
			description = "Write read-only telemetry snapshots to disk"
	)
	default boolean enabled()
	{
		return true;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "outputDirectory",
			name = "Output directory",
			description = "Sessions root where telemetry session folders are written"
	)
	default String outputDirectory()
	{
		return System.getProperty("user.home") + "/.osrs-telemetry/sessions";
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "retentionEnabled",
			name = "Enable retention cleanup",
			description = "Delete old closed telemetry segments and sessions when the size cap is exceeded"
	)
	default boolean retentionEnabled()
	{
		return true;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "maxTelemetryGb",
			name = "Max telemetry GB",
			description = "Maximum total telemetry size under the sessions directory"
	)
	default int maxTelemetryGb()
	{
		return 2;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "maxSegmentMb",
			name = "Max segment MB",
			description = "Approximate maximum size of each tick or event segment"
	)
	default int maxSegmentMb()
	{
		return 128;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "cleanupIntervalSeconds",
			name = "Cleanup interval seconds",
			description = "How often retention cleanup checks total telemetry size"
	)
	default int cleanupIntervalSeconds()
	{
		return 60;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "preservePinnedSessions",
			name = "Preserve pinned sessions",
			description = "Do not delete sessions containing pinned.flag during retention cleanup"
	)
	default boolean preservePinnedSessions()
	{
		return true;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "allowDeletingClosedSegmentsFromActiveSession",
			name = "Delete closed active segments",
			description = "Allow retention cleanup to delete old closed segments from the current active session"
	)
	default boolean allowDeletingClosedSegmentsFromActiveSession()
	{
		return true;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "telemetryRecordingMode",
			name = "Recording mode",
			description = "LIVE_COMPACT_ONLY is normal live mode; DEBUG_RECORDING preserves full raw tick/event/frame capture for audit and batch tools."
	)
	default TelemetryRecordingMode telemetryRecordingMode()
	{
		return TelemetryRecordingMode.LIVE_COMPACT_ONLY;
	}

	@ConfigItem(
			section = debugAuditSection,
			keyName = "debugRecordRawTicks",
			name = "Debug record raw ticks",
			description = "Write full raw tick JSONL outside DEBUG_RECORDING mode. Leave disabled for normal compact live use."
	)
	default boolean debugRecordRawTicks()
	{
		return false;
	}

	@ConfigItem(
			section = debugAuditSection,
			keyName = "debugRecordRawEvents",
			name = "Debug record raw events",
			description = "Write raw event JSONL outside DEBUG_RECORDING mode. Leave disabled for normal compact live use."
	)
	default boolean debugRecordRawEvents()
	{
		return false;
	}

	@ConfigItem(
			section = debugAuditSection,
			keyName = "debugRecordFrames",
			name = "Record frames in recording modes",
			description = "Allow frame capture in LIVE_COMPACT_WITH_FRAMES, HYBRID_DEBUG, and DEBUG_RECORDING when screenshot capture is enabled."
	)
	default boolean debugRecordFrames()
	{
		return true;
	}

	@ConfigItem(
			section = debugAuditSection,
			keyName = "debugFrameIntervalTicks",
			name = "Live frame interval ticks",
			description = "Frame interval used by LIVE_COMPACT_WITH_FRAMES. DEBUG_RECORDING keeps using Screenshot tick interval."
	)
	default int debugFrameIntervalTicks()
	{
		return 5;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLivePacketsRequiredForLive",
			name = "Require compact packets for live",
			description = "Force compact packet emission for live recording modes so normal live mode does not depend on raw tick JSONL."
	)
	default boolean compactLivePacketsRequiredForLive()
	{
		return true;
	}

	@ConfigItem(
			section = debugAuditSection,
			keyName = "rawSnapshotSampleIntervalTicks",
			name = "Raw snapshot sample interval",
			description = "Future hybrid debug hook. 0 disables sampled raw snapshots."
	)
	default int rawSnapshotSampleIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "captureScreenshots",
			name = "Capture screenshots",
			description = "Capture one read-only canvas frame per configured game tick"
	)
	default boolean captureScreenshots()
	{
		return true;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "screenshotEveryTicks",
			name = "Screenshot tick interval",
			description = "Capture a frame every N game ticks"
	)
	default int screenshotEveryTicks()
	{
		return 1;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "screenshotFormat",
			name = "Screenshot format",
			description = "Frame image format: jpg or png"
	)
	default String screenshotFormat()
	{
		return "jpg";
	}

	@ConfigItem(
			section = framesSection,
			keyName = "jpegQuality",
			name = "JPEG quality",
			description = "JPEG frame quality from 0.0 to 1.0"
	)
	default double jpegQuality()
	{
		return 0.75;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "includeFramePathInTicks",
			name = "Include frame path in ticks",
			description = "Write the relative frame path into each captured tick record"
	)
	default boolean includeFramePathInTicks()
	{
		return true;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "maxFrameStorageMb",
			name = "Max frame storage MB",
			description = "Maximum storage for frame files in the active session"
	)
	default int maxFrameStorageMb()
	{
		return 1024;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "frameCleanupIntervalSeconds",
			name = "Frame cleanup interval seconds",
			description = "How often old frame cleanup checks the active frames folder"
	)
	default int frameCleanupIntervalSeconds()
	{
		return 10;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "deleteOldFrames",
			name = "Delete old frames",
			description = "Delete oldest frame files when the frame storage cap is exceeded"
	)
	default boolean deleteOldFrames()
	{
		return true;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "maxFrameQueueSize",
			name = "Max frame queue size",
			description = "Maximum pending frame writes before new frames are dropped"
	)
	default int maxFrameQueueSize()
	{
		return 250;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "frameCaptureMode",
			name = "Frame capture mode",
			description = "Preferred frame capture mode: RUNELITE_ONLY"
	)
	default String frameCaptureMode()
	{
		return "RUNELITE_ONLY";
	}

	@ConfigItem(
			section = framesSection,
			keyName = "allowScreenRectangleFallback",
			name = "Allow screen rectangle fallback",
			description = "Allow opt-in Robot screen rectangle capture if RuneLite-only capture is unavailable"
	)
	default boolean allowScreenRectangleFallback()
	{
		return false;
	}

	@ConfigItem(
			section = framesSection,
			keyName = "includeClientFrame",
			name = "Include client frame",
			description = "Include RuneLite client chrome around the game frame when supported"
	)
	default boolean includeClientFrame()
	{
		return false;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "sceneCaptureMode",
			name = "Scene capture mode",
			description = "Diagnostic scene object coverage. LOCAL_DEFAULT preserves radius 12/max 250; wide/full are raw-force modes; static index mode reduces repeated unchanged scenery."
	)
	default SceneCaptureMode sceneCaptureMode()
	{
		return SceneCaptureMode.LOCAL_DEFAULT;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "sceneIndexRescanIntervalTicks",
			name = "Scene index rescan interval",
			description = "Diagnostic static scene index full-resync interval in ticks. 0 disables periodic resync."
	)
	default int sceneIndexRescanIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "keepDespawnedSceneObjectsInIndex",
			name = "Keep despawned scene objects",
			description = "Keep despawned scene object records in the diagnostic static scene index."
	)
	default boolean keepDespawnedSceneObjectsInIndex()
	{
		return true;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "maxSceneIndexObjects",
			name = "Max scene index objects",
			description = "Safety cap for diagnostic static scene index object count."
	)
	default int maxSceneIndexObjects()
	{
		return 50000;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "sceneProjectionRefreshMode",
			name = "Scene projection refresh mode",
			description = "Projection subset for static scene index diagnostic mode."
	)
	default SceneProjectionRefreshMode sceneProjectionRefreshMode()
	{
		return SceneProjectionRefreshMode.VISIBLE_AND_NEARBY;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "emitCompactLivePackets",
			name = "Emit compact live packets",
			description = "Write bounded read-only compact NDJSON live packets for the normal live bridge."
	)
	default boolean emitCompactLivePackets()
	{
		return true;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "emitCompactLiveStream",
			name = "Emit compact live stream",
			description = "Publish compact live packets over a read-only localhost TCP NDJSON stream. Disabled until a local processor is ready."
	)
	default boolean emitCompactLiveStream()
	{
		return false;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamHost",
			name = "Compact stream host",
			description = "Loopback bind host for compact live stream. Non-loopback addresses are rejected."
	)
	default String compactLiveStreamHost()
	{
		return "127.0.0.1";
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamPort",
			name = "Compact stream port",
			description = "Local TCP port for compact live stream NDJSON packets."
	)
	default int compactLiveStreamPort()
	{
		return 8891;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamQueueSize",
			name = "Compact stream queue size",
			description = "Maximum pending stream packets before dropping new stream packets instead of blocking RuneLite."
	)
	default int compactLiveStreamQueueSize()
	{
		return 5000;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamCircuitBreakerEnabled",
			name = "Compact stream circuit breaker",
			description = "Temporarily disables the experimental stream if writes or queue pressure look unsafe. Compact packet files stay unaffected."
	)
	default boolean compactLiveStreamCircuitBreakerEnabled()
	{
		return true;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamMaxWriteMillis",
			name = "Compact stream max write ms",
			description = "Maximum stream worker write time before the circuit breaker pauses stream publishing."
	)
	default int compactLiveStreamMaxWriteMillis()
	{
		return 20;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamDisableSeconds",
			name = "Compact stream pause seconds",
			description = "How long the circuit breaker pauses stream publishing after unsafe stream behavior is detected."
	)
	default int compactLiveStreamDisableSeconds()
	{
		return 10;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactLiveStreamAlsoWriteFiles",
			name = "Stream also writes files",
			description = "Keep the compact packet file bridge as a debug mirror when compact live stream is enabled."
	)
	default boolean compactLiveStreamAlsoWriteFiles()
	{
		return true;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "enablePluginSnapshotEndpoint",
			name = "Enable plugin snapshot endpoint",
			description = "Opt-in local dev bridge. Disabled by default. Serves cached read-only telemetry only and does not execute game actions."
	)
	default boolean enablePluginSnapshotEndpoint()
	{
		return false;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotHost",
			name = "Snapshot host",
			description = "Local bind host for the read-only snapshot endpoint. Default is 127.0.0.1."
	)
	default String pluginSnapshotHost()
	{
		return "127.0.0.1";
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotPort",
			name = "Snapshot port",
			description = "Local port for the read-only cached snapshot endpoint."
	)
	default int pluginSnapshotPort()
	{
		return 8893;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotAuthToken",
			name = "Snapshot auth token",
			description = "Optional local token. When set, requests must include X-Plugin-Snapshot-Token."
	)
	default String pluginSnapshotAuthToken()
	{
		return "";
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotMaxProjectionRefs",
			name = "Snapshot max projection refs",
			description = "Maximum projection refs returned by the read-only snapshot endpoint."
	)
	default int pluginSnapshotMaxProjectionRefs()
	{
		return 500;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotMaxResponseBytes",
			name = "Snapshot max response bytes",
			description = "Maximum response bytes for cached snapshot responses. Requests above this fail safely."
	)
	default int pluginSnapshotMaxResponseBytes()
	{
		return 1048576;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotAllowNonLocalHost",
			name = "Allow non-local snapshot host",
			description = "Leave disabled. When false, the endpoint only binds loopback addresses."
	)
	default boolean pluginSnapshotAllowNonLocalHost()
	{
		return false;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotEnabledInNormalLive",
			name = "Snapshot endpoint in normal live",
			description = "Experimental opt-in. Normal live still uses compact packet files until snapshot-vs-file comparison is implemented."
	)
	default boolean pluginSnapshotEnabledInNormalLive()
	{
		return false;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "compactLiveSegmentMb",
			name = "Compact live segment MB",
			description = "Approximate maximum size of each compact live packet segment."
	)
	default int compactLiveSegmentMb()
	{
		return 64;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "compactLiveRetentionTicks",
			name = "Compact live retention ticks",
			description = "Approximate tick window to retain for compact live packet segments. 0 disables tick retention."
	)
	default int compactLiveRetentionTicks()
	{
		return 5000;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "compactLiveRetentionMb",
			name = "Compact live retention MB",
			description = "Approximate maximum compact live packet storage per session. 0 disables byte retention."
	)
	default int compactLiveRetentionMb()
	{
		return 512;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "compactLiveRetentionSegments",
			name = "Compact live retention segments",
			description = "Maximum compact live packet segments to keep per session. 0 disables segment-count retention."
	)
	default int compactLiveRetentionSegments()
	{
		return 16;
	}

	@ConfigItem(
			section = retentionStorageSection,
			keyName = "compactLiveQueueSize",
			name = "Compact live queue size",
			description = "Maximum pending compact live packets before new live packets are dropped instead of blocking the client thread."
	)
	default int compactLiveQueueSize()
	{
		return 5000;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLiveIncludeHeavyGeometry",
			name = "Compact live heavy geometry",
			description = "Include debug polygon geometry in compact live projection packets. Disabled keeps live packets small."
	)
	default boolean compactLiveIncludeHeavyGeometry()
	{
		return false;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLiveIncludeClickableHull",
			name = "Compact live clickable hull",
			description = "Debug/overlay only: include observed clickbox/clickable hull polygons for capped visible projection refs."
	)
	default boolean compactLiveIncludeClickableHull()
	{
		return false;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLiveGeometryMaxRefs",
			name = "Compact live geometry max refs",
			description = "Maximum visible refs per tick that may include compact polygon geometry. Values are clamped from 0 to 200."
	)
	default int compactLiveGeometryMaxRefs()
	{
		return 50;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLiveIncludeCanvasTilePolygon",
			name = "Compact live tile polygons",
			description = "Debug/overlay only: include canvas tile polygons for capped visible projection refs."
	)
	default boolean compactLiveIncludeCanvasTilePolygon()
	{
		return false;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLiveIncludeConvexHull",
			name = "Compact live convex hull",
			description = "Debug/overlay only: include convex hull fallback polygons for capped visible projection refs."
	)
	default boolean compactLiveIncludeConvexHull()
	{
		return false;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "emitCompactNavigationPackets",
			name = "Emit compact navigation packets",
			description = "Include read-only collision/navigation summary packets in the compact live stream."
	)
	default boolean emitCompactNavigationPackets()
	{
		return true;
	}

	@ConfigItem(
			section = normalLiveSection,
			keyName = "compactNavigationEmitCollisionWindow",
			name = "Compact navigation collision window",
			description = "Emit a bounded read-only local collision window around the player for lightweight reachability QA."
	)
	default boolean compactNavigationEmitCollisionWindow()
	{
		return true;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactNavigationCollisionWindowRadius",
			name = "Compact navigation window radius",
			description = "Scene-tile radius for compact collision window packets. Values are clamped between 8 and 52."
	)
	default int compactNavigationCollisionWindowRadius()
	{
		return 24;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactNavigationIncludeFullCollisionGrid",
			name = "Compact navigation full collision grid",
			description = "Debug only: include full collision flag grids in compact live packets when the interval is enabled."
	)
	default boolean compactNavigationIncludeFullCollisionGrid()
	{
		return false;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactNavigationGridIntervalTicks",
			name = "Compact navigation grid interval ticks",
			description = "Debug-only full collision grid packet interval. 0 disables full grid packets."
	)
	default int compactNavigationGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactNavigationFullGridIntervalTicks",
			name = "Compact navigation full grid interval",
			description = "Debug-only full collision grid packet interval. 0 disables full grid packets."
	)
	default int compactNavigationFullGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactNavigationHashOnly",
			name = "Compact navigation hash only",
			description = "Keep normal compact navigation packets to collision summary/hash fields instead of full grid data."
	)
	default boolean compactNavigationHashOnly()
	{
		return true;
	}

	@ConfigItem(
			section = advancedSection,
			keyName = "compactLivePacketTypes",
			name = "Compact live packet types",
			description = "Comma-separated compact packet groups to emit: baseline,sceneDelta,projection,inventory,inventoryDelta,activity,navigation,collisionWindow,collisionGrid,writerHealth, or all."
	)
	default String compactLivePacketTypes()
	{
		return "all";
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayEnabled",
			name = "Telemetry debug overlay",
			description = "Draw a read-only telemetry QA overlay from overlay_debug_state.json. Disabled by default."
	)
	default boolean telemetryDebugOverlayEnabled()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayMode",
			name = "Debug overlay mode",
			description = "Which read-only telemetry details to draw."
	)
	default TelemetryDebugOverlayMode telemetryDebugOverlayMode()
	{
		return TelemetryDebugOverlayMode.CANDIDATES;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayMaxTargets",
			name = "Debug overlay max targets",
			description = "Maximum candidates to draw. The overlay clamps this between 0 and 200."
	)
	default int telemetryDebugOverlayMaxTargets()
	{
		return 25;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowLabels",
			name = "Debug overlay labels",
			description = "Show compact candidate labels."
	)
	default boolean telemetryDebugOverlayShowLabels()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowAimPoints",
			name = "Debug overlay aim points",
			description = "Draw read-only candidate aim point markers."
	)
	default boolean telemetryDebugOverlayShowAimPoints()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowReachability",
			name = "Debug overlay reachability",
			description = "Color and label candidates by read-only reachability when available."
	)
	default boolean telemetryDebugOverlayShowReachability()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayGeometryMode",
			name = "Debug overlay geometry mode",
			description = "Which read-only target geometry to draw. Clickable hull prefers the observed clickbox shape and falls back safely."
	)
	default TelemetryDebugOverlayGeometryMode telemetryDebugOverlayGeometryMode()
	{
		return TelemetryDebugOverlayGeometryMode.CLICKABLE_HULL;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowClickableHull",
			name = "Debug overlay clickable hull",
			description = "Draw observed clickbox/clickable hull polygons when available. Falls back to other geometry without executing actions."
	)
	default boolean telemetryDebugOverlayShowClickableHull()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowBounds",
			name = "Debug overlay bounds",
			description = "Draw compact bounds rectangles when enabled by geometry mode or fallback."
	)
	default boolean telemetryDebugOverlayShowBounds()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowCanvasTilePolygon",
			name = "Debug overlay tile polygon",
			description = "Draw canvas tile polygons for debug. Leave disabled for less clutter."
	)
	default boolean telemetryDebugOverlayShowCanvasTilePolygon()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowCollisionWindow",
			name = "Debug overlay collision window",
			description = "Show collision window summary in the overlay status panel."
	)
	default boolean telemetryDebugOverlayShowCollisionWindow()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowLatestEvent",
			name = "Debug overlay latest event",
			description = "Show one compact read-only live event summary in the overlay status panel."
	)
	default boolean telemetryDebugOverlayShowLatestEvent()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayStatePath",
			name = "Debug overlay state path",
			description = "Optional explicit path to overlay_debug_state.json. Leave blank to use the current telemetry session."
	)
	default String telemetryDebugOverlayStatePath()
	{
		return "";
	}
}

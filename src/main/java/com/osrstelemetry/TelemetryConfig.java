package com.osrstelemetry;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;
import net.runelite.client.config.ConfigSection;

@ConfigGroup("osrs-telemetry")
public interface TelemetryConfig extends Config
{
	@ConfigSection(
			name = "Core",
			description = "Current read-only telemetry runtime settings.",
			position = 0
	)
	String coreSection = "core";

	@ConfigSection(
			name = "Snapshot Endpoint",
			description = "Read-only localhost bridge for the Python daemon and Knowledge Fabric.",
			position = 1
	)
	String pluginSnapshotSection = "pluginSnapshot";

	@ConfigSection(
			name = "Overlay",
			description = "Optional read-only visual overlay settings.",
			position = 2
	)
	String visualQaOverlaySection = "visualQaOverlay";

	@ConfigSection(
			name = "Developer Diagnostics",
			description = "Advanced bounded diagnostics. Leave disabled for normal runtime.",
			position = 3
	)
	String developerDiagnosticsSection = "developerDiagnostics";

	@ConfigItem(
			section = coreSection,
			keyName = "enabled",
			name = "Enable telemetry",
			description = "Keep the read-only plugin telemetry cache active.",
			position = 0
	)
	default boolean enabled()
	{
		return true;
	}

	@ConfigItem(
			section = coreSection,
			keyName = "outputDirectory",
			name = "Output directory",
			description = "Sessions root for bounded manifests, dictionaries, and explicit debug artifacts.",
			position = 1
	)
	default String outputDirectory()
	{
		return System.getProperty("user.home") + "/.osrs-telemetry/sessions";
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "enablePluginSnapshotEndpoint",
			name = "Enable snapshot endpoint",
			description = "Serve cached read-only telemetry on localhost for the current pipeline.",
			position = 0
	)
	default boolean enablePluginSnapshotEndpoint()
	{
		return false;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotHost",
			name = "Snapshot host",
			description = "Bind host for the read-only snapshot endpoint. Use 127.0.0.1 unless intentionally testing otherwise.",
			position = 1
	)
	default String pluginSnapshotHost()
	{
		return "127.0.0.1";
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotPort",
			name = "Snapshot port",
			description = "Local port for the read-only snapshot endpoint.",
			position = 2
	)
	default int pluginSnapshotPort()
	{
		return 8893;
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotAuthToken",
			name = "Snapshot auth token",
			description = "Optional local token. When set, requests must include X-Plugin-Snapshot-Token.",
			position = 3
	)
	default String pluginSnapshotAuthToken()
	{
		return "";
	}

	@ConfigItem(
			section = pluginSnapshotSection,
			keyName = "pluginSnapshotAllowNonLocalHost",
			name = "Allow non-local host",
			description = "Leave disabled for normal use. When false, only loopback hosts are allowed.",
			position = 4
	)
	default boolean pluginSnapshotAllowNonLocalHost()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayEnabled",
			name = "Overlay enabled",
			description = "Draw read-only telemetry markers from overlay_debug_state.json.",
			position = 0
	)
	default boolean telemetryDebugOverlayEnabled()
	{
		return false;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayMode",
			name = "Overlay mode",
			description = "Which read-only telemetry marker set to draw.",
			position = 1
	)
	default TelemetryDebugOverlayMode telemetryDebugOverlayMode()
	{
		return TelemetryDebugOverlayMode.CANDIDATES;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayMaxTargets",
			name = "Max overlay markers",
			description = "Maximum overlay targets to draw. The overlay clamps this between 0 and 200.",
			position = 2
	)
	default int telemetryDebugOverlayMaxTargets()
	{
		return 32;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowLabels",
			name = "Show labels",
			description = "Show compact candidate labels.",
			position = 3
	)
	default boolean telemetryDebugOverlayShowLabels()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowAimPoints",
			name = "Show safe aimpoints",
			description = "Draw read-only aim point markers.",
			position = 4
	)
	default boolean telemetryDebugOverlayShowAimPoints()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayGeometryMode",
			name = "Geometry mode",
			description = "Which read-only target geometry to draw when geometry is available.",
			position = 5
	)
	default TelemetryDebugOverlayGeometryMode telemetryDebugOverlayGeometryMode()
	{
		return TelemetryDebugOverlayGeometryMode.CLICKABLE_HULL;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowClickableHull",
			name = "Show bounds/hulls",
			description = "Draw observed clickbox or hull geometry when available.",
			position = 6
	)
	default boolean telemetryDebugOverlayShowClickableHull()
	{
		return true;
	}

	@ConfigItem(
			section = visualQaOverlaySection,
			keyName = "telemetryDebugOverlayShowBounds",
			name = "Show bounds",
			description = "Draw compact bounds rectangles when enabled by geometry mode or fallback.",
			position = 7
	)
	default boolean telemetryDebugOverlayShowBounds()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "retentionEnabled",
			name = "Enable retention cleanup",
			description = "Delete old closed telemetry segments and sessions when the size cap is exceeded.",
			hidden = true
	)
	default boolean retentionEnabled()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "maxTelemetryGb",
			name = "Max telemetry GB",
			description = "Maximum total telemetry size under the sessions directory.",
			hidden = true
	)
	default int maxTelemetryGb()
	{
		return 2;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "maxSegmentMb",
			name = "Max segment MB",
			description = "Maximum segment size for explicitly enabled raw diagnostic streams.",
			hidden = true
	)
	default int maxSegmentMb()
	{
		return 128;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "cleanupIntervalSeconds",
			name = "Cleanup interval seconds",
			description = "How often retention cleanup checks total telemetry size.",
			hidden = true
	)
	default int cleanupIntervalSeconds()
	{
		return 60;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "preservePinnedSessions",
			name = "Preserve pinned sessions",
			description = "Do not delete sessions containing pinned.flag during retention cleanup.",
			hidden = true
	)
	default boolean preservePinnedSessions()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "allowDeletingClosedSegmentsFromActiveSession",
			name = "Delete closed active segments",
			description = "Allow retention cleanup to delete old closed segments from the current active session.",
			hidden = true
	)
	default boolean allowDeletingClosedSegmentsFromActiveSession()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "pluginSnapshotMaxProjectionRefs",
			name = "Snapshot max projection refs",
			description = "Maximum projection refs returned by the read-only snapshot endpoint.",
			hidden = true
	)
	default int pluginSnapshotMaxProjectionRefs()
	{
		return 500;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "pluginSnapshotMaxResponseBytes",
			name = "Snapshot max response bytes",
			description = "Maximum response bytes for cached snapshot responses. Requests above this fail safely.",
			hidden = true
	)
	default int pluginSnapshotMaxResponseBytes()
	{
		return 1048576;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "sceneCaptureMode",
			name = "Scene capture mode",
			description = "Diagnostic scene object coverage mode.",
			hidden = true
	)
	default SceneCaptureMode sceneCaptureMode()
	{
		return SceneCaptureMode.LOCAL_DEFAULT;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "sceneIndexRescanIntervalTicks",
			name = "Scene index rescan interval",
			description = "Diagnostic static scene index full-resync interval in ticks. 0 disables periodic resync.",
			hidden = true
	)
	default int sceneIndexRescanIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "keepDespawnedSceneObjectsInIndex",
			name = "Keep despawned scene objects",
			description = "Keep despawned scene object records in the diagnostic static scene index.",
			hidden = true
	)
	default boolean keepDespawnedSceneObjectsInIndex()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "maxSceneIndexObjects",
			name = "Max scene index objects",
			description = "Size cap for diagnostic static scene index object count.",
			hidden = true
	)
	default int maxSceneIndexObjects()
	{
		return 50000;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "sceneProjectionRefreshMode",
			name = "Scene projection refresh mode",
			description = "Projection subset for static scene index diagnostic mode.",
			hidden = true
	)
	default SceneProjectionRefreshMode sceneProjectionRefreshMode()
	{
		return SceneProjectionRefreshMode.VISIBLE_AND_NEARBY;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLiveIncludeHeavyGeometry",
			name = "Include heavy geometry",
			description = "Include capped debug polygon geometry in snapshot/live-cache projection data.",
			hidden = true
	)
	default boolean compactLiveIncludeHeavyGeometry()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLiveIncludeClickableHull",
			name = "Include clickable hull",
			description = "Include observed clickbox/clickable hull polygons for capped visible projection refs.",
			hidden = true
	)
	default boolean compactLiveIncludeClickableHull()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLiveGeometryMaxRefs",
			name = "Geometry max refs",
			description = "Maximum visible refs that may include compact polygon geometry. Values are clamped from 0 to 200.",
			hidden = true
	)
	default int compactLiveGeometryMaxRefs()
	{
		return 50;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLiveIncludeCanvasTilePolygon",
			name = "Include tile polygons",
			description = "Include canvas tile polygons for capped visible projection refs.",
			hidden = true
	)
	default boolean compactLiveIncludeCanvasTilePolygon()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLiveIncludeConvexHull",
			name = "Include convex hull",
			description = "Include convex hull fallback polygons for capped visible projection refs.",
			hidden = true
	)
	default boolean compactLiveIncludeConvexHull()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "emitCompactNavigationPackets",
			name = "Emit navigation cache",
			description = "Include bounded collision/navigation summary data in the live cache and snapshot endpoint.",
			hidden = true
	)
	default boolean emitCompactNavigationPackets()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationEmitCollisionWindow",
			name = "Emit collision window",
			description = "Emit a bounded local collision window around the player for lightweight reachability diagnostics.",
			hidden = true
	)
	default boolean compactNavigationEmitCollisionWindow()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationCollisionWindowRadius",
			name = "Collision window radius",
			description = "Scene-tile radius for compact collision windows. Values are clamped between 8 and 52.",
			hidden = true
	)
	default int compactNavigationCollisionWindowRadius()
	{
		return 24;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationIncludeFullCollisionGrid",
			name = "Include full collision grid",
			description = "Debug only: include full collision flag grids when the interval is enabled.",
			hidden = true
	)
	default boolean compactNavigationIncludeFullCollisionGrid()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationGridIntervalTicks",
			name = "Grid interval ticks",
			description = "Debug-only full collision grid refresh interval. 0 disables full grid data.",
			hidden = true
	)
	default int compactNavigationGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationFullGridIntervalTicks",
			name = "Full grid interval",
			description = "Debug-only full collision grid interval. 0 disables full grid data.",
			hidden = true
	)
	default int compactNavigationFullGridIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactNavigationHashOnly",
			name = "Navigation hash only",
			description = "Keep normal navigation cache data to collision summary/hash fields instead of full grid data.",
			hidden = true
	)
	default boolean compactNavigationHashOnly()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "compactLivePacketTypes",
			name = "Live cache payload groups",
			description = "In-memory live cache groups to refresh. This is not a file packet archive.",
			hidden = true
	)
	default String compactLivePacketTypes()
	{
		return "all";
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "telemetryDebugOverlayShowReachability",
			name = "Overlay reachability",
			description = "Color and label candidates by read-only reachability when available.",
			hidden = true
	)
	default boolean telemetryDebugOverlayShowReachability()
	{
		return true;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "telemetryDebugOverlayShowCanvasTilePolygon",
			name = "Overlay tile polygon",
			description = "Draw canvas tile polygons for debug. Leave disabled for less clutter.",
			hidden = true
	)
	default boolean telemetryDebugOverlayShowCanvasTilePolygon()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "telemetryDebugOverlayShowCollisionWindow",
			name = "Overlay collision window",
			description = "Show collision window summary in the overlay status panel.",
			hidden = true
	)
	default boolean telemetryDebugOverlayShowCollisionWindow()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "telemetryDebugOverlayShowLatestEvent",
			name = "Overlay latest event",
			description = "Show one compact read-only live event summary in the overlay status panel.",
			hidden = true
	)
	default boolean telemetryDebugOverlayShowLatestEvent()
	{
		return false;
	}

	@ConfigItem(
			section = developerDiagnosticsSection,
			keyName = "telemetryDebugOverlayStatePath",
			name = "Overlay state path",
			description = "Optional explicit path to overlay_debug_state.json. Leave blank to use the current telemetry session.",
			hidden = true
	)
	default String telemetryDebugOverlayStatePath()
	{
		return "";
	}

	@ConfigItem(keyName = "workflowPreset", name = "Retired workflow preset", description = "Retired from the RuneLite UI.", hidden = true)
	default TelemetryWorkflowPreset workflowPreset()
	{
		return TelemetryWorkflowPreset.DAILY_LIVE;
	}

	@ConfigItem(keyName = "presetPreviewOnly", name = "Retired preset preview", description = "Retired from the RuneLite UI.", hidden = true)
	default boolean presetPreviewOnly()
	{
		return false;
	}

	@ConfigItem(keyName = "applyWorkflowPreset", name = "Retired preset apply", description = "Retired from the RuneLite UI.", hidden = true)
	default boolean applyWorkflowPreset()
	{
		return false;
	}

	@ConfigItem(keyName = "telemetryRecordingMode", name = "Retired recording mode", description = "Normal runtime is snapshot/cache only.", hidden = true)
	default TelemetryRecordingMode telemetryRecordingMode()
	{
		return TelemetryRecordingMode.LIVE_COMPACT_ONLY;
	}

	@ConfigItem(keyName = "debugRecordRawTicks", name = "Retired raw tick recording", description = "Continuous raw tick JSONL is retired from normal runtime.", hidden = true)
	default boolean debugRecordRawTicks()
	{
		return false;
	}

	@ConfigItem(keyName = "debugRecordRawEvents", name = "Retired raw event recording", description = "Continuous raw event JSONL is retired from normal runtime.", hidden = true)
	default boolean debugRecordRawEvents()
	{
		return false;
	}

	@ConfigItem(keyName = "debugRecordFrames", name = "Retired frame recording toggle", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default boolean debugRecordFrames()
	{
		return false;
	}

	@ConfigItem(keyName = "debugFrameIntervalTicks", name = "Retired frame interval", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default int debugFrameIntervalTicks()
	{
		return 5;
	}

	@ConfigItem(keyName = "rawSnapshotSampleIntervalTicks", name = "Retired raw snapshot interval", description = "Raw snapshot sampling is retired from normal runtime.", hidden = true)
	default int rawSnapshotSampleIntervalTicks()
	{
		return 0;
	}

	@ConfigItem(keyName = "captureScreenshots", name = "Retired screenshot capture", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default boolean captureScreenshots()
	{
		return false;
	}

	@ConfigItem(keyName = "screenshotEveryTicks", name = "Retired screenshot interval", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default int screenshotEveryTicks()
	{
		return 1;
	}

	@ConfigItem(keyName = "screenshotFormat", name = "Retired screenshot format", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default String screenshotFormat()
	{
		return "jpg";
	}

	@ConfigItem(keyName = "jpegQuality", name = "Retired JPEG quality", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default double jpegQuality()
	{
		return 0.75;
	}

	@ConfigItem(keyName = "includeFramePathInTicks", name = "Retired frame path in ticks", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default boolean includeFramePathInTicks()
	{
		return false;
	}

	@ConfigItem(keyName = "maxFrameStorageMb", name = "Retired frame storage cap", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default int maxFrameStorageMb()
	{
		return 128;
	}

	@ConfigItem(keyName = "frameCleanupIntervalSeconds", name = "Retired frame cleanup interval", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default int frameCleanupIntervalSeconds()
	{
		return 10;
	}

	@ConfigItem(keyName = "deleteOldFrames", name = "Retired frame cleanup", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default boolean deleteOldFrames()
	{
		return true;
	}

	@ConfigItem(keyName = "maxFrameQueueSize", name = "Retired frame queue size", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default int maxFrameQueueSize()
	{
		return 10;
	}

	@ConfigItem(keyName = "frameCaptureMode", name = "Retired frame capture mode", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default String frameCaptureMode()
	{
		return "RUNELITE_ONLY";
	}

	@ConfigItem(keyName = "allowScreenRectangleFallback", name = "Retired screen rectangle fallback", description = "Screen rectangle frame capture is not part of the current pipeline.", hidden = true)
	default boolean allowScreenRectangleFallback()
	{
		return false;
	}

	@ConfigItem(keyName = "includeClientFrame", name = "Retired client frame capture", description = "Use explicit visual debug bundles for screenshots.", hidden = true)
	default boolean includeClientFrame()
	{
		return false;
	}

	@ConfigItem(keyName = "pluginSnapshotEnabledInNormalLive", name = "Retired normal-live endpoint alias", description = "Use Enable snapshot endpoint instead.", hidden = true)
	default boolean pluginSnapshotEnabledInNormalLive()
	{
		return false;
	}
}

package com.osrstelemetry;

import java.util.Arrays;
import java.util.List;

final class TelemetryConfigKeys
{
	static final String CONFIG_GROUP = "osrs-telemetry";

	static final List<String> EXPOSED_KEYS = Arrays.asList(
			"enabled",
			"outputDirectory",
			"enablePluginSnapshotEndpoint",
			"pluginSnapshotHost",
			"pluginSnapshotPort",
			"pluginSnapshotAuthToken",
			"pluginSnapshotAllowNonLocalHost",
			"telemetryDebugOverlayEnabled",
			"telemetryDebugOverlayMode",
			"telemetryDebugOverlayMaxTargets",
			"telemetryDebugOverlayShowLabels",
			"telemetryDebugOverlayShowAimPoints",
			"telemetryDebugOverlayGeometryMode",
			"telemetryDebugOverlayShowClickableHull",
			"telemetryDebugOverlayShowBounds");

	static final List<String> DEVELOPER_KEYS = Arrays.asList(
			"retentionEnabled",
			"maxTelemetryGb",
			"maxSegmentMb",
			"cleanupIntervalSeconds",
			"preservePinnedSessions",
			"allowDeletingClosedSegmentsFromActiveSession",
			"pluginSnapshotMaxProjectionRefs",
			"pluginSnapshotMaxResponseBytes",
			"sceneCaptureMode",
			"sceneIndexRescanIntervalTicks",
			"keepDespawnedSceneObjectsInIndex",
			"maxSceneIndexObjects",
			"sceneProjectionRefreshMode",
			"compactLiveIncludeHeavyGeometry",
			"compactLiveIncludeClickableHull",
			"compactLiveGeometryMaxRefs",
			"compactLiveIncludeCanvasTilePolygon",
			"compactLiveIncludeConvexHull",
			"emitCompactNavigationPackets",
			"compactNavigationEmitCollisionWindow",
			"compactNavigationCollisionWindowRadius",
			"compactNavigationIncludeFullCollisionGrid",
			"compactNavigationGridIntervalTicks",
			"compactNavigationFullGridIntervalTicks",
			"compactNavigationHashOnly",
			"compactLivePacketTypes",
			"telemetryDebugOverlayShowReachability",
			"telemetryDebugOverlayShowCanvasTilePolygon",
			"telemetryDebugOverlayShowCollisionWindow",
			"telemetryDebugOverlayShowLatestEvent",
			"telemetryDebugOverlayStatePath");

	static final List<String> RETIRED_KEYS = Arrays.asList(
			"workflowPreset",
			"presetPreviewOnly",
			"applyWorkflowPreset",
			"telemetryRecordingMode",
			"debugRecordRawTicks",
			"debugRecordRawEvents",
			"debugRecordFrames",
			"debugFrameIntervalTicks",
			"rawSnapshotSampleIntervalTicks",
			"captureScreenshots",
			"screenshotEveryTicks",
			"screenshotFormat",
			"jpegQuality",
			"includeFramePathInTicks",
			"maxFrameStorageMb",
			"frameCleanupIntervalSeconds",
			"deleteOldFrames",
			"maxFrameQueueSize",
			"frameCaptureMode",
			"allowScreenRectangleFallback",
			"includeClientFrame",
			"pluginSnapshotEnabledInNormalLive",
			"emitCompactLivePackets",
			"emitCompactLiveStream",
			"compactLivePacketsRequiredForLive",
			"compactLiveStreamAlsoWriteFiles",
			"compactLiveStreamHost",
			"compactLiveStreamPort",
			"compactLiveStreamQueueSize",
			"compactLiveStreamCircuitBreakerEnabled",
			"compactLiveStreamMaxWriteMillis",
			"livePacketArchiveEnabled",
			"livePacketDirectory",
			"livePacketSegmentMb");

	private TelemetryConfigKeys()
	{
	}
}

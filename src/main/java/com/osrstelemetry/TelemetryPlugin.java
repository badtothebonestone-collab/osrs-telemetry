package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.awt.Canvas;
import java.awt.Dimension;
import java.awt.GraphicsConfiguration;
import java.awt.Image;
import java.awt.IllegalComponentStateException;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.Shape;
import java.awt.Window;
import java.awt.geom.PathIterator;
import java.awt.geom.AffineTransform;
import java.awt.image.BufferedImage;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import javax.inject.Inject;
import javax.swing.SwingUtilities;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.CollisionData;
import net.runelite.api.CollisionDataFlag;
import net.runelite.api.DecorativeObject;
import net.runelite.api.GameState;
import net.runelite.api.GameObject;
import net.runelite.api.GraphicsObject;
import net.runelite.api.GroundObject;
import net.runelite.api.Hitsplat;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.MenuEntry;
import net.runelite.api.NPC;
import net.runelite.api.NPCComposition;
import net.runelite.api.ObjectComposition;
import net.runelite.api.Perspective;
import net.runelite.api.Player;
import net.runelite.api.Point;
import net.runelite.api.Prayer;
import net.runelite.api.Projectile;
import net.runelite.api.Scene;
import net.runelite.api.Skill;
import net.runelite.api.Tile;
import net.runelite.api.TileItem;
import net.runelite.api.TileObject;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.ActorDeath;
import net.runelite.api.events.AnimationChanged;
import net.runelite.api.events.ChatMessage;
import net.runelite.api.events.ClientTick;
import net.runelite.api.events.DecorativeObjectDespawned;
import net.runelite.api.events.DecorativeObjectSpawned;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GameObjectDespawned;
import net.runelite.api.events.GameObjectSpawned;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GraphicsObjectCreated;
import net.runelite.api.events.GroundObjectDespawned;
import net.runelite.api.events.GroundObjectSpawned;
import net.runelite.api.events.HitsplatApplied;
import net.runelite.api.events.InteractingChanged;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.api.events.ItemDespawned;
import net.runelite.api.events.ItemQuantityChanged;
import net.runelite.api.events.ItemSpawned;
import net.runelite.api.events.MenuOpened;
import net.runelite.api.events.MenuOptionClicked;
import net.runelite.api.events.NpcChanged;
import net.runelite.api.events.NpcDespawned;
import net.runelite.api.events.NpcSpawned;
import net.runelite.api.events.OverheadTextChanged;
import net.runelite.api.events.PlayerChanged;
import net.runelite.api.events.PlayerDespawned;
import net.runelite.api.events.PlayerSpawned;
import net.runelite.api.events.PostMenuSort;
import net.runelite.api.events.ProjectileMoved;
import net.runelite.api.events.StatChanged;
import net.runelite.api.events.VarClientIntChanged;
import net.runelite.api.events.VarClientStrChanged;
import net.runelite.api.events.VarbitChanged;
import net.runelite.api.events.WallObjectDespawned;
import net.runelite.api.events.WallObjectSpawned;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.events.ConfigChanged;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.ui.DrawManager;
import net.runelite.client.ui.overlay.OverlayManager;
import net.runelite.client.util.ImageCapture;
import net.runelite.client.util.ImageUtil;
import net.runelite.api.events.WidgetClosed;
import net.runelite.api.events.WidgetLoaded;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;

@Slf4j
@PluginDescriptor(
		name = "Telemetry Collector",
		description = "Read-only telemetry logger for external analysis",
		tags = {"telemetry", "data", "logger"}
)
public class TelemetryPlugin extends Plugin
{
	private static final int MAX_GROUND_ITEMS = 250;
	private static final int INVENTORY_SLOT_COUNT = 28;
	private static final String PACKET_BASELINE = "live_baseline_packet.v1";
	private static final String PACKET_SCENE_DELTA = "live_scene_delta_packet.v1";
	private static final String PACKET_PROJECTION = "live_projection_packet.v1";
	private static final String PACKET_INVENTORY = "live_inventory_packet.v1";
	private static final String PACKET_INVENTORY_DELTA = "live_inventory_delta_packet.v1";
	private static final String PACKET_ACTIVITY = "live_activity_packet.v1";
	private static final String PACKET_NAVIGATION = "live_navigation_packet.v1";
	private static final String PACKET_COLLISION_WINDOW = "live_collision_window_packet.v1";
	private static final String PACKET_COLLISION_GRID = "live_collision_grid_packet.v1";
	private static final String PACKET_BANK_UI = "live_bank_ui_packet.v1";
	private static final String PACKET_DIALOGUE_STATE = "live_dialogue_state_packet.v1";
	private static final String PACKET_COMBAT_STATE = "live_combat_state_packet.v1";
	private static final String PACKET_WRITER_HEALTH = "live_writer_health_packet.v1";
	private static final int DIALOGUE_WIDGET_SCAN_LIMIT = 160;
	private static final int MAX_SERVICE_SCENE_OBJECTS = 32;
	private static final int SERVICE_SCENE_OBJECT_RADIUS = 48;
	private static final int MAX_RECENT_COMBAT_EVENTS = 20;
	private static final int MAX_COMBAT_ACTORS = 12;
	private static final int COMBAT_NPC_RADIUS_TILES = 16;
	private static final int COMPACT_LIVE_GEOMETRY_MAX_REFS_HARD_CAP = 200;
	private static final int COLLISION_MOVEMENT_MASK = CollisionDataFlag.BLOCK_MOVEMENT_NORTH_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_NORTH
			| CollisionDataFlag.BLOCK_MOVEMENT_NORTH_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_EAST
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH
			| CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_WEST
			| CollisionDataFlag.BLOCK_MOVEMENT_OBJECT
			| CollisionDataFlag.BLOCK_MOVEMENT_FLOOR_DECORATION
			| CollisionDataFlag.BLOCK_MOVEMENT_FLOOR
			| CollisionDataFlag.BLOCK_MOVEMENT_FULL;

	@Inject
	private Client client;

	@Inject
	private ClientThread clientThread;

	@Inject
	private Gson gson;

	@Inject
	private TelemetryConfig config;

	@Inject
	private ConfigManager configManager;

	@Inject
	private DrawManager drawManager;

	@Inject
	private OverlayManager overlayManager;

	@Inject
	private TelemetryDebugOverlay debugOverlay;

	@Inject
	private ImageCapture imageCapture;

	private TelemetryWriter writer;
	private PluginLiveCache liveCache;
	private PluginSnapshotEndpoint pluginSnapshotEndpoint;
	private final ClientTickHotState clientTickHotState = new ClientTickHotState();
	private final WorldModelCache worldModelCache = new WorldModelCache();
	private volatile Map<String, Object> latestHoverMenu;
	private volatile Map<String, Object> lastMenuOptionClicked;
	private long tickId = 0;
	private long clientTickId = 0;
	private long eventSeq = 0;
	private final List<Map<String, Object>> recentInteractingChanges = new ArrayList<>();
	private final List<Map<String, Object>> recentHitsplats = new ArrayList<>();
	private final List<Map<String, Object>> recentActorDeaths = new ArrayList<>();
	private final List<Map<String, Object>> recentAnimations = new ArrayList<>();
	private final List<Map<String, Object>> recentGraphics = new ArrayList<>();
	private final List<Map<String, Object>> recentOverheadText = new ArrayList<>();
	private final List<Map<String, Object>> recentChatMessages = new ArrayList<>();
	private final List<Map<String, Object>> recentStatChanges = new ArrayList<>();
	private final Set<Integer> knownItemIds = new HashSet<>();
	private final Set<Integer> knownNpcIds = new HashSet<>();
	private final Set<Integer> knownObjectIds = new HashSet<>();
	private final Map<Integer, DefinitionName> itemNameCache = new LinkedHashMap<>();
	private final Map<Integer, DefinitionName> npcNameCache = new LinkedHashMap<>();
	private final Map<Integer, DefinitionName> objectNameCache = new LinkedHashMap<>();
	private final Map<String, SceneIndexEntry> sceneObjectIndex = new LinkedHashMap<>();
	private final Set<String> dirtySceneObjectKeys = new HashSet<>();
	private final Map<String, TickSnapshot.SceneObjectSnapshot> sceneProjectionCache = new LinkedHashMap<>();
	private boolean sceneIndexNeedsFullResync = true;
	private String sceneIndexResyncReason = "startup";
	private int sceneIndexPlane = -1;
	private long lastSceneIndexResyncTick = -1;
	private String lastSceneProjectionStateHash;
	private Map<String, Object> lastCompactInventorySnapshot;
	private Map<String, Object> lastCompactBankContainerSnapshot;
	private int lastActivityAnimation = Integer.MIN_VALUE;
	private int lastActivityPoseAnimation = Integer.MIN_VALUE;
	private String lastActivityInteractingSignature;
	private boolean debugOverlayRegistered;
	private int lastCompactGeometryRefsWithPolygons;
	private int lastCompactGeometryRefsSkippedByCap;
	private int lastCompactGeometryMaxRefs;
	private boolean lastCompactGeometryCapHit;
	private boolean lastCompactLiveIncludeClickableHull;
	private boolean lastCompactLiveIncludeCanvasTilePolygon;
	private boolean lastCompactLiveIncludeConvexHull;
	private int lastCompactHullsEmitted;
	private int lastCompactHullDroppedOffscreen;
	private int lastCompactHullDroppedNoCanvasIntersection;
	private int lastCompactHullDroppedByCap;
	private int lastCompactHullDroppedNullClickbox;

	@Provides
	TelemetryConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(TelemetryConfig.class);
	}

	private void cleanupRetiredConfigKeys()
	{
		if (configManager == null)
		{
			return;
		}

		String oldEndpointAlias = configManager.getConfiguration(
				TelemetryConfigKeys.CONFIG_GROUP,
				"pluginSnapshotEnabledInNormalLive");
		String currentEndpoint = configManager.getConfiguration(
				TelemetryConfigKeys.CONFIG_GROUP,
				"enablePluginSnapshotEndpoint");
		if (Boolean.parseBoolean(oldEndpointAlias) && !Boolean.parseBoolean(currentEndpoint))
		{
			configManager.setConfiguration(TelemetryConfigKeys.CONFIG_GROUP, "enablePluginSnapshotEndpoint", true);
		}

		int removed = 0;
		for (String key : TelemetryConfigKeys.RETIRED_KEYS)
		{
			if (configManager.getConfiguration(TelemetryConfigKeys.CONFIG_GROUP, key) != null)
			{
				configManager.unsetConfiguration(TelemetryConfigKeys.CONFIG_GROUP, key);
				removed++;
			}
		}

		if (removed > 0)
		{
			log.info("Cleaned {} retired Telemetry Collector config keys", removed);
		}
	}

	@Override
	protected void startUp() throws Exception
	{
		cleanupRetiredConfigKeys();
		TelemetryRecordingMode recordingMode = recordingMode();

		knownItemIds.clear();
		knownNpcIds.clear();
		knownObjectIds.clear();
		itemNameCache.clear();
		npcNameCache.clear();
		objectNameCache.clear();
		clearSceneIndex("startup");
		lastCompactInventorySnapshot = null;
		lastCompactBankContainerSnapshot = null;
		lastActivityAnimation = Integer.MIN_VALUE;
		lastActivityPoseAnimation = Integer.MIN_VALUE;
		lastActivityInteractingSignature = null;
		liveCache = new PluginLiveCache(gson);
		worldModelCache.clear("startup");

		writer = new TelemetryWriter(
				config.outputDirectory(),
				gson,
				config.maxSegmentMb(),
				config.retentionEnabled(),
				config.maxTelemetryGb(),
				config.cleanupIntervalSeconds(),
				config.preservePinnedSessions(),
				config.allowDeletingClosedSegmentsFromActiveSession(),
				recordingMode,
				rawTickRecordingEnabled(recordingMode),
				rawEventRecordingEnabled(recordingMode),
				frameRecordingEnabled(recordingMode),
				effectiveScreenshotEveryTicks(recordingMode),
				config.screenshotFormat(),
				config.jpegQuality(),
				config.maxFrameStorageMb(),
				config.frameCleanupIntervalSeconds(),
				config.deleteOldFrames(),
				config.maxFrameQueueSize(),
				config.frameCaptureMode(),
				config.allowScreenRectangleFallback(),
				liveCache);
		writer.start();
		startPluginSnapshotEndpoint(recordingMode);
		if (!debugOverlayRegistered)
		{
			overlayManager.add(debugOverlay);
			debugOverlayRegistered = true;
		}

		log.info("Telemetry Collector started");
	}

	private TelemetryRecordingMode recordingMode()
	{
		TelemetryRecordingMode mode = config.telemetryRecordingMode();
		return mode == null ? TelemetryRecordingMode.LIVE_COMPACT_ONLY : mode;
	}

	private boolean rawTickRecordingEnabled(TelemetryRecordingMode mode)
	{
		return mode == TelemetryRecordingMode.DEBUG_RECORDING || config.debugRecordRawTicks();
	}

	private boolean rawEventRecordingEnabled(TelemetryRecordingMode mode)
	{
		return mode == TelemetryRecordingMode.DEBUG_RECORDING || config.debugRecordRawEvents();
	}

	private boolean frameRecordingEnabled(TelemetryRecordingMode mode)
	{
		if (!config.captureScreenshots() || !config.debugRecordFrames())
		{
			return false;
		}

		return mode == TelemetryRecordingMode.LIVE_COMPACT_WITH_FRAMES
				|| mode == TelemetryRecordingMode.DEBUG_RECORDING
				|| mode == TelemetryRecordingMode.HYBRID_DEBUG;
	}

	private int effectiveScreenshotEveryTicks(TelemetryRecordingMode mode)
	{
		if (mode == TelemetryRecordingMode.LIVE_COMPACT_WITH_FRAMES)
		{
			return Math.max(1, config.debugFrameIntervalTicks());
		}

		return Math.max(1, config.screenshotEveryTicks());
	}

	@Override
	protected void shutDown() throws Exception
	{
		if (debugOverlayRegistered)
		{
			overlayManager.remove(debugOverlay);
			debugOverlayRegistered = false;
		}
		stopPluginSnapshotEndpoint();
		if (writer != null)
		{
			writer.close();
			writer = null;
		}
		liveCache = null;
		clearSceneIndex("shutdown");
		worldModelCache.clear("shutdown");

		log.info("Telemetry Collector stopped");
	}

	private void startPluginSnapshotEndpoint(TelemetryRecordingMode recordingMode)
	{
		if (!pluginSnapshotEndpointEnabled(recordingMode) || pluginSnapshotEndpoint != null || liveCache == null)
		{
			return;
		}

		pluginSnapshotEndpoint = new PluginSnapshotEndpoint(
				liveCache,
				gson,
				config.pluginSnapshotHost(),
				config.pluginSnapshotPort(),
				config.pluginSnapshotAuthToken(),
				config.pluginSnapshotMaxProjectionRefs(),
				config.pluginSnapshotMaxResponseBytes(),
				config.pluginSnapshotAllowNonLocalHost(),
				new TelemetryPresetApplier(configManager),
				clientTickHotState,
				this::pluginSnapshotTileProjections,
				this::pluginSnapshotWorldModelQuery);
		try
		{
			pluginSnapshotEndpoint.start();
		}
		catch (Exception e)
		{
			log.warn("Plugin snapshot endpoint failed to start; live runtime file archives are retired", e);
			stopPluginSnapshotEndpoint();
		}
	}

	private boolean pluginSnapshotEndpointEnabled(TelemetryRecordingMode recordingMode)
	{
		return config.enablePluginSnapshotEndpoint();
	}

	private void stopPluginSnapshotEndpoint()
	{
		if (pluginSnapshotEndpoint == null)
		{
			return;
		}

		pluginSnapshotEndpoint.close();
		pluginSnapshotEndpoint = null;
	}

	Path currentOverlayDebugStatePath()
	{
		TelemetryWriter currentWriter = writer;
		if (currentWriter == null)
		{
			return null;
		}
		return currentWriter.getSessionDir().resolve("interaction_geometry").resolve("live").resolve("overlay_debug_state.json");
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (event == null || !TelemetryConfigKeys.CONFIG_GROUP.equals(event.getGroup()))
		{
			return;
		}

		String key = event.getKey();
		if ("applyWorkflowPreset".equals(key) && config.applyWorkflowPreset())
		{
			applySelectedWorkflowPreset();
			return;
		}

		if (isSnapshotEndpointConfigKey(key))
		{
			restartPluginSnapshotEndpoint();
		}
	}

	private void applySelectedWorkflowPreset()
	{
		TelemetryWorkflowPreset preset = config.workflowPreset();
		if (preset == null)
		{
			preset = TelemetryWorkflowPreset.DAILY_LIVE;
		}
		boolean preview = config.presetPreviewOnly();
		TelemetryPresetApplier applier = new TelemetryPresetApplier(configManager);
		Map<String, Object> result = applier.apply(preset.name(), preview);
		log.info("Telemetry workflow preset {} {}: {}", preset.name(), preview ? "preview" : "apply", result.get("status"));
		configManager.setConfiguration(TelemetryPresetApplier.CONFIG_GROUP, "applyWorkflowPreset", false);
		if (!preview)
		{
			restartPluginSnapshotEndpoint();
		}
	}

	private boolean isSnapshotEndpointConfigKey(String key)
	{
		return Set.of(
				"enablePluginSnapshotEndpoint",
				"pluginSnapshotHost",
				"pluginSnapshotPort",
				"pluginSnapshotAuthToken",
				"pluginSnapshotMaxProjectionRefs",
				"pluginSnapshotMaxResponseBytes",
				"pluginSnapshotAllowNonLocalHost").contains(key);
	}

	private void restartPluginSnapshotEndpoint()
	{
		stopPluginSnapshotEndpoint();
		startPluginSnapshotEndpoint(recordingMode());
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		long snapshotStartNanos = System.nanoTime();
		TelemetryWriter currentWriter = writer;

		if (!config.enabled() || currentWriter == null)
		{
			return;
		}

		tickId++;

		TickSnapshot snapshot = new TickSnapshot();
		List<String> captureErrors = new ArrayList<>();
		snapshot.schemaVersion = "0.1.0";
		snapshot.tickId = tickId;
		snapshot.timestampUtc = Instant.now().toString();
		GameState gameState = null;

		try
		{
			gameState = client.getGameState();
			snapshot.gameState = String.valueOf(gameState);
		}
		catch (Exception e)
		{
			recordCaptureFailure(captureErrors, "gameState", e);
		}

		try
		{
			if (gameState == GameState.LOGGED_IN)
			{
				safeCapture(captureErrors, "cameraViewport", () -> captureCameraViewport(snapshot));
				safeCapture(captureErrors, "localPlayer", () -> captureLocalPlayer(snapshot));
				safeCapture(captureErrors, "inventory", () -> captureInventory(snapshot));
				safeCapture(captureErrors, "equipment", () -> captureEquipment(snapshot));
				safeCapture(captureErrors, "skills", () -> captureSkills(snapshot));
				safeCapture(captureErrors, "npcs", () -> captureNpcs(snapshot));
				safeCapture(captureErrors, "players", () -> capturePlayers(snapshot));
				safeCapture(captureErrors, "widgets", () -> captureWidgets(snapshot));
				safeCapture(captureErrors, "bankUi", () -> captureBankUi(snapshot));
				safeCapture(captureErrors, "dialogueState", () -> captureDialogueState(snapshot));
				safeCapture(captureErrors, "scene", () -> captureScene(snapshot));
				safeCapture(captureErrors, "status", () -> captureStatus(snapshot));
				safeCapture(captureErrors, "activePrayers", () -> captureActivePrayers(snapshot));
			}
		}
		finally
		{
			snapshot.captureErrors = captureErrors.toArray(new String[0]);
			snapshot.writerQueueSize = currentWriter.getQueueSize();
			snapshot.writerDroppedRecords = currentWriter.getDroppedRecords();
			snapshot.snapshotBuildDurationMillis = elapsedMillis(snapshotStartNanos);
			boolean tickEnqueuedAsync = captureFrame(snapshot, captureErrors, currentWriter);
			snapshot.captureErrors = captureErrors.toArray(new String[0]);

			if (!tickEnqueuedAsync)
			{
				enqueueTickSnapshot(currentWriter, snapshot);
			}
		}
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("gameState", String.valueOf(event.getGameState()));

		logEvent("GameStateChanged", payload);
		recordGameStateHotSample(event.getGameState());

		if (event.getGameState() == GameState.LOADING
				|| event.getGameState() == GameState.LOGIN_SCREEN
				|| event.getGameState() == GameState.HOPPING)
		{
			clearSceneIndex("gameState:" + event.getGameState());
			worldModelCache.clear("gameState:" + event.getGameState());
		}
	}

	private void recordGameStateHotSample(GameState gameState)
	{
		Map<String, Object> payload = clientTickPayload("GameStateChanged");
		payload.put("gameState", gameState == null ? null : String.valueOf(gameState));
		clientTickHotState.recordClientTick(payload);
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		logEvent("ItemContainerChanged", itemContainerPayload(event));
	}

	@Subscribe
	public void onGameObjectSpawned(GameObjectSpawned event)
	{
		indexSceneObjectFromEvent("GAME_OBJECT", event.getGameObject(), event.getGameObject() == null ? -1 : event.getGameObject().getOrientation());
		worldModelCache.markDirty("gameObjectSpawned");
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		despawnSceneObjectFromEvent("GAME_OBJECT", event.getGameObject(), event.getGameObject() == null ? -1 : event.getGameObject().getOrientation());
		worldModelCache.markDirty("gameObjectDespawned");
	}

	@Subscribe
	public void onWallObjectSpawned(WallObjectSpawned event)
	{
		indexSceneObjectFromEvent("WALL_OBJECT", event.getWallObject(), event.getWallObject() == null ? -1 : event.getWallObject().getOrientationA());
		worldModelCache.markDirty("wallObjectSpawned");
	}

	@Subscribe
	public void onWallObjectDespawned(WallObjectDespawned event)
	{
		despawnSceneObjectFromEvent("WALL_OBJECT", event.getWallObject(), event.getWallObject() == null ? -1 : event.getWallObject().getOrientationA());
		worldModelCache.markDirty("wallObjectDespawned");
	}

	@Subscribe
	public void onDecorativeObjectSpawned(DecorativeObjectSpawned event)
	{
		indexSceneObjectFromEvent("DECORATIVE_OBJECT", event.getDecorativeObject(), -1);
		worldModelCache.markDirty("decorativeObjectSpawned");
	}

	@Subscribe
	public void onDecorativeObjectDespawned(DecorativeObjectDespawned event)
	{
		despawnSceneObjectFromEvent("DECORATIVE_OBJECT", event.getDecorativeObject(), -1);
		worldModelCache.markDirty("decorativeObjectDespawned");
	}

	@Subscribe
	public void onGroundObjectSpawned(GroundObjectSpawned event)
	{
		indexSceneObjectFromEvent("GROUND_OBJECT", event.getGroundObject(), -1);
		worldModelCache.markDirty("groundObjectSpawned");
	}

	@Subscribe
	public void onGroundObjectDespawned(GroundObjectDespawned event)
	{
		despawnSceneObjectFromEvent("GROUND_OBJECT", event.getGroundObject(), -1);
		worldModelCache.markDirty("groundObjectDespawned");
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("skill", String.valueOf(event.getSkill()));
		payload.put("xp", event.getXp());
		payload.put("level", event.getLevel());
		payload.put("boostedLevel", event.getBoostedLevel());

		rememberRecent(recentStatChanges, payload);
		logEvent("StatChanged", payload);
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("type", event.getType() == null ? null : String.valueOf(event.getType()));
		payload.put("name", truncate(event.getName(), 128));
		payload.put("sender", truncate(event.getSender(), 128));
		payload.put("message", truncate(event.getMessage(), 256));
		payload.put("timestamp", event.getTimestamp());

		rememberRecent(recentChatMessages, payload);
		logEvent("ChatMessage", payload);
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		Map<String, Object> payload = hoverMenuPayload();
		payload.put("sampleSource", "MenuOpened");
		payload.put("sourceEvent", "MenuOpened");
		payload.put("menuEntryCount", event.getMenuEntries() == null ? 0 : event.getMenuEntries().length);
		logEvent("MenuOpened", payload);
		clientTickHotState.recordPostMenuSort(payload);
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		clientTickId++;
		clientTickHotState.recordClientTick(clientTickPayload("ClientTick"));
	}

	@Subscribe
	public void onPostMenuSort(PostMenuSort event)
	{
		Map<String, Object> payload = hoverMenuPayload();
		latestHoverMenu = payload;
		clientTickHotState.recordPostMenuSort(payload);
	}

	@Subscribe
	public void onMenuOptionClicked(MenuOptionClicked event)
	{
		Map<String, Object> payload = menuOptionClickedPayload(event);
		lastMenuOptionClicked = payload;
		clientTickHotState.recordMenuOptionClicked(payload);
	}

	@Subscribe
	public void onVarbitChanged(VarbitChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("index", event.getIndex());
		payload.put("varpId", event.getVarpId());
		payload.put("varbitId", event.getVarbitId());
		payload.put("value", event.getValue());

		logEvent("VarbitChanged", payload);
	}

	@Subscribe
	public void onVarClientIntChanged(VarClientIntChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		int index = event.getIndex();
		payload.put("index", index);
		payload.put("value", client.getVarcIntValue(index));

		logEvent("VarClientIntChanged", payload);
	}

	@Subscribe
	public void onVarClientStrChanged(VarClientStrChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		int index = event.getIndex();
		payload.put("index", index);
		payload.put("value", truncate(client.getVarcStrValue(index), 256));

		logEvent("VarClientStrChanged", payload);
	}

	@Subscribe
	public void onAnimationChanged(AnimationChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("actor", actorPayload(event.getActor()));

		rememberRecent(recentAnimations, payload);
		logEvent("AnimationChanged", payload);
	}

	@Subscribe
	public void onInteractingChanged(InteractingChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("source", actorPayload(event.getSource()));
		payload.put("target", actorPayload(event.getTarget()));

		rememberRecent(recentInteractingChanges, payload);
		logEvent("InteractingChanged", payload);
	}

	@Subscribe
	public void onHitsplatApplied(HitsplatApplied event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Hitsplat hitsplat = event.getHitsplat();
		payload.put("actor", actorPayload(event.getActor()));

		if (hitsplat != null)
		{
			payload.put("hitsplatType", hitsplat.getHitsplatType());
			payload.put("amount", hitsplat.getAmount());
			payload.put("disappearsOnGameCycle", hitsplat.getDisappearsOnGameCycle());
			payload.put("mine", hitsplat.isMine());
			payload.put("others", hitsplat.isOthers());
		}

		rememberRecent(recentHitsplats, payload);
		logEvent("HitsplatApplied", payload);
	}

	@Subscribe
	public void onProjectileMoved(ProjectileMoved event)
	{
		Map<String, Object> payload = projectilePayload(event.getProjectile());
		LocalPoint position = event.getPosition();
		payload.put("z", event.getZ());

		if (position != null)
		{
			payload.put("localX", position.getX());
			payload.put("localY", position.getY());
			WorldPoint worldPoint = WorldPoint.fromLocal(client, position);

			if (worldPoint != null)
			{
				payload.put("worldX", worldPoint.getX());
				payload.put("worldY", worldPoint.getY());
				payload.put("plane", worldPoint.getPlane());
			}
		}

		logEvent("ProjectileMoved", payload);
	}

	@Subscribe
	public void onGraphicsObjectCreated(GraphicsObjectCreated event)
	{
		Map<String, Object> payload = graphicsObjectPayload(event.getGraphicsObject());
		rememberRecent(recentGraphics, payload);
		logEvent("GraphicsObjectCreated", payload);
	}

	@Subscribe
	public void onOverheadTextChanged(OverheadTextChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("actor", actorPayload(event.getActor()));
		payload.put("text", truncate(event.getOverheadText(), 256));

		rememberRecent(recentOverheadText, payload);
		logEvent("OverheadTextChanged", payload);
	}

	@Subscribe
	public void onNpcSpawned(NpcSpawned event)
	{
		rememberNpc(event.getNpc());
		logEvent("NpcSpawned", actorPayload(event.getNpc()));
	}

	@Subscribe
	public void onNpcDespawned(NpcDespawned event)
	{
		rememberNpc(event.getNpc());
		logEvent("NpcDespawned", actorPayload(event.getNpc()));
	}

	@Subscribe
	public void onNpcChanged(NpcChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		rememberNpc(event.getNpc());
		payload.put("npc", actorPayload(event.getNpc()));

		if (event.getOld() != null)
		{
			payload.put("oldId", event.getOld().getId());
			payload.put("oldName", event.getOld().getName());
		}

		logEvent("NpcChanged", payload);
	}

	@Subscribe
	public void onActorDeath(ActorDeath event)
	{
		if (event.getActor() instanceof NPC)
		{
			rememberNpc((NPC) event.getActor());
			Map<String, Object> payload = actorPayload(event.getActor());
			rememberRecent(recentActorDeaths, payload);
			logEvent("NpcDeath", payload);
		}
	}

	private boolean captureFrame(TickSnapshot snapshot, List<String> captureErrors, TelemetryWriter currentWriter)
	{
		if (!currentWriter.isFrameRecordingEnabled())
		{
			snapshot.frameCaptureStatus = "DISABLED_BY_RECORDING_MODE";
			currentWriter.recordFrameSuppressedByMode();
			return false;
		}

		int interval = currentWriter.getScreenshotEveryTicks();

		if (interval <= 0)
		{
			snapshot.frameCaptureStatus = "DISABLED";
			return false;
		}

		if (snapshot.tickId % interval != 0)
		{
			snapshot.frameCaptureStatus = "SKIPPED_INTERVAL";
			return false;
		}

		String format = normalizeScreenshotFormat(config.screenshotFormat());
		String relativePath = String.format("frames/frame-tick-%08d.%s", snapshot.tickId, format);
		String captureMode = normalizeFrameCaptureMode(config.frameCaptureMode());
		String requestedAtUtc = Instant.now().toString();
		snapshot.frameCaptureSource = captureMode;

		try
		{
			if ("RUNELITE_ONLY".equals(captureMode))
			{
				if (config.includeFramePathInTicks())
				{
					snapshot.framePath = relativePath;
				}

				snapshot.frameCaptureStatus = "QUEUED";
				requestRuneliteOnlyFrame(relativePath, currentWriter, snapshot, captureErrors);
				return true;
			}

			if (!config.allowScreenRectangleFallback())
			{
				snapshot.frameCaptureStatus = "CAPTURE_FAILED";
				snapshot.frameCaptureWarning = "Screen rectangle fallback is disabled";
				captureErrors.add("frame");
				currentWriter.recordFrameIndex(
						snapshot.tickId,
						relativePath,
						captureMode,
						snapshot.frameCaptureStatus,
						requestedAtUtc,
						Instant.now().toString(),
						snapshot.frameCaptureWarning);
				return false;
			}

			BufferedImage frame = captureScreenRectangle();
			String capturedAtUtc = Instant.now().toString();

			if (frame == null)
			{
				snapshot.frameCaptureStatus = "CAPTURE_FAILED";
				captureErrors.add("frame");
				currentWriter.recordFrameIndex(
						snapshot.tickId,
						relativePath,
						"SCREEN_RECTANGLE",
						snapshot.frameCaptureStatus,
						requestedAtUtc,
						capturedAtUtc,
						"screen rectangle capture returned null");
				return false;
			}

			snapshot.frameCaptureSource = "SCREEN_RECTANGLE";
			snapshot.frameCaptureWarning = "Screen rectangle capture may include overlapping windows";

			if (config.includeFramePathInTicks())
			{
				snapshot.framePath = relativePath;
			}

			if (currentWriter.enqueueFrame(
					snapshot.tickId,
					relativePath,
					frame,
					snapshot.frameCaptureSource,
					requestedAtUtc,
					capturedAtUtc))
			{
				snapshot.frameCaptureStatus = "QUEUED";
			}
			else
			{
				snapshot.frameCaptureStatus = "DROPPED_QUEUE_FULL";
			}
		}
		catch (Exception e)
		{
			snapshot.frameCaptureStatus = "CAPTURE_FAILED";
			captureErrors.add("frame");
			currentWriter.recordFrameIndex(
					snapshot.tickId,
					relativePath,
					captureMode,
					snapshot.frameCaptureStatus,
					requestedAtUtc,
					Instant.now().toString(),
					e.toString());
			log.warn("Telemetry frame capture failed", e);
		}

		return false;
	}

	private void requestRuneliteOnlyFrame(String relativePath, TelemetryWriter currentWriter, TickSnapshot snapshot, List<String> captureErrors)
	{
		String requestedAtUtc = Instant.now().toString();

		drawManager.requestNextFrameListener((image) ->
		{
			try
			{
				BufferedImage frame = copyRuneliteFrame(image);
				String capturedAtUtc = Instant.now().toString();

				if (frame == null)
				{
					snapshot.frameCaptureStatus = "CAPTURE_FAILED";
					captureErrors.add("frame");
					currentWriter.recordFrameIndex(
							snapshot.tickId,
							relativePath,
							snapshot.frameCaptureSource,
							snapshot.frameCaptureStatus,
							requestedAtUtc,
							capturedAtUtc,
							"RuneLite frame capture returned null");
				}
				else if (currentWriter.enqueueFrame(
						snapshot.tickId,
						relativePath,
						frame,
						snapshot.frameCaptureSource,
						requestedAtUtc,
						capturedAtUtc))
				{
					snapshot.frameCaptureStatus = "QUEUED";
				}
				else
				{
					snapshot.frameCaptureStatus = "DROPPED_QUEUE_FULL";
				}
			}
			catch (Exception e)
			{
				snapshot.frameCaptureStatus = "CAPTURE_FAILED";
				captureErrors.add("frame");
				currentWriter.recordFrameIndex(
						snapshot.tickId,
						relativePath,
						snapshot.frameCaptureSource,
						snapshot.frameCaptureStatus,
						requestedAtUtc,
						Instant.now().toString(),
						e.toString());
				log.warn("Telemetry RuneLite-only frame capture failed", e);
			}
			finally
			{
				snapshot.captureErrors = captureErrors.toArray(new String[0]);
				enqueueTickSnapshot(currentWriter, snapshot);
			}
		});
	}

	private void enqueueTickSnapshot(TelemetryWriter currentWriter, TickSnapshot snapshot)
	{
		try
		{
			enqueueCompactLivePackets(currentWriter, snapshot);
			if (currentWriter.isRawTickRecordingEnabled())
			{
				currentWriter.enqueueTick(gson.toJson(snapshot));
			}
			else
			{
				currentWriter.recordRawTickSuppressedByMode();
			}
		}
		catch (Exception e)
		{
			log.warn("Failed to enqueue tick telemetry", e);
		}
	}

	private void enqueueCompactLivePackets(TelemetryWriter currentWriter, TickSnapshot snapshot)
	{
		if (!currentWriter.isCompactLivePacketsEnabled() || snapshot == null)
		{
			return;
		}

		if (compactPacketTypeEnabled("baseline"))
		{
			currentWriter.enqueueLivePacket(PACKET_BASELINE, snapshot.tickId, snapshot.timestampUtc, baselinePayload(snapshot));
		}

		if (compactPacketTypeEnabled("sceneDelta"))
		{
			currentWriter.enqueueLivePacket(PACKET_SCENE_DELTA, snapshot.tickId, snapshot.timestampUtc, sceneDeltaPayload(snapshot));
		}

		if (compactPacketTypeEnabled("projection"))
		{
			currentWriter.enqueueLivePacket(PACKET_PROJECTION, snapshot.tickId, snapshot.timestampUtc, projectionPayload(snapshot));
		}

		Map<String, Object> compactInventoryPayload = null;
		if (compactPacketTypeEnabled("inventory") || compactPacketTypeEnabled("inventoryDelta"))
		{
			compactInventoryPayload = inventoryPayload(snapshot);
		}

		if (compactPacketTypeEnabled("inventory"))
		{
			currentWriter.enqueueLivePacket(PACKET_INVENTORY, snapshot.tickId, snapshot.timestampUtc, compactInventoryPayload);
		}

		if (compactPacketTypeEnabled("inventoryDelta"))
		{
			Map<String, Object> deltaPayload = inventoryDeltaPayload(snapshot, compactInventoryPayload);
			if (deltaPayload != null)
			{
				currentWriter.enqueueLivePacket(PACKET_INVENTORY_DELTA, snapshot.tickId, snapshot.timestampUtc, deltaPayload);
			}
		}

		if (compactPacketTypeEnabled("activity"))
		{
			currentWriter.enqueueLivePacket(PACKET_ACTIVITY, snapshot.tickId, snapshot.timestampUtc, activityPayload(snapshot));
		}

		boolean navigationEffective = emitNavigationEffective(currentWriter);
		boolean collisionWindowEffective = emitCollisionWindowEffective(currentWriter);
		boolean bankUiEffective = emitBankUiEffective(currentWriter);

		if (navigationEffective)
		{
			currentWriter.enqueueLivePacket(PACKET_NAVIGATION, snapshot.tickId, snapshot.timestampUtc, navigationPayload(snapshot));
		}

		if (collisionWindowEffective)
		{
			currentWriter.enqueueLivePacket(PACKET_COLLISION_WINDOW, snapshot.tickId, snapshot.timestampUtc, collisionWindowPayload(snapshot));
		}

		if (bankUiEffective)
		{
			currentWriter.updateLiveCache(PACKET_BANK_UI, snapshot.tickId, snapshot.timestampUtc, bankUiPayload(snapshot));
		}

		if (bankUiEffective)
		{
			currentWriter.updateLiveCache(PACKET_DIALOGUE_STATE, snapshot.tickId, snapshot.timestampUtc, dialogueStatePayload(snapshot));
		}

		if (compactPacketTypeEnabled("combatState"))
		{
			currentWriter.updateLiveCache(PACKET_COMBAT_STATE, snapshot.tickId, snapshot.timestampUtc, combatStatePayload(snapshot));
		}

		if (config.emitCompactNavigationPackets()
				&& config.compactNavigationIncludeFullCollisionGrid()
				&& compactNavigationFullGridIntervalTicks() > 0
				&& snapshot.tickId % compactNavigationFullGridIntervalTicks() == 0
				&& compactPacketTypeEnabled("collisionGrid"))
		{
			currentWriter.enqueueLivePacket(PACKET_COLLISION_GRID, snapshot.tickId, snapshot.timestampUtc, collisionGridPayload(snapshot));
		}

		if (compactPacketTypeEnabled("writerHealth"))
		{
			currentWriter.enqueueLivePacket(PACKET_WRITER_HEALTH, snapshot.tickId, snapshot.timestampUtc, writerHealthPayload(currentWriter, snapshot));
		}
	}

	private boolean snapshotNoFileLiveCacheOnly(TelemetryWriter currentWriter)
	{
		return CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(
				currentWriter.isLiveCacheEnabled(),
				false,
				false,
				pluginSnapshotEndpoint != null);
	}

	private boolean emitNavigationEffective(TelemetryWriter currentWriter)
	{
		return CompactLiveEmissionPolicy.navigationEffective(
				config.emitCompactNavigationPackets(),
				compactPacketTypeEnabled("navigation"),
				snapshotNoFileLiveCacheOnly(currentWriter));
	}

	private boolean emitCollisionWindowEffective(TelemetryWriter currentWriter)
	{
		return CompactLiveEmissionPolicy.collisionWindowEffective(
				config.emitCompactNavigationPackets(),
				config.compactNavigationEmitCollisionWindow(),
				compactPacketTypeEnabled("navigation"),
				compactPacketTypeEnabled("collisionWindow"),
				snapshotNoFileLiveCacheOnly(currentWriter));
	}

	private boolean emitBankUiEffective(TelemetryWriter currentWriter)
	{
		return CompactLiveEmissionPolicy.bankUiEffective(
				compactPacketTypeEnabled("bankUi"),
				snapshotNoFileLiveCacheOnly(currentWriter));
	}

	private boolean compactPacketTypeEnabled(String packetGroup)
	{
		String configured = config.compactLivePacketTypes();

		if (configured == null || configured.isBlank())
		{
			return true;
		}

		for (String part : configured.split(","))
		{
			String normalized = normalizePacketGroup(part);

			if ("all".equals(normalized) || normalizePacketGroup(packetGroup).equals(normalized))
			{
				return true;
			}
		}

		return false;
	}

	private String normalizePacketGroup(String value)
	{
		if (value == null)
		{
			return "";
		}

		return value.trim().replace("-", "").replace("_", "").toLowerCase();
	}

	private Map<String, Object> baselinePayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("tick", snapshot.tickId);
		payload.put("gameState", snapshot.gameState);
		payload.put("player", playerPayload(snapshot));
		payload.put("cameraViewport", cameraViewportPayload(snapshot));
		payload.put("inputGeometry", inputGeometryPayload(snapshot));
		payload.put("latestFramePath", snapshot.framePath);
		payload.put("frameCaptureStatus", snapshot.frameCaptureStatus);
		payload.put("sceneCaptureMode", snapshot.sceneCaptureSummary == null ? null : snapshot.sceneCaptureSummary.sceneCaptureMode);
		payload.put("source", sourceCompletenessPayload(snapshot));
		return payload;
	}

	private Map<String, Object> playerPayload(TickSnapshot snapshot)
	{
		Map<String, Object> player = new LinkedHashMap<>();

		if (snapshot.localPlayer != null)
		{
			player.put("worldX", snapshot.localPlayer.worldX);
			player.put("worldY", snapshot.localPlayer.worldY);
			player.put("plane", snapshot.localPlayer.plane);
			player.put("localX", snapshot.localPlayer.localX);
			player.put("localY", snapshot.localPlayer.localY);
			player.put("sceneX", snapshot.localPlayer.sceneX);
			player.put("sceneY", snapshot.localPlayer.sceneY);
			player.put("animation", snapshot.localPlayer.animation);
			player.put("poseAnimation", snapshot.localPlayer.poseAnimation);
			player.put("combatLevel", snapshot.localPlayer.combatLevel);
		}

		if (snapshot.status != null)
		{
			player.put("runEnergyRaw", snapshot.status.runEnergyRaw);
			player.put("runEnergyPercent", snapshot.status.runEnergyPercent);
			player.put("weight", snapshot.status.weight);
			player.put("hitpointsBoosted", snapshot.status.hitpointsBoosted);
			player.put("hitpointsReal", snapshot.status.hitpointsReal);
			player.put("localHealthRatio", snapshot.status.localHealthRatio);
			player.put("localHealthScale", snapshot.status.localHealthScale);
			player.put("interacting", interactingPayload(snapshot.status));
		}

		return player;
	}

	private Map<String, Object> interactingPayload(TickSnapshot.StatusSnapshot status)
	{
		Map<String, Object> interacting = new LinkedHashMap<>();
		interacting.put("type", status.interactingType);
		interacting.put("index", status.interactingIndex);
		interacting.put("id", status.interactingId);
		interacting.put("name", status.interactingName);
		interacting.put("worldX", status.interactingWorldX);
		interacting.put("worldY", status.interactingWorldY);
		interacting.put("plane", status.interactingPlane);
		return interacting;
	}

	private Map<String, Object> cameraViewportPayload(TickSnapshot snapshot)
	{
		Map<String, Object> camera = new LinkedHashMap<>();
		camera.put("cameraX", snapshot.cameraX);
		camera.put("cameraY", snapshot.cameraY);
		camera.put("cameraZ", snapshot.cameraZ);
		camera.put("cameraPitch", snapshot.cameraPitch);
		camera.put("cameraYaw", snapshot.cameraYaw);
		camera.put("viewportWidth", snapshot.viewportWidth);
		camera.put("viewportHeight", snapshot.viewportHeight);
		camera.put("viewportXOffset", snapshot.viewportXOffset);
		camera.put("viewportYOffset", snapshot.viewportYOffset);
		camera.put("canvasWidth", snapshot.canvasWidth);
		camera.put("canvasHeight", snapshot.canvasHeight);
		camera.put("projectionStateHash", snapshot.sceneProjectionSummary == null ? null : snapshot.sceneProjectionSummary.projectionStateHash);
		return camera;
	}

	private Map<String, Object> inputGeometryPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		TickSnapshot.InputGeometrySnapshot geometry = snapshot == null ? null : snapshot.inputGeometry;
		payload.put("schema", "input_geometry.v1");
		payload.put("sourceTick", geometry == null ? (snapshot == null ? null : snapshot.tickId) : geometry.sourceTick);
		payload.put("geometryAvailable", geometry == null ? false : geometry.geometryAvailable);
		payload.put("reason", geometry == null ? "geometry_unavailable" : geometry.reason);
		payload.put("canvasWidth", geometry == null ? null : geometry.canvasWidth);
		payload.put("canvasHeight", geometry == null ? null : geometry.canvasHeight);
		payload.put("sourceCanvasWidth", geometry == null ? null : geometry.sourceCanvasWidth);
		payload.put("sourceCanvasHeight", geometry == null ? null : geometry.sourceCanvasHeight);
		payload.put("canvasScreenX", geometry == null ? null : geometry.canvasScreenX);
		payload.put("canvasScreenY", geometry == null ? null : geometry.canvasScreenY);
		payload.put("clientWindowX", geometry == null ? null : geometry.clientWindowX);
		payload.put("clientWindowY", geometry == null ? null : geometry.clientWindowY);
		payload.put("clientWindowWidth", geometry == null ? null : geometry.clientWindowWidth);
		payload.put("clientWindowHeight", geometry == null ? null : geometry.clientWindowHeight);
		payload.put("displayScaleX", geometry == null ? null : geometry.displayScaleX);
		payload.put("displayScaleY", geometry == null ? null : geometry.displayScaleY);
		payload.put("isCanvasShowing", geometry == null ? null : geometry.isCanvasShowing);
		payload.put("isClientFocused", geometry == null ? null : geometry.isClientFocused);
		return payload;
	}

	private Map<String, Object> sourceCompletenessPayload(TickSnapshot snapshot)
	{
		Map<String, Object> source = new LinkedHashMap<>();
		TickSnapshot.SceneCaptureSummary capture = snapshot.sceneCaptureSummary;
		TickSnapshot.SceneIndexSummary index = snapshot.sceneIndexSummary;
		boolean capHit = (capture != null && (capture.sceneObjectCapHit || capture.sceneObjectsSkippedByCap > 0))
				|| (index != null && index.indexCapHit);
		boolean complete = capture == null || (!capHit && capture.sceneObjectsSeen == capture.sceneObjectsCaptured);

		source.put("sourceSceneKnowledgeComplete", complete);
		source.put("sourceCapHit", capHit);
		source.put("sceneObjectsSeen", capture == null ? null : capture.sceneObjectsSeen);
		source.put("sceneObjectsCaptured", capture == null ? null : capture.sceneObjectsCaptured);
		source.put("sceneObjectsSkippedByCap", capture == null ? null : capture.sceneObjectsSkippedByCap);
		source.put("sceneObjectCapHit", capture == null ? null : capture.sceneObjectCapHit);
		source.put("indexCapHit", index == null ? null : index.indexCapHit);
		return source;
	}

	private Map<String, Object> sceneDeltaPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("sceneIndexSummary", snapshot.sceneIndexSummary);
		payload.put("sceneCaptureSummary", snapshot.sceneCaptureSummary);
		payload.put("sceneObjectDeltas", compactDeltas(snapshot.sceneObjectDeltas));
		return payload;
	}

	private Map<String, Object> compactDeltas(TickSnapshot.SceneObjectDeltas deltas)
	{
		Map<String, Object> compact = new LinkedHashMap<>();

		if (deltas == null)
		{
			compact.put("newObjects", new ArrayList<>());
			compact.put("updatedObjects", new ArrayList<>());
			compact.put("despawnedObjects", new ArrayList<>());
			return compact;
		}

		compact.put("newObjects", compactSceneObjects(deltas.newObjects, false));
		compact.put("updatedObjects", compactSceneObjects(deltas.updatedObjects, false));
		compact.put("despawnedObjects", compactSceneObjects(deltas.despawnedObjects, false));
		return compact;
	}

	private Map<String, Object> projectionPayload(TickSnapshot snapshot)
	{
		CompactProjectionGeometryOptions geometryOptions = compactProjectionGeometryOptions(snapshot);
		List<Map<String, Object>> serviceSceneObjects = compactServiceSceneObjects(snapshot);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("sceneProjectionSummary", snapshot.sceneProjectionSummary);
		payload.put("projectionStateHash", snapshot.sceneProjectionSummary == null ? null : snapshot.sceneProjectionSummary.projectionStateHash);
		payload.put("refreshMode", snapshot.sceneProjectionSummary == null ? null : snapshot.sceneProjectionSummary.projectionRefreshMode);
		payload.put("visibleObjectRefs", compactSceneObjects(snapshot.visibleSceneObjectRefs, true, geometryOptions));
		payload.put("serviceSceneObjects", serviceSceneObjects);
		payload.put("serviceSceneObjectCount", serviceSceneObjects.size());
		payload.put("serviceSceneObjectCap", MAX_SERVICE_SCENE_OBJECTS);
		payload.put("serviceSceneObjectRadius", SERVICE_SCENE_OBJECT_RADIUS);
		payload.put("serviceSceneObjectCapHit", serviceSceneObjects.size() >= MAX_SERVICE_SCENE_OBJECTS);
		payload.put("geometryEmission", compactProjectionGeometrySummary(geometryOptions));
		recordLastCompactProjectionGeometry(geometryOptions);
		return payload;
	}

	private List<Map<String, Object>> compactServiceSceneObjects(TickSnapshot snapshot)
	{
		List<TickSnapshot.SceneObjectSnapshot> matches = new ArrayList<>();

		if (snapshot != null)
		{
			for (SceneIndexEntry entry : sceneObjectIndex.values())
			{
				if (!serviceSceneEntryEligible(entry, snapshot))
				{
					continue;
				}

				TickSnapshot.SceneObjectSnapshot object = snapshotFromIndex(entry, false);
				matches.add(object);
			}
		}

		matches.sort(Comparator
				.comparingInt((TickSnapshot.SceneObjectSnapshot object) -> serviceScenePriority(serviceSceneClass(object.objectName, object.actions)))
				.thenComparingDouble(object -> serviceSceneDistanceSquared(object, snapshot))
				.thenComparing(object -> String.valueOf(object.objectKey)));

		List<Map<String, Object>> compact = new ArrayList<>();
		for (TickSnapshot.SceneObjectSnapshot object : matches)
		{
			if (compact.size() >= MAX_SERVICE_SCENE_OBJECTS)
			{
				break;
			}

			Map<String, Object> payload = compactSceneObject(object, false, null, false);
			String serviceClass = serviceSceneClass(object.objectName, object.actions);
			payload.put("classId", serviceClass);
			payload.put("serviceSceneClass", serviceClass);
			payload.put("serviceSceneLane", "loadedServiceScene");
			compact.add(payload);
		}

		for (Map<String, Object> npc : compactServiceNpcCandidates(snapshot))
		{
			if (compact.size() >= MAX_SERVICE_SCENE_OBJECTS)
			{
				break;
			}

			compact.add(npc);
		}

		return compact;
	}

	private boolean serviceSceneEntryEligible(SceneIndexEntry entry, TickSnapshot snapshot)
	{
		if (entry == null || !entry.present || snapshot == null || snapshot.localPlayer == null)
		{
			return false;
		}
		if (entry.plane != snapshot.localPlayer.plane)
		{
			return false;
		}
		if (snapshot.localPlayer.sceneX == null || snapshot.localPlayer.sceneY == null)
		{
			return false;
		}
		if (Math.abs(entry.sceneX - snapshot.localPlayer.sceneX) > SERVICE_SCENE_OBJECT_RADIUS
				|| Math.abs(entry.sceneY - snapshot.localPlayer.sceneY) > SERVICE_SCENE_OBJECT_RADIUS)
		{
			return false;
		}
		return isServiceSceneClass(serviceSceneClass(entry.objectName, entry.actions));
	}

	private List<Map<String, Object>> compactServiceNpcCandidates(TickSnapshot snapshot)
	{
		List<Map<String, Object>> compact = new ArrayList<>();
		if (snapshot == null || snapshot.localPlayer == null || snapshot.npcs == null)
		{
			return compact;
		}

		for (TickSnapshot.NpcSnapshot npc : snapshot.npcs)
		{
			if (npc == null || npc.plane != snapshot.localPlayer.plane)
			{
				continue;
			}
			String name = firstNonBlank(npc.npcName, npc.name);
			String serviceClass = serviceSceneClass(name, null);
			if (!"banker".equals(serviceClass))
			{
				continue;
			}
			if (snapshot.localPlayer.sceneX != null && snapshot.localPlayer.sceneY != null
					&& npc.localX != null && npc.localY != null)
			{
				int npcSceneX = npc.worldX - (snapshot.localPlayer.worldX - snapshot.localPlayer.sceneX);
				int npcSceneY = npc.worldY - (snapshot.localPlayer.worldY - snapshot.localPlayer.sceneY);
				if (Math.abs(npcSceneX - snapshot.localPlayer.sceneX) > SERVICE_SCENE_OBJECT_RADIUS
						|| Math.abs(npcSceneY - snapshot.localPlayer.sceneY) > SERVICE_SCENE_OBJECT_RADIUS)
				{
					continue;
				}
			}

			Map<String, Object> payload = new LinkedHashMap<>();
			payload.put("targetKey", "npc:" + npc.index + ":" + npc.id + ":" + npc.worldX + ":" + npc.worldY + ":" + npc.plane);
			payload.put("targetType", "npc");
			payload.put("id", npc.id);
			payload.put("name", name);
			payload.put("classId", "banker");
			payload.put("serviceCandidateType", "banker");
			payload.put("serviceSceneClass", "banker");
			payload.put("serviceSceneLane", "loadedServiceScene");
			payload.put("worldX", npc.worldX);
			payload.put("worldY", npc.worldY);
			payload.put("plane", npc.plane);
			payload.put("localX", npc.localX);
			payload.put("localY", npc.localY);
			payload.put("present", !npc.dead);
			payload.put("source", "loadedServiceScene");
			compact.add(payload);
		}

		return compact;
	}

	private double serviceSceneDistanceSquared(TickSnapshot.SceneObjectSnapshot object, TickSnapshot snapshot)
	{
		if (object == null || snapshot == null || snapshot.localPlayer == null)
		{
			return Double.MAX_VALUE;
		}
		int dx = object.worldX - snapshot.localPlayer.worldX;
		int dy = object.worldY - snapshot.localPlayer.worldY;
		return (double) dx * dx + (double) dy * dy;
	}

	private int serviceScenePriority(String serviceClass)
	{
		if ("bank_booth".equals(serviceClass))
		{
			return 0;
		}
		if ("banker".equals(serviceClass))
		{
			return 1;
		}
		if ("bank_chest".equals(serviceClass))
		{
			return 2;
		}
		if ("deposit_box".equals(serviceClass))
		{
			return 3;
		}
		if ("deposit_chest".equals(serviceClass))
		{
			return 4;
		}
		return 5;
	}

	private boolean isServiceSceneClass(String serviceClass)
	{
		return "bank_booth".equals(serviceClass)
				|| "banker".equals(serviceClass)
				|| "bank_chest".equals(serviceClass)
				|| "deposit_box".equals(serviceClass)
				|| "deposit_chest".equals(serviceClass)
				|| "bank_related".equals(serviceClass)
				|| "bank_service".equals(serviceClass);
	}

	private String serviceSceneClass(String name, String[] actions)
	{
		String text = (name == null ? "" : name).toLowerCase(Locale.ROOT);
		String actionText = "";
		if (actions != null)
		{
			List<String> clean = new ArrayList<>();
			for (String action : actions)
			{
				if (action != null && !action.isBlank())
				{
					clean.add(action.toLowerCase(Locale.ROOT));
				}
			}
			actionText = String.join(" ", clean);
		}

		if (text.contains("bank deposit box") || text.contains("deposit box"))
		{
			return "deposit_box";
		}
		if (text.contains("deposit chest"))
		{
			return "deposit_chest";
		}
		if (text.contains("bank booth"))
		{
			return "bank_booth";
		}
		if (text.contains("banker"))
		{
			return "banker";
		}
		if (text.contains("bank chest"))
		{
			return "bank_chest";
		}
		if (text.contains("bank table"))
		{
			return "bank_related";
		}
		if (text.contains("bank") || actionText.contains("bank"))
		{
			return "bank_service";
		}
		if (actionText.contains("deposit"))
		{
			return "deposit_box";
		}
		return "";
	}

	private String firstNonBlank(String first, String second)
	{
		if (first != null && !first.isBlank())
		{
			return first;
		}
		if (second != null && !second.isBlank())
		{
			return second;
		}
		return "";
	}

	private List<Map<String, Object>> compactSceneObjects(TickSnapshot.SceneObjectSnapshot[] objects, boolean includeProjection)
	{
		return compactSceneObjects(objects, includeProjection, null);
	}

	private List<Map<String, Object>> compactSceneObjects(
			TickSnapshot.SceneObjectSnapshot[] objects,
			boolean includeProjection,
			CompactProjectionGeometryOptions geometryOptions)
	{
		List<Map<String, Object>> compact = new ArrayList<>();
		Set<String> polygonObjectKeys = includeProjection
				? selectProjectionPolygonObjectKeys(objects, geometryOptions)
				: new HashSet<>();

		if (objects == null)
		{
			return compact;
		}

		for (TickSnapshot.SceneObjectSnapshot object : objects)
		{
			if (object != null)
			{
				boolean includePolygons = includeProjection
						&& polygonObjectKeys.contains(compactProjectionGeometryKey(object));
				compact.add(compactSceneObject(object, includeProjection, geometryOptions, includePolygons));
			}
		}

		return compact;
	}

	private Set<String> selectProjectionPolygonObjectKeys(
			TickSnapshot.SceneObjectSnapshot[] objects,
			CompactProjectionGeometryOptions geometryOptions)
	{
		Set<String> selected = new HashSet<>();

		if (objects == null || geometryOptions == null || !geometryOptions.includeAnyPolygons)
		{
			return selected;
		}

		List<TickSnapshot.SceneObjectSnapshot> eligible = new ArrayList<>();

		for (TickSnapshot.SceneObjectSnapshot object : objects)
		{
			if (object == null)
			{
				continue;
			}
			if (!object.onScreen)
			{
				if (object.geometryAvailable)
				{
					geometryOptions.hullDroppedNoCanvasIntersection++;
				}
				else
				{
					geometryOptions.hullDroppedOffscreen++;
				}
				continue;
			}
			if (geometryOptions.includeClickableHull && object.clickboxPolygon == null)
			{
				geometryOptions.hullDroppedNullClickbox++;
			}
			if (!hasEnabledProjectionPolygon(object, geometryOptions))
			{
				continue;
			}
			eligible.add(object);
		}

		eligible.sort(Comparator
				.comparingDouble((TickSnapshot.SceneObjectSnapshot object) -> projectionGeometryPriority(object, geometryOptions))
				.thenComparing(object -> String.valueOf(object.objectKey)));

		for (TickSnapshot.SceneObjectSnapshot object : eligible)
		{
			if (geometryOptions.refsWithPolygons >= geometryOptions.maxRefs)
			{
				geometryOptions.refsSkippedByCap++;
				geometryOptions.hullDroppedByCap++;
				geometryOptions.capHit = true;
				continue;
			}

			selected.add(compactProjectionGeometryKey(object));
			geometryOptions.refsWithPolygons++;
		}

		return selected;
	}

	private Map<String, Object> compactSceneObject(
			TickSnapshot.SceneObjectSnapshot object,
			boolean includeProjection,
			CompactProjectionGeometryOptions geometryOptions,
			boolean includePolygons)
	{
		Map<String, Object> compact = new LinkedHashMap<>();
		compact.put("objectKey", object.objectKey);
		compact.put("targetType", "sceneObject");
		compact.put("id", object.id);
		compact.put("hash", object.hash);
		compact.put("name", object.objectName);
		compact.put("nameSource", object.objectNameSource);
		compact.put("actions", compactActions(object.actions));
		compact.put("kind", object.kind);
		compact.put("layer", object.kind);
		compact.put("worldX", object.worldX);
		compact.put("worldY", object.worldY);
		compact.put("plane", object.plane);
		compact.put("sceneX", object.sceneX);
		compact.put("sceneY", object.sceneY);
		compact.put("present", object.present);
		compact.put("despawnedTick", object.despawnedTick);
		compact.put("source", object.source);
		compact.put("firstSeenTick", object.firstSeenTick);
		compact.put("lastSeenTick", object.lastSeenTick);
		compact.put("lastUpdatedTick", object.lastUpdatedTick);

		if (includeProjection)
		{
			compact.put("onScreen", object.onScreen);
			compact.put("geometryAvailable", object.geometryAvailable);
			compact.put("aimPoint", aimPointPayload(object));
			compact.put("geometrySummary", geometrySummaryPayload(object));
			compact.put("projectionVersion", object.projectionVersion);
			compact.put("geometryWarning", object.geometryWarning);
			compact.put("bounds", preferredBoundsPayload(object));
			compact.put("geometrySource", geometrySourceFor(object, geometryOptions, includePolygons));

			if (includePolygons)
			{
				putProjectionPolygonFields(compact, object, geometryOptions);
			}

			if (geometryOptions != null && geometryOptions.includeAnyPolygons)
			{
				compact.put("clickableHullAvailable", compact.containsKey("clickableHull"));
				if (!compact.containsKey("clickableHull"))
				{
					compact.put("clickableHullMissingReason", clickableHullMissingReason(object, geometryOptions, includePolygons));
				}
			}
		}

		return compact;
	}

	private String compactProjectionGeometryKey(TickSnapshot.SceneObjectSnapshot object)
	{
		if (object == null)
		{
			return "";
		}
		if (object.objectKey != null && !object.objectKey.isBlank())
		{
			return object.objectKey;
		}
		return object.id + ":" + object.hash + ":" + object.worldX + ":" + object.worldY + ":" + object.plane + ":" + object.kind;
	}

	private double projectionGeometryPriority(
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions)
	{
		double sourcePriority = 3_000_000_000.0;
		if (object.clickboxPolygon != null)
		{
			sourcePriority = 0.0;
		}
		else if (object.convexHullPolygon != null)
		{
			sourcePriority = 1_000_000_000.0;
		}
		else if (object.canvasTilePolygon != null)
		{
			sourcePriority = 2_000_000_000.0;
		}
		return sourcePriority + playerSceneDistanceSquared(object, geometryOptions) * 1_000_000.0 + screenCenterDistanceSquared(object);
	}

	private double playerSceneDistanceSquared(
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions)
	{
		if (object == null || geometryOptions == null
				|| geometryOptions.playerSceneX == null
				|| geometryOptions.playerSceneY == null
				|| geometryOptions.playerPlane == null
				|| object.plane != geometryOptions.playerPlane)
		{
			return 1_000_000.0;
		}
		double dx = object.sceneX - geometryOptions.playerSceneX;
		double dy = object.sceneY - geometryOptions.playerSceneY;
		return dx * dx + dy * dy;
	}

	private double screenCenterDistanceSquared(TickSnapshot.SceneObjectSnapshot object)
	{
		Rectangle visibleArea = currentVisibleArea();
		TickSnapshot.CanvasPoint center = geometryCenterPoint(object);
		if (visibleArea == null || center == null)
		{
			return Double.MAX_VALUE;
		}
		double dx = center.x - visibleArea.getCenterX();
		double dy = center.y - visibleArea.getCenterY();
		return dx * dx + dy * dy;
	}

	private TickSnapshot.CanvasPoint geometryCenterPoint(TickSnapshot.SceneObjectSnapshot object)
	{
		if (object.clickboxBounds != null)
		{
			return boundsCenter(object.clickboxBounds);
		}
		if (object.convexHullBounds != null)
		{
			return boundsCenter(object.convexHullBounds);
		}
		TickSnapshot.Bounds tileBounds = boundsSnapshot(object.canvasTilePolygon);
		if (tileBounds != null)
		{
			return boundsCenter(tileBounds);
		}
		return object.canvasLocation;
	}

	private boolean hasEnabledProjectionPolygon(
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions)
	{
		return (geometryOptions.includeClickableHull && object.clickboxPolygon != null)
				|| (geometryOptions.includeConvexHull && object.convexHullPolygon != null)
				|| (geometryOptions.includeCanvasTilePolygon && object.canvasTilePolygon != null);
	}

	private void putProjectionPolygonFields(
			Map<String, Object> compact,
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions)
	{
		List<Map<String, Object>> clickboxPoints = polygonPointsPayload(object.clickboxPolygon);
		if (geometryOptions.includeClickableHull && clickboxPoints != null)
		{
			compact.put("clickableHull", clickboxPoints);
			compact.put("clickboxPolygon", clickboxPoints);
			geometryOptions.hullsEmitted++;
		}

		List<Map<String, Object>> convexPoints = polygonPointsPayload(object.convexHullPolygon);
		if (geometryOptions.includeConvexHull && convexPoints != null)
		{
			compact.put("convexHull", convexPoints);
			compact.put("convexHullPolygon", convexPoints);
		}

		List<Map<String, Object>> tilePoints = polygonPointsPayload(object.canvasTilePolygon);
		if (geometryOptions.includeCanvasTilePolygon && tilePoints != null)
		{
			compact.put("canvasTilePolygon", tilePoints);
		}
	}

	private CompactProjectionGeometryOptions compactProjectionGeometryOptions(TickSnapshot snapshot)
	{
		TelemetryDebugOverlayGeometryMode mode = config.telemetryDebugOverlayGeometryMode();
		boolean heavyGeometry = config.compactLiveIncludeHeavyGeometry();
		boolean overlayEnabled = config.telemetryDebugOverlayEnabled();
		boolean overlayHullMode = overlayEnabled && (
				config.telemetryDebugOverlayShowClickableHull()
						|| mode == TelemetryDebugOverlayGeometryMode.CLICKABLE_HULL
						|| mode == TelemetryDebugOverlayGeometryMode.HULL_AND_BOUNDS
						|| mode == TelemetryDebugOverlayGeometryMode.ALL_GEOMETRY_DEBUG);
		boolean overlayTileMode = overlayEnabled && (
				config.telemetryDebugOverlayShowCanvasTilePolygon()
						|| mode == TelemetryDebugOverlayGeometryMode.TILE_POLYGON
						|| mode == TelemetryDebugOverlayGeometryMode.ALL_GEOMETRY_DEBUG);
		boolean includeClickableHull = heavyGeometry || config.compactLiveIncludeClickableHull() || overlayHullMode;
		boolean includeConvexHull = heavyGeometry || config.compactLiveIncludeConvexHull() || overlayHullMode;
		boolean includeCanvasTilePolygon = heavyGeometry || config.compactLiveIncludeCanvasTilePolygon() || overlayTileMode;
		int maxRefs = clampCompactLiveGeometryMaxRefs(config.compactLiveGeometryMaxRefs());
		return new CompactProjectionGeometryOptions(
				includeClickableHull,
				includeConvexHull,
				includeCanvasTilePolygon,
				maxRefs,
				snapshot == null || snapshot.localPlayer == null ? null : snapshot.localPlayer.sceneX,
				snapshot == null || snapshot.localPlayer == null ? null : snapshot.localPlayer.sceneY,
				snapshot == null || snapshot.localPlayer == null ? null : snapshot.localPlayer.plane);
	}

	private Map<String, Object> compactProjectionGeometrySummary(CompactProjectionGeometryOptions geometryOptions)
	{
		Map<String, Object> summary = new LinkedHashMap<>();
		summary.put("includeClickableHull", geometryOptions.includeClickableHull);
		summary.put("includeConvexHull", geometryOptions.includeConvexHull);
		summary.put("includeCanvasTilePolygon", geometryOptions.includeCanvasTilePolygon);
		summary.put("maxRefs", geometryOptions.maxRefs);
		summary.put("refsWithPolygons", geometryOptions.refsWithPolygons);
		summary.put("refsSkippedByCap", geometryOptions.refsSkippedByCap);
		summary.put("capHit", geometryOptions.capHit);
		summary.put("hullEmitted", geometryOptions.hullsEmitted);
		summary.put("hullDroppedOffscreen", geometryOptions.hullDroppedOffscreen);
		summary.put("hullDroppedNoCanvasIntersection", geometryOptions.hullDroppedNoCanvasIntersection);
		summary.put("hullDroppedByCap", geometryOptions.hullDroppedByCap);
		summary.put("hullDroppedNullClickbox", geometryOptions.hullDroppedNullClickbox);
		return summary;
	}

	private void recordLastCompactProjectionGeometry(CompactProjectionGeometryOptions geometryOptions)
	{
		lastCompactGeometryRefsWithPolygons = geometryOptions.refsWithPolygons;
		lastCompactGeometryRefsSkippedByCap = geometryOptions.refsSkippedByCap;
		lastCompactGeometryMaxRefs = geometryOptions.maxRefs;
		lastCompactGeometryCapHit = geometryOptions.capHit;
		lastCompactLiveIncludeClickableHull = geometryOptions.includeClickableHull;
		lastCompactLiveIncludeCanvasTilePolygon = geometryOptions.includeCanvasTilePolygon;
		lastCompactLiveIncludeConvexHull = geometryOptions.includeConvexHull;
		lastCompactHullsEmitted = geometryOptions.hullsEmitted;
		lastCompactHullDroppedOffscreen = geometryOptions.hullDroppedOffscreen;
		lastCompactHullDroppedNoCanvasIntersection = geometryOptions.hullDroppedNoCanvasIntersection;
		lastCompactHullDroppedByCap = geometryOptions.hullDroppedByCap;
		lastCompactHullDroppedNullClickbox = geometryOptions.hullDroppedNullClickbox;
	}

	static int clampCompactLiveGeometryMaxRefs(int value)
	{
		return Math.max(0, Math.min(COMPACT_LIVE_GEOMETRY_MAX_REFS_HARD_CAP, value));
	}

	static List<Map<String, Object>> polygonPointsPayload(int[][] polygon)
	{
		if (polygon == null || polygon.length < 3)
		{
			return null;
		}

		List<Map<String, Object>> points = new ArrayList<>();

		for (int[] point : polygon)
		{
			if (point == null || point.length < 2)
			{
				return null;
			}

			Map<String, Object> payload = new LinkedHashMap<>();
			payload.put("x", point[0]);
			payload.put("y", point[1]);
			points.add(payload);
		}

		return points;
	}

	private String geometrySourceFor(
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions,
			boolean includePolygons)
	{
		if (includePolygons && geometryOptions != null)
		{
			if (geometryOptions.includeClickableHull && object.clickboxPolygon != null)
			{
				return "clickbox";
			}
			if (geometryOptions.includeConvexHull && object.convexHullPolygon != null)
			{
				return "convexHull";
			}
			if (geometryOptions.includeCanvasTilePolygon && object.canvasTilePolygon != null)
			{
				return "canvasTilePolygon";
			}
		}
		if (geometryOptions != null && geometryOptions.includeAnyPolygons)
		{
			return fallbackProjectionGeometrySource(object);
		}

		return geometrySourceFor(object);
	}

	private String fallbackProjectionGeometrySource(TickSnapshot.SceneObjectSnapshot object)
	{
		if (object == null)
		{
			return "none";
		}
		if (object.clickboxBounds != null || object.convexHullBounds != null || boundsSnapshot(object.canvasTilePolygon) != null)
		{
			return "bounds";
		}
		if (object.canvasLocation != null)
		{
			return "aimPoint";
		}
		return "none";
	}

	private String clickableHullMissingReason(
			TickSnapshot.SceneObjectSnapshot object,
			CompactProjectionGeometryOptions geometryOptions,
			boolean includePolygons)
	{
		if (object == null)
		{
			return "object unavailable";
		}
		if (!geometryOptions.includeClickableHull)
		{
			return "clickable hull emission disabled";
		}
		if (!object.onScreen)
		{
			return "target off screen";
		}
		if (object.clickboxPolygon == null)
		{
			return "clickbox unavailable";
		}
		if (!includePolygons)
		{
			return "compact geometry cap reached";
		}
		return "clickbox polygon unavailable";
	}

	private String geometrySourceFor(TickSnapshot.SceneObjectSnapshot object)
	{
		if (object == null)
		{
			return "none";
		}
		if (object.clickboxPolygon != null)
		{
			return "clickbox";
		}
		if (object.convexHullPolygon != null)
		{
			return "convexHull";
		}
		if (object.canvasTilePolygon != null)
		{
			return "canvasTilePolygon";
		}
		if (object.clickboxBounds != null || object.convexHullBounds != null || boundsSnapshot(object.canvasTilePolygon) != null)
		{
			return "bounds";
		}
		if (object.canvasLocation != null)
		{
			return "aimPoint";
		}
		return "none";
	}

	private List<String> compactActions(String[] actions)
	{
		List<String> compact = new ArrayList<>();

		if (actions == null)
		{
			return compact;
		}

		for (String action : actions)
		{
			if (action != null && !action.isBlank() && !"null".equalsIgnoreCase(action))
			{
				compact.add(action);
			}
		}

		return compact;
	}

	private Map<String, Object> aimPointPayload(TickSnapshot.SceneObjectSnapshot object)
	{
		TickSnapshot.CanvasPoint point = null;
		String source = null;

		if (object.clickboxBounds != null)
		{
			point = boundsCenter(object.clickboxBounds);
			source = "clickboxBoundsCenter";
		}
		else if (object.convexHullBounds != null)
		{
			point = boundsCenter(object.convexHullBounds);
			source = "convexHullBoundsCenter";
		}
		else if (object.canvasLocation != null)
		{
			point = object.canvasLocation;
			source = "canvasLocation";
		}
		else if (object.canvasTilePolygon != null)
		{
			point = polygonCenter(object.canvasTilePolygon);
			source = "canvasTilePolygonCenter";
		}

		if (point == null)
		{
			return null;
		}

		Map<String, Object> aim = new LinkedHashMap<>();
		aim.put("canvasX", point.x);
		aim.put("canvasY", point.y);
		aim.put("source", source);
		return aim;
	}

	private TickSnapshot.CanvasPoint boundsCenter(TickSnapshot.Bounds bounds)
	{
		if (bounds == null)
		{
			return null;
		}

		TickSnapshot.CanvasPoint point = new TickSnapshot.CanvasPoint();
		point.x = bounds.x + bounds.w / 2;
		point.y = bounds.y + bounds.h / 2;
		return point;
	}

	private Map<String, Object> geometrySummaryPayload(TickSnapshot.SceneObjectSnapshot object)
	{
		Map<String, Object> summary = new LinkedHashMap<>();
		boolean hasClickbox = object.clickboxBounds != null || object.clickboxPolygon != null;
		boolean hasConvexHull = object.convexHullBounds != null || object.convexHullPolygon != null;
		boolean hasCanvasTilePolygon = object.canvasTilePolygon != null;
		summary.put("hasClickbox", hasClickbox);
		summary.put("hasConvexHull", hasConvexHull);
		summary.put("hasCanvasTilePolygon", hasCanvasTilePolygon);
		summary.put("clickboxBounds", boundsPayload(object.clickboxBounds));
		summary.put("convexHullBounds", boundsPayload(object.convexHullBounds));

		TickSnapshot.Bounds tileBounds = boundsSnapshot(object.canvasTilePolygon);
		summary.put("canvasTileBounds", boundsPayload(tileBounds));
		return summary;
	}

	private Map<String, Object> preferredBoundsPayload(TickSnapshot.SceneObjectSnapshot object)
	{
		Map<String, Object> bounds = boundsPayload(object.clickboxBounds);
		if (bounds != null)
		{
			return bounds;
		}

		bounds = boundsPayload(object.convexHullBounds);
		if (bounds != null)
		{
			return bounds;
		}

		return boundsPayload(boundsSnapshot(object.canvasTilePolygon));
	}

	private Map<String, Object> boundsPayload(TickSnapshot.Bounds bounds)
	{
		if (bounds == null)
		{
			return null;
		}

		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("x", bounds.x);
		payload.put("y", bounds.y);
		payload.put("w", bounds.w);
		payload.put("h", bounds.h);
		return payload;
	}

	private Map<String, Object> inventoryPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("inventory", itemContainerSnapshot(snapshot.inventory));
		payload.put("equipment", itemContainerSnapshot(snapshot.equipment));
		payload.put("inventoryDeltaTrackingAvailable", true);
		payload.put("inventoryDeltaPacketType", PACKET_INVENTORY_DELTA);
		return payload;
	}

	private Map<String, Object> bankUiPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		TickSnapshot.BankUiSnapshot bankUi = snapshot == null ? null : snapshot.bankUi;

		payload.put("schema", "bank_ui_context_payload.v1");
		payload.put("tick", snapshot == null ? null : snapshot.tickId);
		payload.put("topLevelInterfaceId", bankUi == null ? null : bankUi.topLevelInterfaceId);
		payload.put("bankOpen", bankUi == null ? null : bankUi.bankOpen);
		payload.put("bankPinOpen", bankUi == null ? null : bankUi.bankPinOpen);
		payload.put("bankRootVisible", bankUi == null ? null : bankUi.bankRootVisible);
		payload.put("bankContainerVisible", bankUi == null ? null : bankUi.bankContainerVisible);
		payload.put("bankInventoryVisible", bankUi == null ? null : bankUi.bankInventoryVisible);
		payload.put("depositInventoryButtonVisible", bankUi == null ? null : bankUi.depositInventoryButtonVisible);
		payload.put("closeButtonVisible", bankUi == null ? null : bankUi.closeButtonVisible);
		payload.put("bankCloseButtonVisible", bankUi == null ? null : bankUi.bankCloseButtonVisible);
		payload.put("keyboardClosePossible", bankUi == null ? null : bankUi.keyboardClosePossible);
		payload.put("bankRootWidget", bankUi == null ? null : bankUi.bankRootWidget);
		payload.put("bankContainerWidget", bankUi == null ? null : bankUi.bankContainerWidget);
		payload.put("bankInventoryWidget", bankUi == null ? null : bankUi.bankInventoryWidget);
		payload.put("depositInventoryButtonWidget", bankUi == null ? null : bankUi.depositInventoryButtonWidget);
		payload.put("closeButtonWidget", bankUi == null ? null : bankUi.closeButtonWidget);
		payload.put("bankPinWidget", bankUi == null ? null : bankUi.bankPinWidget);
		payload.put("inventorySlots", bankUi == null || bankUi.inventorySlotWidgets == null ? new TickSnapshot.InventorySlotWidgetSnapshot[0] : bankUi.inventorySlotWidgets);
		payload.put("inventorySlotWidgets", bankUi == null || bankUi.inventorySlotWidgets == null ? new TickSnapshot.InventorySlotWidgetSnapshot[0] : bankUi.inventorySlotWidgets);
		payload.put("inventorySummary", itemContainerSnapshot(snapshot == null ? null : snapshot.inventory));
		Map<String, Object> bankSummary = itemContainerSummary(bankUi == null ? null : bankUi.bankItems);
		payload.put("bankSummary", bankSummary);
		payload.put("bankContainerDelta", bankContainerDeltaPayload(snapshot, bankSummary));
		return payload;
	}

	private Map<String, Object> dialogueStatePayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		TickSnapshot.DialogueStateSnapshot dialogue = snapshot == null ? null : snapshot.dialogueState;

		payload.put("schema", "dialogue_state.v1");
		payload.put("tick", snapshot == null ? null : snapshot.tickId);
		payload.put("active", dialogue != null && Boolean.TRUE.equals(dialogue.active));
		payload.put("type", dialogue == null ? "unknown" : dialogue.type);
		payload.put("promptText", dialogue == null ? "" : dialogue.promptText);
		payload.put("options", dialogue == null || dialogue.options == null ? new TickSnapshot.DialogueOptionSnapshot[0] : dialogue.options);
		payload.put("canUseNumberKeys", dialogue == null ? null : dialogue.canUseNumberKeys);
		payload.put("canUseSpaceContinue", dialogue == null ? null : dialogue.canUseSpaceContinue);
		payload.put("source", dialogue == null ? "widget_root_scan" : dialogue.source);
		payload.put("widgetRootIds", dialogue == null || dialogue.widgetRootIds == null ? new Integer[0] : dialogue.widgetRootIds);
		payload.put("latestClientTick", dialogue == null ? null : dialogue.latestClientTick);
		payload.put("wallTimeMillis", dialogue == null ? null : dialogue.wallTimeMillis);
		return payload;
	}

	private Map<String, Object> combatStatePayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Player localPlayer = client.getLocalPlayer();
		Actor playerInteracting = localPlayer == null ? null : localPlayer.getInteracting();
		List<Map<String, Object>> actorsInteractingWithPlayer = actorsInteractingWithLocalPlayer(localPlayer, snapshot);
		List<Map<String, Object>> nearbyHostileNpcs = nearbyHostileNpcs(localPlayer, snapshot);
		Map<String, Object> playerHealth = new LinkedHashMap<>();
		TickSnapshot.StatusSnapshot status = snapshot == null ? null : snapshot.status;

		playerHealth.put("ratio", localPlayer == null ? null : localPlayer.getHealthRatio());
		playerHealth.put("scale", localPlayer == null ? null : localPlayer.getHealthScale());
		playerHealth.put("boostedHitpoints", status == null ? null : status.hitpointsBoosted);
		playerHealth.put("realHitpoints", status == null ? null : status.hitpointsReal);

		payload.put("schema", "combat_state.v1");
		payload.put("tick", snapshot == null ? null : snapshot.tickId);
		payload.put("exportSeq", null);
		payload.put("stateAgeMs", 0);
		payload.put("inCombat", playerInteracting != null || !actorsInteractingWithPlayer.isEmpty());
		payload.put("playerInteracting", actorPayload(playerInteracting));
		payload.put("actorsInteractingWithPlayer", actorsInteractingWithPlayer);
		payload.put("nearbyHostileNpcs", nearbyHostileNpcs);
		payload.put("recentInteractingChanges", recentCopy(recentInteractingChanges));
		payload.put("recentHitsplats", recentCopy(recentHitsplats));
		payload.put("recentActorDeaths", recentCopy(recentActorDeaths));
		payload.put("recentAnimations", recentCopy(recentAnimations));
		payload.put("recentGraphics", recentCopy(recentGraphics));
		payload.put("recentOverheadText", recentCopy(recentOverheadText));
		payload.put("recentChatMessages", recentCopy(recentChatMessages));
		payload.put("recentStatChanges", recentCopy(recentStatChanges));
		payload.put("playerHealth", playerHealth);
		payload.put("warnings", List.of());
		return payload;
	}

	private List<Map<String, Object>> actorsInteractingWithLocalPlayer(Player localPlayer, TickSnapshot snapshot)
	{
		List<Map<String, Object>> result = new ArrayList<>();
		if (localPlayer == null || snapshot == null)
		{
			return result;
		}
		List<NPC> npcs = client.getNpcs();
		if (npcs != null)
		{
			for (NPC npc : npcs)
			{
				if (npc != null && npc.getInteracting() == localPlayer)
				{
					Map<String, Object> actor = actorPayload(npc);
					actor.put("distanceTiles", distanceTiles(localPlayer, npc));
					result.add(actor);
					if (result.size() >= MAX_COMBAT_ACTORS)
					{
						return result;
					}
				}
			}
		}
		List<Player> players = client.getPlayers();
		if (players == null)
		{
			return result;
		}
		for (Player player : players)
		{
			if (player != null && player != localPlayer && player.getInteracting() == localPlayer)
			{
				Map<String, Object> actor = actorPayload(player);
				actor.put("distanceTiles", distanceTiles(localPlayer, player));
				result.add(actor);
				if (result.size() >= MAX_COMBAT_ACTORS)
				{
					return result;
				}
			}
		}
		return result;
	}

	private List<Map<String, Object>> nearbyHostileNpcs(Player localPlayer, TickSnapshot snapshot)
	{
		List<Map<String, Object>> result = new ArrayList<>();
		if (localPlayer == null || snapshot == null || snapshot.npcs == null)
		{
			return result;
		}
		for (TickSnapshot.NpcSnapshot npc : snapshot.npcs)
		{
			if (npc == null || npc.combatLevel <= 0)
			{
				continue;
			}
			int distance = distanceTiles(localPlayer, npc.worldX, npc.worldY, npc.plane);
			if (distance < 0 || distance > COMBAT_NPC_RADIUS_TILES)
			{
				continue;
			}
			Map<String, Object> record = new LinkedHashMap<>();
			record.put("actorType", "NPC");
			record.put("index", npc.index);
			record.put("id", npc.id);
			record.put("name", safeString(npc.name != null ? npc.name : npc.npcName));
			record.put("combatLevel", npc.combatLevel);
			record.put("worldX", npc.worldX);
			record.put("worldY", npc.worldY);
			record.put("plane", npc.plane);
			record.put("animation", npc.animation);
			record.put("healthRatio", npc.healthRatio);
			record.put("healthScale", npc.healthScale);
			record.put("dead", npc.dead);
			record.put("distanceTiles", distance);
			record.put("onScreen", npc.onScreen);
			record.put("geometryAvailable", npc.geometryAvailable);
			result.add(record);
			if (result.size() >= MAX_COMBAT_ACTORS)
			{
				break;
			}
		}
		result.sort(Comparator.comparingInt(item -> getInt(item.get("distanceTiles"), 999)));
		return result;
	}

	private int distanceTiles(Actor source, Actor target)
	{
		WorldPoint sourcePoint = source == null ? null : source.getWorldLocation();
		WorldPoint targetPoint = target == null ? null : target.getWorldLocation();
		if (sourcePoint == null || targetPoint == null || sourcePoint.getPlane() != targetPoint.getPlane())
		{
			return -1;
		}
		return Math.max(
				Math.abs(sourcePoint.getX() - targetPoint.getX()),
				Math.abs(sourcePoint.getY() - targetPoint.getY()));
	}

	private int distanceTiles(Player localPlayer, int worldX, int worldY, int plane)
	{
		WorldPoint local = localPlayer == null ? null : localPlayer.getWorldLocation();
		if (local == null || plane != local.getPlane())
		{
			return -1;
		}
		return Math.max(Math.abs(local.getX() - worldX), Math.abs(local.getY() - worldY));
	}

	private Map<String, Object> itemContainerSnapshot(TickSnapshot.InventorySlot[] slots)
	{
		return itemContainerPayload(slots, true);
	}

	private Map<String, Object> itemContainerSummary(TickSnapshot.InventorySlot[] slots)
	{
		return itemContainerPayload(slots, false);
	}

	private Map<String, Object> itemContainerPayload(TickSnapshot.InventorySlot[] slots, boolean includeItems)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<Map<String, Object>> items = new ArrayList<>();
		List<Integer> uniqueItemIds = new ArrayList<>();
		Set<Integer> uniqueSeen = new HashSet<>();
		Map<Integer, Integer> totalQuantityByItemId = new LinkedHashMap<>();
		int freeSlots = 0;
		int filledSlots = 0;
		int totalItemQuantity = 0;
		StringBuilder signature = new StringBuilder();

		if (slots == null)
		{
			payload.put("known", false);
			return payload;
		}

		payload.put("slotCount", slots.length);
		for (TickSnapshot.InventorySlot slot : slots)
		{
			if (slot == null || slot.itemId <= 0 || slot.quantity <= 0)
			{
				freeSlots++;
				continue;
			}

			filledSlots++;
			totalItemQuantity += slot.quantity;
			signature.append(slot.slot).append(':').append(slot.itemId).append(':').append(slot.quantity).append(';');
			if (uniqueSeen.add(slot.itemId))
			{
				uniqueItemIds.add(slot.itemId);
			}
			totalQuantityByItemId.put(slot.itemId, totalQuantityByItemId.getOrDefault(slot.itemId, 0) + slot.quantity);

			if (includeItems)
			{
				Map<String, Object> item = new LinkedHashMap<>();
				item.put("slot", slot.slot);
				item.put("itemId", slot.itemId);
				item.put("quantity", slot.quantity);
				items.add(item);
			}
		}

		payload.put("known", true);
		payload.put("freeSlots", freeSlots);
		payload.put("filledSlots", filledSlots);
		payload.put("occupiedSlots", filledSlots);
		payload.put("itemCount", totalItemQuantity);
		payload.put("totalItemQuantity", totalItemQuantity);
		payload.put("uniqueItemIds", uniqueItemIds);
		payload.put("uniqueItemCount", uniqueItemIds.size());
		payload.put("totalQuantityByItemId", totalQuantityByItemId);
		payload.put("signature", hashName(signature.toString()));
		if (includeItems)
		{
			payload.put("items", items);
		}
		return payload;
	}

	@SuppressWarnings("unchecked")
	private Map<String, Object> inventoryDeltaPayload(TickSnapshot snapshot, Map<String, Object> compactInventoryPayload)
	{
		if (compactInventoryPayload == null)
		{
			return null;
		}

		Object inventoryObject = compactInventoryPayload.get("inventory");
		if (!(inventoryObject instanceof Map))
		{
			return null;
		}

		Map<String, Object> current = (Map<String, Object>) inventoryObject;
		if (!Boolean.TRUE.equals(current.get("known")))
		{
			lastCompactInventorySnapshot = current;
			return null;
		}

		Map<String, Object> previous = lastCompactInventorySnapshot;
		lastCompactInventorySnapshot = current;

		if (previous == null || !Boolean.TRUE.equals(previous.get("known")))
		{
			return null;
		}

		String previousSignature = stringValue(previous.get("signature"));
		String currentSignature = stringValue(current.get("signature"));
		boolean sameSignature = previousSignature.equals(currentSignature);
		boolean sameFreeSlots = getInt(previous.get("freeSlots"), -1) == getInt(current.get("freeSlots"), -1);
		boolean sameFilledSlots = getInt(previous.get("filledSlots"), -1) == getInt(current.get("filledSlots"), -1);

		if (sameSignature && sameFreeSlots && sameFilledSlots)
		{
			return null;
		}

		List<Map<String, Object>> changedSlots = changedInventorySlots(previous, current);
		List<Map<String, Object>> quantityChanges = inventoryQuantityChanges(previous, current);
		List<Map<String, Object>> addedItems = new ArrayList<>();
		List<Map<String, Object>> removedItems = new ArrayList<>();

		for (Map<String, Object> change : quantityChanges)
		{
			int delta = getInt(change.get("delta"), 0);
			if (delta > 0)
			{
				addedItems.add(change);
			}
			else if (delta < 0)
			{
				removedItems.add(change);
			}
		}

		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("tick", snapshot.tickId);
		payload.put("inventorySignatureBefore", previousSignature);
		payload.put("inventorySignatureAfter", currentSignature);
		payload.put("changedSlots", changedSlots);
		payload.put("addedItems", addedItems);
		payload.put("removedItems", removedItems);
		payload.put("quantityChanges", quantityChanges);
		payload.put("freeSlotsBefore", nullableInt(previous.get("freeSlots")));
		payload.put("freeSlotsAfter", nullableInt(current.get("freeSlots")));
		payload.put("filledSlotsBefore", nullableInt(previous.get("filledSlots")));
		payload.put("filledSlotsAfter", nullableInt(current.get("filledSlots")));
		payload.put("inventoryFull", getInt(current.get("freeSlots"), -1) == 0);
		payload.put("generatedFromItemContainerChanged", false);
		payload.put("eventSource", "gameTickInventorySnapshot");
		return payload;
	}

	private List<Map<String, Object>> changedInventorySlots(Map<String, Object> previous, Map<String, Object> current)
	{
		Map<Integer, Map<String, Object>> previousSlots = itemsBySlot(previous);
		Map<Integer, Map<String, Object>> currentSlots = itemsBySlot(current);
		Set<Integer> slots = new HashSet<>();
		slots.addAll(previousSlots.keySet());
		slots.addAll(currentSlots.keySet());

		List<Map<String, Object>> changes = new ArrayList<>();
		for (Integer slot : slots)
		{
			Map<String, Object> before = previousSlots.get(slot);
			Map<String, Object> after = currentSlots.get(slot);
			int beforeItemId = before == null ? -1 : getInt(before.get("itemId"), -1);
			int beforeQuantity = before == null ? 0 : getInt(before.get("quantity"), 0);
			int afterItemId = after == null ? -1 : getInt(after.get("itemId"), -1);
			int afterQuantity = after == null ? 0 : getInt(after.get("quantity"), 0);

			if (beforeItemId == afterItemId && beforeQuantity == afterQuantity)
			{
				continue;
			}

			Map<String, Object> change = new LinkedHashMap<>();
			change.put("slot", slot);
			change.put("beforeItemId", beforeItemId);
			change.put("beforeQuantity", beforeQuantity);
			change.put("afterItemId", afterItemId);
			change.put("afterQuantity", afterQuantity);
			changes.add(change);
		}

		return changes;
	}

	@SuppressWarnings("unchecked")
	private Map<Integer, Map<String, Object>> itemsBySlot(Map<String, Object> snapshot)
	{
		Map<Integer, Map<String, Object>> bySlot = new LinkedHashMap<>();
		Object itemsObject = snapshot.get("items");
		if (!(itemsObject instanceof List))
		{
			return bySlot;
		}

		for (Object itemObject : (List<Object>) itemsObject)
		{
			if (!(itemObject instanceof Map))
			{
				continue;
			}

			Map<String, Object> item = (Map<String, Object>) itemObject;
			Integer slot = nullableInt(item.get("slot"));
			if (slot != null)
			{
				bySlot.put(slot, item);
			}
		}

		return bySlot;
	}

	private List<Map<String, Object>> inventoryQuantityChanges(Map<String, Object> previous, Map<String, Object> current)
	{
		Map<Integer, Integer> previousCounts = itemQuantityById(previous);
		Map<Integer, Integer> currentCounts = itemQuantityById(current);
		Set<Integer> itemIds = new HashSet<>();
		itemIds.addAll(previousCounts.keySet());
		itemIds.addAll(currentCounts.keySet());

		List<Map<String, Object>> changes = new ArrayList<>();
		for (Integer itemId : itemIds)
		{
			int before = previousCounts.getOrDefault(itemId, 0);
			int after = currentCounts.getOrDefault(itemId, 0);
			if (before == after)
			{
				continue;
			}

			Map<String, Object> change = new LinkedHashMap<>();
			change.put("itemId", itemId);
			change.put("beforeQuantity", before);
			change.put("afterQuantity", after);
			change.put("delta", after - before);
			change.put("changeType", after > before ? "itemAdded" : "itemRemoved");
			changes.add(change);
		}

		return changes;
	}

	private Map<String, Object> bankContainerDeltaPayload(TickSnapshot snapshot, Map<String, Object> current)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<String> warnings = new ArrayList<>();
		payload.put("schema", "bank_container_delta.v1");
		payload.put("tick", snapshot == null ? null : snapshot.tickId);
		payload.put("source", "gameTickBankSnapshot");
		payload.put("changedItems", new ArrayList<Map<String, Object>>());
		payload.put("warnings", warnings);

		if (current == null || !Boolean.TRUE.equals(current.get("known")))
		{
			warnings.add("current bank container snapshot unavailable");
			payload.put("available", false);
			return payload;
		}

		Map<String, Object> previous = lastCompactBankContainerSnapshot;
		lastCompactBankContainerSnapshot = current;
		if (previous == null || !Boolean.TRUE.equals(previous.get("known")))
		{
			warnings.add("previous bank container snapshot unavailable");
			payload.put("available", false);
			return payload;
		}

		List<Map<String, Object>> changes = inventoryQuantityChanges(previous, current);
		for (Map<String, Object> change : changes)
		{
			change.put("source", "snapshot_diff");
		}
		payload.put("available", !changes.isEmpty());
		payload.put("changedItems", changes);
		payload.put("itemCountBefore", nullableInt(previous.get("itemCount")));
		payload.put("itemCountAfter", nullableInt(current.get("itemCount")));
		payload.put("signatureBefore", stringValue(previous.get("signature")));
		payload.put("signatureAfter", stringValue(current.get("signature")));
		return payload;
	}

	@SuppressWarnings("unchecked")
	private Map<Integer, Integer> itemQuantityById(Map<String, Object> snapshot)
	{
		Map<Integer, Integer> counts = new LinkedHashMap<>();
		Object totalsObject = snapshot.get("totalQuantityByItemId");
		if (totalsObject instanceof Map)
		{
			for (Map.Entry<?, ?> entry : ((Map<?, ?>) totalsObject).entrySet())
			{
				int itemId = getInt(entry.getKey(), -1);
				int quantity = getInt(entry.getValue(), 0);
				if (itemId > 0 && quantity > 0)
				{
					counts.put(itemId, counts.getOrDefault(itemId, 0) + quantity);
				}
			}
			return counts;
		}
		Object itemsObject = snapshot.get("items");
		if (!(itemsObject instanceof List))
		{
			return counts;
		}

		for (Object itemObject : (List<Object>) itemsObject)
		{
			if (!(itemObject instanceof Map))
			{
				continue;
			}

			Map<String, Object> item = (Map<String, Object>) itemObject;
			int itemId = getInt(item.get("itemId"), -1);
			int quantity = getInt(item.get("quantity"), 0);
			if (itemId > 0 && quantity > 0)
			{
				counts.put(itemId, counts.getOrDefault(itemId, 0) + quantity);
			}
		}

		return counts;
	}

	private Integer nullableInt(Object value)
	{
		return value instanceof Number ? ((Number) value).intValue() : null;
	}

	private String stringValue(Object value)
	{
		return value == null ? "" : String.valueOf(value);
	}

	private Map<String, Object> activityPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<String> changedFields = new ArrayList<>();
		int animation = Integer.MIN_VALUE;
		int poseAnimation = Integer.MIN_VALUE;
		String interactingSignature = null;

		if (snapshot.localPlayer != null)
		{
			animation = snapshot.localPlayer.animation;
			poseAnimation = snapshot.localPlayer.poseAnimation;
			payload.put("animation", animation);
			payload.put("previousAnimation", lastActivityAnimation == Integer.MIN_VALUE ? null : lastActivityAnimation);
			payload.put("poseAnimation", poseAnimation);
			payload.put("previousPoseAnimation", lastActivityPoseAnimation == Integer.MIN_VALUE ? null : lastActivityPoseAnimation);
			payload.put("combatLevel", snapshot.localPlayer.combatLevel);

			if (lastActivityAnimation != Integer.MIN_VALUE && lastActivityAnimation != animation)
			{
				changedFields.add("animation");
			}
			if (lastActivityPoseAnimation != Integer.MIN_VALUE && lastActivityPoseAnimation != poseAnimation)
			{
				changedFields.add("poseAnimation");
			}
		}

		if (snapshot.status != null)
		{
			payload.put("interacting", interactingPayload(snapshot.status));
			interactingSignature = interactingSignature(snapshot.status);
			payload.put("previousInteractingSignature", lastActivityInteractingSignature);
			payload.put("interactingSignature", interactingSignature);
			if (lastActivityInteractingSignature != null && !lastActivityInteractingSignature.equals(interactingSignature))
			{
				changedFields.add("interacting");
			}
			payload.put("runEnergyRaw", snapshot.status.runEnergyRaw);
			payload.put("runEnergyPercent", snapshot.status.runEnergyPercent);
			payload.put("hitpointsBoosted", snapshot.status.hitpointsBoosted);
			payload.put("hitpointsReal", snapshot.status.hitpointsReal);
		}

		payload.put("changedFields", changedFields);
		payload.put("activityChanged", !changedFields.isEmpty());
		payload.put("eventSource", "gameTickActivitySnapshot");
		payload.put("movementKnown", false);
		payload.put("interpretation", "observed_facts_only");

		if (animation != Integer.MIN_VALUE)
		{
			lastActivityAnimation = animation;
		}
		if (poseAnimation != Integer.MIN_VALUE)
		{
			lastActivityPoseAnimation = poseAnimation;
		}
		if (interactingSignature != null)
		{
			lastActivityInteractingSignature = interactingSignature;
		}

		return payload;
	}

	private String interactingSignature(TickSnapshot.StatusSnapshot status)
	{
		if (status == null)
		{
			return "";
		}

		return stringValue(status.interactingType)
				+ ":" + status.interactingIndex
				+ ":" + status.interactingId
				+ ":" + stringValue(status.interactingName)
				+ ":" + status.interactingWorldX
				+ ":" + status.interactingWorldY
				+ ":" + status.interactingPlane;
	}

	private Map<String, Object> navigationPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("tick", snapshot.tickId);
		payload.put("plane", snapshot.localPlayer == null ? null : snapshot.localPlayer.plane);
		payload.put("player", navigationPlayerPayload(snapshot));
		payload.put("collision", collisionSummaryPayload(snapshot, false));
		payload.put("bounds", navigationBoundsPayload(snapshot));
		payload.put("source", navigationSourcePayload());
		payload.put("fullCollisionGridIncluded", false);
		payload.put("fullCollisionGridConfigured", config.compactNavigationIncludeFullCollisionGrid());
		payload.put("hashOnly", config.compactNavigationHashOnly());
		return payload;
	}

	private Map<String, Object> collisionGridPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("tick", snapshot.tickId);
		payload.put("plane", snapshot.localPlayer == null ? null : snapshot.localPlayer.plane);
		payload.put("collision", collisionSummaryPayload(snapshot, true));
		payload.put("source", navigationSourcePayload());
		payload.put("encoding", "json-int-grid");
		payload.put("debugOnly", true);
		return payload;
	}

	private Map<String, Object> collisionWindowPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		int plane = snapshot.localPlayer == null ? -1 : snapshot.localPlayer.plane;
		Integer playerSceneX = snapshot.localPlayer == null ? null : snapshot.localPlayer.sceneX;
		Integer playerSceneY = snapshot.localPlayer == null ? null : snapshot.localPlayer.sceneY;
		int radius = compactNavigationCollisionWindowRadius();
		CollisionData collisionData = collisionDataForPlane(plane);
		int[][] flags = collisionData == null ? null : collisionData.getFlags();
		List<String> warnings = new ArrayList<>();

		payload.put("tick", snapshot.tickId);
		payload.put("plane", plane >= 0 ? plane : null);
		payload.put("playerSceneX", playerSceneX);
		payload.put("playerSceneY", playerSceneY);
		payload.put("windowRadius", radius);
		payload.put("mapWidth", flags == null ? null : flags.length);
		payload.put("mapHeight", flags == null ? null : collisionHeight(flags));
		payload.put("generatedFromPlane", plane >= 0 ? plane : null);
		payload.put("encoding", "json-rows-int-flags");

		if (flags == null || playerSceneX == null || playerSceneY == null)
		{
			warnings.add("collision window unavailable: collision flags or player scene tile missing");
			payload.put("collisionKnown", false);
			payload.put("warnings", warnings);
			return payload;
		}

		int mapWidth = flags.length;
		int mapHeight = collisionHeight(flags);
		int minSceneX = Math.max(0, playerSceneX - radius);
		int maxSceneX = Math.min(mapWidth - 1, playerSceneX + radius);
		int minSceneY = Math.max(0, playerSceneY - radius);
		int maxSceneY = Math.min(mapHeight - 1, playerSceneY + radius);
		List<List<Integer>> rows = new ArrayList<>();
		long hash = 1469598103934665603L;

		for (int sceneY = minSceneY; sceneY <= maxSceneY; sceneY++)
		{
			List<Integer> row = new ArrayList<>();
			for (int sceneX = minSceneX; sceneX <= maxSceneX; sceneX++)
			{
				int value = collisionFlagAt(flags, sceneX, sceneY);
				row.add(value);
				hash = collisionHashStep(hash, value);
			}
			rows.add(row);
		}

		int width = Math.max(0, maxSceneX - minSceneX + 1);
		int height = Math.max(0, maxSceneY - minSceneY + 1);
		payload.put("collisionKnown", true);
		payload.put("minSceneX", minSceneX);
		payload.put("maxSceneX", maxSceneX);
		payload.put("minSceneY", minSceneY);
		payload.put("maxSceneY", maxSceneY);
		payload.put("width", width);
		payload.put("height", height);
		payload.put("flags", rows);
		payload.put("collisionWindowTileCount", width * height);
		payload.put("collisionWindowHash", Long.toUnsignedString(hash, 16));
		payload.put("windowHash", Long.toUnsignedString(hash, 16));
		payload.put("movementMask", COLLISION_MOVEMENT_MASK);
		payload.put("warnings", warnings);
		return payload;
	}

	private Map<String, Object> navigationPlayerPayload(TickSnapshot snapshot)
	{
		Map<String, Object> player = new LinkedHashMap<>();

		if (snapshot.localPlayer == null)
		{
			return player;
		}

		player.put("worldX", snapshot.localPlayer.worldX);
		player.put("worldY", snapshot.localPlayer.worldY);
		player.put("plane", snapshot.localPlayer.plane);
		player.put("sceneX", snapshot.localPlayer.sceneX);
		player.put("sceneY", snapshot.localPlayer.sceneY);
		player.put("localX", snapshot.localPlayer.localX);
		player.put("localY", snapshot.localPlayer.localY);
		return player;
	}

	private Map<String, Object> navigationBoundsPayload(TickSnapshot snapshot)
	{
		Map<String, Object> bounds = new LinkedHashMap<>();
		int width = 0;
		int height = 0;
		int plane = snapshot.localPlayer == null ? -1 : snapshot.localPlayer.plane;
		CollisionData collision = collisionDataForPlane(plane);

		if (collision != null && collision.getFlags() != null)
		{
			int[][] flags = collision.getFlags();
			width = flags.length;
			height = collisionHeight(flags);
		}

		bounds.put("sceneMinX", width > 0 ? 0 : null);
		bounds.put("sceneMaxX", width > 0 ? width - 1 : null);
		bounds.put("sceneMinY", height > 0 ? 0 : null);
		bounds.put("sceneMaxY", height > 0 ? height - 1 : null);
		return bounds;
	}

	private Map<String, Object> navigationSourcePayload()
	{
		Map<String, Object> source = new LinkedHashMap<>();
		WorldView worldView = client.getTopLevelWorldView();
		source.put("worldViewId", worldView == null ? null : worldView.getId());
		source.put("topLevelWorldView", worldView == null || worldView.isTopLevel());
		source.put("currentPlane", worldView == null ? client.getPlane() : worldView.getPlane());
		source.put("baseX", worldView == null ? client.getBaseX() : worldView.getBaseX());
		source.put("baseY", worldView == null ? client.getBaseY() : worldView.getBaseY());
		source.put("warning", "Collision summary is read-only and does not include route planning.");
		return source;
	}

	private Map<String, Object> collisionSummaryPayload(TickSnapshot snapshot, boolean includeGrid)
	{
		Map<String, Object> collision = new LinkedHashMap<>();
		int plane = snapshot.localPlayer == null ? -1 : snapshot.localPlayer.plane;
		CollisionData collisionData = collisionDataForPlane(plane);

		collision.put("collisionKnown", collisionData != null && collisionData.getFlags() != null);
		collision.put("planeKnown", plane >= 0);
		collision.put("plane", plane >= 0 ? plane : null);

		if (collisionData == null || collisionData.getFlags() == null)
		{
			collision.put("warning", "collision maps unavailable");
			return collision;
		}

		int[][] flags = collisionData.getFlags();
		int width = flags.length;
		int height = collisionHeight(flags);
		int blockedMovementTileCount = 0;
		int blockedFullTileCount = 0;
		long hash = 1469598103934665603L;

		for (int x = 0; x < flags.length; x++)
		{
			int[] column = flags[x];
			if (column == null)
			{
				hash = collisionHashStep(hash, 0);
				continue;
			}

			for (int y = 0; y < column.length; y++)
			{
				int value = column[y];
				hash = collisionHashStep(hash, value);
				if ((value & COLLISION_MOVEMENT_MASK) != 0)
				{
					blockedMovementTileCount++;
				}
				if ((value & CollisionDataFlag.BLOCK_MOVEMENT_FULL) != 0)
				{
					blockedFullTileCount++;
				}
			}
		}

		collision.put("mapWidth", width);
		collision.put("mapHeight", height);
		collision.put("blockedMovementTileCount", blockedMovementTileCount);
		collision.put("blockedFullTileCount", blockedFullTileCount);
		collision.put("collisionHash", Long.toUnsignedString(hash, 16));
		collision.put("collisionMapVersion", Long.toUnsignedString(hash, 16));
		collision.put("movementMask", COLLISION_MOVEMENT_MASK);

		if (includeGrid)
		{
			collision.put("flags", flags);
		}

		return collision;
	}

	private CollisionData collisionDataForPlane(int plane)
	{
		if (plane < 0)
		{
			return null;
		}

		WorldView worldView = client.getTopLevelWorldView();
		CollisionData[] collisionMaps = worldView == null ? client.getCollisionMaps() : worldView.getCollisionMaps();

		if (collisionMaps == null || plane >= collisionMaps.length)
		{
			return null;
		}

		return collisionMaps[plane];
	}

	private int compactNavigationCollisionWindowRadius()
	{
		return Math.max(8, Math.min(52, config.compactNavigationCollisionWindowRadius()));
	}

	private int compactNavigationFullGridIntervalTicks()
	{
		int configured = config.compactNavigationFullGridIntervalTicks();
		if (configured > 0)
		{
			return configured;
		}
		return config.compactNavigationGridIntervalTicks();
	}

	private int collisionFlagAt(int[][] flags, int sceneX, int sceneY)
	{
		if (flags == null || sceneX < 0 || sceneX >= flags.length)
		{
			return 0;
		}

		int[] column = flags[sceneX];
		if (column == null || sceneY < 0 || sceneY >= column.length)
		{
			return 0;
		}

		return column[sceneY];
	}

	private int collisionHeight(int[][] flags)
	{
		int height = 0;
		if (flags == null)
		{
			return height;
		}

		for (int[] column : flags)
		{
			if (column != null)
			{
				height = Math.max(height, column.length);
			}
		}
		return height;
	}

	private long collisionHashStep(long current, int value)
	{
		long hash = current ^ (value & 0xffffffffL);
		return hash * 1099511628211L;
	}

	private Map<String, Object> writerHealthPayload(TelemetryWriter currentWriter, TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<String> liveCachePayloadTypes = currentWriter.getLiveCachePayloadTypes();
		Map<String, Object> liveCacheHealth = currentWriter.getLiveCacheHealth();
		payload.put("recordingMode", currentWriter.getRecordingMode());
		payload.put("rawTickRecordingEnabled", currentWriter.isRawTickRecordingEnabled());
		payload.put("rawEventRecordingEnabled", currentWriter.isRawEventRecordingEnabled());
		payload.put("frameRecordingEnabled", currentWriter.isFrameRecordingEnabled());
		payload.put("compactPacketRecordingEnabled", currentWriter.isCompactLivePacketsEnabled());
		payload.put("rawTicksWritten", currentWriter.getRawTicksWritten());
		payload.put("rawTicksSuppressedByMode", currentWriter.getRawTicksSuppressedByMode());
		payload.put("rawEventsWritten", currentWriter.getRawEventsWritten());
		payload.put("rawEventsSuppressedByMode", currentWriter.getRawEventsSuppressedByMode());
		payload.put("framesWritten", currentWriter.getFramesWritten());
		payload.put("framesSuppressedByMode", currentWriter.getFramesSuppressedByMode());
		payload.put("rawWriterQueueDepth", currentWriter.getQueueSize());
		payload.put("droppedRawRecords", currentWriter.getDroppedRecords());
		payload.put("droppedFrameCount", currentWriter.getDroppedFrameCount());
		payload.put("compactLiveEnabled", currentWriter.isCompactLivePacketsEnabled());
		payload.put("livePacketsRuntimeRemoved", true);
		payload.put("ndjsonRuntimeRemoved", true);
		payload.put("jsonlRuntimeRemoved", true);
		payload.put("livePacketWriterActive", false);
		payload.put("compactLivePacketFilesEnabled", false);
		payload.put("compactLiveQueueDepth", currentWriter.getLivePacketQueueDepth());
		payload.put("livePacketsWritten", currentWriter.getLivePacketsWritten());
		payload.put("livePacketsDropped", currentWriter.getLivePacketsDropped());
		payload.put("livePacketWriteErrors", currentWriter.getLivePacketWriteErrors());
		payload.put("livePacketLastWriteMillis", currentWriter.getLivePacketLastWriteMillis());
		payload.put("livePacketSegmentCount", currentWriter.getLivePacketSegmentCount());
		payload.put("livePacketTotalBytes", currentWriter.getLivePacketTotalBytes());
		payload.put("livePacketSegmentsPruned", currentWriter.getLivePacketSegmentsPruned());
		payload.put("livePacketRetentionBytes", 0L);
		payload.put("livePacketRetentionSegments", 0L);
		payload.put("livePacketActiveSegment", currentWriter.getLivePacketActiveSegment());
		payload.put("liveCacheEnabled", currentWriter.isLiveCacheEnabled());
		payload.put("liveCacheUpdates", currentWriter.getLiveCacheUpdates());
		payload.put("liveCacheUpdateErrors", currentWriter.getLiveCacheUpdateErrors());
		payload.put("liveCachePayloadTypes", liveCachePayloadTypes);
		payload.put("liveCacheLatestTick", currentWriter.getLiveCacheLatestTick());
		payload.put("liveCacheLatestSequence", currentWriter.getLiveCacheLatestSequence());
		payload.put("liveCacheEstimatedBytes", currentWriter.getLiveCacheEstimatedBytes());
		payload.put("liveCacheHealth", liveCacheHealth);
		payload.put("navigationCachePresent", liveCachePayloadTypes.contains(PACKET_NAVIGATION));
		payload.put("collisionWindowCachePresent", liveCachePayloadTypes.contains(PACKET_COLLISION_WINDOW));
		payload.put("bankUiCachePresent", liveCachePayloadTypes.contains(PACKET_BANK_UI));
		payload.put("dialogueStateCachePresent", liveCachePayloadTypes.contains(PACKET_DIALOGUE_STATE));
		payload.put("combatStateCachePresent", liveCachePayloadTypes.contains(PACKET_COMBAT_STATE));
		payload.put("navigationPacketBuiltThisTick", liveCachePacketBuiltThisTick(liveCacheHealth, PACKET_NAVIGATION, snapshot));
		payload.put("collisionWindowPacketBuiltThisTick", liveCachePacketBuiltThisTick(liveCacheHealth, PACKET_COLLISION_WINDOW, snapshot));
		payload.put("bankUiPacketBuiltThisTick", liveCachePacketBuiltThisTick(liveCacheHealth, PACKET_BANK_UI, snapshot));
		payload.put("dialogueStatePacketBuiltThisTick", liveCachePacketBuiltThisTick(liveCacheHealth, PACKET_DIALOGUE_STATE, snapshot));
		payload.put("combatStatePacketBuiltThisTick", liveCachePacketBuiltThisTick(liveCacheHealth, PACKET_COMBAT_STATE, snapshot));
		payload.put("emitNavigationEffective", emitNavigationEffective(currentWriter));
		payload.put("emitCollisionWindowEffective", emitCollisionWindowEffective(currentWriter));
		payload.put("emitBankUiEffective", emitBankUiEffective(currentWriter));
		payload.put("emitCompactNavigationPacketsConfigured", config.emitCompactNavigationPackets());
		payload.put("compactNavigationEmitCollisionWindowConfigured", config.compactNavigationEmitCollisionWindow());
		payload.put("compactLivePacketTypesConfigured", config.compactLivePacketTypes());
		payload.put("snapshotNoFileLiveCacheOnly", snapshotNoFileLiveCacheOnly(currentWriter));
		payload.put("compactLiveStreamEnabled", false);
		payload.put("compactLiveStreamHost", null);
		payload.put("compactLiveStreamPort", 0);
		payload.put("compactLiveStreamQueueSize", 0);
		payload.put("compactLiveStreamAlsoWriteFiles", false);
		payload.put("compactLiveStreamCircuitBreakerEnabled", false);
		payload.put("compactLiveStreamMaxWriteMillisConfigured", 0);
		payload.put("compactLiveStreamQueueDepth", currentWriter.getCompactLiveStreamQueueDepth());
		payload.put("compactLiveStreamClientCount", currentWriter.getCompactLiveStreamClientCount());
		payload.put("compactLiveStreamPacketsOffered", currentWriter.getCompactLiveStreamPacketsOffered());
		payload.put("compactLiveStreamPacketsWritten", currentWriter.getCompactLiveStreamPacketsWritten());
		payload.put("compactLiveStreamPacketsDropped", currentWriter.getCompactLiveStreamPacketsDropped());
		payload.put("compactLiveStreamPacketsDroppedNoClients", currentWriter.getCompactLiveStreamPacketsDroppedNoClients());
		payload.put("compactLiveStreamPacketsDroppedByCircuitBreaker", currentWriter.getCompactLiveStreamPacketsDroppedByCircuitBreaker());
		payload.put("compactLiveStreamWriteErrors", currentWriter.getCompactLiveStreamWriteErrors());
		payload.put("compactLiveStreamAcceptedClients", currentWriter.getCompactLiveStreamAcceptedClients());
		payload.put("compactLiveStreamDisconnectedClients", currentWriter.getCompactLiveStreamDisconnectedClients());
		payload.put("compactLiveStreamLastWriteMillis", currentWriter.getCompactLiveStreamLastWriteMillis());
		payload.put("compactLiveStreamMaxWriteMillisObserved", currentWriter.getCompactLiveStreamMaxWriteMillisObserved());
		payload.put("compactLiveStreamCircuitBreakerTripped", currentWriter.isCompactLiveStreamCircuitBreakerTripped());
		payload.put("compactLiveStreamCircuitBreakerReason", currentWriter.getCompactLiveStreamCircuitBreakerReason());
		payload.put("compactLiveStreamDisabledUntilUtc", currentWriter.getCompactLiveStreamDisabledUntilUtc());
		payload.put("compactLiveStreamCircuitBreakerTrips", currentWriter.getCompactLiveStreamCircuitBreakerTrips());
		payload.put("compactLiveStreamPacketsByType", currentWriter.getCompactLiveStreamPacketsByType());
		payload.put("compactLiveStreamPacketsOfferedByType", currentWriter.getCompactLiveStreamPacketsOfferedByType());
		payload.put("compactLiveStreamPacketsSentByType", currentWriter.getCompactLiveStreamPacketsSentByType());
		payload.put("compactLiveStreamPacketsDroppedByType", currentWriter.getCompactLiveStreamPacketsDroppedByType());
		payload.put("compactLiveStreamLatestOfferedTickByType", currentWriter.getCompactLiveStreamLatestOfferedTickByType());
		payload.put("compactLiveStreamLatestTickByType", currentWriter.getCompactLiveStreamLatestTickByType());
		payload.put("compactLiveIncludeHeavyGeometry", config.compactLiveIncludeHeavyGeometry());
		payload.put("compactLiveIncludeClickableHull", lastCompactLiveIncludeClickableHull);
		payload.put("compactLiveIncludeCanvasTilePolygon", lastCompactLiveIncludeCanvasTilePolygon);
		payload.put("compactLiveIncludeConvexHull", lastCompactLiveIncludeConvexHull);
		payload.put("compactLiveGeometryMaxRefs", lastCompactGeometryMaxRefs);
		payload.put("compactLiveGeometryRefsWithPolygons", lastCompactGeometryRefsWithPolygons);
		payload.put("compactLiveGeometryRefsSkippedByCap", lastCompactGeometryRefsSkippedByCap);
		payload.put("compactLiveGeometryCapHit", lastCompactGeometryCapHit);
		payload.put("compactLiveHullsEmitted", lastCompactHullsEmitted);
		payload.put("compactLiveHullDroppedOffscreen", lastCompactHullDroppedOffscreen);
		payload.put("compactLiveHullDroppedNoCanvasIntersection", lastCompactHullDroppedNoCanvasIntersection);
		payload.put("compactLiveHullDroppedByCap", lastCompactHullDroppedByCap);
		payload.put("compactLiveHullDroppedNullClickbox", lastCompactHullDroppedNullClickbox);
		payload.put("rawRecordingEnabled", currentWriter.isRawRecordingEnabled());
		return payload;
	}

	@SuppressWarnings("unchecked")
	private boolean liveCachePacketBuiltThisTick(Map<String, Object> liveCacheHealth, String packetType, TickSnapshot snapshot)
	{
		if (liveCacheHealth == null || snapshot == null)
		{
			return false;
		}
		Object raw = liveCacheHealth.get("liveCacheLatestTickByType");
		if (!(raw instanceof Map<?, ?>))
		{
			return false;
		}
		Map<Object, Object> latestTickByType = (Map<Object, Object>) raw;
		Object tick = latestTickByType.get(packetType);
		return tick instanceof Number && ((Number) tick).longValue() == snapshot.tickId;
	}

	private BufferedImage copyRuneliteFrame(Image image)
	{
		if (image == null)
		{
			return null;
		}

		if (config.includeClientFrame())
		{
			return imageCapture.addClientFrame(image);
		}

		return ImageUtil.bufferedImageFromImage(image);
	}

	private BufferedImage captureScreenRectangle() throws Exception
	{
		Canvas canvas = client.getCanvas();

		if (canvas == null)
		{
			return null;
		}

		Dimension size = canvas.getSize();

		if (size.width <= 0 || size.height <= 0 || !canvas.isShowing())
		{
			return null;
		}

		java.awt.Point location = canvas.getLocationOnScreen();
		Rectangle rectangle = new Rectangle(location.x, location.y, size.width, size.height);
		return new Robot().createScreenCapture(rectangle);
	}

	private String normalizeScreenshotFormat(String format)
	{
		if (format == null)
		{
			return "jpg";
		}

		String normalized = format.trim().toLowerCase();
		return "png".equals(normalized) ? "png" : "jpg";
	}

	private String normalizeFrameCaptureMode(String mode)
	{
		if (mode == null)
		{
			return "RUNELITE_ONLY";
		}

		String normalized = mode.trim().toUpperCase();
		return "SCREEN_RECTANGLE".equals(normalized) ? "SCREEN_RECTANGLE" : "RUNELITE_ONLY";
	}

	@Subscribe
	public void onPlayerSpawned(PlayerSpawned event)
	{
		logEvent("PlayerSpawned", actorPayload(event.getPlayer()));
	}

	@Subscribe
	public void onPlayerDespawned(PlayerDespawned event)
	{
		logEvent("PlayerDespawned", actorPayload(event.getPlayer()));
	}

	@Subscribe
	public void onPlayerChanged(PlayerChanged event)
	{
		logEvent("PlayerChanged", actorPayload(event.getPlayer()));
	}

	@Subscribe
	public void onItemSpawned(ItemSpawned event)
	{
		rememberItem(event.getItem());
		logEvent("ItemSpawned", itemEventPayload(event.getTile(), event.getItem()));
	}

	@Subscribe
	public void onItemDespawned(ItemDespawned event)
	{
		rememberItem(event.getItem());
		logEvent("ItemDespawned", itemEventPayload(event.getTile(), event.getItem()));
	}

	@Subscribe
	public void onItemQuantityChanged(ItemQuantityChanged event)
	{
		Map<String, Object> payload = itemEventPayload(event.getTile(), event.getItem());
		payload.put("oldQuantity", event.getOldQuantity());
		payload.put("newQuantity", event.getNewQuantity());
		rememberItem(event.getItem());

		logEvent("ItemQuantityChanged", payload);
	}

	@Subscribe
	public void onWidgetLoaded(WidgetLoaded event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("groupId", event.getGroupId());

		logEvent("WidgetLoaded", payload);
	}

	@Subscribe
	public void onWidgetClosed(WidgetClosed event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("groupId", event.getGroupId());

		logEvent("WidgetClosed", payload);
	}

	private void captureStatus(TickSnapshot snapshot)
	{
		TickSnapshot.StatusSnapshot status = new TickSnapshot.StatusSnapshot();
		Player localPlayer = client.getLocalPlayer();
		status.runEnergyRaw = client.getEnergy();
		status.runEnergyPercent = status.runEnergyRaw / 100.0;
		status.weight = client.getWeight();
		status.hitpointsBoosted = client.getBoostedSkillLevel(Skill.HITPOINTS);
		status.hitpointsReal = client.getRealSkillLevel(Skill.HITPOINTS);
		status.prayerBoosted = client.getBoostedSkillLevel(Skill.PRAYER);
		status.prayerReal = client.getRealSkillLevel(Skill.PRAYER);
		status.interactingIndex = -1;
		status.interactingId = -1;
		status.interactingWorldX = -1;
		status.interactingWorldY = -1;
		status.interactingPlane = -1;

		if (localPlayer != null)
		{
			status.localHealthRatio = localPlayer.getHealthRatio();
			status.localHealthScale = localPlayer.getHealthScale();

			Actor interacting = localPlayer.getInteracting();
			Map<String, Object> interactingPayload = actorPayload(interacting);
			status.interactingType = (String) interactingPayload.get("actorType");
			status.interactingName = interactingPayload.containsKey("name")
					? (String) interactingPayload.get("name")
					: (String) interactingPayload.get("nameHash");
			status.interactingIndex = getInt(interactingPayload.get("index"), -1);
			status.interactingId = getInt(interactingPayload.get("id"), -1);
			status.interactingWorldX = getInt(interactingPayload.get("worldX"), -1);
			status.interactingWorldY = getInt(interactingPayload.get("worldY"), -1);
			status.interactingPlane = getInt(interactingPayload.get("plane"), -1);
		}

		snapshot.status = status;
	}

	private void captureActivePrayers(TickSnapshot snapshot)
	{
		List<TickSnapshot.ActivePrayerSnapshot> prayers = new ArrayList<>();

		for (Prayer prayer : Prayer.values())
		{
			int varbit = prayer.getVarbit();

			if (varbit < 0)
			{
				continue;
			}

			TickSnapshot.ActivePrayerSnapshot prayerSnapshot = new TickSnapshot.ActivePrayerSnapshot();
			prayerSnapshot.name = prayer.name();
			prayerSnapshot.varbit = varbit;
			prayerSnapshot.active = client.getVarbitValue(varbit) == 1;
			prayers.add(prayerSnapshot);
		}

		snapshot.activePrayers = prayers.toArray(new TickSnapshot.ActivePrayerSnapshot[0]);
	}

	private void captureCameraViewport(TickSnapshot snapshot)
	{
		snapshot.cameraX = client.getCameraX();
		snapshot.cameraY = client.getCameraY();
		snapshot.cameraZ = client.getCameraZ();
		snapshot.cameraYaw = client.getCameraYaw();
		snapshot.cameraPitch = client.getCameraPitch();
		snapshot.viewportWidth = client.getViewportWidth();
		snapshot.viewportHeight = client.getViewportHeight();
		snapshot.viewportXOffset = client.getViewportXOffset();
		snapshot.viewportYOffset = client.getViewportYOffset();
		snapshot.canvasWidth = client.getCanvasWidth();
		snapshot.canvasHeight = client.getCanvasHeight();

		Canvas canvas = client.getCanvas();

		if (canvas != null && (snapshot.canvasWidth == null || snapshot.canvasHeight == null
				|| snapshot.canvasWidth <= 0 || snapshot.canvasHeight <= 0))
		{
			Dimension size = canvas.getSize();

			if (size != null)
			{
				snapshot.canvasWidth = size.width;
				snapshot.canvasHeight = size.height;
			}
		}

		snapshot.inputGeometry = captureInputGeometry(snapshot, canvas);
	}

	private TickSnapshot.InputGeometrySnapshot captureInputGeometry(TickSnapshot snapshot, Canvas canvas)
	{
		TickSnapshot.InputGeometrySnapshot geometry = new TickSnapshot.InputGeometrySnapshot();
		geometry.sourceTick = snapshot == null ? null : snapshot.tickId;
		geometry.geometryAvailable = false;
		geometry.reason = "canvas_unavailable";
		geometry.canvasWidth = snapshot == null ? null : snapshot.canvasWidth;
		geometry.canvasHeight = snapshot == null ? null : snapshot.canvasHeight;
		geometry.sourceCanvasWidth = snapshot == null ? null : snapshot.canvasWidth;
		geometry.sourceCanvasHeight = snapshot == null ? null : snapshot.canvasHeight;

		if (canvas == null)
		{
			return geometry;
		}

		Dimension size = canvas.getSize();
		if (size != null)
		{
			geometry.canvasWidth = size.width;
			geometry.canvasHeight = size.height;
		}
		geometry.isCanvasShowing = canvas.isShowing();
		geometry.isClientFocused = canvas.isFocusOwner();

		GraphicsConfiguration graphicsConfiguration = canvas.getGraphicsConfiguration();
		if (graphicsConfiguration != null)
		{
			AffineTransform transform = graphicsConfiguration.getDefaultTransform();
			if (transform != null)
			{
				geometry.displayScaleX = transform.getScaleX();
				geometry.displayScaleY = transform.getScaleY();
			}
		}

		Window window = SwingUtilities.getWindowAncestor(canvas);
		if (window != null)
		{
			Rectangle bounds = window.getBounds();
			if (bounds != null)
			{
				geometry.clientWindowX = bounds.x;
				geometry.clientWindowY = bounds.y;
				geometry.clientWindowWidth = bounds.width;
				geometry.clientWindowHeight = bounds.height;
			}
			geometry.isClientFocused = window.isFocused();
		}

		try
		{
			java.awt.Point location = canvas.getLocationOnScreen();
			if (location != null)
			{
				geometry.canvasScreenX = location.x;
				geometry.canvasScreenY = location.y;
				geometry.geometryAvailable = geometry.canvasWidth != null && geometry.canvasHeight != null
						&& geometry.canvasWidth > 0 && geometry.canvasHeight > 0;
				geometry.reason = geometry.geometryAvailable ? "available" : "canvas_size_unavailable";
			}
		}
		catch (IllegalComponentStateException e)
		{
			geometry.geometryAvailable = false;
			geometry.reason = "canvas_not_showing";
		}
		catch (RuntimeException e)
		{
			geometry.geometryAvailable = false;
			geometry.reason = "canvas_location_unavailable";
		}

		return geometry;
	}

	private void captureWidgets(TickSnapshot snapshot)
	{
		Widget[] roots = client.getWidgetRoots();

		if (roots == null || roots.length == 0)
		{
			snapshot.widgets = new TickSnapshot.WidgetSnapshot[0];
			return;
		}

		TickSnapshot.WidgetSnapshot[] widgets = new TickSnapshot.WidgetSnapshot[roots.length];

		for (int i = 0; i < roots.length; i++)
		{
			Widget widget = roots[i];

			if (widget == null)
			{
				continue;
			}

			TickSnapshot.WidgetSnapshot widgetSnapshot = new TickSnapshot.WidgetSnapshot();
			widgetSnapshot.index = i;
			widgetSnapshot.id = widget.getId();
			widgetSnapshot.type = widget.getType();
			widgetSnapshot.hidden = widget.isHidden();
			widgetSnapshot.text = cleanWidgetText(widget.getText());
			widgetSnapshot.name = cleanWidgetText(widget.getName());
			widgetSnapshot.x = widget.getCanvasLocation() != null ? widget.getCanvasLocation().getX() : -1;
			widgetSnapshot.y = widget.getCanvasLocation() != null ? widget.getCanvasLocation().getY() : -1;
			widgetSnapshot.width = widget.getWidth();
			widgetSnapshot.height = widget.getHeight();

			Widget[] children = widget.getChildren();
			widgetSnapshot.childCount = children == null ? 0 : children.length;

			widgets[i] = widgetSnapshot;
		}

		snapshot.widgets = widgets;
	}

	private void captureBankUi(TickSnapshot snapshot)
	{
		TickSnapshot.BankUiSnapshot bankUi = new TickSnapshot.BankUiSnapshot();
		Widget bankRoot = client.getWidget(InterfaceID.Bankmain.UNIVERSE);
		Widget bankFrame = client.getWidget(InterfaceID.Bankmain.FRAME);
		Widget bankItemsContainer = client.getWidget(InterfaceID.Bankmain.ITEMS_CONTAINER);
		Widget bankItems = client.getWidget(InterfaceID.Bankmain.ITEMS);
		Widget bankDepositInventory = client.getWidget(InterfaceID.Bankmain.DEPOSITINV);
		Widget bankMenuButton = client.getWidget(InterfaceID.Bankmain.MENU_BUTTON);
		Widget inventoryItems = client.getWidget(InterfaceID.Inventory.ITEMS);
		Widget bankSideItems = client.getWidget(InterfaceID.Bankside.ITEMS);
		Widget depositRoot = client.getWidget(InterfaceID.BankDepositbox.UNIVERSE);
		Widget depositFrame = client.getWidget(InterfaceID.BankDepositbox.FRAME);
		Widget depositContents = client.getWidget(InterfaceID.BankDepositbox.CONTENTS);
		Widget depositInventory = client.getWidget(InterfaceID.BankDepositbox.INVENTORY);
		Widget depositInventoryButton = client.getWidget(InterfaceID.BankDepositbox.DEPOSIT_INV);
		Widget depositMenuButton = client.getWidget(InterfaceID.BankDepositbox.MENU_BUTTON);
		Widget bankPinRoot = client.getWidget(InterfaceID.BankpinKeypad.UNIVERSE);
		boolean bankRootVisible = widgetVisible(bankRoot);
		boolean depositRootVisible = widgetVisible(depositRoot);
		boolean bankContainerVisible = widgetVisible(bankItemsContainer) || widgetVisible(bankItems) || widgetVisible(depositContents);
		boolean bankInventoryVisible = widgetVisible(bankItems) || widgetVisible(depositInventory);
		boolean depositButtonVisible = widgetVisible(bankDepositInventory) || widgetVisible(depositInventoryButton);
		boolean bankPinVisible = widgetVisible(bankPinRoot);
		Widget closeButton = firstVisibleClosableWidget(bankFrame, bankRoot, bankMenuButton, depositFrame, depositRoot, depositMenuButton);

		bankUi.bankRootVisible = bankRootVisible;
		bankUi.bankOpen = bankRootVisible || depositRootVisible;
		bankUi.bankPinOpen = bankPinVisible;
		bankUi.bankContainerVisible = bankContainerVisible;
		bankUi.bankInventoryVisible = bankInventoryVisible;
		bankUi.depositInventoryButtonVisible = depositButtonVisible;
		bankUi.topLevelInterfaceId = firstVisibleTopLevelId(bankRoot, depositRoot, bankPinRoot);
		bankUi.closeButtonVisible = widgetVisible(closeButton);
		bankUi.bankCloseButtonVisible = bankUi.closeButtonVisible;
		bankUi.keyboardClosePossible = bankUi.bankOpen && !bankPinVisible && bankUi.topLevelInterfaceId != null;
		bankUi.bankRootWidget = widgetSnapshot(-1, widgetVisible(bankRoot) ? bankRoot : depositRoot);
		bankUi.bankContainerWidget = widgetSnapshot(-1, firstVisibleWidget(bankItemsContainer, bankItems, depositContents));
		bankUi.bankInventoryWidget = widgetSnapshot(-1, firstVisibleWidget(bankItems, depositInventory));
		bankUi.depositInventoryButtonWidget = widgetSnapshot(-1, firstVisibleWidget(bankDepositInventory, depositInventoryButton));
		bankUi.closeButtonWidget = widgetSnapshot(-1, closeButton);
		bankUi.bankPinWidget = widgetSnapshot(-1, bankPinRoot);
		bankUi.bankItems = itemContainerSlots(client.getItemContainer(InventoryID.BANK), 0);
		bankUi.inventorySlotWidgets = inventorySlotWidgetSnapshots(firstVisibleWidget(bankSideItems, inventoryItems, depositInventory), snapshot.inventory);
		snapshot.bankUi = bankUi;
	}

	private TickSnapshot.InventorySlotWidgetSnapshot[] inventorySlotWidgetSnapshots(Widget inventoryItems, TickSnapshot.InventorySlot[] inventorySlots)
	{
		if (!widgetVisible(inventoryItems))
		{
			return new TickSnapshot.InventorySlotWidgetSnapshot[0];
		}

		Widget[] children = inventoryItems.getDynamicChildren();
		if (children == null || children.length == 0)
		{
			children = inventoryItems.getChildren();
		}
		if (children == null || children.length == 0)
		{
			children = inventoryItems.getNestedChildren();
		}
		if (children == null || children.length == 0)
		{
			return new TickSnapshot.InventorySlotWidgetSnapshot[0];
		}

		List<TickSnapshot.InventorySlotWidgetSnapshot> snapshots = new ArrayList<>();
		for (int i = 0; i < children.length && i < INVENTORY_SLOT_COUNT; i++)
		{
			Widget child = children[i];
			if (child == null)
			{
				continue;
			}
			TickSnapshot.InventorySlotWidgetSnapshot slot = inventorySlotWidgetSnapshot(child, i, inventorySlots);
			if (slot != null)
			{
				snapshots.add(slot);
			}
		}
		return snapshots.toArray(new TickSnapshot.InventorySlotWidgetSnapshot[0]);
	}

	private TickSnapshot.InventorySlotWidgetSnapshot inventorySlotWidgetSnapshot(Widget widget, int fallbackSlot, TickSnapshot.InventorySlot[] inventorySlots)
	{
		int slotIndex = widget.getIndex() >= 0 ? widget.getIndex() : fallbackSlot;
		if (slotIndex < 0 || slotIndex >= INVENTORY_SLOT_COUNT)
		{
			return null;
		}

		int itemId = widget.getItemId();
		int quantity = widget.getItemQuantity();
		if (inventorySlots != null && slotIndex < inventorySlots.length && inventorySlots[slotIndex] != null)
		{
			if (itemId <= 0)
			{
				itemId = inventorySlots[slotIndex].itemId;
			}
			if (quantity <= 0)
			{
				quantity = inventorySlots[slotIndex].quantity;
			}
		}
		if (itemId <= 0 || quantity <= 0)
		{
			return null;
		}

		TickSnapshot.Bounds bounds = widgetBounds(widget);
		if (bounds == null)
		{
			return null;
		}

		TickSnapshot.InventorySlotWidgetSnapshot snapshot = new TickSnapshot.InventorySlotWidgetSnapshot();
		snapshot.slot = slotIndex;
		snapshot.itemId = itemId;
		snapshot.quantity = quantity;
		snapshot.widgetId = widget.getId();
		snapshot.widgetIndex = widget.getIndex();
		snapshot.visible = widgetVisible(widget);
		snapshot.bounds = bounds;
		TickSnapshot.CanvasPoint aim = new TickSnapshot.CanvasPoint();
		aim.x = bounds.x + bounds.w / 2;
		aim.y = bounds.y + bounds.h / 2;
		snapshot.aimPoint = aim;
		snapshot.actions = widget.getActions();
		snapshot.source = "inventory_widget";
		return snapshot;
	}

	private void captureDialogueState(TickSnapshot snapshot)
	{
		TickSnapshot.DialogueStateSnapshot dialogue = new TickSnapshot.DialogueStateSnapshot();
		dialogue.schema = "dialogue_state.v1";
		dialogue.active = false;
		dialogue.type = "unknown";
		dialogue.promptText = "";
		dialogue.options = new TickSnapshot.DialogueOptionSnapshot[0];
		dialogue.canUseNumberKeys = null;
		dialogue.canUseSpaceContinue = null;
		dialogue.source = "widget_root_scan";
		dialogue.latestClientTick = clientTickId;
		dialogue.wallTimeMillis = System.currentTimeMillis();

		Widget[] roots = client.getWidgetRoots();
		if (roots == null || roots.length == 0)
		{
			dialogue.widgetRootIds = new Integer[0];
			snapshot.dialogueState = dialogue;
			return;
		}

		List<Widget> visibleTextWidgets = new ArrayList<>();
		List<Integer> rootIds = new ArrayList<>();
		int[] visited = new int[] {0};
		for (Widget root : roots)
		{
			if (root == null)
			{
				continue;
			}
			if (widgetVisible(root))
			{
				int rootId = root.getId() >>> 16;
				if (!rootIds.contains(rootId))
				{
					rootIds.add(rootId);
				}
			}
			collectVisibleTextWidgets(root, visibleTextWidgets, visited);
			if (visited[0] >= DIALOGUE_WIDGET_SCAN_LIMIT)
			{
				break;
			}
		}
		dialogue.widgetRootIds = rootIds.toArray(new Integer[0]);

		String prompt = "";
		List<TickSnapshot.DialogueOptionSnapshot> options = new ArrayList<>();
		boolean clickToContinue = false;
		for (Widget widget : visibleTextWidgets)
		{
			String text = cleanWidgetText(widget.getText());
			if (text.isEmpty())
			{
				text = cleanWidgetText(widget.getName());
			}
			if (text.isEmpty())
			{
				continue;
			}
			String lower = text.toLowerCase(Locale.ROOT);
			if (lower.contains("click here to continue"))
			{
				clickToContinue = true;
			}
			if (prompt.isEmpty() && isDialoguePromptText(lower))
			{
				prompt = text;
			}
			if (isDialogueOptionText(lower))
			{
				options.add(dialogueOptionSnapshot(options.size() + 1, widget, text));
			}
		}

		if (!options.isEmpty())
		{
			dialogue.active = true;
			dialogue.type = "options";
			dialogue.promptText = prompt;
			dialogue.options = options.toArray(new TickSnapshot.DialogueOptionSnapshot[0]);
			dialogue.canUseNumberKeys = true;
			dialogue.canUseSpaceContinue = false;
		}
		else if (clickToContinue)
		{
			dialogue.active = true;
			dialogue.type = "click_to_continue";
			dialogue.promptText = prompt.isEmpty() ? "Click here to continue" : prompt;
			dialogue.options = new TickSnapshot.DialogueOptionSnapshot[0];
			dialogue.canUseNumberKeys = false;
			dialogue.canUseSpaceContinue = true;
		}

		snapshot.dialogueState = dialogue;
	}

	private void collectVisibleTextWidgets(Widget widget, List<Widget> output, int[] visited)
	{
		if (widget == null || visited[0] >= DIALOGUE_WIDGET_SCAN_LIMIT)
		{
			return;
		}
		visited[0]++;
		if (widgetVisible(widget))
		{
			String text = cleanWidgetText(widget.getText());
			String name = cleanWidgetText(widget.getName());
			if (!text.isEmpty() || !name.isEmpty())
			{
				output.add(widget);
			}
			Widget[] children = widget.getChildren();
			if (children != null)
			{
				for (Widget child : children)
				{
					collectVisibleTextWidgets(child, output, visited);
					if (visited[0] >= DIALOGUE_WIDGET_SCAN_LIMIT)
					{
						break;
					}
				}
			}
		}
	}

	private boolean isDialoguePromptText(String lower)
	{
		return lower.contains("climb up or down")
				|| lower.contains("up or down the stairs")
				|| lower.contains("choose an option");
	}

	private boolean isDialogueOptionText(String lower)
	{
		if (lower.contains(" or down") && lower.contains("?"))
		{
			return false;
		}
		return lower.startsWith("climb up")
				|| lower.startsWith("climb down")
				|| lower.startsWith("1. climb up")
				|| lower.startsWith("2. climb down")
				|| lower.startsWith("1 climb up")
				|| lower.startsWith("2 climb down");
	}

	private TickSnapshot.DialogueOptionSnapshot dialogueOptionSnapshot(int index, Widget widget, String text)
	{
		TickSnapshot.DialogueOptionSnapshot option = new TickSnapshot.DialogueOptionSnapshot();
		option.index = index;
		option.key = inferredDialogueOptionKey(index, text);
		option.text = text;
		option.widgetGroup = widget.getId() >>> 16;
		option.widgetChild = widget.getId() & 0xFFFF;
		option.bounds = widgetBounds(widget);
		option.visible = widgetVisible(widget);
		return option;
	}

	private String inferredDialogueOptionKey(int index, String text)
	{
		String value = cleanWidgetText(text);
		if (value.startsWith("1.") || value.startsWith("1 "))
		{
			return "1";
		}
		if (value.startsWith("2.") || value.startsWith("2 "))
		{
			return "2";
		}
		if (index >= 1 && index <= 9)
		{
			return Integer.toString(index);
		}
		return null;
	}

	private TickSnapshot.Bounds widgetBounds(Widget widget)
	{
		if (!widgetVisible(widget) || widget.getCanvasLocation() == null)
		{
			return null;
		}
		TickSnapshot.Bounds bounds = new TickSnapshot.Bounds();
		bounds.x = widget.getCanvasLocation().getX();
		bounds.y = widget.getCanvasLocation().getY();
		bounds.w = widget.getWidth();
		bounds.h = widget.getHeight();
		return bounds;
	}

	private boolean widgetVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	private Widget firstVisibleWidget(Widget... widgets)
	{
		if (widgets == null)
		{
			return null;
		}
		for (Widget widget : widgets)
		{
			if (widgetVisible(widget))
			{
				return widget;
			}
		}
		return null;
	}

	private Widget firstVisibleClosableWidget(Widget... widgets)
	{
		if (widgets == null)
		{
			return null;
		}
		for (Widget widget : widgets)
		{
			if (widgetVisible(widget) && widgetHasCloseAction(widget))
			{
				return widget;
			}
		}
		return null;
	}

	private boolean widgetHasCloseAction(Widget widget)
	{
		if (!widgetVisible(widget) || widget.getActions() == null)
		{
			return false;
		}
		for (String action : widget.getActions())
		{
			if (action != null && "close".equals(action.trim().toLowerCase(Locale.ROOT)))
			{
				return true;
			}
		}
		return false;
	}

	private Integer firstVisibleTopLevelId(Widget... widgets)
	{
		Widget widget = firstVisibleWidget(widgets);
		return widget == null ? null : widget.getId() >>> 16;
	}

	private TickSnapshot.WidgetSnapshot widgetSnapshot(int index, Widget widget)
	{
		if (!widgetVisible(widget))
		{
			return null;
		}

		TickSnapshot.WidgetSnapshot widgetSnapshot = new TickSnapshot.WidgetSnapshot();
		widgetSnapshot.index = index;
		widgetSnapshot.id = widget.getId();
		widgetSnapshot.type = widget.getType();
		widgetSnapshot.hidden = widget.isHidden();
		widgetSnapshot.text = cleanWidgetText(widget.getText());
		widgetSnapshot.name = cleanWidgetText(widget.getName());
		widgetSnapshot.x = widget.getCanvasLocation() != null ? widget.getCanvasLocation().getX() : -1;
		widgetSnapshot.y = widget.getCanvasLocation() != null ? widget.getCanvasLocation().getY() : -1;
		widgetSnapshot.width = widget.getWidth();
		widgetSnapshot.height = widget.getHeight();
		Widget[] children = widget.getChildren();
		widgetSnapshot.childCount = children == null ? 0 : children.length;
		return widgetSnapshot;
	}

	private String cleanWidgetText(String value)
	{
		if (value == null)
		{
			return "";
		}

		return value
				.replaceAll("<[^>]*>", "")
				.replace('\u00A0', ' ')
				.trim();
	}

	private void captureLocalPlayer(TickSnapshot snapshot)
	{
		Player player = client.getLocalPlayer();

		if (player == null)
		{
			return;
		}

		WorldPoint wp = player.getWorldLocation();

		TickSnapshot.LocalPlayer localPlayer = new TickSnapshot.LocalPlayer();
		if (wp != null)
		{
			localPlayer.worldX = wp.getX();
			localPlayer.worldY = wp.getY();
			localPlayer.plane = wp.getPlane();
		}
		LocalPoint localLocation = player.getLocalLocation();
		if (localLocation != null)
		{
			localPlayer.localX = localLocation.getX();
			localPlayer.localY = localLocation.getY();
			localPlayer.sceneX = localLocation.getSceneX();
			localPlayer.sceneY = localLocation.getSceneY();
		}
		localPlayer.animation = player.getAnimation();
		localPlayer.poseAnimation = player.getPoseAnimation();
		localPlayer.combatLevel = player.getCombatLevel();

		snapshot.localPlayer = localPlayer;
	}

	private void captureInventory(TickSnapshot snapshot)
	{
		ItemContainer inventory = client.getItemContainer(InventoryID.INVENTORY);

		if (inventory == null)
		{
			return;
		}

		Item[] items = inventory.getItems();
		int slotCount = Math.max(INVENTORY_SLOT_COUNT, items.length);
		snapshot.inventory = new TickSnapshot.InventorySlot[slotCount];

		for (int i = 0; i < slotCount; i++)
		{
			Item item = i < items.length ? items[i] : null;

			TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
			slot.slot = i;
			slot.itemId = item == null ? -1 : item.getId();
			slot.quantity = item == null ? 0 : item.getQuantity();
			rememberItem(slot.itemId);

			snapshot.inventory[i] = slot;
		}
	}

	private TickSnapshot.InventorySlot[] itemContainerSlots(ItemContainer container, int minSlotCount)
	{
		if (container == null)
		{
			return null;
		}

		Item[] items = container.getItems();
		int slotCount = Math.max(Math.max(0, minSlotCount), items.length);
		TickSnapshot.InventorySlot[] slots = new TickSnapshot.InventorySlot[slotCount];

		for (int i = 0; i < slotCount; i++)
		{
			Item item = i < items.length ? items[i] : null;
			TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
			slot.slot = i;
			slot.itemId = item == null ? -1 : item.getId();
			slot.quantity = item == null ? 0 : item.getQuantity();
			rememberItem(slot.itemId);
			slots[i] = slot;
		}

		return slots;
	}

	private void captureEquipment(TickSnapshot snapshot)
	{
		ItemContainer equipment = client.getItemContainer(InventoryID.EQUIPMENT);

		if (equipment == null)
		{
			snapshot.equipment = new TickSnapshot.InventorySlot[0];
			return;
		}

		Item[] items = equipment.getItems();
		snapshot.equipment = new TickSnapshot.InventorySlot[items.length];

		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];

			TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
			slot.slot = i;
			slot.itemId = item == null ? -1 : item.getId();
			slot.quantity = item == null ? 0 : item.getQuantity();
			rememberItem(slot.itemId);

			snapshot.equipment[i] = slot;
		}
	}
	private void captureSkills(TickSnapshot snapshot)
	{
		ArrayList<TickSnapshot.SkillSnapshot> skills = new ArrayList<>();

		for (Skill skill : Skill.values())
		{
			if (skill == Skill.OVERALL)
			{
				continue;
			}

			TickSnapshot.SkillSnapshot skillSnapshot = new TickSnapshot.SkillSnapshot();
			skillSnapshot.name = skill.name();
			skillSnapshot.realLevel = client.getRealSkillLevel(skill);
			skillSnapshot.boostedLevel = client.getBoostedSkillLevel(skill);
			skillSnapshot.xp = client.getSkillExperience(skill);

			skills.add(skillSnapshot);
		}

		snapshot.skills = skills.toArray(new TickSnapshot.SkillSnapshot[0]);
	}
	private void captureNpcs(TickSnapshot snapshot)
	{
		List<NPC> npcs = client.getNpcs();

		if (npcs == null || npcs.isEmpty())
		{
			snapshot.npcs = new TickSnapshot.NpcSnapshot[0];
			return;
		}

		snapshot.npcs = new TickSnapshot.NpcSnapshot[npcs.size()];

		for (int i = 0; i < npcs.size(); i++)
		{
			NPC npc = npcs.get(i);

			if (npc == null)
			{
				continue;
			}

			TickSnapshot.NpcSnapshot npcSnapshot = new TickSnapshot.NpcSnapshot();
			rememberNpc(npc);
			npcSnapshot.index = npc.getIndex();
			npcSnapshot.id = npc.getId();
			npcSnapshot.name = npc.getName();
			DefinitionName npcName = npcNameLookup(npc);
			npcSnapshot.npcName = npcName.name;
			npcSnapshot.npcNameSource = npcName.source;
			npcSnapshot.combatLevel = npc.getCombatLevel();

			WorldPoint worldLocation = npc.getWorldLocation();

			if (worldLocation != null)
			{
				npcSnapshot.worldX = worldLocation.getX();
				npcSnapshot.worldY = worldLocation.getY();
				npcSnapshot.plane = worldLocation.getPlane();
			}

			npcSnapshot.animation = npc.getAnimation();
			npcSnapshot.poseAnimation = npc.getPoseAnimation();
			npcSnapshot.orientation = npc.getOrientation();
			npcSnapshot.healthRatio = npc.getHealthRatio();
			npcSnapshot.healthScale = npc.getHealthScale();
			npcSnapshot.dead = npc.isDead();
			applyActorProjection(npcSnapshot, npc);

			snapshot.npcs[i] = npcSnapshot;
		}
	}
	private void capturePlayers(TickSnapshot snapshot)
	{
		List<Player> players = client.getPlayers();
		Player localPlayer = client.getLocalPlayer();

		if (players == null || players.isEmpty())
		{
			snapshot.players = new TickSnapshot.PlayerSnapshot[0];
			return;
		}

		snapshot.players = players.stream()
				.filter(player -> player != null)
				.filter(player -> localPlayer == null || player != localPlayer)
				.map(this::toPlayerSnapshot)
				.toArray(TickSnapshot.PlayerSnapshot[]::new);
	}

	private void captureScene(TickSnapshot snapshot)
	{
		long sceneStartNanos = System.nanoTime();
		SceneCaptureMode sceneCaptureMode = sceneCaptureMode();
		TickSnapshot.SceneCaptureSummary summary = newSceneCaptureSummary(sceneCaptureMode);
		snapshot.sceneCaptureSummary = summary;

		try
		{
			if (sceneCaptureMode == SceneCaptureMode.STATIC_SCENE_INDEX_DIAGNOSTIC)
			{
				captureSceneWithStaticIndex(snapshot, summary);
				return;
			}

			Player localPlayer = client.getLocalPlayer();

			if (localPlayer == null || localPlayer.getWorldLocation() == null)
			{
				snapshot.sceneObjects = new TickSnapshot.SceneObjectSnapshot[0];
				snapshot.groundItems = new TickSnapshot.GroundItemSnapshot[0];
				return;
			}

			WorldView worldView = client.getTopLevelWorldView();
			Scene scene = worldView == null ? client.getScene() : worldView.getScene();

			if (scene == null || scene.getTiles() == null)
			{
				snapshot.sceneObjects = new TickSnapshot.SceneObjectSnapshot[0];
				snapshot.groundItems = new TickSnapshot.GroundItemSnapshot[0];
				return;
			}

			WorldPoint localWorld = localPlayer.getWorldLocation();
			int plane = localWorld.getPlane();
			Tile[][][] tiles = scene.getTiles();

			if (plane < 0 || plane >= tiles.length || tiles[plane] == null)
			{
				snapshot.sceneObjects = new TickSnapshot.SceneObjectSnapshot[0];
				snapshot.groundItems = new TickSnapshot.GroundItemSnapshot[0];
				return;
			}

			int baseX = worldView == null ? scene.getBaseX() : worldView.getBaseX();
			int baseY = worldView == null ? scene.getBaseY() : worldView.getBaseY();
			int centerSceneX = localWorld.getX() - baseX;
			int centerSceneY = localWorld.getY() - baseY;
			Tile[][] planeTiles = tiles[plane];
			List<TickSnapshot.SceneObjectSnapshot> sceneObjects = new ArrayList<>();
			List<TickSnapshot.GroundItemSnapshot> groundItems = new ArrayList<>();
			summary.scannedPlane = plane;

			int minSceneX = sceneCaptureMode.fullCurrentPlaneScan() ? 0 : Math.max(0, centerSceneX - sceneCaptureMode.radius());
			int maxSceneX = sceneCaptureMode.fullCurrentPlaneScan() ? planeTiles.length - 1 : Math.min(planeTiles.length - 1, centerSceneX + sceneCaptureMode.radius());
			summary.scanMinSceneX = minSceneX;
			summary.scanMaxSceneX = maxSceneX;
			summary.scanWidth = Math.max(0, maxSceneX - minSceneX + 1);

			for (int sceneX = minSceneX; sceneX <= maxSceneX; sceneX++)
			{
				Tile[] column = planeTiles[sceneX];

				if (column == null)
				{
					continue;
				}

				int minSceneY = sceneCaptureMode.fullCurrentPlaneScan() ? 0 : Math.max(0, centerSceneY - sceneCaptureMode.radius());
				int maxSceneY = sceneCaptureMode.fullCurrentPlaneScan() ? column.length - 1 : Math.min(column.length - 1, centerSceneY + sceneCaptureMode.radius());
				summary.scanMinSceneY = summary.scannedTiles == 0 ? minSceneY : Math.min(summary.scanMinSceneY, minSceneY);
				summary.scanMaxSceneY = summary.scannedTiles == 0 ? maxSceneY : Math.max(summary.scanMaxSceneY, maxSceneY);

				for (int sceneY = minSceneY; sceneY <= maxSceneY; sceneY++)
				{
					Tile tile = column[sceneY];

					if (tile == null)
					{
						continue;
					}

					summary.scannedTiles++;
					boolean tileHadObjects = captureTileObjects(tile, sceneObjects, summary);
					boolean tileHadGroundItems = captureTileGroundItems(tile, groundItems, summary);

					if (tileHadObjects || tileHadGroundItems)
					{
						summary.tilesWithObjects++;
					}
				}
			}

			summary.sceneObjectsCaptured = sceneObjects.size();
			summary.groundItemsCaptured = groundItems.size();
			summary.sceneObjectCapHit = sceneObjects.size() >= sceneCaptureMode.maxSceneObjects() || summary.sceneObjectsSkippedByCap > 0;
			summary.groundItemCapHit = groundItems.size() >= MAX_GROUND_ITEMS || summary.groundItemsSkippedByCap > 0;
			summary.scanHeight = Math.max(0, summary.scanMaxSceneY - summary.scanMinSceneY + 1);
			summary.captureRatio = summary.sceneObjectsSeen == 0 ? 1.0 : (double) summary.sceneObjectsCaptured / (double) summary.sceneObjectsSeen;
			snapshot.sceneObjects = sceneObjects.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
			snapshot.groundItems = groundItems.toArray(new TickSnapshot.GroundItemSnapshot[0]);
		}
		finally
		{
			snapshot.sceneCaptureDurationMillis = elapsedMillis(sceneStartNanos);
		}
	}

	private SceneCaptureMode sceneCaptureMode()
	{
		SceneCaptureMode mode = config.sceneCaptureMode();
		return mode == null ? SceneCaptureMode.LOCAL_DEFAULT : mode;
	}

	private TickSnapshot.SceneCaptureSummary newSceneCaptureSummary(SceneCaptureMode mode)
	{
		TickSnapshot.SceneCaptureSummary summary = new TickSnapshot.SceneCaptureSummary();
		summary.sceneCaptureMode = mode.name();
		summary.fullCurrentPlaneScan = mode.fullCurrentPlaneScan();
		summary.configuredRadius = mode.radius();
		summary.configuredMaxSceneObjects = mode.maxSceneObjects();
		summary.scanRadius = mode.radius();
		summary.maxSceneObjects = mode.maxSceneObjects();
		summary.maxGroundItems = MAX_GROUND_ITEMS;
		return summary;
	}

	private void captureSceneWithStaticIndex(TickSnapshot snapshot, TickSnapshot.SceneCaptureSummary captureSummary)
	{
		long updateStartNanos = System.nanoTime();
		TickSnapshot.SceneIndexSummary indexSummary = new TickSnapshot.SceneIndexSummary();
		TickSnapshot.SceneProjectionSummary projectionSummary = new TickSnapshot.SceneProjectionSummary();
		TickSnapshot.SceneObjectDeltas deltas = new TickSnapshot.SceneObjectDeltas();
		List<TickSnapshot.SceneObjectSnapshot> newObjects = new ArrayList<>();
		List<TickSnapshot.SceneObjectSnapshot> updatedObjects = new ArrayList<>();
		List<TickSnapshot.SceneObjectSnapshot> despawnedObjects = new ArrayList<>();
		snapshot.sceneIndexSummary = indexSummary;
		snapshot.sceneProjectionSummary = projectionSummary;
		snapshot.sceneObjectDeltas = deltas;
		snapshot.sceneObjects = new TickSnapshot.SceneObjectSnapshot[0];
		snapshot.groundItems = new TickSnapshot.GroundItemSnapshot[0];

		SceneContext context = sceneContext();

		if (context == null)
		{
			indexSummary.sceneCaptureMode = SceneCaptureMode.STATIC_SCENE_INDEX_DIAGNOSTIC.name();
			indexSummary.indexEnabled = true;
			indexSummary.resyncReason = "scene unavailable";
			return;
		}

		int rescanInterval = Math.max(0, config.sceneIndexRescanIntervalTicks());
		boolean rescanDue = rescanInterval > 0 && lastSceneIndexResyncTick >= 0 && tickId - lastSceneIndexResyncTick >= rescanInterval;
		boolean planeChanged = sceneIndexPlane != -1 && sceneIndexPlane != context.plane;
		boolean shouldFullResync = sceneObjectIndex.isEmpty() || sceneIndexNeedsFullResync || rescanDue || planeChanged;

		if (planeChanged)
		{
			clearSceneIndex("planeChanged");
		}

		if (rescanDue)
		{
			sceneIndexResyncReason = "periodicResync";
		}

		if (shouldFullResync)
		{
			long buildStartNanos = System.nanoTime();
			fullResyncSceneIndex(context, captureSummary, indexSummary, newObjects, updatedObjects);
			indexSummary.sceneIndexBuildDurationMillis = elapsedMillis(buildStartNanos);
			indexSummary.fullResyncThisTick = true;
			indexSummary.resyncReason = sceneIndexResyncReason;
			sceneIndexNeedsFullResync = false;
			sceneIndexResyncReason = null;
			sceneIndexPlane = context.plane;
			lastSceneIndexResyncTick = tickId;
		}

		collectDirtySceneDeltas(newObjects, updatedObjects, despawnedObjects);
		indexSummary.sceneIndexUpdateDurationMillis = elapsedMillis(updateStartNanos);
		indexSummary.sceneCaptureMode = SceneCaptureMode.STATIC_SCENE_INDEX_DIAGNOSTIC.name();
		indexSummary.indexEnabled = true;
		indexSummary.indexObjectCount = sceneObjectIndex.size();
		indexSummary.presentObjectCount = presentSceneIndexObjectCount();
		indexSummary.newlyIndexedCount = newObjects.size();
		indexSummary.updatedCount = updatedObjects.size();
		indexSummary.despawnedCount = despawnedObjects.size();
		indexSummary.maxSceneIndexObjects = maxSceneIndexObjects();
		indexSummary.indexCapHit = sceneObjectIndex.size() >= maxSceneIndexObjects();
		deltas.newObjects = newObjects.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
		deltas.updatedObjects = updatedObjects.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
		deltas.despawnedObjects = despawnedObjects.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
		refreshSceneProjections(snapshot, context, projectionSummary);
		captureSummary.sceneObjectsSeen = indexSummary.presentObjectCount;
		captureSummary.sceneObjectsCaptured = indexSummary.presentObjectCount;
		captureSummary.captureRatio = 1.0;
	}

	private SceneContext sceneContext()
	{
		Player localPlayer = client.getLocalPlayer();

		if (localPlayer == null || localPlayer.getWorldLocation() == null)
		{
			return null;
		}

		WorldView worldView = client.getTopLevelWorldView();
		Scene scene = worldView == null ? client.getScene() : worldView.getScene();

		if (scene == null || scene.getTiles() == null)
		{
			return null;
		}

		WorldPoint localWorld = localPlayer.getWorldLocation();
		int plane = localWorld.getPlane();
		Tile[][][] tiles = scene.getTiles();

		if (plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			return null;
		}

		SceneContext context = new SceneContext();
		context.worldView = worldView;
		context.scene = scene;
		context.localWorld = localWorld;
		context.plane = plane;
		context.planeTiles = tiles[plane];
		context.baseX = worldView == null ? scene.getBaseX() : worldView.getBaseX();
		context.baseY = worldView == null ? scene.getBaseY() : worldView.getBaseY();
		context.centerSceneX = localWorld.getX() - context.baseX;
		context.centerSceneY = localWorld.getY() - context.baseY;
		return context;
	}

	private void fullResyncSceneIndex(SceneContext context, TickSnapshot.SceneCaptureSummary captureSummary, TickSnapshot.SceneIndexSummary indexSummary, List<TickSnapshot.SceneObjectSnapshot> newObjects, List<TickSnapshot.SceneObjectSnapshot> updatedObjects)
	{
		int maxIndexObjects = maxSceneIndexObjects();
		Set<String> seenKeys = new HashSet<>();
		int minSceneX = 0;
		int maxSceneX = context.planeTiles.length - 1;
		captureSummary.scannedPlane = context.plane;
		captureSummary.scanMinSceneX = minSceneX;
		captureSummary.scanMaxSceneX = maxSceneX;
		captureSummary.scanWidth = Math.max(0, maxSceneX - minSceneX + 1);

		for (int sceneX = minSceneX; sceneX <= maxSceneX; sceneX++)
		{
			Tile[] column = context.planeTiles[sceneX];

			if (column == null)
			{
				continue;
			}

			int minSceneY = 0;
			int maxSceneY = column.length - 1;
			captureSummary.scanMinSceneY = captureSummary.scannedTiles == 0 ? minSceneY : Math.min(captureSummary.scanMinSceneY, minSceneY);
			captureSummary.scanMaxSceneY = captureSummary.scannedTiles == 0 ? maxSceneY : Math.max(captureSummary.scanMaxSceneY, maxSceneY);

			for (int sceneY = minSceneY; sceneY <= maxSceneY; sceneY++)
			{
				Tile tile = column[sceneY];

				if (tile == null)
				{
					continue;
				}

				captureSummary.scannedTiles++;
				if (indexTileObjects(tile, captureSummary, seenKeys, maxIndexObjects, newObjects, updatedObjects))
				{
					captureSummary.tilesWithObjects++;
				}
			}
		}

		captureSummary.scanHeight = Math.max(0, captureSummary.scanMaxSceneY - captureSummary.scanMinSceneY + 1);
		for (SceneIndexEntry entry : sceneObjectIndex.values())
		{
			if (entry.present && entry.plane == context.plane && !seenKeys.contains(entry.objectKey))
			{
				entry.present = false;
				entry.despawnedTick = tickId;
				entry.lastUpdatedTick = tickId;
				entry.source = "refreshedScan";
				dirtySceneObjectKeys.add(entry.objectKey);
			}
		}
	}

	private boolean indexTileObjects(Tile tile, TickSnapshot.SceneCaptureSummary summary, Set<String> seenKeys, int maxIndexObjects, List<TickSnapshot.SceneObjectSnapshot> newObjects, List<TickSnapshot.SceneObjectSnapshot> updatedObjects)
	{
		boolean hadObjects = false;
		WallObject wallObject = tile.getWallObject();
		hadObjects |= indexSceneObject("WALL_OBJECT", wallObject, wallObject == null ? -1 : wallObject.getOrientationA(), "initialFullPlaneScan", summary, seenKeys, maxIndexObjects, newObjects, updatedObjects, false);
		hadObjects |= indexSceneObject("GROUND_OBJECT", tile.getGroundObject(), -1, "initialFullPlaneScan", summary, seenKeys, maxIndexObjects, newObjects, updatedObjects, false);
		hadObjects |= indexSceneObject("DECORATIVE_OBJECT", tile.getDecorativeObject(), -1, "initialFullPlaneScan", summary, seenKeys, maxIndexObjects, newObjects, updatedObjects, false);
		GameObject[] gameObjects = tile.getGameObjects();

		if (gameObjects == null)
		{
			return hadObjects;
		}

		for (GameObject gameObject : gameObjects)
		{
			hadObjects |= indexSceneObject("GAME_OBJECT", gameObject, gameObject == null ? -1 : gameObject.getOrientation(), "initialFullPlaneScan", summary, seenKeys, maxIndexObjects, newObjects, updatedObjects, true);
		}

		return hadObjects;
	}

	private boolean indexSceneObject(String kind, TileObject object, int orientation, String source, TickSnapshot.SceneCaptureSummary summary, Set<String> seenKeys, int maxIndexObjects, List<TickSnapshot.SceneObjectSnapshot> newObjects, List<TickSnapshot.SceneObjectSnapshot> updatedObjects, boolean countNull)
	{
		if (object == null)
		{
			if (countNull)
			{
				summary.nullObjectsSkipped++;
			}
			return false;
		}

		incrementSceneObjectSeen(summary, kind);
		summary.sceneObjectsSeen++;
		String key = sceneObjectKey(kind, object, orientation);
		seenKeys.add(key);
		SceneIndexEntry existing = sceneObjectIndex.get(key);

		if (existing == null && sceneObjectIndex.size() >= maxIndexObjects)
		{
			incrementSceneObjectSkippedByCap(summary, kind);
			summary.sceneObjectsSkippedByCap++;
			summary.sceneObjectCapHit = true;
			return true;
		}

		TickSnapshot.SceneObjectSnapshot snapshot = sceneObjectSnapshot(kind, object, orientation, source, false);

		if (existing == null)
		{
			SceneIndexEntry entry = SceneIndexEntry.from(snapshot, tickId);
			sceneObjectIndex.put(key, entry);
			newObjects.add(snapshotFromIndex(entry, false));
		}
		else
		{
			boolean changed = existing.updateFrom(snapshot, tickId, source);
			if (changed)
			{
				updatedObjects.add(snapshotFromIndex(existing, false));
			}
		}

		incrementSceneObjectCaptured(summary, kind);
		return true;
	}

	private void indexSceneObjectFromEvent(String kind, TileObject object, int orientation)
	{
		if (object == null)
		{
			return;
		}

		String key = sceneObjectKey(kind, object, orientation);
		if (!sceneObjectIndex.containsKey(key) && sceneObjectIndex.size() >= maxSceneIndexObjects())
		{
			return;
		}

		TickSnapshot.SceneObjectSnapshot snapshot = sceneObjectSnapshot(kind, object, orientation, "spawnedEvent", false);
		SceneIndexEntry entry = sceneObjectIndex.get(key);

		if (entry == null)
		{
			sceneObjectIndex.put(key, SceneIndexEntry.from(snapshot, tickId));
		}
		else
		{
			entry.updateFrom(snapshot, tickId, "spawnedEvent");
		}

		dirtySceneObjectKeys.add(key);
	}

	private void despawnSceneObjectFromEvent(String kind, TileObject object, int orientation)
	{
		if (object == null)
		{
			return;
		}

		String key = sceneObjectKey(kind, object, orientation);
		SceneIndexEntry entry = sceneObjectIndex.get(key);

		if (entry == null)
		{
			TickSnapshot.SceneObjectSnapshot snapshot = sceneObjectSnapshot(kind, object, orientation, "despawnedEvent", false);
			entry = SceneIndexEntry.from(snapshot, tickId);
			sceneObjectIndex.put(key, entry);
		}

		entry.present = false;
		entry.despawnedTick = tickId;
		entry.lastUpdatedTick = tickId;
		entry.source = "despawnedEvent";
		dirtySceneObjectKeys.add(key);

		if (!config.keepDespawnedSceneObjectsInIndex())
		{
			sceneObjectIndex.remove(key);
			sceneProjectionCache.remove(key);
		}
	}

	private void collectDirtySceneDeltas(List<TickSnapshot.SceneObjectSnapshot> newObjects, List<TickSnapshot.SceneObjectSnapshot> updatedObjects, List<TickSnapshot.SceneObjectSnapshot> despawnedObjects)
	{
		for (String key : new ArrayList<>(dirtySceneObjectKeys))
		{
			SceneIndexEntry entry = sceneObjectIndex.get(key);

			if (entry == null)
			{
				continue;
			}

			TickSnapshot.SceneObjectSnapshot snapshot = snapshotFromIndex(entry, false);

			if (!entry.present)
			{
				despawnedObjects.add(snapshot);
			}
			else if (entry.firstSeenTick == tickId)
			{
				newObjects.add(snapshot);
			}
			else
			{
				updatedObjects.add(snapshot);
			}
		}

		dirtySceneObjectKeys.clear();
	}

	private void refreshSceneProjections(TickSnapshot snapshot, SceneContext context, TickSnapshot.SceneProjectionSummary projectionSummary)
	{
		long projectionStartNanos = System.nanoTime();
		SceneProjectionRefreshMode refreshMode = sceneProjectionRefreshMode();
		String stateHash = projectionStateHash(context);
		boolean stateChanged = !stateHash.equals(lastSceneProjectionStateHash);
		List<TickSnapshot.SceneObjectSnapshot> refs = new ArrayList<>();
		projectionSummary.projectionStateHash = stateHash;
		projectionSummary.projectionStateChanged = stateChanged;
		projectionSummary.projectionRefreshMode = refreshMode.name();

		for (SceneIndexEntry entry : sceneObjectIndex.values())
		{
			if (!entry.present)
			{
				continue;
			}

			TickSnapshot.SceneObjectSnapshot cached = sceneProjectionCache.get(entry.objectKey);
			boolean shouldUpdate = stateChanged || cached == null || shouldRefreshProjection(entry, cached, context, refreshMode);

			if (shouldUpdate)
			{
				projectionSummary.projectionCandidatesConsidered++;
				TickSnapshot.SceneObjectSnapshot projected = snapshotFromIndex(entry, false);
				TileObject object = findTileObjectForEntry(context, entry);

				if (object != null)
				{
					applySceneObjectProjection(projected, object);
				}
				else
				{
					projected.geometryWarning = "object not found for projection refresh";
				}

				projected.projectionVersion = tickId;
				sceneProjectionCache.put(entry.objectKey, projected);
				cached = projected;
				projectionSummary.projectionObjectsUpdated++;
			}
			else
			{
				projectionSummary.projectionObjectsReused++;
			}

			if (cached != null && (cached.onScreen || cached.geometryAvailable))
			{
				refs.add(cached);
			}
		}

		lastSceneProjectionStateHash = stateHash;
		snapshot.visibleSceneObjectRefs = refs.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
		projectionSummary.visibleObjectCount = refs.size();

		for (TickSnapshot.SceneObjectSnapshot ref : refs)
		{
			if (ref.onScreen)
			{
				projectionSummary.onScreenObjectCount++;
			}

			if (ref.geometryAvailable)
			{
				projectionSummary.geometryAvailableCount++;
			}
			else
			{
				projectionSummary.missingGeometryCount++;
			}
		}

		projectionSummary.projectionDurationMillis = elapsedMillis(projectionStartNanos);
	}

	private boolean shouldRefreshProjection(SceneIndexEntry entry, TickSnapshot.SceneObjectSnapshot cached, SceneContext context, SceneProjectionRefreshMode refreshMode)
	{
		if (refreshMode == SceneProjectionRefreshMode.ALL_PRESENT_OBJECTS)
		{
			return true;
		}

		if (refreshMode == SceneProjectionRefreshMode.VISIBLE_ONLY)
		{
			return cached.onScreen;
		}

		if (cached.onScreen)
		{
			return true;
		}

		int radius = SceneCaptureMode.STATIC_SCENE_INDEX_DIAGNOSTIC.radius();
		return Math.abs(entry.sceneX - context.centerSceneX) <= radius && Math.abs(entry.sceneY - context.centerSceneY) <= radius;
	}

	private TileObject findTileObjectForEntry(SceneContext context, SceneIndexEntry entry)
	{
		if (entry.sceneX < 0 || entry.sceneX >= context.planeTiles.length)
		{
			return null;
		}

		Tile[] column = context.planeTiles[entry.sceneX];

		if (column == null || entry.sceneY < 0 || entry.sceneY >= column.length)
		{
			return null;
		}

		Tile tile = column[entry.sceneY];

		if (tile == null)
		{
			return null;
		}

		if ("WALL_OBJECT".equals(entry.kind))
		{
			return tile.getWallObject();
		}

		if ("GROUND_OBJECT".equals(entry.kind))
		{
			return tile.getGroundObject();
		}

		if ("DECORATIVE_OBJECT".equals(entry.kind))
		{
			return tile.getDecorativeObject();
		}

		GameObject[] gameObjects = tile.getGameObjects();

		if (gameObjects == null)
		{
			return null;
		}

		for (GameObject gameObject : gameObjects)
		{
			if (gameObject != null && sceneObjectKey("GAME_OBJECT", gameObject, gameObject.getOrientation()).equals(entry.objectKey))
			{
				return gameObject;
			}
		}

		return null;
	}

	private String projectionStateHash(SceneContext context)
	{
		Canvas canvas = client.getCanvas();
		String text = context.plane
				+ ":" + client.getCameraX()
				+ ":" + client.getCameraY()
				+ ":" + client.getCameraZ()
				+ ":" + client.getCameraPitch()
				+ ":" + client.getCameraYaw()
				+ ":" + client.getViewportXOffset()
				+ ":" + client.getViewportYOffset()
				+ ":" + client.getViewportWidth()
				+ ":" + client.getViewportHeight()
				+ ":" + (canvas == null ? -1 : canvas.getWidth())
				+ ":" + (canvas == null ? -1 : canvas.getHeight())
				+ ":" + context.localWorld.getX()
				+ ":" + context.localWorld.getY();
		return hashName(text);
	}

	private int maxSceneIndexObjects()
	{
		return Math.max(1, config.maxSceneIndexObjects());
	}

	private SceneProjectionRefreshMode sceneProjectionRefreshMode()
	{
		SceneProjectionRefreshMode mode = config.sceneProjectionRefreshMode();
		return mode == null ? SceneProjectionRefreshMode.VISIBLE_AND_NEARBY : mode;
	}

	private int presentSceneIndexObjectCount()
	{
		int count = 0;

		for (SceneIndexEntry entry : sceneObjectIndex.values())
		{
			if (entry.present)
			{
				count++;
			}
		}

		return count;
	}

	private TickSnapshot.SceneObjectSnapshot snapshotFromIndex(SceneIndexEntry entry, boolean includeProjection)
	{
		TickSnapshot.SceneObjectSnapshot snapshot = new TickSnapshot.SceneObjectSnapshot();
		snapshot.objectKey = entry.objectKey;
		snapshot.kind = entry.kind;
		snapshot.id = entry.id;
		snapshot.hash = entry.hash;
		snapshot.objectName = entry.objectName;
		snapshot.objectNameSource = entry.objectNameSource;
		snapshot.actions = entry.actions;
		snapshot.worldX = entry.worldX;
		snapshot.worldY = entry.worldY;
		snapshot.plane = entry.plane;
		snapshot.orientation = entry.orientation;
		snapshot.sceneX = entry.sceneX;
		snapshot.sceneY = entry.sceneY;
		snapshot.localX = entry.localX;
		snapshot.localY = entry.localY;
		snapshot.firstSeenTick = entry.firstSeenTick;
		snapshot.lastSeenTick = entry.lastSeenTick;
		snapshot.lastUpdatedTick = entry.lastUpdatedTick;
		snapshot.present = entry.present;
		snapshot.despawnedTick = entry.despawnedTick;
		snapshot.source = entry.source;
		return snapshot;
	}

	private void clearSceneIndex(String reason)
	{
		sceneObjectIndex.clear();
		dirtySceneObjectKeys.clear();
		sceneProjectionCache.clear();
		sceneIndexNeedsFullResync = true;
		sceneIndexResyncReason = reason;
		sceneIndexPlane = -1;
		lastSceneIndexResyncTick = -1;
		lastSceneProjectionStateHash = null;
	}




	private boolean captureTileObjects(Tile tile, List<TickSnapshot.SceneObjectSnapshot> sceneObjects, TickSnapshot.SceneCaptureSummary summary)
	{
		boolean hadObjects = false;
		WallObject wallObject = tile.getWallObject();

		hadObjects |= addSceneObject(sceneObjects, summary, "WALL_OBJECT", wallObject, wallObject == null ? -1 : wallObject.getOrientationA(), false);
		hadObjects |= addSceneObject(sceneObjects, summary, "GROUND_OBJECT", tile.getGroundObject(), -1, false);
		hadObjects |= addSceneObject(sceneObjects, summary, "DECORATIVE_OBJECT", tile.getDecorativeObject(), -1, false);

		GameObject[] gameObjects = tile.getGameObjects();

		if (gameObjects == null)
		{
			return hadObjects;
		}

		for (GameObject gameObject : gameObjects)
		{
			hadObjects |= addSceneObject(sceneObjects, summary, "GAME_OBJECT", gameObject, gameObject == null ? -1 : gameObject.getOrientation(), true);
		}

		return hadObjects;
	}

	private boolean addSceneObject(List<TickSnapshot.SceneObjectSnapshot> sceneObjects, TickSnapshot.SceneCaptureSummary summary, String kind, TileObject object, int orientation, boolean countNull)
	{
		if (object == null)
		{
			if (countNull)
			{
				summary.nullObjectsSkipped++;
			}

			return false;
		}

		incrementSceneObjectSeen(summary, kind);
		summary.sceneObjectsSeen++;

		if (sceneObjects.size() >= summary.configuredMaxSceneObjects)
		{
			incrementSceneObjectSkippedByCap(summary, kind);
			summary.sceneObjectsSkippedByCap++;
			return true;
		}

		TickSnapshot.SceneObjectSnapshot snapshot = sceneObjectSnapshot(kind, object, orientation, "fullSnapshot", true);
		sceneObjects.add(snapshot);
		incrementSceneObjectCaptured(summary, kind);
		return true;
	}

	private TickSnapshot.SceneObjectSnapshot sceneObjectSnapshot(String kind, TileObject object, int orientation, String source, boolean includeProjection)
	{
		WorldPoint worldLocation = object.getWorldLocation();
		Point sceneLocation = worldLocationToSceneLocation(object);
		TickSnapshot.SceneObjectSnapshot snapshot = new TickSnapshot.SceneObjectSnapshot();
		snapshot.kind = kind;
		snapshot.id = object.getId();
		snapshot.hash = objectHash(object);
		snapshot.objectKey = sceneObjectKey(kind, object, orientation);
		DefinitionName objectName = objectNameLookup(snapshot.id);
		snapshot.objectName = objectName.name;
		snapshot.objectNameSource = objectName.source;
		snapshot.actions = objectActions(snapshot.id);
		rememberObject(snapshot.id);
		snapshot.orientation = orientation;
		snapshot.sceneX = sceneLocation == null ? -1 : sceneLocation.getX();
		snapshot.sceneY = sceneLocation == null ? -1 : sceneLocation.getY();
		snapshot.present = true;
		snapshot.source = source;

		if (worldLocation != null)
		{
			snapshot.worldX = worldLocation.getX();
			snapshot.worldY = worldLocation.getY();
			snapshot.plane = worldLocation.getPlane();
		}
		else
		{
			snapshot.plane = object.getPlane();
		}

		if (includeProjection)
		{
			applySceneObjectProjection(snapshot, object);
		}

		return snapshot;
	}

	private void incrementSceneObjectSeen(TickSnapshot.SceneCaptureSummary summary, String kind)
	{
		if ("GAME_OBJECT".equals(kind))
		{
			summary.gameObjectsSeen++;
		}
		else if ("WALL_OBJECT".equals(kind))
		{
			summary.wallObjectsSeen++;
		}
		else if ("DECORATIVE_OBJECT".equals(kind))
		{
			summary.decorativeObjectsSeen++;
		}
		else if ("GROUND_OBJECT".equals(kind))
		{
			summary.groundObjectsSeen++;
		}
	}

	private void incrementSceneObjectCaptured(TickSnapshot.SceneCaptureSummary summary, String kind)
	{
		if ("GAME_OBJECT".equals(kind))
		{
			summary.gameObjectsCaptured++;
		}
		else if ("WALL_OBJECT".equals(kind))
		{
			summary.wallObjectsCaptured++;
		}
		else if ("DECORATIVE_OBJECT".equals(kind))
		{
			summary.decorativeObjectsCaptured++;
		}
		else if ("GROUND_OBJECT".equals(kind))
		{
			summary.groundObjectsCaptured++;
		}
	}

	private void incrementSceneObjectSkippedByCap(TickSnapshot.SceneCaptureSummary summary, String kind)
	{
		if ("GAME_OBJECT".equals(kind))
		{
			summary.gameObjectsSkippedByCap++;
		}
		else if ("WALL_OBJECT".equals(kind))
		{
			summary.wallObjectsSkippedByCap++;
		}
		else if ("DECORATIVE_OBJECT".equals(kind))
		{
			summary.decorativeObjectsSkippedByCap++;
		}
		else if ("GROUND_OBJECT".equals(kind))
		{
			summary.groundObjectsSkippedByCap++;
		}
	}

	private Point worldLocationToSceneLocation(TileObject object)
	{
		WorldPoint worldLocation = object.getWorldLocation();
		WorldView worldView = object.getWorldView();

		if (worldLocation == null || worldView == null)
		{
			return null;
		}

		return new Point(worldLocation.getX() - worldView.getBaseX(), worldLocation.getY() - worldView.getBaseY());
	}

	private Long objectHash(TileObject object)
	{
		try
		{
			return object == null ? null : object.getHash();
		}
		catch (Exception e)
		{
			return null;
		}
	}

	private String sceneObjectKey(String kind, TileObject object, int orientation)
	{
		if (object == null)
		{
			return kind + ":missing";
		}

		WorldPoint worldLocation = object.getWorldLocation();
		Point sceneLocation = worldLocationToSceneLocation(object);
		Long hash = objectHash(object);
		int plane = worldLocation == null ? object.getPlane() : worldLocation.getPlane();
		int worldX = worldLocation == null ? -1 : worldLocation.getX();
		int worldY = worldLocation == null ? -1 : worldLocation.getY();
		int sceneX = sceneLocation == null ? -1 : sceneLocation.getX();
		int sceneY = sceneLocation == null ? -1 : sceneLocation.getY();
		return plane + ":" + worldX + ":" + worldY + ":" + sceneX + ":" + sceneY + ":" + kind + ":" + object.getId() + ":" + (hash == null ? "nohash" : hash) + ":" + orientation;
	}

	private String[] objectActions(int objectId)
	{
		try
		{
			ObjectComposition composition = client.getObjectDefinition(objectId);

			if (composition == null)
			{
				return null;
			}

			return composition.getActions();
		}
		catch (Exception e)
		{
			return null;
		}
	}

	private void applySceneObjectProjection(TickSnapshot.SceneObjectSnapshot snapshot, TileObject object)
	{
		SceneObjectProjection projection = captureSceneObjectProjection(object);
		snapshot.localX = projection.localX;
		snapshot.localY = projection.localY;
		snapshot.canvasLocation = projection.canvasLocation;
		snapshot.canvasTilePolygon = projection.canvasTilePolygon;
		snapshot.clickboxBounds = projection.clickboxBounds;
		snapshot.clickboxPolygon = projection.clickboxPolygon;
		snapshot.convexHullBounds = projection.convexHullBounds;
		snapshot.convexHullPolygon = projection.convexHullPolygon;
		snapshot.onScreen = projection.onScreen;
		snapshot.geometryAvailable = projection.geometryAvailable;
		snapshot.geometryWarning = projection.geometryWarning;
	}

	private SceneObjectProjection captureSceneObjectProjection(TileObject object)
	{
		SceneObjectProjection projection = new SceneObjectProjection();
		List<String> warnings = new ArrayList<>();

		if (object == null)
		{
			projection.geometryWarning = "object missing";
			return projection;
		}

		try
		{
			LocalPoint localLocation = object.getLocalLocation();

			if (localLocation != null)
			{
				projection.localX = localLocation.getX();
				projection.localY = localLocation.getY();
			}
			else
			{
				warnings.add("local location unavailable");
			}
		}
		catch (Exception e)
		{
			warnings.add("local location failed: " + exceptionSummary(e));
		}

		try
		{
			projection.canvasLocation = canvasPointSnapshot(object.getCanvasLocation());
		}
		catch (Exception e)
		{
			warnings.add("canvas location failed: " + exceptionSummary(e));
		}

		try
		{
			projection.canvasTilePolygon = polygonSnapshot(object.getCanvasTilePoly());
		}
		catch (Exception e)
		{
			warnings.add("canvas tile polygon failed: " + exceptionSummary(e));
		}

		try
		{
			Shape clickbox = object.getClickbox();
			projection.clickboxBounds = boundsSnapshot(clickbox);
			projection.clickboxPolygon = polygonSnapshot(clickbox);
		}
		catch (Exception e)
		{
			warnings.add("clickbox failed: " + exceptionSummary(e));
		}

		try
		{
			Shape convexHull = tileObjectConvexHull(object);
			projection.convexHullBounds = boundsSnapshot(convexHull);
			projection.convexHullPolygon = polygonSnapshot(convexHull);
		}
		catch (Exception e)
		{
			warnings.add("convex hull failed: " + exceptionSummary(e));
		}

		projection.geometryAvailable = projection.canvasLocation != null
				|| projection.canvasTilePolygon != null
				|| projection.clickboxBounds != null
				|| projection.clickboxPolygon != null
				|| projection.convexHullBounds != null
				|| projection.convexHullPolygon != null;
		projection.onScreen = projection.geometryAvailable && geometryIntersectsVisibleArea(
				projection.canvasLocation,
				combinePolygons(projection.canvasTilePolygon, projection.clickboxPolygon, projection.convexHullPolygon),
				projection.clickboxBounds,
				projection.convexHullBounds);

		if (!projection.geometryAvailable && warnings.isEmpty())
		{
			warnings.add("projection returned no canvas geometry");
		}

		if (!warnings.isEmpty())
		{
			projection.geometryWarning = String.join("; ", warnings);
		}

		return projection;
	}

	private Shape tileObjectConvexHull(TileObject object)
	{
		if (object instanceof GameObject)
		{
			return ((GameObject) object).getConvexHull();
		}

		if (object instanceof WallObject)
		{
			return ((WallObject) object).getConvexHull();
		}

		if (object instanceof DecorativeObject)
		{
			return ((DecorativeObject) object).getConvexHull();
		}

		if (object instanceof GroundObject)
		{
			return ((GroundObject) object).getConvexHull();
		}

		return null;
	}

	private boolean captureTileGroundItems(Tile tile, List<TickSnapshot.GroundItemSnapshot> groundItems, TickSnapshot.SceneCaptureSummary summary)
	{
		List<TileItem> tileItems = tile.getGroundItems();

		if (tileItems == null || tileItems.isEmpty())
		{
			return false;
		}

		WorldPoint worldLocation = tile.getWorldLocation();
		Point sceneLocation = tile.getSceneLocation();
		boolean hadGroundItems = false;

		for (TileItem item : tileItems)
		{
			if (item == null)
			{
				summary.nullGroundItemsSkipped++;
				continue;
			}

			hadGroundItems = true;
			summary.groundItemsSeen++;

			if (groundItems.size() >= MAX_GROUND_ITEMS)
			{
				summary.groundItemsSkippedByCap++;
				continue;
			}

			TickSnapshot.GroundItemSnapshot snapshot = new TickSnapshot.GroundItemSnapshot();
			snapshot.id = item.getId();
			DefinitionName itemName = itemNameLookup(snapshot.id);
			snapshot.itemName = itemName.name;
			snapshot.itemNameSource = itemName.source;
			snapshot.quantity = item.getQuantity();
			rememberItem(snapshot.id);
			snapshot.sceneX = sceneLocation == null ? -1 : sceneLocation.getX();
			snapshot.sceneY = sceneLocation == null ? -1 : sceneLocation.getY();

			if (worldLocation != null)
			{
				snapshot.worldX = worldLocation.getX();
				snapshot.worldY = worldLocation.getY();
				snapshot.plane = worldLocation.getPlane();
			}
			else
			{
				snapshot.plane = tile.getPlane();
			}

			applyGroundItemProjection(snapshot, tile);
			groundItems.add(snapshot);
		}

		return hadGroundItems;
	}

	private void applyGroundItemProjection(TickSnapshot.GroundItemSnapshot snapshot, Tile tile)
	{
		GroundItemProjection projection = captureGroundItemProjection(tile);
		snapshot.localX = projection.localX;
		snapshot.localY = projection.localY;
		snapshot.canvasTilePolygon = projection.canvasTilePolygon;
		snapshot.canvasCenter = projection.canvasCenter;
		snapshot.onScreen = projection.onScreen;
		snapshot.geometryAvailable = projection.geometryAvailable;
		snapshot.geometryWarning = projection.geometryWarning;
	}

	private GroundItemProjection captureGroundItemProjection(Tile tile)
	{
		GroundItemProjection projection = new GroundItemProjection();
		List<String> warnings = new ArrayList<>();
		LocalPoint localLocation = null;

		if (tile == null)
		{
			projection.geometryWarning = "tile missing";
			return projection;
		}

		try
		{
			localLocation = tile.getLocalLocation();

			if (localLocation != null)
			{
				projection.localX = localLocation.getX();
				projection.localY = localLocation.getY();
			}
			else
			{
				warnings.add("local location unavailable");
			}
		}
		catch (Exception e)
		{
			warnings.add("local location failed: " + exceptionSummary(e));
		}

		if (localLocation != null)
		{
			try
			{
				projection.canvasTilePolygon = polygonSnapshot(Perspective.getCanvasTilePoly(client, localLocation));
				projection.canvasCenter = polygonCenter(projection.canvasTilePolygon);
			}
			catch (Exception e)
			{
				warnings.add("canvas tile polygon failed: " + exceptionSummary(e));
			}
		}

		projection.geometryAvailable = projection.canvasTilePolygon != null || projection.canvasCenter != null;
		projection.onScreen = projection.geometryAvailable && geometryIntersectsVisibleArea(
				projection.canvasCenter,
				projection.canvasTilePolygon);

		if (!projection.geometryAvailable && warnings.isEmpty())
		{
			warnings.add("projection returned no canvas geometry");
		}

		if (!warnings.isEmpty())
		{
			projection.geometryWarning = String.join("; ", warnings);
		}

		return projection;
	}

	private TickSnapshot.PlayerSnapshot toPlayerSnapshot(Player player)
	{
		TickSnapshot.PlayerSnapshot playerSnapshot = new TickSnapshot.PlayerSnapshot();

		playerSnapshot.index = player.getId();
		playerSnapshot.nameHash = hashName(player.getName());
		playerSnapshot.combatLevel = player.getCombatLevel();

		WorldPoint worldLocation = player.getWorldLocation();

		if (worldLocation != null)
		{
			playerSnapshot.worldX = worldLocation.getX();
			playerSnapshot.worldY = worldLocation.getY();
			playerSnapshot.plane = worldLocation.getPlane();
		}

		playerSnapshot.animation = player.getAnimation();
		playerSnapshot.poseAnimation = player.getPoseAnimation();
		playerSnapshot.orientation = player.getOrientation();
		playerSnapshot.healthRatio = player.getHealthRatio();
		playerSnapshot.healthScale = player.getHealthScale();
		applyActorProjection(playerSnapshot, player);

		return playerSnapshot;
	}

	private void applyActorProjection(TickSnapshot.NpcSnapshot snapshot, Actor actor)
	{
		ActorProjection projection = captureActorProjection(actor);
		snapshot.localX = projection.localX;
		snapshot.localY = projection.localY;
		snapshot.canvasPoint = projection.canvasPoint;
		snapshot.clickboxBounds = projection.clickboxBounds;
		snapshot.convexHullBounds = projection.convexHullBounds;
		snapshot.onScreen = projection.onScreen;
		snapshot.geometryAvailable = projection.geometryAvailable;
		snapshot.geometryWarning = projection.geometryWarning;
	}

	private void applyActorProjection(TickSnapshot.PlayerSnapshot snapshot, Actor actor)
	{
		ActorProjection projection = captureActorProjection(actor);
		snapshot.localX = projection.localX;
		snapshot.localY = projection.localY;
		snapshot.canvasPoint = projection.canvasPoint;
		snapshot.clickboxBounds = projection.clickboxBounds;
		snapshot.convexHullBounds = projection.convexHullBounds;
		snapshot.onScreen = projection.onScreen;
		snapshot.geometryAvailable = projection.geometryAvailable;
		snapshot.geometryWarning = projection.geometryWarning;
	}

	private ActorProjection captureActorProjection(Actor actor)
	{
		ActorProjection projection = new ActorProjection();
		List<String> warnings = new ArrayList<>();

		if (actor == null)
		{
			projection.geometryWarning = "actor missing";
			return projection;
		}

		LocalPoint localLocation = null;

		try
		{
			localLocation = actor.getLocalLocation();

			if (localLocation != null)
			{
				projection.localX = localLocation.getX();
				projection.localY = localLocation.getY();
			}
			else
			{
				warnings.add("local location unavailable");
			}
		}
		catch (Exception e)
		{
			warnings.add("local location failed: " + exceptionSummary(e));
		}

		if (localLocation != null)
		{
			try
			{
				Point canvasPoint = Perspective.localToCanvas(client, localLocation, actorPlane(actor));
				projection.canvasPoint = canvasPointSnapshot(canvasPoint);
			}
			catch (Exception e)
			{
				warnings.add("canvas projection failed: " + exceptionSummary(e));
			}
		}

		try
		{
			projection.convexHullBounds = boundsSnapshot(actor.getConvexHull());
		}
		catch (Exception e)
		{
			warnings.add("convex hull failed: " + exceptionSummary(e));
		}

		projection.geometryAvailable = projection.canvasPoint != null
				|| projection.clickboxBounds != null
				|| projection.convexHullBounds != null;
		projection.onScreen = projection.geometryAvailable && geometryIntersectsVisibleArea(
				projection.canvasPoint,
				projection.clickboxBounds,
				projection.convexHullBounds);

		if (!projection.geometryAvailable && warnings.isEmpty())
		{
			warnings.add("projection returned no canvas geometry");
		}

		if (!warnings.isEmpty())
		{
			projection.geometryWarning = String.join("; ", warnings);
		}

		return projection;
	}

	private int actorPlane(Actor actor)
	{
		WorldPoint worldLocation = actor.getWorldLocation();

		if (worldLocation != null)
		{
			return worldLocation.getPlane();
		}

		return client.getPlane();
	}

	private TickSnapshot.CanvasPoint canvasPointSnapshot(Point point)
	{
		if (point == null)
		{
			return null;
		}

		TickSnapshot.CanvasPoint snapshot = new TickSnapshot.CanvasPoint();
		snapshot.x = point.getX();
		snapshot.y = point.getY();
		return snapshot;
	}

	private TickSnapshot.Bounds boundsSnapshot(Shape shape)
	{
		if (shape == null)
		{
			return null;
		}

		Rectangle bounds = shape.getBounds();

		if (bounds == null || bounds.width <= 0 || bounds.height <= 0)
		{
			return null;
		}

		TickSnapshot.Bounds snapshot = new TickSnapshot.Bounds();
		snapshot.x = bounds.x;
		snapshot.y = bounds.y;
		snapshot.w = bounds.width;
		snapshot.h = bounds.height;
		return snapshot;
	}

	private int[][] polygonSnapshot(Shape shape)
	{
		if (shape == null)
		{
			return null;
		}

		if (shape instanceof Polygon)
		{
			return polygonSnapshot((Polygon) shape);
		}

		List<int[]> points = new ArrayList<>();
		PathIterator iterator = shape.getPathIterator(null, 1.0);
		double[] coords = new double[6];

		while (!iterator.isDone() && points.size() < 64)
		{
			int segmentType = iterator.currentSegment(coords);

			if (segmentType == PathIterator.SEG_MOVETO || segmentType == PathIterator.SEG_LINETO)
			{
				points.add(new int[] {(int) Math.round(coords[0]), (int) Math.round(coords[1])});
			}

			iterator.next();
		}

		if (points.isEmpty())
		{
			return null;
		}

		return points.toArray(new int[0][]);
	}

	private int[][] polygonSnapshot(Polygon polygon)
	{
		if (polygon == null || polygon.npoints <= 0)
		{
			return null;
		}

		int[][] points = new int[polygon.npoints][2];

		for (int i = 0; i < polygon.npoints; i++)
		{
			points[i][0] = polygon.xpoints[i];
			points[i][1] = polygon.ypoints[i];
		}

		return points;
	}

	private int[][] combinePolygons(int[][]... polygons)
	{
		List<int[]> points = new ArrayList<>();

		for (int[][] polygon : polygons)
		{
			if (polygon == null)
			{
				continue;
			}

			for (int[] point : polygon)
			{
				if (point != null && point.length >= 2)
				{
					points.add(new int[] {point[0], point[1]});
				}
			}
		}

		return points.isEmpty() ? null : points.toArray(new int[0][]);
	}

	private TickSnapshot.CanvasPoint polygonCenter(int[][] polygon)
	{
		TickSnapshot.Bounds bounds = boundsSnapshot(polygon);

		if (bounds == null)
		{
			return null;
		}

		TickSnapshot.CanvasPoint point = new TickSnapshot.CanvasPoint();
		point.x = bounds.x + bounds.w / 2;
		point.y = bounds.y + bounds.h / 2;
		return point;
	}

	private TickSnapshot.Bounds boundsSnapshot(int[][] polygon)
	{
		if (polygon == null || polygon.length == 0)
		{
			return null;
		}

		int minX = Integer.MAX_VALUE;
		int minY = Integer.MAX_VALUE;
		int maxX = Integer.MIN_VALUE;
		int maxY = Integer.MIN_VALUE;
		boolean sawPoint = false;

		for (int[] point : polygon)
		{
			if (point == null || point.length < 2)
			{
				continue;
			}

			sawPoint = true;
			minX = Math.min(minX, point[0]);
			minY = Math.min(minY, point[1]);
			maxX = Math.max(maxX, point[0]);
			maxY = Math.max(maxY, point[1]);
		}

		if (!sawPoint)
		{
			return null;
		}

		TickSnapshot.Bounds bounds = new TickSnapshot.Bounds();
		bounds.x = minX;
		bounds.y = minY;
		bounds.w = Math.max(1, maxX - minX);
		bounds.h = Math.max(1, maxY - minY);
		return bounds;
	}

	private boolean geometryIntersectsVisibleArea(TickSnapshot.CanvasPoint point, TickSnapshot.Bounds... bounds)
	{
		return geometryIntersectsVisibleArea(point, null, bounds);
	}

	private boolean geometryIntersectsVisibleArea(TickSnapshot.CanvasPoint point, int[][] polygon, TickSnapshot.Bounds... bounds)
	{
		Rectangle visibleArea = currentVisibleArea();

		if (visibleArea == null || visibleArea.width <= 0 || visibleArea.height <= 0)
		{
			return false;
		}

		if (point != null && visibleArea.contains(point.x, point.y))
		{
			return true;
		}

		TickSnapshot.Bounds polygonBounds = boundsSnapshot(polygon);

		if (polygonBounds != null && visibleArea.intersects(new Rectangle(
				polygonBounds.x,
				polygonBounds.y,
				polygonBounds.w,
				polygonBounds.h)))
		{
			return true;
		}

		for (TickSnapshot.Bounds bound : bounds)
		{
			if (bound != null && visibleArea.intersects(new Rectangle(bound.x, bound.y, bound.w, bound.h)))
			{
				return true;
			}
		}

		return false;
	}

	private Map<String, Object> pluginSnapshotTileProjections(List<Map<String, Object>> requests)
	{
		if (clientThread == null)
		{
			return tileProjectionFailurePayload("client thread unavailable");
		}
		CompletableFuture<Map<String, Object>> future = new CompletableFuture<>();
		clientThread.invoke(() ->
		{
			try
			{
				future.complete(buildTileProjectionPayload(requests));
			}
			catch (RuntimeException e)
			{
				future.complete(tileProjectionFailurePayload("tile projection failed: " + exceptionSummary(e)));
			}
		});
		try
		{
			return future.get(200, TimeUnit.MILLISECONDS);
		}
		catch (TimeoutException e)
		{
			return tileProjectionFailurePayload("tile projection timed out");
		}
		catch (Exception e)
		{
			return tileProjectionFailurePayload("tile projection interrupted: " + exceptionSummary(e));
		}
	}

	private Map<String, Object> pluginSnapshotWorldModelQuery(List<String> needs, Map<String, Object> request)
	{
		if (clientThread == null)
		{
			return worldModelFailurePayload(needs, "client thread unavailable");
		}
		CompletableFuture<Map<String, Object>> future = new CompletableFuture<>();
		clientThread.invoke(() ->
		{
			try
			{
				Map<String, Object> identity = new LinkedHashMap<>();
				addSessionIdentity(identity);
				future.complete(worldModelCache.query(client, needs == null ? List.of() : needs, request == null ? Map.of() : request, tickId, clientTickId, identity));
			}
			catch (RuntimeException e)
			{
				future.complete(worldModelFailurePayload(needs, "world model query failed: " + exceptionSummary(e)));
			}
		});
		try
		{
			return future.get(250, TimeUnit.MILLISECONDS);
		}
		catch (TimeoutException e)
		{
			return worldModelFailurePayload(needs, "world model query timed out");
		}
		catch (Exception e)
		{
			return worldModelFailurePayload(needs, "world model query interrupted: " + exceptionSummary(e));
		}
	}

	private Map<String, Object> worldModelFailurePayload(List<String> needs, String reason)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Map<String, Object> quality = new LinkedHashMap<>();
		quality.put("worldModelAvailable", false);
		quality.put("worldModelAgeMs", null);
		quality.put("objectCensusCapHit", null);
		quality.put("collisionAvailable", false);
		quality.put("projectionAuditAvailable", false);
		payload.put("schema", "world_model_query_response.v1");
		payload.put("snapshotSchema", WorldModelCache.SCHEMA);
		payload.put("status", "WARN");
		payload.put("needs", needs == null ? List.of() : List.copyOf(needs));
		payload.put("payloads", Map.of());
		payload.put("quality", quality);
		payload.put("warnings", List.of(reason));
		return payload;
	}

	private Map<String, Object> tileProjectionFailurePayload(String reason)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "tile_projection_response.v1");
		payload.put("status", "WARN");
		payload.put("tick", tickId);
		payload.put("clientTick", clientTickId);
		payload.put("tiles", List.of());
		payload.put("warnings", List.of(reason));
		return payload;
	}

	private Map<String, Object> buildTileProjectionPayload(List<Map<String, Object>> requests)
	{
		List<Map<String, Object>> tiles = new ArrayList<>();
		for (Map<String, Object> request : requests)
		{
			tiles.add(projectRequestedWorldTile(request));
		}
		boolean allPass = true;
		for (Map<String, Object> tile : tiles)
		{
			if (!"PASS".equals(tile.get("status")))
			{
				allPass = false;
				break;
			}
		}
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "tile_projection_response.v1");
		payload.put("status", allPass ? "PASS" : "WARN");
		payload.put("tick", tickId);
		payload.put("clientTick", clientTickId);
		payload.put("gameState", client.getGameState() == null ? null : client.getGameState().name());
		payload.put("tiles", tiles);
		return payload;
	}

	private Map<String, Object> projectRequestedWorldTile(Map<String, Object> request)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		String label = request == null ? "" : String.valueOf(request.getOrDefault("label", ""));
		int worldX = request == null ? -1 : getInt(request.get("worldX"), -1);
		int worldY = request == null ? -1 : getInt(request.get("worldY"), -1);
		int plane = request == null ? client.getPlane() : getInt(request.get("plane"), client.getPlane());
		payload.put("schema", "tile_projection.v1");
		payload.put("label", label);
		payload.put("worldX", worldX);
		payload.put("worldY", worldY);
		payload.put("plane", plane);
		payload.put("clientPlane", client.getPlane());
		payload.put("status", "PASS");
		List<String> warnings = new ArrayList<>();

		if (worldX < 0 || worldY < 0)
		{
			payload.put("status", "FAIL");
			payload.put("reason", "world tile missing");
			payload.put("geometryAvailable", false);
			payload.put("onScreen", false);
			return payload;
		}
		if (plane != client.getPlane())
		{
			warnings.add("requested tile plane differs from client plane");
		}

		LocalPoint localPoint = null;
		try
		{
			localPoint = LocalPoint.fromWorld(client, new WorldPoint(worldX, worldY, plane));
		}
		catch (RuntimeException e)
		{
			warnings.add("local point failed: " + exceptionSummary(e));
		}

		if (localPoint == null)
		{
			payload.put("status", "WARN");
			payload.put("reason", "world tile is outside the loaded scene");
			payload.put("geometryAvailable", false);
			payload.put("onScreen", false);
			if (!warnings.isEmpty())
			{
				payload.put("warnings", warnings);
			}
			return payload;
		}

		payload.put("localX", localPoint.getX());
		payload.put("localY", localPoint.getY());
		payload.put("sceneX", localPoint.getSceneX());
		payload.put("sceneY", localPoint.getSceneY());

		int[][] polygon = null;
		TickSnapshot.CanvasPoint center = null;
		try
		{
			polygon = polygonSnapshot(Perspective.getCanvasTilePoly(client, localPoint));
			center = polygonCenter(polygon);
		}
		catch (RuntimeException e)
		{
			warnings.add("canvas tile projection failed: " + exceptionSummary(e));
		}

		TickSnapshot.Bounds tileBounds = boundsSnapshot(polygon);
		boolean degeneratePolygon = isDegeneratePolygon(polygon);
		if (degeneratePolygon)
		{
			polygon = null;
			center = null;
			tileBounds = null;
			warnings.add("tile projection returned degenerate canvas polygon");
		}
		boolean geometryAvailable = polygon != null || center != null || tileBounds != null;
		boolean onScreen = geometryAvailable && geometryIntersectsVisibleArea(center, polygon, tileBounds);
		payload.put("geometryAvailable", geometryAvailable);
		payload.put("onScreen", onScreen);
		payload.put("canvasTilePolygon", polygon);
		payload.put("canvasTileBounds", boundsPayload(tileBounds));
		if (center != null)
		{
			payload.put("canvasCenter", Map.of("x", center.x, "y", center.y));
			payload.put("aimPoint", Map.of("canvasX", center.x, "canvasY", center.y, "source", "tileProjectionCenter"));
		}
		if (!geometryAvailable)
		{
			payload.put("status", "WARN");
			warnings.add("tile projection returned no canvas geometry");
		}
		else if (!onScreen)
		{
			payload.put("status", "WARN");
			warnings.add("tile projection is outside the visible viewport");
		}
		if (!warnings.isEmpty())
		{
			payload.put("warnings", warnings);
			payload.put("reason", warnings.get(0));
		}
		return payload;
	}

	private boolean isDegeneratePolygon(int[][] polygon)
	{
		if (polygon == null || polygon.length < 3)
		{
			return polygon != null;
		}

		int minX = Integer.MAX_VALUE;
		int minY = Integer.MAX_VALUE;
		int maxX = Integer.MIN_VALUE;
		int maxY = Integer.MIN_VALUE;
		int points = 0;

		for (int[] point : polygon)
		{
			if (point == null || point.length < 2)
			{
				continue;
			}

			points++;
			minX = Math.min(minX, point[0]);
			minY = Math.min(minY, point[1]);
			maxX = Math.max(maxX, point[0]);
			maxY = Math.max(maxY, point[1]);
		}

		return points < 3 || maxX <= minX || maxY <= minY;
	}

	private Rectangle currentVisibleArea()
	{
		int viewportWidth = client.getViewportWidth();
		int viewportHeight = client.getViewportHeight();

		if (viewportWidth > 0 && viewportHeight > 0)
		{
			return new Rectangle(
					client.getViewportXOffset(),
					client.getViewportYOffset(),
					viewportWidth,
					viewportHeight);
		}

		int canvasWidth = client.getCanvasWidth();
		int canvasHeight = client.getCanvasHeight();

		if (canvasWidth <= 0 || canvasHeight <= 0)
		{
			Canvas canvas = client.getCanvas();

			if (canvas != null)
			{
				Dimension size = canvas.getSize();

				if (size != null)
				{
					canvasWidth = size.width;
					canvasHeight = size.height;
				}
			}
		}

		if (canvasWidth <= 0 || canvasHeight <= 0)
		{
			return null;
		}

		return new Rectangle(0, 0, canvasWidth, canvasHeight);
	}

	private String exceptionSummary(Exception e)
	{
		return e == null ? "unknown" : e.getClass().getSimpleName();
	}

	private static class CompactProjectionGeometryOptions
	{
		private final boolean includeClickableHull;
		private final boolean includeConvexHull;
		private final boolean includeCanvasTilePolygon;
		private final boolean includeAnyPolygons;
		private final int maxRefs;
		private final Integer playerSceneX;
		private final Integer playerSceneY;
		private final Integer playerPlane;
		private int refsWithPolygons;
		private int refsSkippedByCap;
		private boolean capHit;
		private int hullsEmitted;
		private int hullDroppedOffscreen;
		private int hullDroppedNoCanvasIntersection;
		private int hullDroppedByCap;
		private int hullDroppedNullClickbox;

		private CompactProjectionGeometryOptions(
				boolean includeClickableHull,
				boolean includeConvexHull,
				boolean includeCanvasTilePolygon,
				int maxRefs,
				Integer playerSceneX,
				Integer playerSceneY,
				Integer playerPlane)
		{
			this.includeClickableHull = includeClickableHull;
			this.includeConvexHull = includeConvexHull;
			this.includeCanvasTilePolygon = includeCanvasTilePolygon;
			this.includeAnyPolygons = includeClickableHull || includeConvexHull || includeCanvasTilePolygon;
			this.maxRefs = maxRefs;
			this.playerSceneX = playerSceneX;
			this.playerSceneY = playerSceneY;
			this.playerPlane = playerPlane;
		}
	}

	private static class ActorProjection
	{
		private Integer localX;
		private Integer localY;
		private TickSnapshot.CanvasPoint canvasPoint;
		private TickSnapshot.Bounds clickboxBounds;
		private TickSnapshot.Bounds convexHullBounds;
		private boolean onScreen;
		private boolean geometryAvailable;
		private String geometryWarning;
	}

	private static class SceneObjectProjection
	{
		private Integer localX;
		private Integer localY;
		private TickSnapshot.CanvasPoint canvasLocation;
		private int[][] canvasTilePolygon;
		private TickSnapshot.Bounds clickboxBounds;
		private int[][] clickboxPolygon;
		private TickSnapshot.Bounds convexHullBounds;
		private int[][] convexHullPolygon;
		private boolean onScreen;
		private boolean geometryAvailable;
		private String geometryWarning;
	}

	private static class SceneContext
	{
		private WorldView worldView;
		private Scene scene;
		private WorldPoint localWorld;
		private int plane;
		private Tile[][] planeTiles;
		private int baseX;
		private int baseY;
		private int centerSceneX;
		private int centerSceneY;
	}

	private static class SceneIndexEntry
	{
		private String objectKey;
		private String kind;
		private int id;
		private Long hash;
		private String objectName;
		private String objectNameSource;
		private String[] actions;
		private int worldX;
		private int worldY;
		private int plane;
		private int orientation;
		private int sceneX;
		private int sceneY;
		private Integer localX;
		private Integer localY;
		private long firstSeenTick;
		private long lastSeenTick;
		private long lastUpdatedTick;
		private boolean present;
		private Long despawnedTick;
		private String source;

		private static SceneIndexEntry from(TickSnapshot.SceneObjectSnapshot snapshot, long tickId)
		{
			SceneIndexEntry entry = new SceneIndexEntry();
			entry.objectKey = snapshot.objectKey;
			entry.kind = snapshot.kind;
			entry.id = snapshot.id;
			entry.hash = snapshot.hash;
			entry.objectName = snapshot.objectName;
			entry.objectNameSource = snapshot.objectNameSource;
			entry.actions = snapshot.actions;
			entry.worldX = snapshot.worldX;
			entry.worldY = snapshot.worldY;
			entry.plane = snapshot.plane;
			entry.orientation = snapshot.orientation;
			entry.sceneX = snapshot.sceneX;
			entry.sceneY = snapshot.sceneY;
			entry.localX = snapshot.localX;
			entry.localY = snapshot.localY;
			entry.firstSeenTick = tickId;
			entry.lastSeenTick = tickId;
			entry.lastUpdatedTick = tickId;
			entry.present = true;
			entry.source = snapshot.source;
			return entry;
		}

		private boolean updateFrom(TickSnapshot.SceneObjectSnapshot snapshot, long tickId, String source)
		{
			boolean changed = !present
					|| worldX != snapshot.worldX
					|| worldY != snapshot.worldY
					|| plane != snapshot.plane
					|| sceneX != snapshot.sceneX
					|| sceneY != snapshot.sceneY
					|| orientation != snapshot.orientation
					|| !stringEquals(objectName, snapshot.objectName);
			this.kind = snapshot.kind;
			this.id = snapshot.id;
			this.hash = snapshot.hash;
			this.objectName = snapshot.objectName;
			this.objectNameSource = snapshot.objectNameSource;
			this.actions = snapshot.actions;
			this.worldX = snapshot.worldX;
			this.worldY = snapshot.worldY;
			this.plane = snapshot.plane;
			this.orientation = snapshot.orientation;
			this.sceneX = snapshot.sceneX;
			this.sceneY = snapshot.sceneY;
			this.localX = snapshot.localX;
			this.localY = snapshot.localY;
			this.lastSeenTick = tickId;
			this.present = true;
			this.despawnedTick = null;
			this.source = source;
			if (changed)
			{
				this.lastUpdatedTick = tickId;
			}
			return changed;
		}

		private static boolean stringEquals(String left, String right)
		{
			return left == null ? right == null : left.equals(right);
		}
	}

	private static class GroundItemProjection
	{
		private Integer localX;
		private Integer localY;
		private int[][] canvasTilePolygon;
		private TickSnapshot.CanvasPoint canvasCenter;
		private boolean onScreen;
		private boolean geometryAvailable;
		private String geometryWarning;
	}

	private String hashName(String name)
	{
		if (name == null || name.isBlank())
		{
			return "";
		}

		try
		{
			MessageDigest digest = MessageDigest.getInstance("SHA-256");
			byte[] hash = digest.digest(name.toLowerCase().getBytes(StandardCharsets.UTF_8));

			StringBuilder hex = new StringBuilder();

			for (byte b : hash)
			{
				hex.append(String.format("%02x", b));
			}

			return hex.toString();
		}
		catch (NoSuchAlgorithmException e)
		{
			return "";
		}
	}
	private void logEvent(String eventType, Object payload)
	{
		TelemetryWriter currentWriter = writer;

		if (!config.enabled() || currentWriter == null)
		{
			return;
		}

		if (!currentWriter.isRawEventRecordingEnabled())
		{
			currentWriter.recordRawEventSuppressedByMode();
			return;
		}

		try
		{
			EventRecord record = new EventRecord();
			record.schemaVersion = "0.1.0";
			record.tickId = tickId;
			record.eventSeq = ++eventSeq;
			record.timestampUtc = Instant.now().toString();
			record.eventType = eventType;
			record.payload = payload;

			currentWriter.enqueueEvent(gson.toJson(record));
		}
		catch (Exception e)
		{
			log.warn("Failed to enqueue event telemetry: {}", eventType, e);
		}
	}

	private void rememberRecent(List<Map<String, Object>> buffer, Map<String, Object> payload)
	{
		if (buffer == null || payload == null)
		{
			return;
		}
		Map<String, Object> record = new LinkedHashMap<>();
		record.put("tick", tickId);
		record.put("eventSeq", eventSeq + 1);
		record.put("timestampUtc", Instant.now().toString());
		record.putAll(payload);
		synchronized (buffer)
		{
			buffer.add(record);
			while (buffer.size() > MAX_RECENT_COMBAT_EVENTS)
			{
				buffer.remove(0);
			}
		}
	}

	private List<Map<String, Object>> recentCopy(List<Map<String, Object>> buffer)
	{
		if (buffer == null)
		{
			return List.of();
		}
		synchronized (buffer)
		{
			List<Map<String, Object>> copy = new ArrayList<>();
			for (Map<String, Object> item : buffer)
			{
				copy.add(new LinkedHashMap<>(item));
			}
			return copy;
		}
	}

	private Map<String, Object> actorPayload(Actor actor)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("actorType", actorType(actor));
		payload.put("index", actorIndex(actor));
		payload.put("id", actorId(actor));

		if (actor instanceof Player)
		{
			payload.put("nameHash", hashName(actor.getName()));
		}
		else if (actor instanceof NPC)
		{
			payload.put("name", safeString(actor.getName()));
		}

		WorldPoint worldLocation = actor == null ? null : actor.getWorldLocation();

		if (worldLocation != null)
		{
			payload.put("worldX", worldLocation.getX());
			payload.put("worldY", worldLocation.getY());
			payload.put("plane", worldLocation.getPlane());
		}

		if (actor != null)
		{
			payload.put("animation", actor.getAnimation());
			payload.put("healthRatio", actor.getHealthRatio());
			payload.put("healthScale", actor.getHealthScale());
			Actor interacting = actor.getInteracting();

			if (interacting != null)
			{
				Map<String, Object> target = new LinkedHashMap<>();
				target.put("actorType", actorType(interacting));
				target.put("index", actorIndex(interacting));
				target.put("id", actorId(interacting));

				if (interacting instanceof Player)
				{
					target.put("nameHash", hashName(interacting.getName()));
				}
				else if (interacting instanceof NPC)
				{
					target.put("name", safeString(interacting.getName()));
				}

				payload.put("interacting", target);
			}
		}

		return payload;
	}

	private String actorType(Actor actor)
	{
		if (actor == null)
		{
			return "UNKNOWN";
		}

		if (actor == client.getLocalPlayer())
		{
			return "LOCAL_PLAYER";
		}

		if (actor instanceof Player)
		{
			return "PLAYER";
		}

		if (actor instanceof NPC)
		{
			return "NPC";
		}

		return "UNKNOWN";
	}

	private int actorIndex(Actor actor)
	{
		if (actor instanceof NPC)
		{
			return ((NPC) actor).getIndex();
		}

		if (actor instanceof Player)
		{
			return ((Player) actor).getId();
		}

		return -1;
	}

	private int actorId(Actor actor)
	{
		if (actor instanceof NPC)
		{
			return ((NPC) actor).getId();
		}

		return -1;
	}

	private Map<String, Object> projectilePayload(Projectile projectile)
	{
		Map<String, Object> payload = new LinkedHashMap<>();

		if (projectile == null)
		{
			return payload;
		}

		payload.put("id", projectile.getId());
		payload.put("x", projectile.getX());
		payload.put("y", projectile.getY());
		payload.put("projectileZ", projectile.getZ());
		payload.put("floor", projectile.getFloor());
		payload.put("height", projectile.getHeight());
		payload.put("startCycle", projectile.getStartCycle());
		payload.put("endCycle", projectile.getEndCycle());
		payload.put("remainingCycles", projectile.getRemainingCycles());
		payload.put("source", actorPayload(projectile.getSourceActor()));
		payload.put("target", actorPayload(projectile.getTargetActor()));
		addWorldPoint(payload, "source", projectile.getSourcePoint());
		addWorldPoint(payload, "target", projectile.getTargetPoint());
		return payload;
	}

	private Map<String, Object> graphicsObjectPayload(GraphicsObject graphicsObject)
	{
		Map<String, Object> payload = new LinkedHashMap<>();

		if (graphicsObject == null)
		{
			return payload;
		}

		payload.put("id", graphicsObject.getId());
		payload.put("startCycle", graphicsObject.getStartCycle());
		payload.put("level", graphicsObject.getLevel());
		payload.put("z", graphicsObject.getZ());
		payload.put("finished", graphicsObject.finished());
		payload.put("animationFrame", graphicsObject.getAnimationFrame());

		LocalPoint localPoint = graphicsObject.getLocation();

		if (localPoint != null)
		{
			payload.put("localX", localPoint.getX());
			payload.put("localY", localPoint.getY());
			WorldPoint worldPoint = graphicsObject.getWorldView() == null
					? WorldPoint.fromLocal(client, localPoint)
					: WorldPoint.fromLocal(graphicsObject.getWorldView(), localPoint.getX(), localPoint.getY(), graphicsObject.getLevel());
			addWorldPoint(payload, "", worldPoint);
		}

		return payload;
	}

	private Map<String, Object> itemEventPayload(Tile tile, TileItem item)
	{
		Map<String, Object> payload = new LinkedHashMap<>();

		if (item != null)
		{
			payload.put("id", item.getId());
			payload.put("quantity", item.getQuantity());
			payload.put("visibleTime", item.getVisibleTime());
			payload.put("despawnTime", item.getDespawnTime());
			payload.put("ownership", item.getOwnership());
			payload.put("private", item.isPrivate());
		}

		if (tile != null)
		{
			WorldPoint worldLocation = tile.getWorldLocation();
			Point sceneLocation = tile.getSceneLocation();
			addWorldPoint(payload, "", worldLocation);

			if (sceneLocation != null)
			{
				payload.put("sceneX", sceneLocation.getX());
				payload.put("sceneY", sceneLocation.getY());
			}
		}

		return payload;
	}

	private void addWorldPoint(Map<String, Object> payload, String prefix, WorldPoint worldPoint)
	{
		if (worldPoint == null)
		{
			return;
		}

		if (prefix == null || prefix.isEmpty())
		{
			payload.put("worldX", worldPoint.getX());
			payload.put("worldY", worldPoint.getY());
			payload.put("plane", worldPoint.getPlane());
			return;
		}

		payload.put(prefix + "WorldX", worldPoint.getX());
		payload.put(prefix + "WorldY", worldPoint.getY());
		payload.put(prefix + "Plane", worldPoint.getPlane());
	}

	private void rememberItem(TileItem item)
	{
		if (item != null)
		{
			rememberItem(item.getId());
		}
	}

	private void rememberItem(int itemId)
	{
		TelemetryWriter currentWriter = writer;

		if (currentWriter == null || itemId < 0 || !knownItemIds.add(itemId))
		{
			return;
		}

		try
		{
			currentWriter.rememberItem(itemId, itemName(itemId));
		}
		catch (Exception e)
		{
			log.debug("Failed to remember item definition {}", itemId, e);
		}
	}

	private void rememberNpc(NPC npc)
	{
		if (npc == null)
		{
			return;
		}

		TelemetryWriter currentWriter = writer;

		if (currentWriter == null || npc.getId() < 0 || !knownNpcIds.add(npc.getId()))
		{
			return;
		}

		try
		{
			currentWriter.rememberNpc(npc.getId(), npcDisplayName(npc));
		}
		catch (Exception e)
		{
			log.debug("Failed to remember npc definition {}", npc.getId(), e);
		}
	}

	private void rememberObject(int objectId)
	{
		TelemetryWriter currentWriter = writer;

		if (currentWriter == null || objectId < 0 || !knownObjectIds.add(objectId))
		{
			return;
		}

		try
		{
			currentWriter.rememberObject(objectId, objectName(objectId));
		}
		catch (Exception e)
		{
			log.debug("Failed to remember object definition {}", objectId, e);
		}
	}

	private String itemName(int itemId)
	{
		return itemNameLookup(itemId).name;
	}

	private DefinitionName itemNameLookup(int itemId)
	{
		if (itemId < 0)
		{
			return DefinitionName.unavailable();
		}

		if (itemNameCache.containsKey(itemId))
		{
			return itemNameCache.get(itemId);
		}

		DefinitionName lookup = DefinitionName.unavailable();

		try
		{
			ItemComposition itemComposition = client.getItemDefinition(itemId);

			if (itemComposition != null)
			{
				String name = usableDefinitionName(itemComposition.getName());
				lookup = name == null ? DefinitionName.fallback() : new DefinitionName(name, "itemDefinition");
			}
		}
		catch (Exception e)
		{
			log.debug("Failed to read item definition {}", itemId, e);
		}

		itemNameCache.put(itemId, lookup);
		return lookup;
	}

	private String objectName(int objectId)
	{
		return objectNameLookup(objectId).name;
	}

	private DefinitionName objectNameLookup(int objectId)
	{
		if (objectId < 0)
		{
			return DefinitionName.unavailable();
		}

		if (objectNameCache.containsKey(objectId))
		{
			return objectNameCache.get(objectId);
		}

		DefinitionName lookup = DefinitionName.unavailable();

		try
		{
			ObjectComposition objectComposition = client.getObjectDefinition(objectId);

			if (objectComposition != null)
			{
				String impostorName = null;

				if (objectComposition.getImpostorIds() != null)
				{
					try
					{
						ObjectComposition impostor = objectComposition.getImpostor();
						impostorName = impostor == null ? null : usableDefinitionName(impostor.getName());
					}
					catch (Exception e)
					{
						log.debug("Failed to read object impostor definition {}", objectId, e);
					}
				}

				if (impostorName != null)
				{
					lookup = new DefinitionName(impostorName, "objectImpostor");
				}
				else
				{
					String name = usableDefinitionName(objectComposition.getName());
					lookup = name == null ? DefinitionName.fallback() : new DefinitionName(name, "objectDefinition");
				}
			}
		}
		catch (Exception e)
		{
			log.debug("Failed to read object definition {}", objectId, e);
		}

		objectNameCache.put(objectId, lookup);
		return lookup;
	}

	private String npcDisplayName(NPC npc)
	{
		return npcNameLookup(npc).name;
	}

	private DefinitionName npcNameLookup(NPC npc)
	{
		if (npc == null)
		{
			return DefinitionName.unavailable();
		}

		String name = usableDefinitionName(npc.getName());

		if (name != null)
		{
			DefinitionName lookup = new DefinitionName(name, "npcName");
			npcNameCache.put(npc.getId(), lookup);
			return lookup;
		}

		return npcDefinitionName(npc);
	}

	private DefinitionName npcDefinitionName(NPC npc)
	{
		int npcId = npc.getId();

		if (npcId < 0)
		{
			return DefinitionName.unavailable();
		}

		if (npcNameCache.containsKey(npcId))
		{
			return npcNameCache.get(npcId);
		}

		DefinitionName lookup = DefinitionName.unavailable();

		try
		{
			NPCComposition transformed = npc.getTransformedComposition();
			String transformedName = transformed == null ? null : usableDefinitionName(transformed.getName());

			if (transformedName != null)
			{
				lookup = new DefinitionName(transformedName, "transformedComposition");
			}
			else
			{
				NPCComposition composition = npc.getComposition();
				String compositionName = composition == null ? null : usableDefinitionName(composition.getName());
				lookup = compositionName == null ? DefinitionName.fallback() : new DefinitionName(compositionName, "npcComposition");
			}
		}
		catch (Exception e)
		{
			log.debug("Failed to read npc definition {}", npcId, e);
		}

		npcNameCache.put(npcId, lookup);
		return lookup;
	}

	private String usableDefinitionName(String value)
	{
		if (value == null)
		{
			return null;
		}

		String trimmed = value.trim();

		if (trimmed.isEmpty() || "null".equalsIgnoreCase(trimmed) || "hidden".equalsIgnoreCase(trimmed))
		{
			return null;
		}

		return trimmed;
	}

	private static class DefinitionName
	{
		private final String name;
		private final String source;

		private DefinitionName(String name, String source)
		{
			this.name = name;
			this.source = source;
		}

		private static DefinitionName fallback()
		{
			return new DefinitionName(null, "fallback");
		}

		private static DefinitionName unavailable()
		{
			return new DefinitionName(null, "unavailable");
		}
	}

	private int getInt(Object value, int fallback)
	{
		if (value instanceof Number)
		{
			return ((Number) value).intValue();
		}
		if (value instanceof String)
		{
			try
			{
				return Integer.parseInt(((String) value).trim());
			}
			catch (NumberFormatException ignored)
			{
				return fallback;
			}
		}
		return fallback;
	}

	private String truncate(String value, int maxLength)
	{
		if (value == null)
		{
			return "";
		}

		return value.length() <= maxLength ? value : value.substring(0, maxLength);
	}

	private Map<String, Object> itemContainerPayload(ItemContainerChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("containerId", event.getContainerId());

		if (event.getItemContainer() != null)
		{
			payload.put("size", event.getItemContainer().size());
		}

		return payload;
	}

	private Map<String, Object> menuOpenedPayload(MenuOpened event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		MenuEntry[] entries = event.getMenuEntries();

		payload.put("menuEntryCount", entries == null ? 0 : entries.length);

		List<Map<String, Object>> entrySummaries = new ArrayList<>();

		if (entries != null)
		{
			for (MenuEntry entry : entries)
			{
				if (entry != null)
				{
					entrySummaries.add(menuEntryPayload(entry));
				}
			}
		}

		payload.put("entries", entrySummaries);
		return payload;
	}

	private Map<String, Object> latestHoverMenuPayload()
	{
		return copyHotMenuSample(latestHoverMenu);
	}

	private Map<String, Object> lastMenuOptionClickedPayload()
	{
		return copyHotMenuSample(lastMenuOptionClicked);
	}

	private Map<String, Object> copyHotMenuSample(Map<String, Object> sample)
	{
		return sample == null ? null : new LinkedHashMap<>(sample);
	}

	private Map<String, Object> clientTickPayload(String sourceEvent)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "plugin_client_tick_sample.v1");
		payload.put("sampleSource", sourceEvent);
		payload.put("sourceEvent", sourceEvent);
		payload.put("clientTick", clientTickId);
		payload.put("gameTickAtSample", tickId);
		payload.put("timestampUtc", Instant.now().toString());
		payload.put("wallTimeMillis", System.currentTimeMillis());
		payload.put("monotonicTimeNanos", System.nanoTime());
		payload.put("gameState", currentGameStateText());
		addSessionIdentity(payload);
		addMouseCanvasPosition(payload);
		return payload;
	}

	private Map<String, Object> hoverMenuPayload()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		MenuEntry[] entries = client.getMenuEntries();
		MenuEntry topEntry = entries == null || entries.length == 0 ? null : entries[entries.length - 1];
		List<Map<String, Object>> topEntries = new ArrayList<>();

		if (entries != null)
		{
			for (int i = entries.length - 1; i >= 0 && topEntries.size() < 5; i--)
			{
				MenuEntry entry = entries[i];
				if (entry != null)
				{
					topEntries.add(menuEntryPayload(entry));
				}
			}
		}

		payload.put("schema", "plugin_hover_menu.v1");
		payload.put("sampleSource", "PostMenuSort");
		payload.put("sourceEvent", "PostMenuSort");
		payload.put("clientTick", clientTickId);
		payload.put("gameTickAtSample", tickId);
		payload.put("timestampUtc", Instant.now().toString());
		payload.put("wallTimeMillis", System.currentTimeMillis());
		payload.put("monotonicTimeNanos", System.nanoTime());
		payload.put("gameState", currentGameStateText());
		payload.put("menuOpen", isMenuOpenSafe());
		addMenuBounds(payload);
		addSessionIdentity(payload);
		addMouseCanvasPosition(payload);
		payload.put("entryCount", entries == null ? 0 : entries.length);
		payload.put("entries", topEntries);
		addTopMenuEntry(payload, topEntry);
		return payload;
	}

	private void addMenuBounds(Map<String, Object> payload)
	{
		Map<String, Object> bounds = new LinkedHashMap<>();
		try
		{
			bounds.put("x", client.getMenuX());
			bounds.put("y", client.getMenuY());
			bounds.put("width", client.getMenuWidth());
			bounds.put("height", client.getMenuHeight());
			bounds.put("scrollable", client.isMenuScrollable());
			bounds.put("scroll", client.getMenuScroll());
			payload.put("menuBounds", bounds);
		}
		catch (RuntimeException ex)
		{
			payload.put("menuBounds", null);
		}
	}

	private void addTopMenuEntry(Map<String, Object> payload, MenuEntry entry)
	{
		if (entry == null)
		{
			payload.put("topOption", "");
			payload.put("topTarget", "");
			payload.put("topType", "");
			payload.put("topIdentifier", -1);
			payload.put("topParam0", -1);
			payload.put("topParam1", -1);
			return;
		}

		payload.put("topOption", safeString(entry.getOption()));
		payload.put("topTarget", safeString(entry.getTarget()));
		payload.put("topType", String.valueOf(entry.getType()));
		payload.put("topIdentifier", entry.getIdentifier());
		payload.put("topParam0", entry.getParam0());
		payload.put("topParam1", entry.getParam1());
	}

	private Map<String, Object> menuOptionClickedPayload(MenuOptionClicked event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", "plugin_menu_option_clicked.v1");
		payload.put("sampleSource", "MenuOptionClicked");
		payload.put("sourceEvent", "MenuOptionClicked");
		payload.put("clientTick", clientTickId);
		payload.put("gameTickAtSample", tickId);
		payload.put("timestampUtc", Instant.now().toString());
		payload.put("wallTimeMillis", System.currentTimeMillis());
		payload.put("monotonicTimeNanos", System.nanoTime());
		payload.put("gameState", currentGameStateText());
		addSessionIdentity(payload);
		addMouseCanvasPosition(payload);
		payload.put("option", event == null ? "" : safeString(event.getMenuOption()));
		payload.put("target", event == null ? "" : safeString(event.getMenuTarget()));
		payload.put("type", event == null ? "" : String.valueOf(event.getMenuAction()));
		payload.put("identifier", event == null ? -1 : event.getId());
		payload.put("param0", event == null ? -1 : event.getParam0());
		payload.put("param1", event == null ? -1 : event.getParam1());
		if (event != null)
		{
			payload.put("itemId", event.getItemId());
			payload.put("consumed", event.isConsumed());
		}
		return payload;
	}

	private void addMouseCanvasPosition(Map<String, Object> payload)
	{
		Point mouse = client == null ? null : client.getMouseCanvasPosition();
		payload.put("mouseCanvasX", mouse == null ? null : mouse.getX());
		payload.put("mouseCanvasY", mouse == null ? null : mouse.getY());
		payload.put("isInCanvas", isMouseInCanvas(mouse));
	}

	private boolean isMouseInCanvas(Point mouse)
	{
		if (mouse == null || client == null)
		{
			return false;
		}
		Canvas canvas = client.getCanvas();
		if (canvas == null)
		{
			return false;
		}
		return mouse.getX() >= 0
				&& mouse.getY() >= 0
				&& mouse.getX() < canvas.getWidth()
				&& mouse.getY() < canvas.getHeight();
	}

	private String currentGameStateText()
	{
		if (client == null)
		{
			return null;
		}
		try
		{
			return String.valueOf(client.getGameState());
		}
		catch (Exception e)
		{
			return null;
		}
	}

	private boolean isMenuOpenSafe()
	{
		if (client == null)
		{
			return false;
		}
		try
		{
			return client.isMenuOpen();
		}
		catch (Exception e)
		{
			return false;
		}
	}

	private void addSessionIdentity(Map<String, Object> payload)
	{
		TelemetryWriter currentWriter = writer;
		if (currentWriter == null)
		{
			return;
		}
		Path sessionDir = currentWriter.getSessionDir();
		if (sessionDir == null)
		{
			return;
		}
		payload.put("sessionPath", sessionDir.toString());
		payload.put("sessionId", sessionDir.getFileName() == null ? null : sessionDir.getFileName().toString());
	}

	private Map<String, Object> menuEntryPayload(MenuEntry entry)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Widget widget = entry.getWidget();

		payload.put("option", safeString(entry.getOption()));
		payload.put("target", safeString(entry.getTarget()));
		payload.put("type", String.valueOf(entry.getType()));
		payload.put("identifier", entry.getIdentifier());
		payload.put("itemId", entry.getItemId());
		payload.put("param0", entry.getParam0());
		payload.put("param1", entry.getParam1());
		payload.put("worldViewId", entry.getWorldViewId());

		if (widget != null)
		{
			payload.put("widgetId", widget.getId());
		}

		return payload;
	}

	private String safeString(String value)
	{
		return value == null ? "" : value;
	}

	private void safeCapture(List<String> captureErrors, String section, Runnable capture)
	{
		try
		{
			capture.run();
		}
		catch (Exception e)
		{
			recordCaptureFailure(captureErrors, section, e);
		}
	}

	private void recordCaptureFailure(List<String> captureErrors, String section, Exception e)
	{
		captureErrors.add(section);
		log.warn("Telemetry capture section failed: {}", section, e);
	}

	private long elapsedMillis(long startNanos)
	{
		return Math.max(0L, (System.nanoTime() - startNanos) / 1_000_000L);
	}
}

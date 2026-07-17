package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.awt.Canvas;
import java.awt.Dimension;
import java.awt.GraphicsConfiguration;
import java.awt.IllegalComponentStateException;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.Shape;
import java.awt.Window;
import java.awt.geom.PathIterator;
import java.awt.geom.AffineTransform;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import javax.inject.Inject;
import javax.swing.SwingUtilities;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.CollisionData;
import net.runelite.api.CollisionDataFlag;
import net.runelite.api.GameState;
import net.runelite.api.GameObject;
import net.runelite.api.GroundObject;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.MenuEntry;
import net.runelite.api.NPC;
import net.runelite.api.Perspective;
import net.runelite.api.Player;
import net.runelite.api.Point;
import net.runelite.api.Prayer;
import net.runelite.api.Skill;
import net.runelite.api.MenuAction;
import net.runelite.api.Tile;
import net.runelite.api.TileObject;
import net.runelite.api.VarClientInt;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.DecorativeObject;
import net.runelite.api.Scene;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.ClientTick;
import net.runelite.api.events.DecorativeObjectDespawned;
import net.runelite.api.events.DecorativeObjectSpawned;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GameObjectDespawned;
import net.runelite.api.events.GameObjectSpawned;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GroundObjectDespawned;
import net.runelite.api.events.GroundObjectSpawned;
import net.runelite.api.events.MenuOpened;
import net.runelite.api.events.MenuOptionClicked;
import net.runelite.api.events.PostMenuSort;
import net.runelite.api.events.WallObjectDespawned;
import net.runelite.api.events.WallObjectSpawned;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.events.ConfigChanged;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.ItemID;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.input.KeyManager;
import net.runelite.client.input.MouseManager;

@Slf4j
@PluginDescriptor(
		name = "Telemetry Collector",
		description = "Read-only live telemetry sensor for the local snapshot endpoint",
		tags = {"telemetry", "sensor", "snapshot"}
)
public class TelemetryPlugin extends Plugin
{
	private static final int INVENTORY_SLOT_COUNT = 28;
	private static final int EMPTY_INVENTORY_WIDGET_ITEM_ID = ItemID.BLANKOBJECT;
	private static final int DIALOGUE_WIDGET_SCAN_LIMIT = 160;
	private static final int CLOSABLE_WIDGET_SCAN_LIMIT = 256;
	static final int HOT_MENU_ENTRY_LIMIT = ClientTickHotState.MAX_MENU_ENTRY_LIMIT;
	private static final long CONTEXT_MENU_SAMPLE_MAX_AGE_MILLIS = 500L;
	private static final long CONTEXT_MENU_SAMPLE_MAX_CLIENT_TICK_DRIFT = 2L;
	private static final int COLLISION_TILE_BLOCK_MASK =
			CollisionDataFlag.BLOCK_MOVEMENT_OBJECT
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
	private KeyManager keyManager;

	@Inject
	private MouseManager mouseManager;

	private PluginLiveCache liveCache;
	private PluginSnapshotEndpoint pluginSnapshotEndpoint;
	private String pluginInstanceId;
	private final ClientTickHotState clientTickHotState = new ClientTickHotState();
	private final WorldModelCache worldModelCache = new WorldModelCache();
	private volatile ClientThreadQueryScheduler clientThreadQueryScheduler;
	private volatile Map<String, Object> latestHoverMenu;
	private volatile Map<String, Object> lastMenuOptionClicked;
	private volatile Map<String, Object> latestCameraPose;
	private CameraInputCapture cameraInputCapture;
	private volatile long tickId = 0;
	private volatile long clientTickId = 0;
	private final Map<Integer, DefinitionName> itemNameCache = new LinkedHashMap<>();
	private int lastActivityAnimation = Integer.MIN_VALUE;
	private int lastActivityPoseAnimation = Integer.MIN_VALUE;
	private String lastActivityInteractingSignature;

	@Provides
	TelemetryConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(TelemetryConfig.class);
	}

	@Override
	protected void startUp() throws Exception
	{
		itemNameCache.clear();
		lastActivityAnimation = Integer.MIN_VALUE;
		lastActivityPoseAnimation = Integer.MIN_VALUE;
		lastActivityInteractingSignature = null;
		liveCache = new PluginLiveCache(gson);
		pluginInstanceId = "plugin-" + ProcessHandle.current().pid() + "-" + Instant.now().toEpochMilli();
		worldModelCache.clear("startup");
		clientThreadQueryScheduler = clientThread == null
				? null
				: new ClientThreadQueryScheduler(runnable -> clientThread.invoke(runnable));
		cameraInputCapture = new CameraInputCapture(
				clientTickHotState::recordCameraInput,
				clientTickHotState::clearCameraInput,
				System::currentTimeMillis,
				System::nanoTime);
		keyManager.registerKeyListener(cameraInputCapture);
		mouseManager.registerMouseListener(cameraInputCapture);
		startPluginSnapshotEndpoint();
		clientThread.invokeLater(() -> publishGameStateBaseline(client.getGameState()));

		log.info("Telemetry Collector started");
	}

	@Override
	protected void shutDown() throws Exception
	{
		stopPluginSnapshotEndpoint();
		if (clientThreadQueryScheduler != null)
		{
			clientThreadQueryScheduler.close();
			clientThreadQueryScheduler = null;
		}
		if (cameraInputCapture != null)
		{
			keyManager.unregisterKeyListener(cameraInputCapture);
			mouseManager.unregisterMouseListener(cameraInputCapture);
			cameraInputCapture.close();
			cameraInputCapture = null;
		}
		liveCache = null;
		pluginInstanceId = null;
		latestCameraPose = null;
		worldModelCache.clear("shutdown");

		log.info("Telemetry Collector stopped");
	}

	private void startPluginSnapshotEndpoint()
	{
		if (!config.enablePluginSnapshotEndpoint() || pluginSnapshotEndpoint != null || liveCache == null)
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
				clientTickHotState,
				this::pluginSnapshotTileProjections,
				this::pluginSnapshotWorldModelQuery);
		if (cameraInputCapture != null)
		{
			pluginSnapshotEndpoint.setCameraInputCaptureLeaseControls(
					cameraInputCapture::renewLease,
					cameraInputCapture::disableLease);
		}
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

	private void stopPluginSnapshotEndpoint()
	{
		if (pluginSnapshotEndpoint == null)
		{
			return;
		}

		pluginSnapshotEndpoint.close();
		pluginSnapshotEndpoint = null;
	}

	@Subscribe
	public void onConfigChanged(ConfigChanged event)
	{
		if (event == null || !TelemetryConfigKeys.CONFIG_GROUP.equals(event.getGroup()))
		{
			return;
		}

		String key = event.getKey();
		if (isSnapshotEndpointConfigKey(key))
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
		startPluginSnapshotEndpoint();
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
		long snapshotStartNanos = System.nanoTime();

		if (!config.enabled() || liveCache == null)
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
			safeCapture(captureErrors, "cameraViewport", () -> captureCameraViewport(snapshot));
			safeCapture(captureErrors, "welcomeScreen", () -> captureWelcomeScreen(snapshot));
			safeCapture(captureErrors, "textInputState", () -> captureTextInputState(snapshot));
			if (gameState == GameState.LOGGED_IN)
			{
				safeCapture(captureErrors, "localPlayer", () -> captureLocalPlayer(snapshot));
				safeCapture(captureErrors, "inventory", () -> captureInventory(snapshot));
				safeCapture(captureErrors, "bankUi", () -> captureBankUi(snapshot));
				safeCapture(captureErrors, "dialogueState", () -> captureDialogueState(snapshot));
				safeCapture(captureErrors, "status", () -> captureStatus(snapshot));
			}
		}
		finally
		{
			snapshot.captureErrors = captureErrors.toArray(new String[0]);
			snapshot.snapshotBuildDurationMillis = elapsedMillis(snapshotStartNanos);
			try
			{
				publishCompactLivePackets(snapshot, snapshotStartNanos);
			}
			catch (RuntimeException e)
			{
				log.warn("Failed to publish live telemetry tick {}", snapshot.tickId, e);
			}
		}
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("gameState", String.valueOf(event.getGameState()));
		if (cameraInputCapture != null && event.getGameState() != GameState.LOGGED_IN)
		{
			cameraInputCapture.updateContext(CameraInputCapture.Context.blocked());
		}

		recordGameStateHotSample(event.getGameState());
		publishGameStateBaseline(event.getGameState());

		if (event.getGameState() == GameState.LOADING
				|| event.getGameState() == GameState.LOGIN_SCREEN
				|| event.getGameState() == GameState.HOPPING)
		{
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
	public void onGameObjectSpawned(GameObjectSpawned event)
	{
		worldModelCache.markDirty("gameObjectSpawned");
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		worldModelCache.markDirty("gameObjectDespawned");
	}

	@Subscribe
	public void onWallObjectSpawned(WallObjectSpawned event)
	{
		worldModelCache.markDirty("wallObjectSpawned");
	}

	@Subscribe
	public void onWallObjectDespawned(WallObjectDespawned event)
	{
		worldModelCache.markDirty("wallObjectDespawned");
	}

	@Subscribe
	public void onDecorativeObjectSpawned(DecorativeObjectSpawned event)
	{
		worldModelCache.markDirty("decorativeObjectSpawned");
	}

	@Subscribe
	public void onDecorativeObjectDespawned(DecorativeObjectDespawned event)
	{
		worldModelCache.markDirty("decorativeObjectDespawned");
	}

	@Subscribe
	public void onGroundObjectSpawned(GroundObjectSpawned event)
	{
		worldModelCache.markDirty("groundObjectSpawned");
	}

	@Subscribe
	public void onGroundObjectDespawned(GroundObjectDespawned event)
	{
		worldModelCache.markDirty("groundObjectDespawned");
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		Map<String, Object> payload = hoverMenuPayload();
		payload.put("sampleSource", "MenuOpened");
		payload.put("sourceEvent", "MenuOpened");
		payload.put("menuEntryCount", event.getMenuEntries() == null ? 0 : event.getMenuEntries().length);
		latestHoverMenu = payload;
		clientTickHotState.recordPostMenuSort(payload);
	}

	@Subscribe
	public void onClientTick(ClientTick event)
	{
		clientTickId++;
		Map<String, Object> cameraPose = currentCameraPosePayload();
		latestCameraPose = cameraPose;
		updateCameraInputContext(cameraPose);
		clientTickHotState.recordClientTick(clientTickPayload("ClientTick", cameraPose));
		if (shouldRefreshOpenMenu(currentMenuOpen()))
		{
			Map<String, Object> payload = hoverMenuPayload();
			payload.put("sampleSource", "ClientTickMenuOpen");
			payload.put("sourceEvent", "ClientTickMenuOpen");
			payload.put("menuOpen", true);
			latestHoverMenu = payload;
			clientTickHotState.recordPostMenuSort(payload);
		}
		if (client.getGameState() != GameState.LOGGED_IN)
		{
			publishGameStateBaseline(client.getGameState());
		}
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

	private void publishCompactLivePackets(TickSnapshot snapshot, long captureStartedNanos)
	{
		publishSensorFrame(snapshot, captureStartedNanos, false);
	}

	private void publishGameStateBaseline(GameState gameState)
	{
		if (!config.enabled() || liveCache == null)
		{
			return;
		}
		long captureStartedNanos = System.nanoTime();
		TickSnapshot snapshot = new TickSnapshot();
		List<String> captureErrors = new ArrayList<>();
		snapshot.schemaVersion = "0.1.0";
		snapshot.tickId = tickId;
		snapshot.timestampUtc = Instant.now().toString();
		snapshot.gameState = String.valueOf(gameState);
		safeCapture(captureErrors, "cameraViewport", () -> captureCameraViewport(snapshot));
		safeCapture(captureErrors, "welcomeScreen", () -> captureWelcomeScreen(snapshot));
		safeCapture(captureErrors, "textInputState", () -> captureTextInputState(snapshot));
		snapshot.captureErrors = captureErrors.toArray(new String[0]);
		snapshot.snapshotBuildDurationMillis = elapsedMillis(captureStartedNanos);
		publishSensorFrame(snapshot, captureStartedNanos, true);
	}

	private void publishSensorFrame(
			TickSnapshot snapshot,
			long captureStartedNanos,
			boolean baselineOnly)
	{
		if (liveCache == null || snapshot == null)
		{
			return;
		}
		String completedAtUtc = Instant.now().toString();
		String sessionId = pluginInstanceId;
		long processId = ProcessHandle.current().pid();
		String geometryFrameId = geometryFrameId(snapshot);
		String frameId = (sessionId == null ? "plugin-unidentified" : sessionId)
				+ ":" + snapshot.tickId + ":" + captureStartedNanos;
		List<String> captureErrors = snapshot.captureErrors == null
				? List.of()
				: Arrays.asList(snapshot.captureErrors);
		boolean loggedIn = "LOGGED_IN".equals(snapshot.gameState);
		boolean baselineAvailable = baselineCaptureAvailable(snapshot, captureErrors);
		boolean inventoryAvailable = !baselineOnly
				&& loggedIn
				&& snapshot.inventory != null
				&& !captureErrors.contains("inventory");
		boolean activityAvailable = !baselineOnly
				&& loggedIn
				&& snapshot.localPlayer != null
				&& snapshot.status != null
				&& !captureErrors.contains("localPlayer")
				&& !captureErrors.contains("status");
		boolean bankUiAvailable = !baselineOnly
				&& loggedIn
				&& snapshot.bankUi != null
				&& !captureErrors.contains("bankUi");
		boolean dialogueAvailable = !baselineOnly
				&& loggedIn
				&& snapshot.dialogueState != null
				&& !captureErrors.contains("dialogueState");

		SensorFrame.Builder builder = SensorFrame.builder(
				frameId,
				snapshot.tickId,
				captureStartedNanos,
				snapshot.timestampUtc)
				.completedAtUtc(completedAtUtc)
				.captureDurationMillis(snapshot.snapshotBuildDurationMillis == null
						? 0L
						: snapshot.snapshotBuildDurationMillis)
				.sessionId(sessionId)
				.clientProcessId(processId)
				.geometryFrameId(geometryFrameId);
		SensorFrame frame;
		try
		{
			builder.fact(gson, SensorFrame.FACT_BASELINE, snapshot.tickId, completedAtUtc,
					baselineAvailable, factErrors(captureErrors, baselineAvailable, "baseline", baselineOnly),
					baselinePayload(snapshot));
			builder.fact(gson, SensorFrame.FACT_INVENTORY, snapshot.tickId, completedAtUtc,
					inventoryAvailable, factErrors(captureErrors, inventoryAvailable, "inventory", baselineOnly),
					inventoryPayload(snapshot));
			builder.fact(gson, SensorFrame.FACT_ACTIVITY, snapshot.tickId, completedAtUtc,
					activityAvailable, factErrors(captureErrors, activityAvailable, "activity", baselineOnly),
					activityPayload(snapshot));
			builder.fact(gson, SensorFrame.FACT_BANK_UI, snapshot.tickId, completedAtUtc,
					bankUiAvailable, factErrors(captureErrors, bankUiAvailable, "bankUi", baselineOnly),
					bankUiPayload(snapshot));
			builder.fact(gson, SensorFrame.FACT_DIALOGUE_STATE, snapshot.tickId, completedAtUtc,
					dialogueAvailable, factErrors(captureErrors, dialogueAvailable, "dialogueState", baselineOnly),
					dialogueStatePayload(snapshot));
			frame = builder.build();
		}
		catch (RuntimeException e)
		{
			log.warn("Sensor frame payload assembly failed; publishing an unavailable replacement", e);
			frame = unavailableSensorFrame(
					snapshot,
					captureStartedNanos,
					completedAtUtc,
					geometryFrameId,
					"frame_payload_assembly_failed:" + e.getClass().getSimpleName());
		}
		if (!liveCache.publish(frame))
		{
			log.warn("Failed to publish atomic sensor frame {}", frameId);
		}
	}

	private SensorFrame unavailableSensorFrame(
			TickSnapshot snapshot,
			long captureStartedNanos,
			String completedAtUtc,
			String geometryFrameId,
			String reason)
	{
		String capturedAtUtc = snapshot.timestampUtc == null
				? completedAtUtc
				: snapshot.timestampUtc;
		String sessionId = pluginInstanceId;
		long processId = ProcessHandle.current().pid();
		SensorFrame.Builder builder = SensorFrame.builder(
				(sessionId == null ? "plugin-unidentified" : sessionId)
						+ ":" + snapshot.tickId + ":failed:" + captureStartedNanos,
				snapshot.tickId,
				captureStartedNanos,
				capturedAtUtc)
				.completedAtUtc(completedAtUtc)
				.sessionId(sessionId)
				.clientProcessId(processId)
				.geometryFrameId(geometryFrameId);
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			builder.fact(
					gson,
					factName,
					snapshot.tickId,
					completedAtUtc,
					false,
					List.of(reason),
					Map.of());
		}
		return builder.build();
	}

	private List<String> factErrors(
			List<String> captureErrors,
			boolean available,
			String section,
			boolean baselineOnly)
	{
		if (available)
		{
			return List.of();
		}
		List<String> errors = new ArrayList<>();
		if (captureErrors != null)
		{
			for (String error : captureErrors)
			{
				if (error != null && (error.equals(section)
						|| ("baseline".equals(section)
						&& ("gameState".equals(error)
						|| "cameraViewport".equals(error)
						|| "welcomeScreen".equals(error)))
						|| ("activity".equals(section)
						&& ("localPlayer".equals(error) || "status".equals(error)))))
				{
					errors.add("capture_failed:" + error);
				}
			}
		}
		if (errors.isEmpty())
		{
			errors.add(baselineOnly && !"baseline".equals(section)
					? "not_captured_for_game_state"
					: "capture_unavailable");
		}
		return List.copyOf(errors);
	}

	private String geometryFrameId(TickSnapshot snapshot)
	{
		TickSnapshot.InputGeometrySnapshot geometry = snapshot == null ? null : snapshot.inputGeometry;
		String signature = String.join("|",
				String.valueOf(snapshot == null ? null : snapshot.cameraX),
				String.valueOf(snapshot == null ? null : snapshot.cameraY),
				String.valueOf(snapshot == null ? null : snapshot.cameraZ),
				String.valueOf(snapshot == null ? null : snapshot.cameraYaw),
				String.valueOf(snapshot == null ? null : snapshot.cameraPitch),
				String.valueOf(snapshot == null ? null : snapshot.zoom3d),
				String.valueOf(snapshot == null ? null : snapshot.viewportWidth),
				String.valueOf(snapshot == null ? null : snapshot.viewportHeight),
				String.valueOf(snapshot == null ? null : snapshot.viewportXOffset),
				String.valueOf(snapshot == null ? null : snapshot.viewportYOffset),
				String.valueOf(geometry == null ? null : geometry.canvasScreenX),
				String.valueOf(geometry == null ? null : geometry.canvasScreenY),
				String.valueOf(geometry == null ? null : geometry.canvasWidth),
				String.valueOf(geometry == null ? null : geometry.canvasHeight),
				String.valueOf(geometry == null ? null : geometry.sourceCanvasWidth),
				String.valueOf(geometry == null ? null : geometry.sourceCanvasHeight),
				String.valueOf(geometry == null ? null : geometry.clientWindowX),
				String.valueOf(geometry == null ? null : geometry.clientWindowY),
				String.valueOf(geometry == null ? null : geometry.clientWindowWidth),
				String.valueOf(geometry == null ? null : geometry.clientWindowHeight),
				String.valueOf(geometry == null ? null : geometry.displayScaleX),
				String.valueOf(geometry == null ? null : geometry.displayScaleY),
				String.valueOf(geometry == null ? null : geometry.coordinateSpace),
				String.valueOf(geometry == null ? null : geometry.isCanvasShowing),
				String.valueOf(geometry == null ? null : geometry.isClientFocused));
		return "geometry-" + hashName(signature);
	}

	private String currentGeometryFrameId()
	{
		TickSnapshot current = new TickSnapshot();
		current.tickId = tickId;
		captureCameraViewport(current);
		return geometryFrameId(current);
	}

	static Map<String, Object> baselinePayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("tick", snapshot.tickId);
		payload.put("gameState", snapshot.gameState);
		payload.put("player", playerPayload(snapshot));
		payload.put("cameraViewport", cameraViewportPayload(snapshot));
		payload.put("inputGeometry", inputGeometryPayload(snapshot));
		payload.put("welcomeScreenVisible", snapshot == null ? null : snapshot.welcomeScreenVisible);
		payload.put("textInputActive", snapshot == null ? null : snapshot.textInputActive);
		payload.put("scenePlayable", scenePlayable(snapshot));
		return payload;
	}

	static boolean scenePlayable(TickSnapshot snapshot)
	{
		return snapshot != null
				&& "LOGGED_IN".equals(snapshot.gameState)
				&& snapshot.localPlayer != null
				&& Boolean.FALSE.equals(snapshot.welcomeScreenVisible);
	}

	static boolean baselineCaptureAvailable(
			TickSnapshot snapshot,
			List<String> captureErrors)
	{
		List<String> errors = captureErrors == null ? List.of() : captureErrors;
		return snapshot != null
				&& snapshot.gameState != null
				&& !errors.contains("gameState")
				&& !errors.contains("cameraViewport")
				&& !errors.contains("welcomeScreen");
	}

	static Map<String, Object> playerPayload(TickSnapshot snapshot)
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
			if (snapshot.localPlayer.canvasX != null && snapshot.localPlayer.canvasY != null)
			{
				player.put("canvasX", snapshot.localPlayer.canvasX);
				player.put("canvasY", snapshot.localPlayer.canvasY);
			}
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

	private static Map<String, Object> interactingPayload(TickSnapshot.StatusSnapshot status)
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

	static Map<String, Object> cameraViewportPayload(TickSnapshot snapshot)
	{
		Map<String, Object> camera = new LinkedHashMap<>();
		camera.put("cameraX", snapshot.cameraX);
		camera.put("cameraY", snapshot.cameraY);
		camera.put("cameraZ", snapshot.cameraZ);
		camera.put("cameraPitch", snapshot.cameraPitch);
		camera.put("cameraYaw", snapshot.cameraYaw);
		camera.put("zoom3d", snapshot.zoom3d);
		camera.put("viewportWidth", snapshot.viewportWidth);
		camera.put("viewportHeight", snapshot.viewportHeight);
		camera.put("viewportXOffset", snapshot.viewportXOffset);
		camera.put("viewportYOffset", snapshot.viewportYOffset);
		camera.put("canvasWidth", snapshot.canvasWidth);
		camera.put("canvasHeight", snapshot.canvasHeight);
		return camera;
	}

	private Map<String, Object> currentCameraPosePayload()
	{
		try
		{
			TickSnapshot snapshot = new TickSnapshot();
			snapshot.tickId = tickId;
			captureCameraViewport(snapshot);
			Map<String, Object> pose = cameraViewportPayload(snapshot);
			pose.put("schema", "camera_pose.v1");
			pose.put("clientTick", clientTickId);
			pose.put("gameTick", tickId);
			pose.put("cameraYawTarget", client.getCameraYawTarget());
			pose.put("cameraPitchTarget", client.getCameraPitchTarget());
			pose.put("geometryFrameId", geometryFrameId(snapshot));
			return pose;
		}
		catch (RuntimeException e)
		{
			return copyCameraPose(latestCameraPose);
		}
	}

	private Map<String, Object> copyCameraPose(Map<String, Object> pose)
	{
		return pose == null ? null : new LinkedHashMap<>(pose);
	}

	private void updateCameraInputContext(Map<String, Object> cameraPose)
	{
		if (cameraInputCapture == null)
		{
			return;
		}
		Canvas canvas = client == null ? null : client.getCanvas();
		cameraInputCapture.updateContext(new CameraInputCapture.Context(
				cameraInputAllowed(canvas),
				canvas,
				clientTickId,
				tickId,
				currentGameStateText(),
				pluginInstanceId,
				ProcessHandle.current().pid(),
				cameraPose));
	}

	private boolean cameraInputAllowed(Canvas canvas)
	{
		try
		{
			if (client == null
					|| config == null
					|| !config.enabled()
					|| canvas == null
					|| client.getGameState() != GameState.LOGGED_IN
					|| client.getLocalPlayer() == null
					|| !canvas.isFocusOwner()
					|| textInputActive(
							client.getFocusedInputFieldWidget() != null,
							client.getVarcIntValue(VarClientInt.INPUT_TYPE)))
			{
				return false;
			}
			return !widgetVisible(client.getWidget(InterfaceID.BankpinKeypad.UNIVERSE));
		}
		catch (RuntimeException ignored)
		{
			return false;
		}
	}

	private Map<String, Object> resolvedClickTarget(MenuOptionClicked event)
	{
		Map<String, Object> target = new LinkedHashMap<>();
		target.put("schema", "plugin_click_target.v1");
		if (event == null)
		{
			target.put("actionFamily", "other");
			target.put("resolution", "unsupported");
			target.put("confidence", "none");
			return target;
		}
		if (event.getMenuAction() != MenuAction.WALK)
		{
			return isTileObjectMenuAction(event.getMenuAction())
					? resolvedTileObjectTarget(event)
					: unsupportedClickTarget();
		}

		MenuEntry entry = event.getMenuEntry();
		int requestedWorldViewId = entry == null ? -1 : entry.getWorldViewId();
		WorldView worldView = client.getWorldView(requestedWorldViewId);
		if (worldView == null)
		{
			worldView = client.getTopLevelWorldView();
		}
		int sceneX = event.getParam0();
		int sceneY = event.getParam1();
		WorldPoint menuPoint = null;
		WorldPoint selectedPoint = null;
		Point selectedScene = null;
		WorldPoint destinationPoint = null;
		if (worldView != null)
		{
			try
			{
				menuPoint = WorldPoint.fromScene(worldView, sceneX, sceneY, worldView.getPlane());
			}
			catch (RuntimeException ignored)
			{
				// Invalid menu coordinates remain explicit unresolved evidence.
			}
			try
			{
				Tile selectedTile = worldView.getSelectedSceneTile();
				if (selectedTile != null)
				{
					selectedPoint = selectedTile.getWorldLocation();
					selectedScene = selectedTile.getSceneLocation();
				}
			}
			catch (RuntimeException ignored)
			{
				// Selected-scene evidence is optional corroboration.
			}
		}
		try
		{
			LocalPoint destination = client.getLocalDestinationLocation();
			if (destination != null)
			{
				WorldView destinationView = client.getWorldView(destination.getWorldView());
				if (destinationView == null)
				{
					destinationView = client.getTopLevelWorldView();
				}
				if (destinationView != null)
				{
					destinationPoint = WorldPoint.fromLocal(
							destinationView,
							destination.getX(),
							destination.getY(),
							destinationView.getPlane());
				}
			}
		}
		catch (RuntimeException ignored)
		{
			// The pre-click local destination can be absent or stale.
		}
		return walkTargetPayload(
				worldView == null ? requestedWorldViewId : worldView.getId(),
				sceneX,
				sceneY,
				menuPoint,
				selectedPoint,
				selectedScene,
				destinationPoint);
	}

	private Map<String, Object> unsupportedClickTarget()
	{
		Map<String, Object> target = new LinkedHashMap<>();
		target.put("schema", "plugin_click_target.v1");
		target.put("actionFamily", "other");
		target.put("resolution", "unsupported");
		target.put("confidence", "none");
		return target;
	}

	private boolean isTileObjectMenuAction(MenuAction action)
	{
		return action != null && String.valueOf(action).contains("GAME_OBJECT");
	}

	private Map<String, Object> resolvedTileObjectTarget(MenuOptionClicked event)
	{
		Map<String, Object> target = new LinkedHashMap<>();
		target.put("schema", "plugin_click_target.v1");
		target.put("actionFamily", "tile_object");
		target.put("activationKind", "unverified");
		target.put("menuSceneX", event.getParam0());
		target.put("menuSceneY", event.getParam1());
		target.put("menuIdentifier", event.getId());

		MenuEntry entry = event.getMenuEntry();
		int requestedWorldViewId = entry == null ? -1 : entry.getWorldViewId();
		WorldView worldView = client.getWorldView(requestedWorldViewId);
		if (worldView == null)
		{
			worldView = client.getTopLevelWorldView();
		}
		target.put("worldViewId", worldView == null ? requestedWorldViewId : worldView.getId());
		List<TileObject> candidates = nearbyTileObjects(
				worldView,
				event.getParam0(),
				event.getParam1(),
				event.getId());
		target.put("candidateCount", candidates.size());
		List<TileObject> footprintCandidates = new ArrayList<>();
		for (TileObject candidate : candidates)
		{
			if (tileObjectOccupiesMenuScene(candidate, event.getParam0(), event.getParam1()))
			{
				footprintCandidates.add(candidate);
			}
		}
		target.put("footprintCandidateCount", footprintCandidates.size());

		Point mouse = client.getMouseCanvasPosition();
		List<TileObject> containing = new ArrayList<>();
		for (TileObject candidate : footprintCandidates)
		{
			InteractionGeometry geometry = authoritativeInteractionGeometry(candidate);
			if (geometry != null
					&& mouse != null
					&& geometry.shape.contains(mouse.getX(), mouse.getY()))
			{
				containing.add(candidate);
			}
		}
		target.put("containingCandidateCount", containing.size());
		if (footprintCandidates.size() != 1)
		{
			target.put("resolution", candidates.isEmpty() ? "unresolved" : "ambiguous");
			target.put("confidence", "none");
			target.put("ambiguityReasons", List.of(
					footprintCandidates.isEmpty()
							? "no same-id object occupied the exact menu scene footprint"
							: "multiple same-id objects occupied the exact menu scene footprint"));
			return target;
		}

		TileObject selected = footprintCandidates.get(0);
		InteractionGeometry geometry = authoritativeInteractionGeometry(selected);
		boolean objectGeometryContainsPoint = containing.size() == 1;
		MenuEntry selectedEntry = event.getMenuEntry();
		int selectedWorldViewId = selectedEntry == null ? -1 : selectedEntry.getWorldViewId();
		boolean contextMenuRowActivation = freshOpenMenuTupleMatches(
						latestHoverMenuPayload(),
						clientTickId,
						System.currentTimeMillis(),
						pluginInstanceId,
						ProcessHandle.current().pid(),
						mouse,
						safeString(event.getMenuOption()),
						safeString(event.getMenuTarget()),
						String.valueOf(event.getMenuAction()),
						event.getId(),
						event.getParam0(),
						event.getParam1(),
						selectedWorldViewId);
		String activationKind = tileObjectActivationKind(
				objectGeometryContainsPoint,
				contextMenuRowActivation);
		boolean objectGeometryActivation = "object_geometry".equals(activationKind);
		target.put("resolution", "exact");
		target.put("confidence", "exact");
		target.put("identitySource", "same_id_menu_scene_footprint");
		target.put("activationKind", activationKind);
		target.put("source", objectGeometryActivation
				? "same_id_clickbox_contains_activation"
				: "menu_identifier_scene_coordinates");
		target.put("object", tileObjectPayload(selected));
		Map<String, Object> geometryPayload = new LinkedHashMap<>();
		geometryPayload.put("geometryFrameId", currentGeometryFrameId());
		geometryPayload.put("source", geometry == null ? null : geometry.source);
		geometryPayload.put("bounds", geometry == null ? null : boundsPayload(boundsSnapshot(geometry.shape)));
		geometryPayload.put("polygon", geometry == null ? null : polygonSnapshot(geometry.shape));
		geometryPayload.put("clickInside", objectGeometryActivation ? Boolean.TRUE : null);
		if (objectGeometryActivation && mouse != null)
		{
			geometryPayload.put("activationPoint", Map.of("x", mouse.getX(), "y", mouse.getY()));
		}
		target.put("geometry", geometryPayload);
		if (contextMenuRowActivation && mouse != null)
		{
			target.put("contextMenuRowPoint", Map.of("x", mouse.getX(), "y", mouse.getY()));
		}
		return target;
	}

	static String tileObjectActivationKind(
			boolean objectGeometryContainsPoint,
			boolean freshContextMenuRow)
	{
		if (freshContextMenuRow)
		{
			return "context_menu_row";
		}
		return objectGeometryContainsPoint ? "object_geometry" : "unverified";
	}

	private List<TileObject> nearbyTileObjects(
			WorldView worldView,
			int sceneX,
			int sceneY,
			int identifier)
	{
		List<TileObject> matches = new ArrayList<>();
		if (worldView == null)
		{
			return matches;
		}
		Scene scene = worldView.getScene();
		Tile[][][] tiles = scene == null ? null : scene.getTiles();
		int plane = worldView.getPlane();
		if (tiles == null || plane < 0 || plane >= tiles.length || tiles[plane] == null)
		{
			return matches;
		}
		Set<TileObject> seen = Collections.newSetFromMap(new IdentityHashMap<>());
		for (int x = Math.max(0, sceneX - 2); x <= Math.min(tiles[plane].length - 1, sceneX + 2); x++)
		{
			if (tiles[plane][x] == null)
			{
				continue;
			}
			for (int y = Math.max(0, sceneY - 2); y <= Math.min(tiles[plane][x].length - 1, sceneY + 2); y++)
			{
				Tile tile = tiles[plane][x][y];
				if (tile == null)
				{
					continue;
				}
				addMatchingTileObject(matches, seen, tile.getWallObject(), identifier);
				addMatchingTileObject(matches, seen, tile.getDecorativeObject(), identifier);
				addMatchingTileObject(matches, seen, tile.getGroundObject(), identifier);
				GameObject[] gameObjects = tile.getGameObjects();
				if (gameObjects != null)
				{
					for (GameObject gameObject : gameObjects)
					{
						addMatchingTileObject(matches, seen, gameObject, identifier);
					}
				}
			}
		}
		return matches;
	}

	private void addMatchingTileObject(
			List<TileObject> matches,
			Set<TileObject> seen,
			TileObject object,
			int identifier)
	{
		if (object != null && object.getId() == identifier && seen.add(object))
		{
			matches.add(object);
		}
	}

	private boolean tileObjectOccupiesMenuScene(TileObject object, int sceneX, int sceneY)
	{
		if (object == null)
		{
			return false;
		}
		try
		{
			if (object instanceof GameObject)
			{
				Point minimum = ((GameObject) object).getSceneMinLocation();
				Point maximum = ((GameObject) object).getSceneMaxLocation();
				if (minimum != null && maximum != null)
				{
					return sceneFootprintContains(
							sceneX,
							sceneY,
							minimum.getX(),
							minimum.getY(),
							maximum.getX(),
							maximum.getY());
				}
			}
			LocalPoint local = object.getLocalLocation();
			return local != null && sceneFootprintContains(
					sceneX,
					sceneY,
					local.getSceneX(),
					local.getSceneY(),
					local.getSceneX(),
					local.getSceneY());
		}
		catch (RuntimeException ignored)
		{
			return false;
		}
	}

	static boolean sceneFootprintContains(
			int sceneX,
			int sceneY,
			int firstX,
			int firstY,
			int secondX,
			int secondY)
	{
		return sceneX >= Math.min(firstX, secondX)
				&& sceneX <= Math.max(firstX, secondX)
				&& sceneY >= Math.min(firstY, secondY)
				&& sceneY <= Math.max(firstY, secondY);
	}

	static boolean freshOpenMenuTupleMatches(
			Map<String, Object> openMenu,
			long currentClientTick,
			long currentWallTimeMillis,
			String expectedSessionId,
			long expectedProcessId,
			Point rowPoint,
			String option,
			String target,
			String type,
			int identifier,
			int param0,
			int param1,
			int worldViewId)
	{
		if (openMenu == null
				|| !Boolean.TRUE.equals(openMenu.get("menuOpen"))
				|| expectedSessionId == null
				|| !expectedSessionId.equals(openMenu.get("sessionId"))
				|| !"LOGGED_IN".equals(openMenu.get("gameState"))
				|| !numericEquals(openMenu.get("clientProcessId"), expectedProcessId)
				|| !freshMenuSample(openMenu, currentClientTick, currentWallTimeMillis)
				|| !pointInsideMenuBounds(openMenu.get("menuBounds"), rowPoint))
		{
			return false;
		}
		Object entriesValue = openMenu.get("entries");
		if (!(entriesValue instanceof Iterable<?>))
		{
			return false;
		}
		for (Object entryValue : (Iterable<?>) entriesValue)
		{
			if (!(entryValue instanceof Map<?, ?>))
			{
				continue;
			}
			Map<?, ?> entry = (Map<?, ?>) entryValue;
			if (textEquals(entry.get("option"), option)
					&& textEquals(entry.get("target"), target)
					&& textEquals(entry.get("type"), type)
					&& numericEquals(entry.get("identifier"), identifier)
					&& numericEquals(entry.get("param0"), param0)
					&& numericEquals(entry.get("param1"), param1)
					&& numericEquals(entry.get("worldViewId"), worldViewId))
			{
				return true;
			}
		}
		return false;
	}

	private static boolean freshMenuSample(
			Map<String, Object> openMenu,
			long currentClientTick,
			long currentWallTimeMillis)
	{
		Object clientTickValue = openMenu.get("clientTick");
		Object wallTimeValue = openMenu.get("wallTimeMillis");
		if (!(clientTickValue instanceof Number) || !(wallTimeValue instanceof Number))
		{
			return false;
		}
		long sampleClientTick = ((Number) clientTickValue).longValue();
		long sampleWallTimeMillis = ((Number) wallTimeValue).longValue();
		long clientTickAge = currentClientTick - sampleClientTick;
		long wallTimeAge = currentWallTimeMillis - sampleWallTimeMillis;
		return clientTickAge >= 0L
				&& clientTickAge <= CONTEXT_MENU_SAMPLE_MAX_CLIENT_TICK_DRIFT
				&& wallTimeAge >= 0L
				&& wallTimeAge <= CONTEXT_MENU_SAMPLE_MAX_AGE_MILLIS;
	}

	private static boolean pointInsideMenuBounds(Object boundsValue, Point point)
	{
		if (!(boundsValue instanceof Map<?, ?>) || point == null)
		{
			return false;
		}
		Map<?, ?> bounds = (Map<?, ?>) boundsValue;
		Long x = numericLong(bounds.get("x"));
		Long y = numericLong(bounds.get("y"));
		Long width = numericLong(bounds.get("width"));
		Long height = numericLong(bounds.get("height"));
		if (x == null || y == null || width == null || height == null || width <= 0L || height <= 0L)
		{
			return false;
		}
		long pointX = point.getX();
		long pointY = point.getY();
		return pointX >= x && pointY >= y && pointX < x + width && pointY < y + height;
	}

	private static boolean numericEquals(Object value, long expected)
	{
		Long number = numericLong(value);
		return number != null && number == expected;
	}

	private static Long numericLong(Object value)
	{
		return value instanceof Number ? ((Number) value).longValue() : null;
	}

	private static boolean textEquals(Object value, String expected)
	{
		return value instanceof String && value.equals(expected == null ? "" : expected);
	}

	private Map<String, Object> tileObjectPayload(TileObject object)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		WorldPoint world = object.getWorldLocation();
		LocalPoint local = object.getLocalLocation();
		String kind = tileObjectKind(object);
		payload.put("objectKey", kind + ":" + object.getId() + ":" + object.getHash()
				+ ":" + (world == null ? "unknown" : world.getX() + ":" + world.getY() + ":" + world.getPlane()));
		payload.put("id", object.getId());
		payload.put("hash", object.getHash());
		payload.put("kind", kind);
		payload.put("worldX", world == null ? null : world.getX());
		payload.put("worldY", world == null ? null : world.getY());
		payload.put("plane", world == null ? object.getPlane() : world.getPlane());
		payload.put("localX", local == null ? null : local.getX());
		payload.put("localY", local == null ? null : local.getY());
		payload.put("sceneX", local == null ? null : local.getSceneX());
		payload.put("sceneY", local == null ? null : local.getSceneY());
		payload.put("orientation", tileObjectOrientation(object));
		return payload;
	}

	private String tileObjectKind(TileObject object)
	{
		if (object instanceof GameObject)
		{
			return "GAME_OBJECT";
		}
		if (object instanceof WallObject)
		{
			return "WALL_OBJECT";
		}
		if (object instanceof DecorativeObject)
		{
			return "DECORATIVE_OBJECT";
		}
		return object instanceof GroundObject ? "GROUND_OBJECT" : "TILE_OBJECT";
	}

	private int tileObjectOrientation(TileObject object)
	{
		if (object instanceof GameObject)
		{
			return ((GameObject) object).getOrientation();
		}
		if (object instanceof WallObject)
		{
			return ((WallObject) object).getOrientationA();
		}
		return 0;
	}

	private InteractionGeometry authoritativeInteractionGeometry(TileObject object)
	{
		if (object == null)
		{
			return null;
		}
		Shape clickbox = object.getClickbox();
		if (clickbox != null && !clickbox.getBounds().isEmpty())
		{
			return new InteractionGeometry("clickbox", clickbox);
		}
		Shape hull = null;
		if (object instanceof GameObject)
		{
			hull = ((GameObject) object).getConvexHull();
		}
		else if (object instanceof WallObject)
		{
			hull = ((WallObject) object).getConvexHull();
		}
		else if (object instanceof DecorativeObject)
		{
			hull = ((DecorativeObject) object).getConvexHull();
		}
		else if (object instanceof GroundObject)
		{
			hull = ((GroundObject) object).getConvexHull();
		}
		if (hull != null && !hull.getBounds().isEmpty())
		{
			return new InteractionGeometry("convex_hull", hull);
		}
		Polygon tile = object.getCanvasTilePoly();
		return tile == null || tile.getBounds().isEmpty()
				? null
				: new InteractionGeometry("canvas_tile_polygon", tile);
	}

	private static final class InteractionGeometry
	{
		private final String source;
		private final Shape shape;

		private InteractionGeometry(String source, Shape shape)
		{
			this.source = source;
			this.shape = shape;
		}
	}

	static Map<String, Object> walkTargetPayload(
			int worldViewId,
			int sceneX,
			int sceneY,
			WorldPoint menuPoint,
			WorldPoint selectedPoint,
			Point selectedScene,
			WorldPoint destinationPoint)
	{
		Map<String, Object> target = new LinkedHashMap<>();
		target.put("schema", "plugin_click_target.v1");
		target.put("actionFamily", "walk_tile");
		target.put("worldViewId", worldViewId);
		target.put("menuSceneX", sceneX);
		target.put("menuSceneY", sceneY);
		target.put("menuParamTile", worldTilePayload(menuPoint));
		target.put("selectedSceneTile", worldTilePayload(selectedPoint));
		target.put("localDestinationTile", worldTilePayload(destinationPoint));
		boolean selectedMatches = selectedScene != null
				&& selectedScene.getX() == sceneX
				&& selectedScene.getY() == sceneY;
		target.put("selectedSceneTileMatchesMenuParams", selectedMatches);

		WorldPoint resolved;
		String source;
		String confidence;
		if (selectedMatches && selectedPoint != null)
		{
			resolved = selectedPoint;
			source = "selected_scene_tile_correlated_with_menu_params";
			confidence = "exact";
		}
		else if (menuPoint != null)
		{
			resolved = menuPoint;
			source = "menu_params";
			confidence = "high";
		}
		else if (destinationPoint != null)
		{
			resolved = destinationPoint;
			source = "local_destination_fallback";
			confidence = "low";
		}
		else
		{
			resolved = null;
			source = "unresolved";
			confidence = "none";
		}
		target.put("resolution", resolved == null ? "unresolved" : "resolved");
		target.put("source", source);
		target.put("confidence", confidence);
		target.put("worldTile", worldTilePayload(resolved));
		return target;
	}

	private static Map<String, Object> worldTilePayload(WorldPoint point)
	{
		if (point == null)
		{
			return null;
		}
		return Map.of(
				"worldX", point.getX(),
				"worldY", point.getY(),
				"plane", point.getPlane());
	}

	static Map<String, Object> inputGeometryPayload(TickSnapshot snapshot)
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
		payload.put("coordinateSpace", geometry == null ? null : geometry.coordinateSpace);
		payload.put("isCanvasShowing", geometry == null ? null : geometry.isCanvasShowing);
		payload.put("isClientFocused", geometry == null ? null : geometry.isClientFocused);
		payload.put("clientProcessId", geometry == null || geometry.clientProcessId == null
				? ProcessHandle.current().pid()
				: geometry.clientProcessId);
		return payload;
	}

	static Map<String, Object> inventoryPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Map<String, Object> inventory = itemContainerSnapshot(snapshot.inventory);
		inventory.put("source", snapshot.inventoryCaptureSource);
		payload.put("inventory", inventory);
		payload.put("equipment", itemContainerSnapshot(snapshot.equipment));
		return payload;
	}

	private Map<String, Object> bankUiPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		TickSnapshot.BankUiSnapshot bankUi = snapshot == null ? null : snapshot.bankUi;

		payload.put("schema", "bank_ui_context_payload.v1");
		payload.put("tick", snapshot == null ? null : snapshot.tickId);
		payload.put("known", bankUiKnown(snapshot));
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
		payload.put("bankSummary", itemContainerSummary(bankUi == null ? null : bankUi.bankItems));
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

	private static Map<String, Object> itemContainerSnapshot(TickSnapshot.InventorySlot[] slots)
	{
		return itemContainerPayload(slots, true);
	}

	private static Map<String, Object> itemContainerSummary(TickSnapshot.InventorySlot[] slots)
	{
		return itemContainerPayload(slots, false);
	}

	private static Map<String, Object> itemContainerPayload(TickSnapshot.InventorySlot[] slots, boolean includeItems)
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

		return safeString(status.interactingType)
				+ ":" + status.interactingIndex
				+ ":" + status.interactingId
				+ ":" + safeString(status.interactingName)
				+ ":" + status.interactingWorldX
				+ ":" + status.interactingWorldY
				+ ":" + status.interactingPlane;
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

	private void captureCameraViewport(TickSnapshot snapshot)
	{
		snapshot.cameraX = client.getCameraX();
		snapshot.cameraY = client.getCameraY();
		snapshot.cameraZ = client.getCameraZ();
		snapshot.cameraYaw = client.getCameraYaw();
		snapshot.cameraPitch = client.getCameraPitch();
		snapshot.zoom3d = client.get3dZoom();
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

	private void captureWelcomeScreen(TickSnapshot snapshot)
	{
		Widget play = client.getWidget(InterfaceID.WelcomeScreen.PLAY);
		Widget clickHere = client.getWidget(InterfaceID.WelcomeScreen.CLICKHERE_TEXT);
		snapshot.welcomeScreenVisible = widgetVisible(play) || widgetVisible(clickHere);
	}

	private void captureTextInputState(TickSnapshot snapshot)
	{
		snapshot.textInputActive = textInputActive(
				client.getFocusedInputFieldWidget() != null,
				client.getVarcIntValue(VarClientInt.INPUT_TYPE));
	}

	static boolean textInputActive(boolean focusedInputField, int inputType)
	{
		return focusedInputField || inputType != 0;
	}

	private TickSnapshot.InputGeometrySnapshot captureInputGeometry(TickSnapshot snapshot, Canvas canvas)
	{
		TickSnapshot.InputGeometrySnapshot geometry = new TickSnapshot.InputGeometrySnapshot();
		geometry.sourceTick = snapshot == null ? null : snapshot.tickId;
		geometry.clientProcessId = ProcessHandle.current().pid();
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
		if (graphicsConfiguration == null)
		{
			geometry.reason = "display_configuration_unavailable";
			return geometry;
		}
		AffineTransform deviceTransform = graphicsConfiguration.getDefaultTransform();
		Rectangle monitorBounds = graphicsConfiguration.getBounds();
		if (!usableDeviceTransform(deviceTransform, monitorBounds))
		{
			geometry.reason = "display_transform_unavailable";
			return geometry;
		}
		geometry.displayScaleX = deviceTransform.getScaleX();
		geometry.displayScaleY = deviceTransform.getScaleY();

		try
		{
			Window window = SwingUtilities.getWindowAncestor(canvas);
			if (window != null)
			{
				Rectangle bounds = window.getBounds();
				if (bounds != null)
				{
					Rectangle deviceBounds = devicePixelBounds(
							bounds, deviceTransform, monitorBounds);
					geometry.clientWindowX = deviceBounds.x;
					geometry.clientWindowY = deviceBounds.y;
					geometry.clientWindowWidth = deviceBounds.width;
					geometry.clientWindowHeight = deviceBounds.height;
				}
				geometry.isClientFocused = window.isFocused();
			}

			java.awt.Point location = canvas.getLocationOnScreen();
			if (location != null && size != null)
			{
				Rectangle deviceBounds = devicePixelBounds(
						new Rectangle(location.x, location.y, size.width, size.height),
						deviceTransform,
						monitorBounds);
				geometry.canvasScreenX = deviceBounds.x;
				geometry.canvasScreenY = deviceBounds.y;
				geometry.canvasWidth = deviceBounds.width;
				geometry.canvasHeight = deviceBounds.height;
				geometry.coordinateSpace = "device_pixels";
				geometry.geometryAvailable = geometry.canvasWidth != null && geometry.canvasHeight != null
						&& geometry.canvasWidth > 0 && geometry.canvasHeight > 0
						&& geometry.sourceCanvasWidth != null && geometry.sourceCanvasWidth > 0
						&& geometry.sourceCanvasHeight != null && geometry.sourceCanvasHeight > 0;
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
			geometry.coordinateSpace = null;
			geometry.reason = "device_pixel_conversion_failed";
		}

		return geometry;
	}

	static boolean usableDeviceTransform(AffineTransform transform, Rectangle monitorBounds)
	{
		if (transform == null || monitorBounds == null
				|| monitorBounds.width <= 0 || monitorBounds.height <= 0)
		{
			return false;
		}
		double scaleX = transform.getScaleX();
		double scaleY = transform.getScaleY();
		return Double.isFinite(scaleX) && scaleX > 0.0 && scaleX <= 16.0
				&& Double.isFinite(scaleY) && scaleY > 0.0 && scaleY <= 16.0
				&& Math.abs(transform.getShearX()) < 0.000000001
				&& Math.abs(transform.getShearY()) < 0.000000001
				&& Math.abs(transform.getTranslateX()) < 0.000000001
				&& Math.abs(transform.getTranslateY()) < 0.000000001;
	}

	static Rectangle devicePixelBounds(
			Rectangle userBounds,
			AffineTransform transform,
			Rectangle monitorBounds)
	{
		if (userBounds == null || userBounds.width <= 0 || userBounds.height <= 0
				|| !usableDeviceTransform(transform, monitorBounds)
				|| !monitorBounds.contains(userBounds))
		{
			throw new IllegalArgumentException(
					"bounds must fit one monitor with a usable device transform");
		}
		long left = monitorBounds.x + scaleDevicePixels(
				(long) userBounds.x - monitorBounds.x,
				transform.getScaleX());
		long top = monitorBounds.y + scaleDevicePixels(
				(long) userBounds.y - monitorBounds.y,
				transform.getScaleY());
		long width = scaleDevicePixels(userBounds.width, transform.getScaleX());
		long height = scaleDevicePixels(userBounds.height, transform.getScaleY());
		if (left < Integer.MIN_VALUE || top < Integer.MIN_VALUE
				|| left > Integer.MAX_VALUE || top > Integer.MAX_VALUE
				|| width <= 0 || height <= 0
				|| width > Integer.MAX_VALUE || height > Integer.MAX_VALUE)
		{
			throw new IllegalArgumentException("device pixel bounds are invalid");
		}
		return new Rectangle((int) left, (int) top, (int) width, (int) height);
	}

	private static long scaleDevicePixels(long value, double scale)
	{
		double scaled = value * scale;
		if (!Double.isFinite(scaled))
		{
			throw new IllegalArgumentException("scaled device coordinate is not finite");
		}
		return (long) Math.ceil(scaled - 0.5d);
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

		List<TickSnapshot.InventorySlotWidgetSnapshot> snapshots = new ArrayList<>();
		for (int i = 0; i < INVENTORY_SLOT_COUNT; i++)
		{
			Widget child = inventoryItems.getChild(i);
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
		snapshot.dialogueState = dialogueStateFromWidgets(
				client.getWidget(InterfaceID.Chatmenu.UNIVERSE),
				client.getWidget(InterfaceID.Chatmenu.OPTIONS),
				new DialogueContinueSurface[] {
						new DialogueContinueSurface(
								client.getWidget(InterfaceID.ChatLeft.TEXT),
								client.getWidget(InterfaceID.ChatLeft.CONTINUE)),
						new DialogueContinueSurface(
								client.getWidget(InterfaceID.ChatRight.TEXT),
								client.getWidget(InterfaceID.ChatRight.CONTINUE)),
						new DialogueContinueSurface(
								client.getWidget(InterfaceID.ChatBoth.TEXT),
								client.getWidget(InterfaceID.ChatBoth.CONTINUE)),
						new DialogueContinueSurface(
								client.getWidget(InterfaceID.Messagebox.TEXT),
								client.getWidget(InterfaceID.Messagebox.CONTINUE))
				},
				clientTickId,
				System.currentTimeMillis());
	}

	TickSnapshot.DialogueStateSnapshot dialogueStateFromWidgets(
			Widget optionUniverse,
			Widget optionContainer,
			DialogueContinueSurface[] continueSurfaces,
			long latestClientTick,
			long wallTimeMillis)
	{
		TickSnapshot.DialogueStateSnapshot dialogue = new TickSnapshot.DialogueStateSnapshot();
		dialogue.schema = "dialogue_state.v1";
		dialogue.active = false;
		dialogue.type = "unknown";
		dialogue.promptText = "";
		dialogue.options = new TickSnapshot.DialogueOptionSnapshot[0];
		dialogue.canUseNumberKeys = null;
		dialogue.canUseSpaceContinue = null;
		dialogue.source = "runelite_dialogue_widget_ids";
		dialogue.latestClientTick = latestClientTick;
		dialogue.wallTimeMillis = wallTimeMillis;

		List<Integer> rootIds = new ArrayList<>();
		addVisibleWidgetGroup(rootIds, optionUniverse);
		if (continueSurfaces != null)
		{
			for (DialogueContinueSurface surface : continueSurfaces)
			{
				if (surface != null)
				{
					addVisibleWidgetGroup(rootIds, surface.textWidget);
					addVisibleWidgetGroup(rootIds, surface.continueWidget);
				}
			}
		}
		dialogue.widgetRootIds = rootIds.toArray(new Integer[0]);

		if (widgetVisible(optionUniverse) && widgetVisible(optionContainer))
		{
			List<Widget> optionRows = dialogueOptionRows(optionContainer);
			if (!optionRows.isEmpty())
			{
				List<TickSnapshot.DialogueOptionSnapshot> options = new ArrayList<>();
				Set<String> optionKeys = new HashSet<>();
				boolean numberKeysUnambiguous = optionRows.size() <= 9;
				for (Widget optionRow : optionRows)
				{
					String text = widgetText(optionRow);
					TickSnapshot.DialogueOptionSnapshot option = dialogueOptionSnapshot(
							options.size() + 1,
							optionRow,
							text);
					options.add(option);
					if (option.key == null || !optionKeys.add(option.key))
					{
						numberKeysUnambiguous = false;
					}
				}
				dialogue.active = true;
				dialogue.type = "options";
				dialogue.promptText = dialoguePromptText(
						optionUniverse,
						optionRows);
				dialogue.options = options.toArray(new TickSnapshot.DialogueOptionSnapshot[0]);
				dialogue.canUseNumberKeys = numberKeysUnambiguous;
				dialogue.canUseSpaceContinue = false;
				return dialogue;
			}
		}

		DialogueContinueSurface observedContinue = null;
		if (continueSurfaces != null)
		{
			for (DialogueContinueSurface surface : continueSurfaces)
			{
				if (surface == null
						|| !widgetVisible(surface.textWidget)
						|| !widgetVisible(surface.continueWidget)
						|| widgetText(surface.textWidget).isEmpty())
				{
					continue;
				}
				if (observedContinue != null)
				{
					return dialogue;
				}
				observedContinue = surface;
			}
		}
		if (observedContinue != null)
		{
			dialogue.active = true;
			dialogue.type = "click_to_continue";
			dialogue.promptText = widgetText(observedContinue.textWidget);
			dialogue.options = new TickSnapshot.DialogueOptionSnapshot[0];
			dialogue.canUseNumberKeys = false;
			dialogue.canUseSpaceContinue = true;
		}
		return dialogue;
	}

	private void addVisibleWidgetGroup(List<Integer> rootIds, Widget widget)
	{
		if (!widgetVisible(widget))
		{
			return;
		}
		int groupId = widget.getId() >>> 16;
		if (!rootIds.contains(groupId))
		{
			rootIds.add(groupId);
		}
	}

	private List<Widget> dialogueOptionRows(Widget optionContainer)
	{
		Widget[] children = optionContainer == null ? null : optionContainer.getChildren();
		if (children == null
				|| children.length == 0
				|| children.length > DIALOGUE_WIDGET_SCAN_LIMIT)
		{
			return List.of();
		}
		List<Widget> rows = new ArrayList<>();
		for (Widget child : children)
		{
			if (widgetVisible(child) && !widgetText(child).isEmpty())
			{
				rows.add(child);
			}
		}
		return rows;
	}

	private String dialoguePromptText(
			Widget optionUniverse,
			List<Widget> optionRows)
	{
		Set<Widget> excluded = Collections.newSetFromMap(new IdentityHashMap<>());
		excluded.addAll(optionRows);
		Set<Widget> visited = Collections.newSetFromMap(new IdentityHashMap<>());
		List<String> promptParts = new ArrayList<>();
		collectDialoguePromptText(
				optionUniverse,
				optionUniverse,
				excluded,
				visited,
				promptParts);
		return String.join(" | ", promptParts);
	}

	private void collectDialoguePromptText(
			Widget widget,
			Widget optionUniverse,
			Set<Widget> excluded,
			Set<Widget> visited,
			List<String> output)
	{
		if (widget == null
				|| visited.size() >= DIALOGUE_WIDGET_SCAN_LIMIT
				|| !visited.add(widget)
				|| excluded.contains(widget)
				|| !widgetVisible(widget))
		{
			return;
		}
		if (widget != optionUniverse)
		{
			String text = widgetText(widget);
			if (!text.isEmpty() && !output.contains(text))
			{
				output.add(text);
			}
		}
		Widget[] children = widget.getChildren();
		if (children == null)
		{
			return;
		}
		for (Widget child : children)
		{
			collectDialoguePromptText(
					child,
					optionUniverse,
					excluded,
					visited,
					output);
			if (visited.size() >= DIALOGUE_WIDGET_SCAN_LIMIT)
			{
				return;
			}
		}
	}

	private String widgetText(Widget widget)
	{
		if (widget == null)
		{
			return "";
		}
		String text = cleanWidgetText(widget.getText());
		return text.isEmpty() ? cleanWidgetText(widget.getName()) : text;
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
		if (value.length() >= 2
				&& value.charAt(0) >= '1'
				&& value.charAt(0) <= '9'
				&& (value.charAt(1) == '.' || Character.isWhitespace(value.charAt(1))))
		{
			return Character.toString(value.charAt(0));
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
		ArrayDeque<Widget> pending = new ArrayDeque<>();
		Set<Widget> visited = Collections.newSetFromMap(new IdentityHashMap<>());
		for (Widget widget : widgets)
		{
			if (widgetVisible(widget))
			{
				pending.addLast(widget);
			}
		}
		while (!pending.isEmpty() && visited.size() < CLOSABLE_WIDGET_SCAN_LIMIT)
		{
			Widget widget = pending.removeFirst();
			if (!visited.add(widget) || !widgetVisible(widget))
			{
				continue;
			}
			if (widgetHasCloseAction(widget))
			{
				return widget;
			}
			Widget[] children = widget.getChildren();
			if (children == null)
			{
				continue;
			}
			for (Widget child : children)
			{
				if (widgetVisible(child) && !visited.contains(child))
				{
					pending.addLast(child);
				}
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
		try
		{
			Point canvasPoint = playerCanvasCenter(player.getCanvasTilePoly());
			if (canvasPoint != null)
			{
				localPlayer.canvasX = canvasPoint.getX();
				localPlayer.canvasY = canvasPoint.getY();
			}
		}
		catch (RuntimeException ignored)
		{
			// This optional projection must not invalidate otherwise-current player facts.
		}
		localPlayer.animation = player.getAnimation();
		localPlayer.poseAnimation = player.getPoseAnimation();
		localPlayer.combatLevel = player.getCombatLevel();

		snapshot.localPlayer = localPlayer;
	}

	static Point playerCanvasCenter(Polygon polygon)
	{
		if (polygon == null || polygon.npoints < 3)
		{
			return null;
		}
		Rectangle bounds = polygon.getBounds();
		if (bounds.width <= 0 || bounds.height <= 0)
		{
			return null;
		}
		return new Point(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
	}

	private void captureInventory(TickSnapshot snapshot)
	{
		ItemContainer inventory = client.getItemContainer(InventoryID.INVENTORY);
		InventoryCapture capture = selectInventoryCapture(
				itemContainerSlots(inventory, INVENTORY_SLOT_COUNT),
				inventory == null
						? visibleInventoryWidgetSlots(
								client.getWidget(InterfaceID.Inventory.ITEMS))
						: null,
				inventory == null
						? visibleInventoryWidgetSlots(
								client.getWidget(InterfaceID.Bankside.ITEMS))
						: null,
				inventory == null
						? visibleInventoryWidgetSlots(
								client.getWidget(InterfaceID.BankDepositbox.INVENTORY))
						: null);
		snapshot.inventory = capture == null ? null : capture.slots;
		snapshot.inventoryCaptureSource = capture == null ? null : capture.source;

		// Equipment is part of the same authoritative core fact as inventory.  A
		// missing or temporarily unreadable container stays explicitly unknown and
		// must never invalidate otherwise-current inventory evidence.
		try
		{
			snapshot.equipment = itemContainerSlots(
					client.getItemContainer(InventoryID.EQUIPMENT), 0);
		}
		catch (RuntimeException ignored)
		{
			snapshot.equipment = null;
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
			slots[i] = slot;
		}

		return slots;
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
		ClientThreadQueryScheduler scheduler = clientThreadQueryScheduler;
		if (clientThread == null || scheduler == null)
		{
			return withClientThreadQueryDiagnostics(
					tileProjectionFailurePayload("client thread unavailable"),
					ClientThreadQueryScheduler.unavailableDiagnostics("tile_projection", "UNAVAILABLE"));
		}
		List<Map<String, Object>> safeRequests = requests == null ? List.of() : requests;
		ClientThreadQueryScheduler.Submission<Map<String, Object>> submission = scheduler.submit(
				"tile_projection",
				clientThreadQueryKey("tile_projection", safeRequests),
				200L,
				() -> buildTileProjectionPayload(safeRequests));
		ClientThreadQueryScheduler.Result<Map<String, Object>> result = submission.await();
		Map<String, Object> payload = result.succeeded()
				? result.value()
				: tileProjectionFailurePayload(clientThreadQueryFailureReason("tile projection", result));
		return withClientThreadQueryDiagnostics(payload, scheduler.diagnostics(submission, result));
	}

	private Map<String, Object> pluginSnapshotWorldModelQuery(List<String> needs, Map<String, Object> request)
	{
		ClientThreadQueryScheduler scheduler = clientThreadQueryScheduler;
		List<String> safeNeeds = needs == null ? List.of() : List.copyOf(needs);
		Map<String, Object> safeRequest = request == null ? Map.of() : request;
		if (clientThread == null || scheduler == null)
		{
			return withClientThreadQueryDiagnostics(
					worldModelFailurePayload(safeNeeds, "client thread unavailable"),
					ClientThreadQueryScheduler.unavailableDiagnostics("world_model", "UNAVAILABLE"));
		}
		Map<String, Object> queryShape = new LinkedHashMap<>();
		queryShape.put("needs", safeNeeds);
		queryShape.put("request", safeRequest);
		ClientThreadQueryScheduler.Submission<Map<String, Object>> submission = scheduler.submit(
				"world_model",
				clientThreadQueryKey("world_model", queryShape),
				250L,
				() -> {
					Map<String, Object> identity = new LinkedHashMap<>();
					addSessionIdentity(identity);
					identity.put("geometryFrameId", currentGeometryFrameId());
					return worldModelCache.query(
							client,
							safeNeeds,
							safeRequest,
							tickId,
							clientTickId,
							identity);
				});
		ClientThreadQueryScheduler.Result<Map<String, Object>> result = submission.await();
		Map<String, Object> payload = result.succeeded()
				? result.value()
				: worldModelFailurePayload(safeNeeds, clientThreadQueryFailureReason("world model query", result));
		return withClientThreadQueryDiagnostics(payload, scheduler.diagnostics(submission, result));
	}

	private String clientThreadQueryKey(String lane, Object requestShape)
	{
		String serializedShape;
		try
		{
			serializedShape = gson == null ? String.valueOf(requestShape) : gson.toJson(requestShape);
		}
		catch (RuntimeException e)
		{
			serializedShape = String.valueOf(requestShape);
		}
		return lane
				+ "|" + String.valueOf(pluginInstanceId)
				+ "|" + tickId
				+ "|" + clientTickId
				+ "|" + serializedShape;
	}

	private String clientThreadQueryFailureReason(
			String label,
			ClientThreadQueryScheduler.Result<?> result)
	{
		switch (result.status())
		{
			case TIMED_OUT:
			case EXPIRED:
			case LATE:
				return label + " timed out";
			case SUPERSEDED:
				return label + " superseded by a newer request";
			case INTERRUPTED:
				return label + " interrupted";
			case CLOSED:
				return label + " scheduler unavailable";
			case FAILED:
				return label + " failed: " + result.failureSummary();
			default:
				return label + " unavailable: " + result.status().name().toLowerCase(Locale.ROOT);
		}
	}

	private Map<String, Object> withClientThreadQueryDiagnostics(
			Map<String, Object> payload,
			Map<String, Object> diagnostics)
	{
		Map<String, Object> enriched = payload == null
				? new LinkedHashMap<>()
				: new LinkedHashMap<>(payload);
		enriched.put("queryDiagnostics", diagnostics);
		return enriched;
	}

	private Map<String, Object> worldModelFailurePayload(List<String> needs, String reason)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		Map<String, Object> metadata = new LinkedHashMap<>();
		metadata.put("sourceTick", tickId);
		metadata.put("capturedAtUtc", Instant.now().toString());
		addSessionIdentity(metadata);
		metadata.put("geometryFrameId", "geometry-unavailable");
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
		payload.put("metadata", metadata);
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
		payload.put("sourceTick", tickId);
		payload.put("clientTick", clientTickId);
		payload.put("capturedAtUtc", Instant.now().toString());
		addSessionIdentity(payload);
		payload.put("geometryFrameId", "geometry-unavailable");
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
		payload.put("sourceTick", tickId);
		payload.put("clientTick", clientTickId);
		payload.put("capturedAtUtc", Instant.now().toString());
		addSessionIdentity(payload);
		payload.put("geometryFrameId", currentGeometryFrameId());
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
			addTileProjectionReadiness(payload, false, false, false, null, null);
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
			addTileProjectionReadiness(payload, false, false, false, null, null);
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
		payload.putAll(requestedTileCollisionSupport(localPoint, plane));

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
		boolean aimPointVisible = center != null && geometryIntersectsVisibleArea(center);
		addTileProjectionReadiness(payload, geometryAvailable, onScreen, aimPointVisible, polygon, center);
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

	private Map<String, Object> requestedTileCollisionSupport(LocalPoint target, int plane)
	{
		Map<String, Object> support = new LinkedHashMap<>();
		support.put("sceneSupported", true);
		Player player = client.getLocalPlayer();
		LocalPoint source = player == null ? null : player.getLocalLocation();
		CollisionData[] maps = client.getCollisionMaps();
		if (plane != client.getPlane() || source == null || maps == null
				|| plane < 0 || plane >= maps.length || maps[plane] == null
				|| maps[plane].getFlags() == null)
		{
			support.put("collisionSupported", false);
			support.put("shortcutClear", false);
			support.put("collisionReason", "collision_evidence_unavailable");
			return support;
		}
		int[][] flags = maps[plane].getFlags();
		boolean reachable = collisionPathReachable(
				flags,
				source.getSceneX(),
				source.getSceneY(),
				target.getSceneX(),
				target.getSceneY());
		boolean clear = collisionLineClear(
				flags,
				source.getSceneX(),
				source.getSceneY(),
				target.getSceneX(),
				target.getSceneY());
		support.put("collisionSupported", reachable);
		support.put("shortcutClear", clear);
		support.put("collisionReason", clear
				? "direct_path_clear"
				: (reachable ? "route_reachable_direct_path_blocked" : "route_blocked"));
		return support;
	}

	static boolean collisionPathReachable(
			int[][] flags,
			int startX,
			int startY,
			int targetX,
			int targetY)
	{
		if (!collisionCellAvailable(flags, startX, startY)
				|| !collisionCellAvailable(flags, targetX, targetY)
				|| collisionTileBlocked(flags[startX][startY])
				|| collisionTileBlocked(flags[targetX][targetY]))
		{
			return false;
		}
		if (startX == targetX && startY == targetY)
		{
			return true;
		}

		int margin = 16;
		int minX = Math.max(0, Math.min(startX, targetX) - margin);
		int maxX = Math.min(flags.length - 1, Math.max(startX, targetX) + margin);
		int minY = Math.max(0, Math.min(startY, targetY) - margin);
		int maxY = Math.max(startY, targetY) + margin;
		boolean[][] visited = new boolean[flags.length][];
		for (int x = 0; x < flags.length; x++)
		{
			visited[x] = new boolean[flags[x] == null ? 0 : flags[x].length];
		}
		ArrayDeque<int[]> pending = new ArrayDeque<>();
		pending.add(new int[]{startX, startY});
		visited[startX][startY] = true;
		while (!pending.isEmpty())
		{
			int[] current = pending.removeFirst();
			for (int dx = -1; dx <= 1; dx++)
			{
				for (int dy = -1; dy <= 1; dy++)
				{
					if (dx == 0 && dy == 0)
					{
						continue;
					}
					int nextX = current[0] + dx;
					int nextY = current[1] + dy;
					if (nextX < minX || nextX > maxX || nextY < minY || nextY > maxY
							|| !collisionCellAvailable(flags, nextX, nextY)
							|| visited[nextX][nextY]
							|| !collisionStepClear(flags, current[0], current[1], nextX, nextY))
					{
						continue;
					}
					if (nextX == targetX && nextY == targetY)
					{
						return true;
					}
					visited[nextX][nextY] = true;
					pending.addLast(new int[]{nextX, nextY});
				}
			}
		}
		return false;
	}

	static boolean collisionLineClear(
			int[][] flags,
			int startX,
			int startY,
			int targetX,
			int targetY)
	{
		if (!collisionCellAvailable(flags, startX, startY)
				|| !collisionCellAvailable(flags, targetX, targetY)
				|| collisionTileBlocked(flags[startX][startY]))
		{
			return false;
		}
		int x = startX;
		int y = startY;
		int distanceX = Math.abs(targetX - startX);
		int distanceY = Math.abs(targetY - startY);
		int stepX = Integer.compare(targetX, startX);
		int stepY = Integer.compare(targetY, startY);
		int error = distanceX - distanceY;
		while (x != targetX || y != targetY)
		{
			int twiceError = error * 2;
			int nextX = x;
			int nextY = y;
			if (twiceError > -distanceY)
			{
				error -= distanceY;
				nextX += stepX;
			}
			if (twiceError < distanceX)
			{
				error += distanceX;
				nextY += stepY;
			}
			if (!collisionStepClear(flags, x, y, nextX, nextY))
			{
				return false;
			}
			x = nextX;
			y = nextY;
		}
		return true;
	}

	private static boolean collisionStepClear(
			int[][] flags,
			int sourceX,
			int sourceY,
			int targetX,
			int targetY)
	{
		if (!collisionCellAvailable(flags, sourceX, sourceY)
				|| !collisionCellAvailable(flags, targetX, targetY))
		{
			return false;
		}
		int dx = targetX - sourceX;
		int dy = targetY - sourceY;
		if (Math.abs(dx) > 1 || Math.abs(dy) > 1 || (dx == 0 && dy == 0))
		{
			return false;
		}
		int source = flags[sourceX][sourceY];
		int target = flags[targetX][targetY];
		if (collisionTileBlocked(target))
		{
			return false;
		}
		if (dx > 0 && ((source & CollisionDataFlag.BLOCK_MOVEMENT_EAST) != 0
				|| (target & CollisionDataFlag.BLOCK_MOVEMENT_WEST) != 0))
		{
			return false;
		}
		if (dx < 0 && ((source & CollisionDataFlag.BLOCK_MOVEMENT_WEST) != 0
				|| (target & CollisionDataFlag.BLOCK_MOVEMENT_EAST) != 0))
		{
			return false;
		}
		if (dy > 0 && ((source & CollisionDataFlag.BLOCK_MOVEMENT_NORTH) != 0
				|| (target & CollisionDataFlag.BLOCK_MOVEMENT_SOUTH) != 0))
		{
			return false;
		}
		if (dy < 0 && ((source & CollisionDataFlag.BLOCK_MOVEMENT_SOUTH) != 0
				|| (target & CollisionDataFlag.BLOCK_MOVEMENT_NORTH) != 0))
		{
			return false;
		}
		if (dx != 0 && dy != 0)
		{
			int sourceDiagonal = dx > 0
					? (dy > 0 ? CollisionDataFlag.BLOCK_MOVEMENT_NORTH_EAST
							: CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_EAST)
					: (dy > 0 ? CollisionDataFlag.BLOCK_MOVEMENT_NORTH_WEST
							: CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_WEST);
			int targetDiagonal = dx > 0
					? (dy > 0 ? CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_WEST
							: CollisionDataFlag.BLOCK_MOVEMENT_NORTH_WEST)
					: (dy > 0 ? CollisionDataFlag.BLOCK_MOVEMENT_SOUTH_EAST
							: CollisionDataFlag.BLOCK_MOVEMENT_NORTH_EAST);
			if ((source & sourceDiagonal) != 0 || (target & targetDiagonal) != 0)
			{
				return false;
			}
			if (!collisionCellAvailable(flags, sourceX + dx, sourceY)
					|| !collisionCellAvailable(flags, sourceX, sourceY + dy)
					|| collisionTileBlocked(flags[sourceX + dx][sourceY])
					|| collisionTileBlocked(flags[sourceX][sourceY + dy]))
			{
				return false;
			}
		}
		return true;
	}

	private static boolean collisionTileBlocked(int flags)
	{
		return (flags & COLLISION_TILE_BLOCK_MASK) != 0;
	}

	private static boolean collisionCellAvailable(int[][] flags, int x, int y)
	{
		return flags != null && x >= 0 && x < flags.length
				&& flags[x] != null && y >= 0 && y < flags[x].length;
	}


	static boolean bankUiKnown(TickSnapshot snapshot)
	{
		return snapshot != null && snapshot.bankUi != null;
	}

	private Map<String, Object> boundsPayload(TickSnapshot.Bounds bounds)
	{
		if (bounds == null)
		{
			return null;
		}
		return Map.of("x", bounds.x, "y", bounds.y, "width", bounds.w, "height", bounds.h);
	}

	static void addTileProjectionReadiness(
			Map<String, Object> payload,
			boolean geometryAvailable,
			boolean onScreen,
			boolean aimPointVisible,
			int[][] polygon,
			TickSnapshot.CanvasPoint aimPoint)
	{
		boolean actionable = tileProjectionActionable(
				geometryAvailable,
				onScreen,
				aimPointVisible,
				polygon,
				aimPoint);
		payload.put("geometryAvailable", geometryAvailable);
		payload.put("onScreen", onScreen);
		payload.put("visible", onScreen);
		payload.put("actionable", actionable);
		payload.put("actionableByCanvas", actionable);
	}

	static boolean tileProjectionActionable(
			boolean geometryAvailable,
			boolean onScreen,
			boolean aimPointVisible,
			int[][] polygon,
			TickSnapshot.CanvasPoint aimPoint)
	{
		if (!geometryAvailable || !onScreen || !aimPointVisible || polygon == null || polygon.length < 3 || aimPoint == null)
		{
			return false;
		}

		Polygon shape = new Polygon();
		for (int[] point : polygon)
		{
			if (point == null || point.length < 2)
			{
				return false;
			}
			shape.addPoint(point[0], point[1]);
		}
		return shape.contains(aimPoint.x, aimPoint.y);
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

	private static String hashName(String name)
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
		return value instanceof Number ? ((Number) value).intValue() : fallback;
	}

	private String truncate(String value, int maxLength)
	{
		if (value == null)
		{
			return "";
		}

		return value.length() <= maxLength ? value : value.substring(0, maxLength);
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
		return clientTickPayload(sourceEvent, currentCameraPosePayload());
	}

	private Map<String, Object> clientTickPayload(
			String sourceEvent,
			Map<String, Object> cameraPose)
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
		payload.put("cameraPose", copyCameraPose(cameraPose));
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
			for (int i = entries.length - 1;
					i >= 0 && topEntries.size() < HOT_MENU_ENTRY_LIMIT;
					i--)
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
		payload.put("cameraPose", copyCameraPose(currentCameraPosePayload()));
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
		long monotonicTimeNanos = System.nanoTime();
		Map<String, Object> cameraPose = currentCameraPosePayload();
		payload.put("schema", "plugin_menu_option_clicked.v1");
		payload.put("sampleSource", "MenuOptionClicked");
		payload.put("sourceEvent", "MenuOptionClicked");
		payload.put("clientTick", clientTickId);
		payload.put("gameTickAtSample", tickId);
		payload.put("timestampUtc", Instant.now().toString());
		payload.put("wallTimeMillis", System.currentTimeMillis());
		payload.put("monotonicTimeNanos", monotonicTimeNanos);
		payload.put("gameState", currentGameStateText());
		addSessionIdentity(payload);
		addMouseCanvasPosition(payload);
		payload.put("cameraPose", copyCameraPose(cameraPose));
		payload.put("geometryFrameId", cameraPose == null ? null : cameraPose.get("geometryFrameId"));
		payload.put("clickEvidenceId", (pluginInstanceId == null ? "plugin-unidentified" : pluginInstanceId)
				+ ":" + clientTickId + ":" + monotonicTimeNanos);
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
			payload.put("resolvedTarget", resolvedClickTarget(event));
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
		return failClosedMenuOpen(currentMenuOpen());
	}

	private Boolean currentMenuOpen()
	{
		if (client == null)
		{
			return null;
		}
		try
		{
			return client.isMenuOpen();
		}
		catch (Exception e)
		{
			return null;
		}
	}

	private TickSnapshot.InventorySlot[] visibleInventoryWidgetSlots(Widget inventoryItems)
	{
		if (!widgetVisible(inventoryItems) || !inventoryItems.isIf3())
		{
			return null;
		}
		Widget[] children = inventoryItems.getDynamicChildren();
		if (children == null || children.length != INVENTORY_SLOT_COUNT)
		{
			return null;
		}
		int[] slotIndexes = new int[children.length];
		int[] itemIds = new int[children.length];
		int[] quantities = new int[children.length];
		for (int i = 0; i < children.length; i++)
		{
			Widget child = children[i];
			if (child == null)
			{
				return null;
			}
			slotIndexes[i] = child.getIndex();
			itemIds[i] = child.getItemId();
			quantities[i] = child.getItemQuantity();
		}
		return inventorySlotsFromVisibleWidgetEvidence(
				true, children.length, slotIndexes, itemIds, quantities);
	}

	static TickSnapshot.InventorySlot[] inventorySlotsFromVisibleWidgetEvidence(
			boolean visible,
			int directChildCount,
			int[] slotIndexes,
			int[] itemIds,
			int[] quantities)
	{
		if (!visible
				|| directChildCount != INVENTORY_SLOT_COUNT
				|| slotIndexes == null
				|| itemIds == null
				|| quantities == null
				|| slotIndexes.length != INVENTORY_SLOT_COUNT
				|| itemIds.length != INVENTORY_SLOT_COUNT
				|| quantities.length != INVENTORY_SLOT_COUNT)
		{
			return null;
		}
		TickSnapshot.InventorySlot[] slots = new TickSnapshot.InventorySlot[INVENTORY_SLOT_COUNT];
		boolean[] seen = new boolean[INVENTORY_SLOT_COUNT];
		for (int i = 0; i < itemIds.length; i++)
		{
			int slotIndex = slotIndexes[i];
			int itemId = itemIds[i];
			int quantity = quantities[i];
			boolean empty = (itemId == -1 && quantity == 0)
					|| (itemId == EMPTY_INVENTORY_WIDGET_ITEM_ID && quantity == 1);
			boolean filled = itemId > 0
					&& itemId != EMPTY_INVENTORY_WIDGET_ITEM_ID
					&& quantity > 0;
			if (slotIndex < 0
					|| slotIndex >= INVENTORY_SLOT_COUNT
					|| seen[slotIndex]
					|| (!empty && !filled))
			{
				return null;
			}
			seen[slotIndex] = true;
			TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
			slot.slot = slotIndex;
			slot.itemId = empty ? -1 : itemId;
			slot.quantity = empty ? 0 : quantity;
			slots[slotIndex] = slot;
		}
		for (int i = 0; i < INVENTORY_SLOT_COUNT; i++)
		{
			if (!seen[i] || slots[i] == null)
			{
				return null;
			}
		}
		return slots;
	}

	static final class InventoryCapture
	{
		final TickSnapshot.InventorySlot[] slots;
		final String source;

		InventoryCapture(TickSnapshot.InventorySlot[] slots, String source)
		{
			this.slots = slots;
			this.source = source;
		}
	}

	static InventoryCapture selectInventoryCapture(
			TickSnapshot.InventorySlot[] itemContainerSlots,
			TickSnapshot.InventorySlot[] inventoryWidgetSlots,
			TickSnapshot.InventorySlot[] bankSideWidgetSlots,
			TickSnapshot.InventorySlot[] depositInventoryWidgetSlots)
	{
		if (itemContainerSlots != null)
		{
			return new InventoryCapture(itemContainerSlots, "item_container");
		}
		if (inventoryWidgetSlots != null)
		{
			return new InventoryCapture(inventoryWidgetSlots, "inventory_widget");
		}
		if (bankSideWidgetSlots != null)
		{
			return new InventoryCapture(bankSideWidgetSlots, "bank_side_widget");
		}
		if (depositInventoryWidgetSlots != null)
		{
			return new InventoryCapture(
					depositInventoryWidgetSlots, "deposit_inventory_widget");
		}
		return null;
	}

	static final class DialogueContinueSurface
	{
		private final Widget textWidget;
		private final Widget continueWidget;

		DialogueContinueSurface(Widget textWidget, Widget continueWidget)
		{
			this.textWidget = textWidget;
			this.continueWidget = continueWidget;
		}
	}

	static boolean shouldRefreshOpenMenu(Boolean menuOpen)
	{
		return Boolean.TRUE.equals(menuOpen);
	}

	static boolean failClosedMenuOpen(Boolean menuOpen)
	{
		return menuOpen == null || menuOpen;
	}

	private void addSessionIdentity(Map<String, Object> payload)
	{
		payload.put("sessionId", pluginInstanceId);
		payload.put("clientProcessId", ProcessHandle.current().pid());
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

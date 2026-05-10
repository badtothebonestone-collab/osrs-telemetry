package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.awt.Canvas;
import java.awt.Dimension;
import java.awt.Image;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.Shape;
import java.awt.geom.PathIterator;
import java.awt.image.BufferedImage;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.inject.Inject;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Actor;
import net.runelite.api.Client;
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
import net.runelite.api.events.NpcChanged;
import net.runelite.api.events.NpcDespawned;
import net.runelite.api.events.NpcSpawned;
import net.runelite.api.events.OverheadTextChanged;
import net.runelite.api.events.PlayerChanged;
import net.runelite.api.events.PlayerDespawned;
import net.runelite.api.events.PlayerSpawned;
import net.runelite.api.events.ProjectileMoved;
import net.runelite.api.events.StatChanged;
import net.runelite.api.events.VarClientIntChanged;
import net.runelite.api.events.VarClientStrChanged;
import net.runelite.api.events.VarbitChanged;
import net.runelite.api.events.WallObjectDespawned;
import net.runelite.api.events.WallObjectSpawned;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.ui.DrawManager;
import net.runelite.client.util.ImageCapture;
import net.runelite.client.util.ImageUtil;
import net.runelite.api.events.WidgetClosed;
import net.runelite.api.events.WidgetLoaded;
import net.runelite.api.widgets.Widget;

@Slf4j
@PluginDescriptor(
		name = "Telemetry Collector",
		description = "Read-only telemetry logger for external analysis",
		tags = {"telemetry", "data", "logger"}
)
public class TelemetryPlugin extends Plugin
{
	private static final int MAX_GROUND_ITEMS = 250;
	private static final String PACKET_BASELINE = "live_baseline_packet.v1";
	private static final String PACKET_SCENE_DELTA = "live_scene_delta_packet.v1";
	private static final String PACKET_PROJECTION = "live_projection_packet.v1";
	private static final String PACKET_INVENTORY = "live_inventory_packet.v1";
	private static final String PACKET_ACTIVITY = "live_activity_packet.v1";
	private static final String PACKET_WRITER_HEALTH = "live_writer_health_packet.v1";

	@Inject
	private Client client;

	@Inject
	private Gson gson;

	@Inject
	private TelemetryConfig config;

	@Inject
	private DrawManager drawManager;

	@Inject
	private ImageCapture imageCapture;

	private TelemetryWriter writer;
	private long tickId = 0;
	private long eventSeq = 0;
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

	@Provides
	TelemetryConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(TelemetryConfig.class);
	}

	@Override
	protected void startUp() throws Exception
	{
		knownItemIds.clear();
		knownNpcIds.clear();
		knownObjectIds.clear();
		itemNameCache.clear();
		npcNameCache.clear();
		objectNameCache.clear();
		clearSceneIndex("startup");

		writer = new TelemetryWriter(
				config.outputDirectory(),
				gson,
				config.maxSegmentMb(),
				config.retentionEnabled(),
				config.maxTelemetryGb(),
				config.cleanupIntervalSeconds(),
				config.preservePinnedSessions(),
				config.allowDeletingClosedSegmentsFromActiveSession(),
				config.screenshotEveryTicks(),
				config.screenshotFormat(),
				config.jpegQuality(),
				config.maxFrameStorageMb(),
				config.frameCleanupIntervalSeconds(),
				config.deleteOldFrames(),
				config.maxFrameQueueSize(),
				config.frameCaptureMode(),
				config.allowScreenRectangleFallback(),
				config.emitCompactLivePackets(),
				config.compactLiveSegmentMb(),
				config.compactLiveRetentionTicks(),
				Math.max(0L, config.compactLiveRetentionMb()) * 1024L * 1024L,
				config.compactLiveRetentionSegments(),
				config.compactLiveQueueSize());
		writer.start();

		log.info("Telemetry Collector started");
	}

	@Override
	protected void shutDown() throws Exception
	{
		if (writer != null)
		{
			writer.close();
			writer = null;
		}
		clearSceneIndex("shutdown");

		log.info("Telemetry Collector stopped");
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

		if (event.getGameState() == GameState.LOADING
				|| event.getGameState() == GameState.LOGIN_SCREEN
				|| event.getGameState() == GameState.HOPPING)
		{
			clearSceneIndex("gameState:" + event.getGameState());
		}
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
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		despawnSceneObjectFromEvent("GAME_OBJECT", event.getGameObject(), event.getGameObject() == null ? -1 : event.getGameObject().getOrientation());
	}

	@Subscribe
	public void onWallObjectSpawned(WallObjectSpawned event)
	{
		indexSceneObjectFromEvent("WALL_OBJECT", event.getWallObject(), event.getWallObject() == null ? -1 : event.getWallObject().getOrientationA());
	}

	@Subscribe
	public void onWallObjectDespawned(WallObjectDespawned event)
	{
		despawnSceneObjectFromEvent("WALL_OBJECT", event.getWallObject(), event.getWallObject() == null ? -1 : event.getWallObject().getOrientationA());
	}

	@Subscribe
	public void onDecorativeObjectSpawned(DecorativeObjectSpawned event)
	{
		indexSceneObjectFromEvent("DECORATIVE_OBJECT", event.getDecorativeObject(), -1);
	}

	@Subscribe
	public void onDecorativeObjectDespawned(DecorativeObjectDespawned event)
	{
		despawnSceneObjectFromEvent("DECORATIVE_OBJECT", event.getDecorativeObject(), -1);
	}

	@Subscribe
	public void onGroundObjectSpawned(GroundObjectSpawned event)
	{
		indexSceneObjectFromEvent("GROUND_OBJECT", event.getGroundObject(), -1);
	}

	@Subscribe
	public void onGroundObjectDespawned(GroundObjectDespawned event)
	{
		despawnSceneObjectFromEvent("GROUND_OBJECT", event.getGroundObject(), -1);
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("skill", String.valueOf(event.getSkill()));
		payload.put("xp", event.getXp());
		payload.put("level", event.getLevel());
		payload.put("boostedLevel", event.getBoostedLevel());

		logEvent("StatChanged", payload);
	}

	@Subscribe
	public void onMenuOpened(MenuOpened event)
	{
		logEvent("MenuOpened", menuOpenedPayload(event));
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

		logEvent("AnimationChanged", payload);
	}

	@Subscribe
	public void onInteractingChanged(InteractingChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("source", actorPayload(event.getSource()));
		payload.put("target", actorPayload(event.getTarget()));

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
		logEvent("GraphicsObjectCreated", graphicsObjectPayload(event.getGraphicsObject()));
	}

	@Subscribe
	public void onOverheadTextChanged(OverheadTextChanged event)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("actor", actorPayload(event.getActor()));
		payload.put("text", truncate(event.getOverheadText(), 256));

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
			logEvent("NpcDeath", actorPayload(event.getActor()));
		}
	}

	private boolean captureFrame(TickSnapshot snapshot, List<String> captureErrors, TelemetryWriter currentWriter)
	{
		if (!config.captureScreenshots())
		{
			snapshot.frameCaptureStatus = "DISABLED";
			return false;
		}

		int interval = config.screenshotEveryTicks();

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
			currentWriter.enqueueTick(gson.toJson(snapshot));
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

		if (compactPacketTypeEnabled("inventory"))
		{
			currentWriter.enqueueLivePacket(PACKET_INVENTORY, snapshot.tickId, snapshot.timestampUtc, inventoryPayload(snapshot));
		}

		if (compactPacketTypeEnabled("activity"))
		{
			currentWriter.enqueueLivePacket(PACKET_ACTIVITY, snapshot.tickId, snapshot.timestampUtc, activityPayload(snapshot));
		}

		if (compactPacketTypeEnabled("writerHealth"))
		{
			currentWriter.enqueueLivePacket(PACKET_WRITER_HEALTH, snapshot.tickId, snapshot.timestampUtc, writerHealthPayload(currentWriter));
		}
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
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("sceneProjectionSummary", snapshot.sceneProjectionSummary);
		payload.put("projectionStateHash", snapshot.sceneProjectionSummary == null ? null : snapshot.sceneProjectionSummary.projectionStateHash);
		payload.put("refreshMode", snapshot.sceneProjectionSummary == null ? null : snapshot.sceneProjectionSummary.projectionRefreshMode);
		payload.put("visibleObjectRefs", compactSceneObjects(snapshot.visibleSceneObjectRefs, true));
		return payload;
	}

	private List<Map<String, Object>> compactSceneObjects(TickSnapshot.SceneObjectSnapshot[] objects, boolean includeProjection)
	{
		List<Map<String, Object>> compact = new ArrayList<>();

		if (objects == null)
		{
			return compact;
		}

		for (TickSnapshot.SceneObjectSnapshot object : objects)
		{
			if (object != null)
			{
				compact.add(compactSceneObject(object, includeProjection));
			}
		}

		return compact;
	}

	private Map<String, Object> compactSceneObject(TickSnapshot.SceneObjectSnapshot object, boolean includeProjection)
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

			if (config.compactLiveIncludeHeavyGeometry())
			{
				compact.put("canvasTilePolygon", object.canvasTilePolygon);
				compact.put("clickboxPolygon", object.clickboxPolygon);
				compact.put("convexHullPolygon", object.convexHullPolygon);
			}
		}

		return compact;
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
		return payload;
	}

	private Map<String, Object> itemContainerSnapshot(TickSnapshot.InventorySlot[] slots)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		List<Map<String, Object>> items = new ArrayList<>();
		int freeSlots = 0;
		int filledSlots = 0;
		int itemCount = 0;
		StringBuilder signature = new StringBuilder();

		if (slots == null)
		{
			payload.put("known", false);
			return payload;
		}

		for (TickSnapshot.InventorySlot slot : slots)
		{
			if (slot == null || slot.itemId <= 0 || slot.quantity <= 0)
			{
				freeSlots++;
				continue;
			}

			filledSlots++;
			itemCount += slot.quantity;
			signature.append(slot.slot).append(':').append(slot.itemId).append(':').append(slot.quantity).append(';');

			Map<String, Object> item = new LinkedHashMap<>();
			item.put("slot", slot.slot);
			item.put("itemId", slot.itemId);
			item.put("quantity", slot.quantity);
			items.add(item);
		}

		payload.put("known", true);
		payload.put("freeSlots", freeSlots);
		payload.put("filledSlots", filledSlots);
		payload.put("itemCount", itemCount);
		payload.put("signature", hashName(signature.toString()));
		payload.put("items", items);
		return payload;
	}

	private Map<String, Object> activityPayload(TickSnapshot snapshot)
	{
		Map<String, Object> payload = new LinkedHashMap<>();

		if (snapshot.localPlayer != null)
		{
			payload.put("animation", snapshot.localPlayer.animation);
			payload.put("poseAnimation", snapshot.localPlayer.poseAnimation);
			payload.put("combatLevel", snapshot.localPlayer.combatLevel);
		}

		if (snapshot.status != null)
		{
			payload.put("interacting", interactingPayload(snapshot.status));
			payload.put("runEnergyRaw", snapshot.status.runEnergyRaw);
			payload.put("runEnergyPercent", snapshot.status.runEnergyPercent);
			payload.put("hitpointsBoosted", snapshot.status.hitpointsBoosted);
			payload.put("hitpointsReal", snapshot.status.hitpointsReal);
		}

		payload.put("movementKnown", false);
		payload.put("interpretation", "observed_facts_only");
		return payload;
	}

	private Map<String, Object> writerHealthPayload(TelemetryWriter currentWriter)
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("rawWriterQueueDepth", currentWriter.getQueueSize());
		payload.put("droppedRawRecords", currentWriter.getDroppedRecords());
		payload.put("droppedFrameCount", currentWriter.getDroppedFrameCount());
		payload.put("compactLiveEnabled", currentWriter.isCompactLivePacketsEnabled());
		payload.put("compactLiveQueueDepth", currentWriter.getLivePacketQueueDepth());
		payload.put("livePacketsWritten", currentWriter.getLivePacketsWritten());
		payload.put("livePacketsDropped", currentWriter.getLivePacketsDropped());
		payload.put("livePacketWriteErrors", currentWriter.getLivePacketWriteErrors());
		payload.put("livePacketLastWriteMillis", currentWriter.getLivePacketLastWriteMillis());
		payload.put("livePacketSegmentCount", currentWriter.getLivePacketSegmentCount());
		payload.put("livePacketTotalBytes", currentWriter.getLivePacketTotalBytes());
		payload.put("livePacketSegmentsPruned", currentWriter.getLivePacketSegmentsPruned());
		payload.put("livePacketRetentionBytes", Math.max(0L, config.compactLiveRetentionMb()) * 1024L * 1024L);
		payload.put("livePacketRetentionSegments", config.compactLiveRetentionSegments());
		payload.put("livePacketActiveSegment", currentWriter.getLivePacketActiveSegment());
		payload.put("rawRecordingEnabled", true);
		return payload;
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
		snapshot.inventory = new TickSnapshot.InventorySlot[items.length];

		for (int i = 0; i < items.length; i++)
		{
			Item item = items[i];

			TickSnapshot.InventorySlot slot = new TickSnapshot.InventorySlot();
			slot.slot = i;
			slot.itemId = item == null ? -1 : item.getId();
			slot.quantity = item == null ? 0 : item.getQuantity();
			rememberItem(slot.itemId);

			snapshot.inventory[i] = slot;
		}
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

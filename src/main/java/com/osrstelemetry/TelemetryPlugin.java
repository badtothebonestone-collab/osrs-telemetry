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
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GraphicsObjectCreated;
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
	private static final int SCENE_CAPTURE_RADIUS = 12;
	private static final int MAX_SCENE_OBJECTS = 250;
	private static final int MAX_GROUND_ITEMS = 250;

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
				config.allowScreenRectangleFallback());
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

		log.info("Telemetry Collector stopped");
	}

	@Subscribe
	public void onGameTick(GameTick event)
	{
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
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		logEvent("ItemContainerChanged", itemContainerPayload(event));
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
			currentWriter.enqueueTick(gson.toJson(snapshot));
		}
		catch (Exception e)
		{
			log.warn("Failed to enqueue tick telemetry", e);
		}
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

		int minSceneX = Math.max(0, centerSceneX - SCENE_CAPTURE_RADIUS);
		int maxSceneX = Math.min(planeTiles.length - 1, centerSceneX + SCENE_CAPTURE_RADIUS);

		for (int sceneX = minSceneX; sceneX <= maxSceneX; sceneX++)
		{
			Tile[] column = planeTiles[sceneX];

			if (column == null)
			{
				continue;
			}

			int minSceneY = Math.max(0, centerSceneY - SCENE_CAPTURE_RADIUS);
			int maxSceneY = Math.min(column.length - 1, centerSceneY + SCENE_CAPTURE_RADIUS);

			for (int sceneY = minSceneY; sceneY <= maxSceneY; sceneY++)
			{
				Tile tile = column[sceneY];

				if (tile == null)
				{
					continue;
				}

				captureTileObjects(tile, sceneObjects);
				captureTileGroundItems(tile, groundItems);

				if (sceneObjects.size() >= MAX_SCENE_OBJECTS && groundItems.size() >= MAX_GROUND_ITEMS)
				{
					break;
				}
			}
		}

		snapshot.sceneObjects = sceneObjects.toArray(new TickSnapshot.SceneObjectSnapshot[0]);
		snapshot.groundItems = groundItems.toArray(new TickSnapshot.GroundItemSnapshot[0]);
	}

	private void captureTileObjects(Tile tile, List<TickSnapshot.SceneObjectSnapshot> sceneObjects)
	{
		if (sceneObjects.size() >= MAX_SCENE_OBJECTS)
		{
			return;
		}

		WallObject wallObject = tile.getWallObject();

		addSceneObject(sceneObjects, "WALL_OBJECT", wallObject, wallObject == null ? -1 : wallObject.getOrientationA());
		addSceneObject(sceneObjects, "GROUND_OBJECT", tile.getGroundObject(), -1);
		addSceneObject(sceneObjects, "DECORATIVE_OBJECT", tile.getDecorativeObject(), -1);

		GameObject[] gameObjects = tile.getGameObjects();

		if (gameObjects == null)
		{
			return;
		}

		for (GameObject gameObject : gameObjects)
		{
			if (sceneObjects.size() >= MAX_SCENE_OBJECTS)
			{
				return;
			}

			addSceneObject(sceneObjects, "GAME_OBJECT", gameObject, gameObject == null ? -1 : gameObject.getOrientation());
		}
	}

	private void addSceneObject(List<TickSnapshot.SceneObjectSnapshot> sceneObjects, String kind, TileObject object, int orientation)
	{
		if (object == null || sceneObjects.size() >= MAX_SCENE_OBJECTS)
		{
			return;
		}

		WorldPoint worldLocation = object.getWorldLocation();
		Point sceneLocation = worldLocationToSceneLocation(object);
		TickSnapshot.SceneObjectSnapshot snapshot = new TickSnapshot.SceneObjectSnapshot();
		snapshot.kind = kind;
		snapshot.id = object.getId();
		DefinitionName objectName = objectNameLookup(snapshot.id);
		snapshot.objectName = objectName.name;
		snapshot.objectNameSource = objectName.source;
		rememberObject(snapshot.id);
		snapshot.orientation = orientation;
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
			snapshot.plane = object.getPlane();
		}

		applySceneObjectProjection(snapshot, object);
		sceneObjects.add(snapshot);
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

	private void captureTileGroundItems(Tile tile, List<TickSnapshot.GroundItemSnapshot> groundItems)
	{
		if (groundItems.size() >= MAX_GROUND_ITEMS)
		{
			return;
		}

		List<TileItem> tileItems = tile.getGroundItems();

		if (tileItems == null || tileItems.isEmpty())
		{
			return;
		}

		WorldPoint worldLocation = tile.getWorldLocation();
		Point sceneLocation = tile.getSceneLocation();

		for (TileItem item : tileItems)
		{
			if (item == null)
			{
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

			if (groundItems.size() >= MAX_GROUND_ITEMS)
			{
				return;
			}
		}
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
}

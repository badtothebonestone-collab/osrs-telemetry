package com.osrstelemetry;

import java.util.ArrayList;
import com.google.gson.Gson;
import com.google.inject.Provides;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.inject.Inject;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.NPC;
import net.runelite.api.Player;
import net.runelite.api.Skill;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.api.events.StatChanged;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
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
	@Inject
	private Client client;

	@Inject
	private Gson gson;

	@Inject
	private TelemetryConfig config;

	private TelemetryWriter writer;
	private long tickId = 0;
	private long eventSeq = 0;

	@Provides
	TelemetryConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(TelemetryConfig.class);
	}

	@Override
	protected void startUp() throws Exception
	{
		writer = new TelemetryWriter(config.outputDirectory());
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
				safeCapture(captureErrors, "localPlayer", () -> captureLocalPlayer(snapshot));
				safeCapture(captureErrors, "inventory", () -> captureInventory(snapshot));
				safeCapture(captureErrors, "equipment", () -> captureEquipment(snapshot));
				safeCapture(captureErrors, "skills", () -> captureSkills(snapshot));
				safeCapture(captureErrors, "npcs", () -> captureNpcs(snapshot));
				safeCapture(captureErrors, "players", () -> capturePlayers(snapshot));
				safeCapture(captureErrors, "widgets", () -> captureWidgets(snapshot));
			}
		}
		finally
		{
			snapshot.captureErrors = captureErrors.toArray(new String[0]);
			snapshot.writerQueueSize = currentWriter.getQueueSize();
			snapshot.writerDroppedRecords = currentWriter.getDroppedRecords();
			snapshot.captureErrors = captureErrors.toArray(new String[0]);

			try
			{
				currentWriter.enqueueTick(gson.toJson(snapshot));
			}
			catch (Exception e)
			{
				log.warn("Failed to enqueue tick telemetry", e);
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
			npcSnapshot.index = npc.getIndex();
			npcSnapshot.id = npc.getId();
			npcSnapshot.name = npc.getName();
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

		return playerSnapshot;
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

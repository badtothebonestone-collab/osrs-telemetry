package com.osrstelemetry;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.time.Instant;
import javax.inject.Inject;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Client;
import net.runelite.api.GameState;
import net.runelite.api.InventoryID;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.Player;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.GameTick;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;

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
		if (!config.enabled() || writer == null)
		{
			return;
		}

		tickId++;

		TickSnapshot snapshot = new TickSnapshot();
		snapshot.schemaVersion = "0.1.0";
		snapshot.tickId = tickId;
		snapshot.timestampUtc = Instant.now().toString();
		snapshot.gameState = String.valueOf(client.getGameState());

		if (client.getGameState() == GameState.LOGGED_IN)
		{
			captureLocalPlayer(snapshot);
			captureInventory(snapshot);
		}

		writer.enqueue(gson.toJson(snapshot));
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
		localPlayer.worldX = wp.getX();
		localPlayer.worldY = wp.getY();
		localPlayer.plane = wp.getPlane();
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
			slot.itemId = item.getId();
			slot.quantity = item.getQuantity();

			snapshot.inventory[i] = slot;
		}
	}
}

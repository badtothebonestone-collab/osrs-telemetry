package com.osrstelemetry;

public class TickSnapshot
{
    public String schemaVersion;
    public long tickId;
    public String timestampUtc;
    public String gameState;

    public LocalPlayer localPlayer;
    public InventorySlot[] inventory;
    public InventorySlot[] equipment;
    public SkillSnapshot[] skills;
    public NpcSnapshot[] npcs;
    public PlayerSnapshot[] players;
    public WidgetSnapshot[] widgets;
    public SceneObjectSnapshot[] sceneObjects;
    public GroundItemSnapshot[] groundItems;
    public StatusSnapshot status;
    public ActivePrayerSnapshot[] activePrayers;
    public String framePath;
    public String frameCaptureStatus;
    public String[] captureErrors;
    public int writerQueueSize;
    public long writerDroppedRecords;

    public static class WidgetSnapshot
    {
        public int index;
        public int id;
        public int type;
        public boolean hidden;
        public String text;
        public String name;
        public int x;
        public int y;
        public int width;
        public int height;
        public int childCount;
    }
    public static class LocalPlayer
    {
        public int worldX;
        public int worldY;
        public int plane;
        public int animation;
        public int poseAnimation;
        public int combatLevel;
    }

    public static class InventorySlot
    {
        public int slot;
        public int itemId;
        public int quantity;
    }
    public static class SkillSnapshot
    {
        public String name;
        public int realLevel;
        public int boostedLevel;
        public int xp;
    }
    public static class NpcSnapshot
    {
        public int index;
        public int id;
        public String name;
        public int combatLevel;
        public int worldX;
        public int worldY;
        public int plane;
        public int animation;
        public int poseAnimation;
        public int orientation;
        public int healthRatio;
        public int healthScale;
        public boolean dead;
    }

    public static class PlayerSnapshot
    {
        public int index;
        public String nameHash;
        public int combatLevel;
        public int worldX;
        public int worldY;
        public int plane;
        public int animation;
        public int poseAnimation;
        public int orientation;
        public int healthRatio;
        public int healthScale;
    }

    public static class SceneObjectSnapshot
    {
        public String kind;
        public int id;
        public int worldX;
        public int worldY;
        public int plane;
        public int orientation;
        public int sceneX;
        public int sceneY;
    }

    public static class GroundItemSnapshot
    {
        public int id;
        public int quantity;
        public int worldX;
        public int worldY;
        public int plane;
        public int sceneX;
        public int sceneY;
    }

    public static class StatusSnapshot
    {
        public int runEnergyRaw;
        public double runEnergyPercent;
        public int weight;
        public int hitpointsBoosted;
        public int hitpointsReal;
        public int prayerBoosted;
        public int prayerReal;
        public int localHealthRatio;
        public int localHealthScale;
        public String interactingType;
        public int interactingIndex;
        public int interactingId;
        public String interactingName;
        public int interactingWorldX;
        public int interactingWorldY;
        public int interactingPlane;
    }

    public static class ActivePrayerSnapshot
    {
        public String name;
        public int varbit;
        public boolean active;
    }
}

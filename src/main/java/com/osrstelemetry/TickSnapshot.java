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
}

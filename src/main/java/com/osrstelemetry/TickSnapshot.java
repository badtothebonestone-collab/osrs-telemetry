package com.osrstelemetry;

public class TickSnapshot
{
    public String schemaVersion;
    public long tickId;
    public String timestampUtc;
    public String gameState;

    public LocalPlayer localPlayer;
    public InventorySlot[] inventory;

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
}

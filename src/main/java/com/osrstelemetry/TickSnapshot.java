package com.osrstelemetry;

public class TickSnapshot
{
    public String schemaVersion;
    public long tickId;
    public String timestampUtc;
    public String gameState;
    public Integer cameraX;
    public Integer cameraY;
    public Integer cameraZ;
    public Integer cameraYaw;
    public Integer cameraPitch;
    public Integer viewportWidth;
    public Integer viewportHeight;
    public Integer viewportXOffset;
    public Integer viewportYOffset;
    public Integer canvasWidth;
    public Integer canvasHeight;

    public LocalPlayer localPlayer;
    public InventorySlot[] inventory;
    public InventorySlot[] equipment;
    public SkillSnapshot[] skills;
    public NpcSnapshot[] npcs;
    public PlayerSnapshot[] players;
    public WidgetSnapshot[] widgets;
    public SceneCaptureSummary sceneCaptureSummary;
    public SceneIndexSummary sceneIndexSummary;
    public SceneProjectionSummary sceneProjectionSummary;
    public SceneObjectDeltas sceneObjectDeltas;
    public SceneObjectSnapshot[] visibleSceneObjectRefs;
    public SceneObjectSnapshot[] sceneObjects;
    public GroundItemSnapshot[] groundItems;
    public StatusSnapshot status;
    public BankUiSnapshot bankUi;
    public ActivePrayerSnapshot[] activePrayers;
    public String framePath;
    public String frameCaptureStatus;
    public String frameCaptureSource;
    public String frameCaptureWarning;
    public String[] captureErrors;
    public int writerQueueSize;
    public long writerDroppedRecords;
    public Long sceneCaptureDurationMillis;
    public Long snapshotBuildDurationMillis;

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

    public static class BankUiSnapshot
    {
        public Integer topLevelInterfaceId;
        public Boolean bankOpen;
        public Boolean bankPinOpen;
        public Boolean bankRootVisible;
        public Boolean bankContainerVisible;
        public Boolean bankInventoryVisible;
        public Boolean depositInventoryButtonVisible;
        public Boolean closeButtonVisible;
        public Boolean bankCloseButtonVisible;
        public Boolean keyboardClosePossible;
        public WidgetSnapshot bankRootWidget;
        public WidgetSnapshot bankContainerWidget;
        public WidgetSnapshot bankInventoryWidget;
        public WidgetSnapshot depositInventoryButtonWidget;
        public WidgetSnapshot closeButtonWidget;
        public WidgetSnapshot bankPinWidget;
        public InventorySlot[] bankItems;
    }

    public static class LocalPlayer
    {
        public int worldX;
        public int worldY;
        public int plane;
        public Integer localX;
        public Integer localY;
        public Integer sceneX;
        public Integer sceneY;
        public int animation;
        public int poseAnimation;
        public int combatLevel;
    }

    public static class CanvasPoint
    {
        public int x;
        public int y;
    }

    public static class Bounds
    {
        public int x;
        public int y;
        public int w;
        public int h;
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
        public String npcName;
        public String npcNameSource;
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
        public Integer localX;
        public Integer localY;
        public CanvasPoint canvasPoint;
        public Bounds clickboxBounds;
        public Bounds convexHullBounds;
        public boolean onScreen;
        public boolean geometryAvailable;
        public String geometryWarning;
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
        public Integer localX;
        public Integer localY;
        public CanvasPoint canvasPoint;
        public Bounds clickboxBounds;
        public Bounds convexHullBounds;
        public boolean onScreen;
        public boolean geometryAvailable;
        public String geometryWarning;
    }

    public static class SceneCaptureSummary
    {
        public String sceneCaptureMode;
        public boolean fullCurrentPlaneScan;
        public int configuredRadius;
        public int configuredMaxSceneObjects;
        public int scanRadius;
        public int maxSceneObjects;
        public int maxGroundItems;
        public int scannedPlane;
        public int scannedTiles;
        public int tilesWithObjects;
        public int scanMinSceneX;
        public int scanMaxSceneX;
        public int scanMinSceneY;
        public int scanMaxSceneY;
        public int scanWidth;
        public int scanHeight;
        public int sceneObjectsSeen;
        public int sceneObjectsCaptured;
        public int sceneObjectsSkippedByCap;
        public boolean sceneObjectCapHit;
        public double captureRatio;
        public int gameObjectsSeen;
        public int wallObjectsSeen;
        public int decorativeObjectsSeen;
        public int groundObjectsSeen;
        public int gameObjectsCaptured;
        public int wallObjectsCaptured;
        public int decorativeObjectsCaptured;
        public int groundObjectsCaptured;
        public int gameObjectsSkippedByCap;
        public int wallObjectsSkippedByCap;
        public int decorativeObjectsSkippedByCap;
        public int groundObjectsSkippedByCap;
        public int nullObjectsSkipped;
        public int groundItemsSeen;
        public int groundItemsCaptured;
        public int groundItemsSkippedByCap;
        public boolean groundItemCapHit;
        public int nullGroundItemsSkipped;
    }

    public static class SceneIndexSummary
    {
        public String sceneCaptureMode;
        public boolean indexEnabled;
        public int indexObjectCount;
        public int presentObjectCount;
        public int newlyIndexedCount;
        public int updatedCount;
        public int despawnedCount;
        public boolean fullResyncThisTick;
        public String resyncReason;
        public boolean indexCapHit;
        public int maxSceneIndexObjects;
        public Long sceneIndexBuildDurationMillis;
        public Long sceneIndexUpdateDurationMillis;
    }

    public static class SceneProjectionSummary
    {
        public String projectionStateHash;
        public boolean projectionStateChanged;
        public String projectionRefreshMode;
        public int projectionCandidatesConsidered;
        public int projectionObjectsUpdated;
        public int projectionObjectsReused;
        public Long projectionDurationMillis;
        public int visibleObjectCount;
        public int onScreenObjectCount;
        public int geometryAvailableCount;
        public int missingGeometryCount;
    }

    public static class SceneObjectDeltas
    {
        public SceneObjectSnapshot[] newObjects;
        public SceneObjectSnapshot[] updatedObjects;
        public SceneObjectSnapshot[] despawnedObjects;
    }

    public static class SceneObjectSnapshot
    {
        public String objectKey;
        public String kind;
        public int id;
        public Long hash;
        public String objectName;
        public String objectNameSource;
        public String[] actions;
        public int worldX;
        public int worldY;
        public int plane;
        public int orientation;
        public int sceneX;
        public int sceneY;
        public Integer localX;
        public Integer localY;
        public CanvasPoint canvasLocation;
        public int[][] canvasTilePolygon;
        public Bounds clickboxBounds;
        public int[][] clickboxPolygon;
        public Bounds convexHullBounds;
        public int[][] convexHullPolygon;
        public boolean onScreen;
        public boolean geometryAvailable;
        public String geometryWarning;
        public long firstSeenTick;
        public long lastSeenTick;
        public long lastUpdatedTick;
        public boolean present;
        public Long despawnedTick;
        public String source;
        public long projectionVersion;
    }

    public static class GroundItemSnapshot
    {
        public int id;
        public String itemName;
        public String itemNameSource;
        public int quantity;
        public int worldX;
        public int worldY;
        public int plane;
        public int sceneX;
        public int sceneY;
        public Integer localX;
        public Integer localY;
        public int[][] canvasTilePolygon;
        public CanvasPoint canvasCenter;
        public boolean onScreen;
        public boolean geometryAvailable;
        public String geometryWarning;
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

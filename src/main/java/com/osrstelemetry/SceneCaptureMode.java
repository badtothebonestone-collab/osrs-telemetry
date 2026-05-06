package com.osrstelemetry;

public enum SceneCaptureMode
{
	LOCAL_DEFAULT(12, 250, false),
	WIDE_DIAGNOSTIC(32, 10000, false),
	FULL_CURRENT_PLANE_DIAGNOSTIC(0, 25000, true),
	STATIC_SCENE_INDEX_DIAGNOSTIC(32, 50000, true);

	private final int radius;
	private final int maxSceneObjects;
	private final boolean fullCurrentPlaneScan;

	SceneCaptureMode(int radius, int maxSceneObjects, boolean fullCurrentPlaneScan)
	{
		this.radius = radius;
		this.maxSceneObjects = maxSceneObjects;
		this.fullCurrentPlaneScan = fullCurrentPlaneScan;
	}

	public int radius()
	{
		return radius;
	}

	public int maxSceneObjects()
	{
		return maxSceneObjects;
	}

	public boolean fullCurrentPlaneScan()
	{
		return fullCurrentPlaneScan;
	}
}

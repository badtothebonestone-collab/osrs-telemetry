package com.osrstelemetry;

public enum TelemetryDebugOverlayGeometryMode
{
	AIM_ONLY("aim_only"),
	BOUNDS("bounds"),
	CLICKABLE_HULL("clickable_hull"),
	TILE_POLYGON("tile_polygon"),
	HULL_AND_BOUNDS("hull_and_bounds"),
	ALL_GEOMETRY_DEBUG("all_geometry_debug");

	private final String label;

	TelemetryDebugOverlayGeometryMode(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}

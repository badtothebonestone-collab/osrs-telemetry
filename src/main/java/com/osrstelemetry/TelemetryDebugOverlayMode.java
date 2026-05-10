package com.osrstelemetry;

public enum TelemetryDebugOverlayMode
{
	CANDIDATES("candidates"),
	REACHABILITY("reachability"),
	COLLISION_WINDOW("collision_window"),
	SUMMARY("summary"),
	ALL("all");

	private final String label;

	TelemetryDebugOverlayMode(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}

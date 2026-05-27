package com.osrstelemetry;

public enum TelemetryWorkflowPreset
{
	DAILY_LIVE("Daily Live"),
	DAILY_SNAPSHOT_NO_FILE("Daily Snapshot No-File"),
	VISUAL_QA("Visual QA"),
	CUSTOM("Custom");

	private final String label;

	TelemetryWorkflowPreset(String label)
	{
		this.label = label;
	}

	@Override
	public String toString()
	{
		return label;
	}
}

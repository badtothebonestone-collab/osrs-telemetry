package com.osrstelemetry;

public enum TelemetryWorkflowPreset
{
	DAILY_LIVE("Daily Live"),
	VISUAL_QA("Visual QA"),
	DEBUG_AUDIT("Debug Audit"),
	PLUGIN_SNAPSHOT_EXPERIMENTAL("Plugin Snapshot Experimental"),
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

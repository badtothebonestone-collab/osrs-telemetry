package com.osrstelemetry;

import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

public class TelemetryPluginTest
{
	public static void main(String[] args) throws Exception
	{
		ExternalPluginManager.loadBuiltin(TelemetryPlugin.class);
		RuneLite.main(args);
	}
}

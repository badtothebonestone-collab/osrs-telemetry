package com.osrstelemetry;

import java.awt.Color;
import org.junit.Assert;
import org.junit.Test;

public class TelemetryDebugOverlayTest
{
	@Test
	public void reachableAssumedIsGreenAndNotBlocked()
	{
		Color color = TelemetryDebugOverlay.colorFor("reachable", "live_assumed");
		Assert.assertEquals(TelemetryDebugOverlay.reachabilityToken("reachable"), "R");
		Assert.assertEquals(new Color(66, 220, 110), color);
	}

	@Test
	public void blockedAssumedIsRed()
	{
		Color color = TelemetryDebugOverlay.colorFor("blocked", "live_assumed");
		Assert.assertEquals(TelemetryDebugOverlay.reachabilityToken("blocked"), "BLOCK");
		Assert.assertEquals(new Color(240, 85, 85), color);
	}

	@Test
	public void staleOrDepletedIsGray()
	{
		Assert.assertEquals(new Color(165, 165, 165), TelemetryDebugOverlay.colorFor("reachable", "depleted_or_stump"));
		Assert.assertEquals(new Color(165, 165, 165), TelemetryDebugOverlay.colorFor("reachable", "stale"));
	}
}

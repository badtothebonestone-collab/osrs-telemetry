package com.osrstelemetry;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class TelemetryRecordingModeTest
{
	@Rule
	public TemporaryFolder temporaryFolder = new TemporaryFolder();

	@Test
	public void liveCompactOnlyStartsWithoutRawTickEventFrameOrLivePacketStreams() throws Exception
	{
		TelemetryWriter writer = newWriter(
				TelemetryRecordingMode.LIVE_COMPACT_ONLY,
				false,
				false,
				false,
				new PluginLiveCache(new Gson()));

		writer.start();
		try
		{
			Path session = writer.getSessionDir();
			assertFalse(writer.isRawTickRecordingEnabled());
			assertFalse(writer.isRawEventRecordingEnabled());
			assertFalse(writer.isFrameRecordingEnabled());
			assertTrue(writer.isCompactLivePacketsEnabled());
			assertTrue(writer.isLiveCacheEnabled());
			assertFalse(writer.isCompactLivePacketFilesEnabled());
			assertFalse(writer.isCompactLiveStreamEnabled());
			assertFalse(Files.exists(session.resolve("ticks")));
			assertFalse(Files.exists(session.resolve("events")));
			assertFalse(Files.exists(session.resolve("frames")));
			assertFalse(Files.exists(session.resolve("live_packets")));
		}
		finally
		{
			writer.close();
		}
	}

	@Test
	public void debugRecordingPreservesRawTickEventAndFrameStreams() throws Exception
	{
		TelemetryWriter writer = newWriter(
				TelemetryRecordingMode.DEBUG_RECORDING,
				true,
				true,
				true);

		writer.start();
		try
		{
			Path session = writer.getSessionDir();
			assertTrue(writer.isRawTickRecordingEnabled());
			assertTrue(writer.isRawEventRecordingEnabled());
			assertTrue(writer.isFrameRecordingEnabled());
			assertTrue(Files.exists(session.resolve("ticks")));
			assertTrue(Files.exists(session.resolve("events")));
			assertTrue(Files.exists(session.resolve("frames")));
			assertTrue(Files.exists(session.resolve("frame_index.jsonl")));
		}
		finally
		{
			writer.close();
		}
	}

	@Test
	public void livePacketRuntimeIsRemovedEvenWhenCacheIsEnabled() throws Exception
	{
		TelemetryWriter writer = newWriter(
				TelemetryRecordingMode.LIVE_COMPACT_ONLY,
				false,
				false,
				false,
				new PluginLiveCache(new Gson()));

		writer.start();
		try
		{
			Path session = writer.getSessionDir();
			assertTrue(writer.isCompactLivePacketsEnabled());
			assertFalse(writer.isCompactLivePacketFilesEnabled());
			assertFalse(writer.isCompactLiveStreamEnabled());
			assertTrue(writer.enqueueLivePacket(
					"live_baseline_packet.v1",
					7L,
					"2026-05-11T00:00:00Z",
					java.util.Map.of("gameState", "LOGGED_IN")));
			assertFalse(Files.exists(session.resolve("live_packets")));
		}
		finally
		{
			writer.close();
		}
	}

	@Test
	public void liveCacheMakesCompactPayloadsAvailableWithoutFileOrStream()
	{
		PluginLiveCache cache = new PluginLiveCache(new Gson());
		TelemetryWriter writer = newWriter(
				TelemetryRecordingMode.LIVE_COMPACT_ONLY,
				false,
				false,
				false,
				cache);

		assertTrue(writer.isCompactLivePacketsEnabled());
		assertTrue(writer.isLiveCacheEnabled());
		assertTrue(writer.enqueueLivePacket(
				"live_baseline_packet.v1",
				7L,
				"2026-05-11T00:00:00Z",
				java.util.Map.of("gameState", "LOGGED_IN")));
		assertTrue(writer.enqueueLivePacket(
				"live_navigation_packet.v1",
				7L,
				"2026-05-11T00:00:00Z",
				java.util.Map.of("collision", java.util.Map.of("collisionKnown", true))));
		assertTrue(writer.enqueueLivePacket(
				"live_collision_window_packet.v1",
				7L,
				"2026-05-11T00:00:00Z",
				java.util.Map.of("collisionKnown", true, "windowRadius", 24)));
		assertTrue(writer.getLiveCachePayloadTypes().contains("live_baseline_packet.v1"));
		assertTrue(writer.getLiveCachePayloadTypes().contains("live_navigation_packet.v1"));
		assertTrue(writer.getLiveCachePayloadTypes().contains("live_collision_window_packet.v1"));
		assertTrue(writer.getLiveCacheUpdates() > 0L);
	}

	private TelemetryWriter newWriter(
			TelemetryRecordingMode mode,
			boolean rawTicks,
			boolean rawEvents,
			boolean frames)
	{
		return newWriter(mode, rawTicks, rawEvents, frames, null);
	}

	private TelemetryWriter newWriter(
			TelemetryRecordingMode mode,
			boolean rawTicks,
			boolean rawEvents,
			boolean frames,
			PluginLiveCache liveCache)
	{
		return new TelemetryWriter(
				temporaryFolder.getRoot().getAbsolutePath(),
				new Gson(),
				1,
				false,
				1,
				60,
				true,
				true,
				mode,
				rawTicks,
				rawEvents,
				frames,
				1,
				"jpg",
				0.75,
				16,
				10,
				true,
				10,
				"RUNELITE_ONLY",
				false,
				liveCache);
	}
}

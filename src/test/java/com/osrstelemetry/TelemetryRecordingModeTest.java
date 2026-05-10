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
	public void liveCompactOnlyStartsWithoutRawTickEventOrFrameStreams() throws Exception
	{
		TelemetryWriter writer = newWriter(
				TelemetryRecordingMode.LIVE_COMPACT_ONLY,
				false,
				false,
				false,
				true);

		writer.start();
		try
		{
			Path session = writer.getSessionDir();
			assertFalse(writer.isRawTickRecordingEnabled());
			assertFalse(writer.isRawEventRecordingEnabled());
			assertFalse(writer.isFrameRecordingEnabled());
			assertTrue(writer.isCompactLivePacketsEnabled());
			assertFalse(Files.exists(session.resolve("ticks")));
			assertFalse(Files.exists(session.resolve("events")));
			assertFalse(Files.exists(session.resolve("frames")));
			assertTrue(Files.exists(session.resolve("live_packets")));
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

	private TelemetryWriter newWriter(
			TelemetryRecordingMode mode,
			boolean rawTicks,
			boolean rawEvents,
			boolean frames,
			boolean compactPackets)
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
				compactPackets,
				64,
				5000,
				512L * 1024L * 1024L,
				16,
				100);
	}
}

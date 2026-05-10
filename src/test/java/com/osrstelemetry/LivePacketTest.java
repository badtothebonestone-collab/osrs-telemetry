package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class LivePacketTest
{
	@Rule
	public TemporaryFolder temporaryFolder = new TemporaryFolder();

	@Test
	public void livePacketEnvelopeHasRequiredFields()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("gameState", "LOGGED_IN");

		LivePacket packet = new LivePacket(
				"live_baseline_packet.v1",
				"session-1",
				123L,
				1L,
				"2026-05-09T00:00:00Z",
				payload);

		assertEquals(LivePacket.ENVELOPE_SCHEMA, packet.schema);
		assertEquals("live_baseline_packet.v1", packet.packetType);
		assertEquals("session-1", packet.sessionId);
		assertEquals(123L, packet.tick);
		assertEquals(1L, packet.sequence);
		assertEquals("2026-05-09T00:00:00Z", packet.timestampUtc);
		assertNotNull(packet.payload);
	}

	@Test
	public void navigationPacketEnvelopeUsesReadOnlyPacketType()
	{
		Map<String, Object> collision = new LinkedHashMap<>();
		collision.put("collisionKnown", true);
		collision.put("blockedMovementTileCount", 12);
		collision.put("collisionHash", "abc123");
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("collision", collision);
		payload.put("reachabilityComputed", false);

		LivePacket packet = new LivePacket(
				"live_navigation_packet.v1",
				"session-1",
				124L,
				2L,
				"2026-05-09T00:00:01Z",
				payload);

		assertEquals(LivePacket.ENVELOPE_SCHEMA, packet.schema);
		assertEquals("live_navigation_packet.v1", packet.packetType);
		assertEquals(124L, packet.tick);
		assertNotNull(packet.payload);
	}

	@Test
	public void collisionWindowPacketEnvelopeUsesReadOnlyPacketType()
	{
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("collisionKnown", true);
		payload.put("windowRadius", 24);
		payload.put("width", 49);
		payload.put("height", 49);
		payload.put("collisionWindowTileCount", 2401);
		payload.put("collisionWindowHash", "abc123");

		LivePacket packet = new LivePacket(
				"live_collision_window_packet.v1",
				"session-1",
				125L,
				3L,
				"2026-05-09T00:00:02Z",
				payload);

		assertEquals(LivePacket.ENVELOPE_SCHEMA, packet.schema);
		assertEquals("live_collision_window_packet.v1", packet.packetType);
		assertEquals(125L, packet.tick);
		assertNotNull(packet.payload);
	}

	@Test
	public void livePacketWriterSequencesIncrease()
	{
		LivePacketWriter writer = new LivePacketWriter(
				"session-1",
				Path.of(temporaryFolder.getRoot().getAbsolutePath()),
				new Gson(),
				1,
				0L,
				0L,
				0L,
				10);

		LivePacket first = writer.createPacket(
				"live_baseline_packet.v1",
				10L,
				"2026-05-09T00:00:00Z",
				new LinkedHashMap<>());
		LivePacket second = writer.createPacket(
				"live_writer_health_packet.v1",
				10L,
				"2026-05-09T00:00:00Z",
				new LinkedHashMap<>());

		assertEquals(1L, first.sequence);
		assertEquals(2L, second.sequence);
	}

	@Test
	public void livePacketWriterRotatesPrunesAndWritesIndex() throws Exception
	{
		Path session = Path.of(temporaryFolder.getRoot().getAbsolutePath());
		LivePacketWriter writer = new LivePacketWriter(
				"session-1",
				session,
				new Gson(),
				1,
				0L,
				0L,
				2L,
				10);
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("blob", "x".repeat(1_100_000));

		writer.writePacketForTest(writer.createPacket(
				"live_baseline_packet.v1",
				1L,
				"2026-05-09T00:00:00Z",
				payload));
		writer.writePacketForTest(writer.createPacket(
				"live_scene_delta_packet.v1",
				2L,
				"2026-05-09T00:00:01Z",
				payload));
		writer.writePacketForTest(writer.createPacket(
				"live_writer_health_packet.v1",
				3L,
				"2026-05-09T00:00:02Z",
				payload));
		writer.close();

		Path liveDir = session.resolve(LivePacketWriter.LIVE_PACKETS_DIR);
		List<Path> segments = Files.list(liveDir)
				.filter(path -> path.getFileName().toString().endsWith(".ndjson"))
				.collect(java.util.stream.Collectors.toList());
		Path index = liveDir.resolve(LivePacketWriter.INDEX_FILE_NAME);
		Path latest = liveDir.resolve(LivePacketWriter.LATEST_SEGMENT_FILE_NAME);
		String indexText = Files.readString(index, StandardCharsets.UTF_8);
		String latestText = Files.readString(latest, StandardCharsets.UTF_8).trim();

		assertTrue(Files.exists(index));
		assertTrue(Files.exists(latest));
		assertTrue(indexText.contains("\"schema\":\"live_packet_index.v1\""));
		assertTrue(indexText.contains("\"latestTick\":3"));
		assertTrue(indexText.contains("\"retentionSegments\":2"));
		assertTrue(indexText.contains("\"prunedCount\":"));
		assertTrue(segments.size() <= 2);
		assertFalse(latestText.isBlank());
		assertTrue(Files.exists(liveDir.resolve(latestText)));
	}
}

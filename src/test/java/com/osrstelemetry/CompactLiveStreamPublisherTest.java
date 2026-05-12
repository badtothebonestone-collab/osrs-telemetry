package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import com.google.gson.Gson;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class CompactLiveStreamPublisherTest
{
	@Test
	public void enqueueDropsWithoutClientAndCountsProjectionType() throws Exception
	{
		CompactLiveStreamPublisher publisher = new CompactLiveStreamPublisher(
				"test-session",
				new Gson(),
				"127.0.0.1",
				0,
				2,
				true,
				20,
				10);
		publisher.start();
		try
		{
			Map<String, Object> payload = new LinkedHashMap<>();
			payload.put("visibleObjectRefs", java.util.List.of());

			boolean accepted = publisher.enqueue(
					"live_projection_packet.v1",
					1L,
					"2026-05-10T00:00:00Z",
					payload);

			assertFalse(accepted);
			assertEquals(1L, publisher.getOfferedPackets());
			assertEquals(1L, publisher.getDroppedPackets());
			assertEquals(1L, publisher.getDroppedNoClients());
			assertEquals(Long.valueOf(1L), publisher.getOfferedPacketsByType().get("live_projection_packet.v1"));
			assertEquals(Long.valueOf(1L), publisher.getDroppedPacketsByType().get("live_projection_packet.v1"));
		}
		finally
		{
			publisher.close();
		}
	}
}

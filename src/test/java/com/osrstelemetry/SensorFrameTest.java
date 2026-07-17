package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class SensorFrameTest
{
	private final Gson gson = new Gson();

	@Test
	public void ownsImmutableSerializedFactPayloadAndErrors()
	{
		Map<String, Object> mutablePayload = new LinkedHashMap<>();
		mutablePayload.put("gameState", "LOGGED_IN");
		List<String> mutableErrors = new ArrayList<>();
		mutableErrors.add("capture warning");

		SensorFrame.Fact fact = SensorFrame.Fact.fromPayload(
				gson,
				SensorFrame.FACT_BASELINE,
				10L,
				"2026-05-11T00:00:00Z",
				true,
				mutableErrors,
				mutablePayload);
		SensorFrame frame = SensorFrame.builder("frame-10", 10L, 99L, "2026-05-11T00:00:00Z")
				.completedAtUtc("2026-05-11T00:00:00Z")
				.fact(fact)
				.build();

		mutablePayload.put("laterMutation", true);
		mutableErrors.add("later error");
		JsonObject frozenPayload = gson.fromJson(
				frame.getFact(SensorFrame.FACT_BASELINE).getPayloadJson(),
				JsonObject.class);
		assertEquals("LOGGED_IN", frozenPayload.get("gameState").getAsString());
		assertFalse(frozenPayload.has("laterMutation"));
		assertEquals(List.of("capture warning"), fact.getErrors());

		try
		{
			frame.getFacts().clear();
			fail("facts map must be immutable");
		}
		catch (UnsupportedOperationException expected)
		{
			// Expected.
		}
		try
		{
			fact.getErrors().add("mutation");
			fail("errors list must be immutable");
		}
		catch (UnsupportedOperationException expected)
		{
			// Expected.
		}
	}

	@Test
	public void completeFrameReportsCoreFactAndFrameMetadata()
	{
		SensorFrame.Builder builder = SensorFrame.builder(
				"frame-complete",
				12L,
				123_456L,
				"2026-05-11T00:00:00Z")
				.completedAtUtc("2026-05-11T00:00:00.025Z")
				.captureDurationMillis(25L)
				.sessionId("session-1")
				.clientProcessId(4321L)
				.geometryFrameId("geometry-12");
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			builder.fact(
					gson,
					factName,
					12L,
					"2026-05-11T00:00:00.010Z",
					true,
					List.of(),
					Map.of("name", factName));
		}

		SensorFrame frame = builder.build();
		assertEquals(SensorFrame.SCHEMA, frame.getSchema());
		assertTrue(frame.isCoherent());
		assertTrue(frame.isComplete());
		assertEquals(SensorFrame.CORE_FACT_NAMES, frame.getAvailableFacts());
		assertEquals(List.of(), frame.getUnavailableFacts());
		assertEquals(25L, frame.getCaptureDurationMillis());
		assertEquals("session-1", frame.getSessionId());
		assertEquals(Long.valueOf(4321L), frame.getClientProcessId());
		assertEquals("geometry-12", frame.getGeometryFrameId());

		Map<String, Object> metadata = frame.metadata();
		assertEquals("frame-complete", metadata.get("frameId"));
		assertEquals(12L, metadata.get("sourceTick"));
		assertEquals(true, metadata.get("coherent"));
		assertEquals(true, metadata.get("complete"));
		assertNotNull(metadata.get("facts"));
		@SuppressWarnings("unchecked")
		Map<String, Object> factMetadata = (Map<String, Object>) metadata.get("facts");
		assertEquals(SensorFrame.CORE_FACT_NAMES, new ArrayList<>(factMetadata.keySet()));
	}

	@Test
	public void missingOrUnavailableFactMakesFrameIncomplete()
	{
		SensorFrame frame = SensorFrame.builder("partial", 20L, 1L, "2026-05-11T00:00:00Z")
				.completedAtUtc("2026-05-11T00:00:00Z")
				.sessionId("session-partial")
				.clientProcessId(1234L)
				.geometryFrameId("geometry-partial")
				.fact(gson,
						SensorFrame.FACT_BASELINE,
						20L,
						"2026-05-11T00:00:00Z",
						true,
						List.of(),
						Map.of("gameState", "LOGIN_SCREEN"))
				.fact(gson,
						SensorFrame.FACT_INVENTORY,
						20L,
						"2026-05-11T00:00:00Z",
						false,
						List.of("inventory unavailable"),
						Map.of())
				.build();

		assertTrue(frame.isCoherent());
		assertFalse(frame.isComplete());
		assertEquals(List.of(SensorFrame.FACT_BASELINE), frame.getAvailableFacts());
		assertTrue(frame.getUnavailableFacts().contains(SensorFrame.FACT_INVENTORY));
		assertTrue(frame.getUnavailableFacts().contains(SensorFrame.FACT_DIALOGUE_STATE));
	}

	@Test
	public void missingIdentityOrFutureCaptureIsNeverCoherent()
	{
		String future = java.time.Instant.now().plusSeconds(30L).toString();
		SensorFrame missingIdentity = completeFrameBuilder(
				"missing-identity", 21L, "2026-05-11T00:00:00Z", false).build();
		SensorFrame futureFrame = completeFrameBuilder(
				"future", 22L, future, true).build();

		assertFalse(missingIdentity.isCoherent());
		assertFalse(missingIdentity.isComplete());
		assertFalse(futureFrame.isCoherent());
		assertFalse(futureFrame.isComplete());
		assertTrue(futureFrame.ageMillis() < 0L);
	}

	private SensorFrame.Builder completeFrameBuilder(
			String frameId, long tick, String capturedAtUtc, boolean includeIdentity)
	{
		SensorFrame.Builder builder = SensorFrame.builder(frameId, tick, 1L, capturedAtUtc)
				.completedAtUtc(capturedAtUtc);
		if (includeIdentity)
		{
			builder.sessionId("session")
					.clientProcessId(1234L)
					.geometryFrameId("geometry");
		}
		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			builder.fact(gson, factName, tick, capturedAtUtc, true, List.of(), Map.of());
		}
		return builder;
	}

	@Test
	public void mismatchedFactTickMakesFrameIncoherentAndIncomplete()
	{
		SensorFrame frame = SensorFrame.builder("mixed-tick", 30L, 1L, "2026-05-11T00:00:00Z")
				.completedAtUtc("2026-05-11T00:00:00Z")
				.fact(gson,
						SensorFrame.FACT_BASELINE,
						29L,
						"2026-05-11T00:00:00Z",
						true,
						List.of(),
						Map.of("gameState", "LOGGED_IN"))
				.build();

		assertFalse(frame.isCoherent());
		assertFalse(frame.isComplete());
	}
}

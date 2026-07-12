package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.junit.Test;

public class JavaSnapshotFixtureTest
{
	private final Gson gson = new Gson();
	private static final java.nio.file.Path PYTHON_PARSER_FIXTURE = java.nio.file.Path.of(
			"tests", "fixtures", "snapshot_loaded.json");

	@Test
	public void committedFixtureIsIdenticalToRealEndpointRegeneration() throws Exception
	{
		String committedText = Files.readString(
				JavaSnapshotFixture.DEFAULT_OUTPUT,
				StandardCharsets.UTF_8).replace("\r\n", "\n");
		JsonObject committed = gson.fromJson(committedText, JsonObject.class);
		JsonObject regenerated = JavaSnapshotFixture.generatedFixture();

		assertEquals(JavaSnapshotFixture.generatedFixtureText(), committedText);
		assertEquals(regenerated, committed);
		assertEquals(0L, committed.get("serviceTimingMillis").getAsLong());
	}

	@Test
	public void factSizesComeFromRealSensorFrameUtf8Payloads() throws Exception
	{
		JsonObject fixture = gson.fromJson(
				Files.readString(JavaSnapshotFixture.DEFAULT_OUTPUT, StandardCharsets.UTF_8),
				JsonObject.class);
		JsonObject payloads = fixture.getAsJsonObject("payloads");
		JsonObject facts = fixture.getAsJsonObject("sensorFrame").getAsJsonObject("facts");

		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			long expectedBytes = gson.toJson(payloads.get(factName))
					.getBytes(StandardCharsets.UTF_8).length;
			long recordedBytes = facts.getAsJsonObject(factName).get("sizeBytes").getAsLong();
			assertEquals(factName, expectedBytes, recordedBytes);
			assertTrue(factName + " must not use a placeholder size", recordedBytes > 1L);
		}
	}

	@Test
	public void parserFixtureFactSizesMatchItsSerializedPayloads() throws Exception
	{
		JsonObject fixture = gson.fromJson(
				Files.readString(PYTHON_PARSER_FIXTURE, StandardCharsets.UTF_8),
				JsonObject.class);
		JsonObject payloads = fixture.getAsJsonObject("payloads");
		JsonObject facts = fixture.getAsJsonObject("sensorFrame").getAsJsonObject("facts");

		for (String factName : SensorFrame.CORE_FACT_NAMES)
		{
			long expectedBytes = gson.toJson(payloads.get(factName))
					.getBytes(StandardCharsets.UTF_8).length;
			assertEquals(
					factName,
					expectedBytes,
					facts.getAsJsonObject(factName).get("sizeBytes").getAsLong());
		}
	}
}

package com.osrstelemetry;

import com.google.gson.Gson;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Immutable game-tick sensor evidence published as one cache value.
 *
 * <p>The frame owns serialized copies of every fact so neither callers nor the
 * HTTP response assembler can observe later mutations of capture maps.</p>
 */
public final class SensorFrame
{
	private static final long MAX_FUTURE_CLOCK_SKEW_SECONDS = 2L;
	public static final String SCHEMA = "sensor_frame.v1";
	public static final String FACT_BASELINE = "baseline";
	public static final String FACT_INVENTORY = "inventory";
	public static final String FACT_ACTIVITY = "activity";
	public static final String FACT_BANK_UI = "bank_ui";
	public static final String FACT_DIALOGUE_STATE = "dialogue_state";
	public static final List<String> CORE_FACT_NAMES = List.of(
			FACT_BASELINE,
			FACT_INVENTORY,
			FACT_ACTIVITY,
			FACT_BANK_UI,
			FACT_DIALOGUE_STATE);

	private final String frameId;
	private final long sourceTick;
	private final long captureStartedMonotonicNanos;
	private final String capturedAtUtc;
	private final String completedAtUtc;
	private final long captureDurationMillis;
	private final String sessionId;
	private final Long clientProcessId;
	private final String geometryFrameId;
	private final Map<String, Fact> facts;
	private final boolean coherent;
	private final boolean complete;
	private final List<String> availableFacts;
	private final List<String> unavailableFacts;

	private SensorFrame(Builder builder)
	{
		frameId = requireText(builder.frameId, "frameId");
		sourceTick = builder.sourceTick;
		captureStartedMonotonicNanos = builder.captureStartedMonotonicNanos;
		Instant captured = parseInstant(builder.capturedAtUtc, "capturedAtUtc");
		Instant completed = parseInstant(
				builder.completedAtUtc == null ? builder.capturedAtUtc : builder.completedAtUtc,
				"completedAtUtc");
		capturedAtUtc = captured.toString();
		completedAtUtc = completed.toString();
		captureDurationMillis = builder.captureDurationMillis == null
				? Math.max(0L, Duration.between(captured, completed).toMillis())
				: builder.captureDurationMillis;
		if (captureDurationMillis < 0L)
		{
			throw new IllegalArgumentException("captureDurationMillis must be non-negative");
		}
		sessionId = normalizeOptional(builder.sessionId);
		clientProcessId = builder.clientProcessId;
		geometryFrameId = normalizeOptional(builder.geometryFrameId);

		Map<String, Fact> copiedFacts = new LinkedHashMap<>();
		for (String factName : CORE_FACT_NAMES)
		{
			Fact fact = builder.facts.get(factName);
			if (fact != null)
			{
				copiedFacts.put(factName, fact);
			}
		}
		facts = Collections.unmodifiableMap(copiedFacts);

		Instant allowedFuture = Instant.now().plusSeconds(MAX_FUTURE_CLOCK_SKEW_SECONDS);
		boolean sourcesCoherent = !completed.isBefore(captured)
				&& !captured.isAfter(allowedFuture)
				&& !completed.isAfter(allowedFuture)
				&& sessionId != null
				&& clientProcessId != null
				&& clientProcessId > 0L
				&& geometryFrameId != null;
		List<String> available = new ArrayList<>();
		List<String> unavailable = new ArrayList<>();
		for (String factName : CORE_FACT_NAMES)
		{
			Fact fact = copiedFacts.get(factName);
			if (fact == null || !fact.isAvailable())
			{
				unavailable.add(factName);
			}
			else
			{
				available.add(factName);
			}
			if (fact != null)
			{
				Instant factCaptured = parseInstant(fact.getCapturedAtUtc(), "fact.capturedAtUtc");
				sourcesCoherent &= fact.getSourceTick() == sourceTick
						&& !factCaptured.isBefore(captured)
						&& !factCaptured.isAfter(completed);
			}
		}
		availableFacts = List.copyOf(available);
		unavailableFacts = List.copyOf(unavailable);
		coherent = sourcesCoherent;
		complete = coherent && unavailableFacts.isEmpty();
	}

	public static Builder builder(
			String frameId,
			long sourceTick,
			long captureStartedMonotonicNanos,
			String capturedAtUtc)
	{
		return new Builder(frameId, sourceTick, captureStartedMonotonicNanos, capturedAtUtc);
	}

	public Builder toBuilder()
	{
		Builder builder = new Builder(frameId, sourceTick, captureStartedMonotonicNanos, capturedAtUtc)
				.completedAtUtc(completedAtUtc)
				.captureDurationMillis(captureDurationMillis)
				.sessionId(sessionId)
				.clientProcessId(clientProcessId)
				.geometryFrameId(geometryFrameId);
		for (Fact fact : facts.values())
		{
			builder.fact(fact);
		}
		return builder;
	}

	public String getSchema()
	{
		return SCHEMA;
	}

	public String getFrameId()
	{
		return frameId;
	}

	public long getSourceTick()
	{
		return sourceTick;
	}

	public long getCaptureStartedMonotonicNanos()
	{
		return captureStartedMonotonicNanos;
	}

	public String getCapturedAtUtc()
	{
		return capturedAtUtc;
	}

	public String getCompletedAtUtc()
	{
		return completedAtUtc;
	}

	public long getCaptureDurationMillis()
	{
		return captureDurationMillis;
	}

	public String getSessionId()
	{
		return sessionId;
	}

	public Long getClientProcessId()
	{
		return clientProcessId;
	}

	public String getGeometryFrameId()
	{
		return geometryFrameId;
	}

	public Map<String, Fact> getFacts()
	{
		return facts;
	}

	public Fact getFact(String name)
	{
		return name == null ? null : facts.get(name);
	}

	public boolean isCoherent()
	{
		return coherent;
	}

	public boolean isComplete()
	{
		return complete;
	}

	public List<String> getAvailableFacts()
	{
		return availableFacts;
	}

	public List<String> getUnavailableFacts()
	{
		return unavailableFacts;
	}

	public long ageMillis()
	{
		return ageMillis(Instant.now());
	}

	long ageMillis(Instant now)
	{
		Instant effectiveNow = now == null ? Instant.now() : now;
		return Duration.between(Instant.parse(capturedAtUtc), effectiveNow).toMillis();
	}

	public Map<String, Object> metadata()
	{
		Map<String, Object> result = new LinkedHashMap<>();
		Map<String, Object> factMetadata = new LinkedHashMap<>();
		for (Map.Entry<String, Fact> entry : facts.entrySet())
		{
			factMetadata.put(entry.getKey(), entry.getValue().metadata());
		}
		result.put("schema", SCHEMA);
		result.put("frameId", frameId);
		result.put("sourceTick", sourceTick);
		result.put("captureStartedMonotonicNanos", captureStartedMonotonicNanos);
		result.put("capturedAtUtc", capturedAtUtc);
		result.put("completedAtUtc", completedAtUtc);
		result.put("captureDurationMillis", captureDurationMillis);
		result.put("sessionId", sessionId);
		result.put("clientProcessId", clientProcessId);
		result.put("geometryFrameId", geometryFrameId);
		result.put("coherent", coherent);
		result.put("complete", complete);
		result.put("availableFacts", availableFacts);
		result.put("unavailableFacts", unavailableFacts);
		result.put("facts", factMetadata);
		return result;
	}

	public static boolean isCoreFact(String name)
	{
		return CORE_FACT_NAMES.contains(name);
	}

	private static String normalizeOptional(String value)
	{
		return value == null || value.isBlank() ? null : value;
	}

	private static String requireText(String value, String field)
	{
		if (value == null || value.isBlank())
		{
			throw new IllegalArgumentException(field + " must be non-empty");
		}
		return value;
	}

	private static Instant parseInstant(String value, String field)
	{
		try
		{
			return Instant.parse(requireText(value, field));
		}
		catch (RuntimeException e)
		{
			throw new IllegalArgumentException(field + " must be an ISO-8601 instant", e);
		}
	}

	public static final class Fact
	{
		private final String name;
		private final long sourceTick;
		private final String capturedAtUtc;
		private final boolean available;
		private final List<String> errors;
		private final String payloadJson;
		private final long sizeBytes;

		private Fact(
				String name,
				long sourceTick,
				String capturedAtUtc,
				boolean available,
				List<String> errors,
				String payloadJson)
		{
			this.name = requireText(name, "fact.name");
			if (!isCoreFact(this.name))
			{
				throw new IllegalArgumentException("unsupported sensor fact: " + this.name);
			}
			this.sourceTick = sourceTick;
			this.capturedAtUtc = parseInstant(capturedAtUtc, "fact.capturedAtUtc").toString();
			this.available = available;
			List<String> copiedErrors = new ArrayList<>();
			if (errors != null)
			{
				for (String error : errors)
				{
					if (error != null && !error.isBlank())
					{
						copiedErrors.add(error);
					}
				}
			}
			this.errors = List.copyOf(copiedErrors);
			this.payloadJson = Objects.requireNonNull(payloadJson, "payloadJson");
			this.sizeBytes = payloadJson.getBytes(StandardCharsets.UTF_8).length;
		}

		public static Fact fromPayload(
				Gson gson,
				String name,
				long sourceTick,
				String capturedAtUtc,
				boolean available,
				List<String> errors,
				Object payload)
		{
			if (gson == null)
			{
				throw new IllegalArgumentException("gson is required");
			}
			String json = gson.toJson(payload);
			return new Fact(name, sourceTick, capturedAtUtc, available, errors, json == null ? "null" : json);
		}

		public static Fact fromJson(
				String name,
				long sourceTick,
				String capturedAtUtc,
				boolean available,
				List<String> errors,
				String payloadJson)
		{
			return new Fact(name, sourceTick, capturedAtUtc, available, errors, payloadJson);
		}

		public String getName()
		{
			return name;
		}

		public long getSourceTick()
		{
			return sourceTick;
		}

		public String getCapturedAtUtc()
		{
			return capturedAtUtc;
		}

		public boolean isAvailable()
		{
			return available;
		}

		public List<String> getErrors()
		{
			return errors;
		}

		public String getPayloadJson()
		{
			return payloadJson;
		}

		public long getSizeBytes()
		{
			return sizeBytes;
		}

		public long ageMillis()
		{
			return ageMillis(Instant.now());
		}

		long ageMillis(Instant now)
		{
			Instant effectiveNow = now == null ? Instant.now() : now;
			return Duration.between(Instant.parse(capturedAtUtc), effectiveNow).toMillis();
		}

		public Map<String, Object> metadata()
		{
			Map<String, Object> result = new LinkedHashMap<>();
			result.put("sourceTick", sourceTick);
			result.put("capturedAtUtc", capturedAtUtc);
			result.put("available", available);
			result.put("errors", errors);
			result.put("sizeBytes", sizeBytes);
			return result;
		}
	}

	public static final class Builder
	{
		private final String frameId;
		private final long sourceTick;
		private final long captureStartedMonotonicNanos;
		private final String capturedAtUtc;
		private String completedAtUtc;
		private Long captureDurationMillis;
		private String sessionId;
		private Long clientProcessId;
		private String geometryFrameId;
		private final Map<String, Fact> facts = new LinkedHashMap<>();

		private Builder(
				String frameId,
				long sourceTick,
				long captureStartedMonotonicNanos,
				String capturedAtUtc)
		{
			this.frameId = frameId;
			this.sourceTick = sourceTick;
			this.captureStartedMonotonicNanos = captureStartedMonotonicNanos;
			this.capturedAtUtc = capturedAtUtc;
		}

		public Builder completedAtUtc(String value)
		{
			completedAtUtc = value;
			return this;
		}

		public Builder captureDurationMillis(long value)
		{
			captureDurationMillis = value;
			return this;
		}

		public Builder sessionId(String value)
		{
			sessionId = value;
			return this;
		}

		public Builder clientProcessId(Long value)
		{
			clientProcessId = value;
			return this;
		}

		public Builder geometryFrameId(String value)
		{
			geometryFrameId = value;
			return this;
		}

		public Builder fact(Fact value)
		{
			Fact fact = Objects.requireNonNull(value, "fact");
			facts.put(fact.getName(), fact);
			return this;
		}

		public Builder fact(
				Gson gson,
				String name,
				long factSourceTick,
				String factCapturedAtUtc,
				boolean available,
				List<String> errors,
				Object payload)
		{
			return fact(Fact.fromPayload(
					gson,
					name,
					factSourceTick,
					factCapturedAtUtc,
					available,
					errors,
					payload));
		}

		public SensorFrame build()
		{
			return new SensorFrame(this);
		}
	}
}

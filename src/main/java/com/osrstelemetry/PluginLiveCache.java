package com.osrstelemetry;

import com.google.gson.Gson;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import lombok.extern.slf4j.Slf4j;

/**
 * Atomically publishes the most recently captured sensor frame.
 *
 * <p>The {@link AtomicReference} is the cache's only payload-bearing state. The
 * packet-oriented methods remain as a compatibility view for callers migrating
 * to frame publication; they always derive their result from one captured
 * {@link FrameSnapshot}.</p>
 */
@Slf4j
public class PluginLiveCache
{
	private static final String LEGACY_FRAME_PREFIX = "legacy-cache-";
	private static final Map<String, String> PACKET_TYPE_TO_FACT = packetTypeToFact();
	private static final Map<String, String> FACT_TO_PACKET_TYPE = factToPacketType();

	private final Gson gson;
	private final AtomicReference<FrameSnapshot> current = new AtomicReference<>();
	private final AtomicLong sequence = new AtomicLong();
	private final AtomicLong updates = new AtomicLong();
	private final AtomicLong updateErrors = new AtomicLong();

	public PluginLiveCache(Gson gson)
	{
		this.gson = gson;
	}

	/**
	 * Replaces the entire current frame in one publication step.
	 *
	 * <p>An incomplete frame intentionally replaces a prior complete frame; no
	 * facts are carried across frame publications.</p>
	 */
	public boolean publish(SensorFrame frame)
	{
		return publishAt(frame, Instant.now());
	}

	synchronized boolean publishAt(SensorFrame frame, Instant publishedAt)
	{
		if (frame == null)
		{
			return false;
		}

		try
		{
			long nextSequence = sequence.incrementAndGet();
			FrameSnapshot snapshot = new FrameSnapshot(
					frame,
					nextSequence,
					(publishedAt == null ? Instant.now() : publishedAt).toString());
			current.set(snapshot);
			updates.incrementAndGet();
			return true;
		}
		catch (RuntimeException e)
		{
			updateErrors.incrementAndGet();
			log.debug("Failed to publish plugin live sensor frame", e);
			return false;
		}
	}

	/** Returns one stable view of the current publication, or {@code null}. */
	public FrameSnapshot snapshot()
	{
		return current.get();
	}

	/** Returns the current immutable frame, or {@code null}. */
	public SensorFrame currentFrame()
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? null : snapshot.getFrame();
	}

	/**
	 * Compatibility adapter for the pre-frame producer. Same-tick updates are
	 * assembled into a legacy partial frame, while a tick change starts a fresh
	 * frame and therefore cannot retain prior-tick facts.
	 */
	public boolean update(String packetType, long tick, String timestampUtc, Object payload)
	{
		return updateAt(packetType, tick, timestampUtc, payload, Instant.now());
	}

	synchronized boolean updateAt(
			String packetType,
			long tick,
			String timestampUtc,
			Object payload,
			Instant publishedAt)
	{
		String factName = factNameForPacketType(packetType);
		if (factName == null || timestampUtc == null || gson == null)
		{
			return false;
		}

		try
		{
			SensorFrame.Fact nextFact = SensorFrame.Fact.fromPayload(
					gson,
					factName,
					tick,
					timestampUtc,
					true,
					List.of(),
					payload);
			FrameSnapshot priorSnapshot = current.get();
			SensorFrame prior = priorSnapshot == null ? null : priorSnapshot.getFrame();
			boolean mergeLegacyFrame = prior != null
					&& prior.getFrameId().startsWith(LEGACY_FRAME_PREFIX)
					&& prior.getSourceTick() == tick;

			SensorFrame.Builder builder;
			if (mergeLegacyFrame)
			{
				Instant captured = earlier(
						Instant.parse(prior.getCapturedAtUtc()),
						Instant.parse(nextFact.getCapturedAtUtc()));
				Instant completed = later(
						Instant.parse(prior.getCompletedAtUtc()),
						Instant.parse(nextFact.getCapturedAtUtc()));
				builder = SensorFrame.builder(
						prior.getFrameId(),
						tick,
						prior.getCaptureStartedMonotonicNanos(),
						captured.toString())
						.completedAtUtc(completed.toString())
						.sessionId(prior.getSessionId())
						.clientProcessId(prior.getClientProcessId())
						.geometryFrameId(prior.getGeometryFrameId());
				for (SensorFrame.Fact fact : prior.getFacts().values())
				{
					builder.fact(fact);
				}
			}
			else
			{
				builder = SensorFrame.builder(
						LEGACY_FRAME_PREFIX + tick + "-" + (sequence.get() + 1L),
						tick,
						System.nanoTime(),
						nextFact.getCapturedAtUtc())
						.completedAtUtc(nextFact.getCapturedAtUtc());
			}

			return publishAt(builder.fact(nextFact).build(), publishedAt);
		}
		catch (RuntimeException e)
		{
			updateErrors.incrementAndGet();
			log.debug("Failed to update plugin live cache for {}", packetType, e);
			return false;
		}
	}

	/**
	 * Packet-oriented compatibility read. The returned payload is derived from
	 * one frame snapshot and never from independently updated packet state.
	 */
	public CachedPayload get(String packetType)
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? null : snapshot.get(packetType);
	}

	public List<String> packetTypes()
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? List.of() : snapshot.packetTypes();
	}

	public long getUpdates()
	{
		return updates.get();
	}

	public long getUpdateErrors()
	{
		return updateErrors.get();
	}

	public long getLatestTick()
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? -1L : snapshot.getFrame().getSourceTick();
	}

	public long getLatestSequence()
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? 0L : snapshot.getSequence();
	}

	public long getEstimatedBytes()
	{
		FrameSnapshot snapshot = current.get();
		return snapshot == null ? 0L : snapshot.getEstimatedBytes();
	}

	public Map<String, Object> health()
	{
		return healthAt(current.get(), Instant.now());
	}

	Map<String, Object> healthAt(Instant now)
	{
		return healthAt(current.get(), now);
	}

	public Map<String, Object> health(FrameSnapshot snapshot)
	{
		return healthAt(snapshot, Instant.now());
	}

	Map<String, Object> healthAt(FrameSnapshot snapshot, Instant now)
	{
		Instant effectiveNow = now == null ? Instant.now() : now;
		Map<String, Object> health = new LinkedHashMap<>();
		Map<String, Long> latestTickByType = new LinkedHashMap<>();
		Map<String, Long> latestSequenceByType = new LinkedHashMap<>();
		Map<String, Long> ageMillisByType = new LinkedHashMap<>();
		Map<String, Long> payloadBytesByType = new LinkedHashMap<>();

		if (snapshot != null)
		{
			for (String packetType : snapshot.packetTypes())
			{
				CachedPayload payload = snapshot.get(packetType);
				if (payload == null)
				{
					continue;
				}
				latestTickByType.put(packetType, payload.tick);
				latestSequenceByType.put(packetType, payload.sequence);
				ageMillisByType.put(packetType, payload.ageMillis(effectiveNow));
				payloadBytesByType.put(packetType, payload.sizeBytes);
			}
		}

		health.put("liveCacheUpdates", getUpdates());
		health.put("liveCacheUpdateErrors", getUpdateErrors());
		health.put("liveCachePayloadTypes", snapshot == null ? List.of() : snapshot.packetTypes());
		health.put("liveCacheLatestTick", snapshot == null ? -1L : snapshot.getFrame().getSourceTick());
		health.put("liveCacheLatestSequence", snapshot == null ? sequence.get() : snapshot.getSequence());
		health.put("liveCacheEstimatedBytes", snapshot == null ? 0L : snapshot.getEstimatedBytes());
		health.put("liveCacheLatestTickByType", latestTickByType);
		health.put("liveCacheLatestSequenceByType", latestSequenceByType);
		health.put("liveCacheAgeMillisByType", ageMillisByType);
		health.put("liveCachePayloadBytesByType", payloadBytesByType);

		if (snapshot != null)
		{
			SensorFrame frame = snapshot.getFrame();
			health.put("liveCacheFrameSchema", frame.getSchema());
			health.put("liveCacheFrameId", frame.getFrameId());
			health.put("liveCacheFrameSourceTick", frame.getSourceTick());
			health.put("liveCacheFrameCapturedAtUtc", frame.getCapturedAtUtc());
			health.put("liveCacheFrameCompletedAtUtc", frame.getCompletedAtUtc());
			health.put("liveCacheFramePublishedAtUtc", snapshot.getPublishedAtUtc());
			health.put("liveCacheFrameCaptureDurationMillis", frame.getCaptureDurationMillis());
			health.put("liveCacheFrameAgeMillis", frame.ageMillis(effectiveNow));
			health.put("liveCacheFrameSessionId", frame.getSessionId());
			health.put("liveCacheFrameClientProcessId", frame.getClientProcessId());
			health.put("liveCacheFrameGeometryFrameId", frame.getGeometryFrameId());
			health.put("liveCacheFrameCoherent", frame.isCoherent());
			health.put("liveCacheFrameComplete", frame.isComplete());
			health.put("liveCacheFrameAvailableFacts", frame.getAvailableFacts());
			health.put("liveCacheFrameUnavailableFacts", frame.getUnavailableFacts());
		}
		else
		{
			health.put("liveCacheFrameSchema", SensorFrame.SCHEMA);
			health.put("liveCacheFrameId", null);
			health.put("liveCacheFrameSourceTick", -1L);
			health.put("liveCacheFrameCapturedAtUtc", null);
			health.put("liveCacheFrameCompletedAtUtc", null);
			health.put("liveCacheFramePublishedAtUtc", null);
			health.put("liveCacheFrameCaptureDurationMillis", -1L);
			health.put("liveCacheFrameAgeMillis", -1L);
			health.put("liveCacheFrameSessionId", null);
			health.put("liveCacheFrameClientProcessId", null);
			health.put("liveCacheFrameGeometryFrameId", null);
			health.put("liveCacheFrameCoherent", false);
			health.put("liveCacheFrameComplete", false);
			health.put("liveCacheFrameAvailableFacts", List.of());
			health.put("liveCacheFrameUnavailableFacts", SensorFrame.CORE_FACT_NAMES);
		}
		return health;
	}

	public static String factNameForPacketType(String packetType)
	{
		return packetType == null ? null : PACKET_TYPE_TO_FACT.get(packetType);
	}

	public static String packetTypeForFact(String factName)
	{
		return factName == null ? null : FACT_TO_PACKET_TYPE.get(factName);
	}

	private static Instant earlier(Instant left, Instant right)
	{
		return left.isBefore(right) ? left : right;
	}

	private static Instant later(Instant left, Instant right)
	{
		return left.isAfter(right) ? left : right;
	}

	private static Map<String, String> packetTypeToFact()
	{
		Map<String, String> result = new LinkedHashMap<>();
		result.put("live_baseline_packet.v1", SensorFrame.FACT_BASELINE);
		result.put("live_inventory_packet.v1", SensorFrame.FACT_INVENTORY);
		result.put("live_activity_packet.v1", SensorFrame.FACT_ACTIVITY);
		result.put("live_bank_ui_packet.v1", SensorFrame.FACT_BANK_UI);
		result.put("live_dialogue_state_packet.v1", SensorFrame.FACT_DIALOGUE_STATE);
		return Collections.unmodifiableMap(result);
	}

	private static Map<String, String> factToPacketType()
	{
		Map<String, String> result = new LinkedHashMap<>();
		for (Map.Entry<String, String> entry : PACKET_TYPE_TO_FACT.entrySet())
		{
			result.put(entry.getValue(), entry.getKey());
		}
		return Collections.unmodifiableMap(result);
	}

	/** A stable publication captured from the cache's atomic reference. */
	public static final class FrameSnapshot
	{
		private final SensorFrame frame;
		private final long sequence;
		private final String publishedAtUtc;
		private final long estimatedBytes;

		private FrameSnapshot(SensorFrame frame, long sequence, String publishedAtUtc)
		{
			this.frame = frame;
			this.sequence = sequence;
			this.publishedAtUtc = Instant.parse(publishedAtUtc).toString();
			long bytes = 0L;
			for (SensorFrame.Fact fact : frame.getFacts().values())
			{
				bytes += fact.getSizeBytes();
			}
			estimatedBytes = Math.max(0L, bytes);
		}

		public SensorFrame getFrame()
		{
			return frame;
		}

		public long getSequence()
		{
			return sequence;
		}

		public String getPublishedAtUtc()
		{
			return publishedAtUtc;
		}

		public long getEstimatedBytes()
		{
			return estimatedBytes;
		}

		public CachedPayload get(String packetType)
		{
			String factName = factNameForPacketType(packetType);
			SensorFrame.Fact fact = factName == null ? null : frame.getFact(factName);
			if (fact == null || !fact.isAvailable())
			{
				return null;
			}
			return new CachedPayload(packetType, sequence, publishedAtUtc, fact);
		}

		public List<String> packetTypes()
		{
			List<String> types = new ArrayList<>();
			for (String factName : frame.getAvailableFacts())
			{
				String packetType = packetTypeForFact(factName);
				if (packetType != null)
				{
					types.add(packetType);
				}
			}
			Collections.sort(types);
			return List.copyOf(types);
		}
	}

	/** Packet-compatible projection of one immutable frame fact. */
	public static final class CachedPayload
	{
		public final String packetType;
		public final long tick;
		public final long sequence;
		public final String timestampUtc;
		public final String updatedAtUtc;
		public final String cachedAtUtc;
		public final String payloadJson;
		public final long sizeBytes;

		private CachedPayload(
				String packetType,
				long sequence,
				String publishedAtUtc,
				SensorFrame.Fact fact)
		{
			this.packetType = packetType;
			this.tick = fact.getSourceTick();
			this.sequence = sequence;
			this.timestampUtc = fact.getCapturedAtUtc();
			this.updatedAtUtc = fact.getCapturedAtUtc();
			this.cachedAtUtc = publishedAtUtc;
			this.payloadJson = fact.getPayloadJson();
			this.sizeBytes = fact.getSizeBytes();
		}

		public long ageMillis()
		{
			return ageMillis(Instant.now());
		}

		private long ageMillis(Instant now)
		{
			try
			{
				return Math.max(0L, Duration.between(Instant.parse(timestampUtc), now).toMillis());
			}
			catch (RuntimeException e)
			{
				return -1L;
			}
		}
	}
}

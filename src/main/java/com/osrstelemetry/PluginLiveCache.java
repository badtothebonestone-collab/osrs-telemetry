package com.osrstelemetry;

import com.google.gson.Gson;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class PluginLiveCache
{
	private final Gson gson;
	private final ConcurrentHashMap<String, CachedPayload> payloads = new ConcurrentHashMap<>();
	private final AtomicLong sequence = new AtomicLong();
	private final AtomicLong updates = new AtomicLong();
	private final AtomicLong updateErrors = new AtomicLong();
	private final AtomicLong estimatedBytes = new AtomicLong();

	public PluginLiveCache(Gson gson)
	{
		this.gson = gson;
	}

	public boolean update(String packetType, long tick, String timestampUtc, Object payload)
	{
		if (packetType == null || packetType.isBlank() || timestampUtc == null || gson == null)
		{
			return false;
		}

		try
		{
			long nextSequence = sequence.incrementAndGet();
			String now = Instant.now().toString();
			String payloadJson = gson.toJson(payload);
			CachedPayload cachedPayload = new CachedPayload(
					packetType,
					tick,
					nextSequence,
					timestampUtc,
					now,
					now,
					payloadJson,
					estimateBytes(payloadJson));

			CachedPayload previous = payloads.put(packetType, cachedPayload);
			if (previous != null)
			{
				estimatedBytes.addAndGet(-previous.sizeBytes);
			}
			estimatedBytes.addAndGet(cachedPayload.sizeBytes);
			updates.incrementAndGet();
			return true;
		}
		catch (RuntimeException e)
		{
			updateErrors.incrementAndGet();
			log.debug("Failed to update plugin live cache for {}", packetType, e);
			return false;
		}
	}

	public CachedPayload get(String packetType)
	{
		return packetType == null ? null : payloads.get(packetType);
	}

	public List<String> packetTypes()
	{
		List<String> types = new ArrayList<>(payloads.keySet());
		Collections.sort(types);
		return types;
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
		long latest = -1L;
		for (CachedPayload payload : payloads.values())
		{
			latest = Math.max(latest, payload.tick);
		}
		return latest;
	}

	public long getLatestSequence()
	{
		return sequence.get();
	}

	public long getEstimatedBytes()
	{
		return Math.max(0L, estimatedBytes.get());
	}

	public Map<String, Object> health()
	{
		Map<String, Object> health = new LinkedHashMap<>();
		Map<String, Long> latestTickByType = new LinkedHashMap<>();
		Map<String, Long> latestSequenceByType = new LinkedHashMap<>();
		Map<String, Long> ageMillisByType = new LinkedHashMap<>();
		Map<String, Long> payloadBytesByType = new LinkedHashMap<>();
		Instant now = Instant.now();

		for (String packetType : packetTypes())
		{
			CachedPayload payload = payloads.get(packetType);
			if (payload == null)
			{
				continue;
			}

			latestTickByType.put(packetType, payload.tick);
			latestSequenceByType.put(packetType, payload.sequence);
			ageMillisByType.put(packetType, payload.ageMillis(now));
			payloadBytesByType.put(packetType, payload.sizeBytes);
		}

		health.put("liveCacheUpdates", getUpdates());
		health.put("liveCacheUpdateErrors", getUpdateErrors());
		health.put("liveCachePayloadTypes", packetTypes());
		health.put("liveCacheLatestTick", getLatestTick());
		health.put("liveCacheLatestSequence", getLatestSequence());
		health.put("liveCacheEstimatedBytes", getEstimatedBytes());
		health.put("liveCacheLatestTickByType", latestTickByType);
		health.put("liveCacheLatestSequenceByType", latestSequenceByType);
		health.put("liveCacheAgeMillisByType", ageMillisByType);
		health.put("liveCachePayloadBytesByType", payloadBytesByType);
		return health;
	}

	private long estimateBytes(String payloadJson)
	{
		return payloadJson == null ? 4L : payloadJson.length();
	}

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
				long tick,
				long sequence,
				String timestampUtc,
				String updatedAtUtc,
				String cachedAtUtc,
				String payloadJson,
				long sizeBytes)
		{
			this.packetType = packetType;
			this.tick = tick;
			this.sequence = sequence;
			this.timestampUtc = timestampUtc;
			this.updatedAtUtc = updatedAtUtc;
			this.cachedAtUtc = cachedAtUtc;
			this.payloadJson = payloadJson;
			this.sizeBytes = sizeBytes;
		}

		public long ageMillis()
		{
			return ageMillis(Instant.now());
		}

		private long ageMillis(Instant now)
		{
			try
			{
				return Math.max(0L, Duration.between(Instant.parse(cachedAtUtc), now).toMillis());
			}
			catch (RuntimeException e)
			{
				return -1L;
			}
		}
	}
}

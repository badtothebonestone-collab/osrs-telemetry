package com.osrstelemetry;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

class ClientTickHotState
{
	static final String SCHEMA = "client_tick_hot.v1";
	static final int DEFAULT_SAMPLE_CAP = 128;
	private static final int DEFAULT_MENU_ENTRY_LIMIT = 5;

	private final int sampleCap;
	private final ArrayDeque<Map<String, Object>> clientTickSamples = new ArrayDeque<>();
	private final ArrayDeque<Map<String, Object>> postMenuSortSamples = new ArrayDeque<>();
	private final ArrayDeque<Map<String, Object>> clickedSamples = new ArrayDeque<>();
	private Map<String, Object> latestClientTick;
	private Map<String, Object> latestPostMenuSort;
	private Map<String, Object> latestMenuOptionClicked;
	private long droppedClientTickSamples;
	private long droppedPostMenuSortSamples;
	private long droppedClickedSamples;

	ClientTickHotState()
	{
		this(DEFAULT_SAMPLE_CAP);
	}

	ClientTickHotState(int sampleCap)
	{
		this.sampleCap = Math.max(1, sampleCap);
	}

	synchronized void recordClientTick(Map<String, Object> sample)
	{
		Map<String, Object> copy = copySample(sample);
		if (copy == null)
		{
			return;
		}
		latestClientTick = copy;
		droppedClientTickSamples += appendBounded(clientTickSamples, copy);
	}

	synchronized void recordPostMenuSort(Map<String, Object> sample)
	{
		Map<String, Object> copy = copySample(sample);
		if (copy == null)
		{
			return;
		}
		latestPostMenuSort = copy;
		droppedPostMenuSortSamples += appendBounded(postMenuSortSamples, copy);
	}

	synchronized void recordMenuOptionClicked(Map<String, Object> sample)
	{
		Map<String, Object> copy = copySample(sample);
		if (copy == null)
		{
			return;
		}
		latestMenuOptionClicked = copy;
		droppedClickedSamples += appendBounded(clickedSamples, copy);
	}

	synchronized Map<String, Object> latestPostMenuSort()
	{
		return copySample(latestPostMenuSort);
	}

	synchronized Map<String, Object> latestMenuOptionClicked()
	{
		return copySample(latestMenuOptionClicked);
	}

	synchronized Map<String, Object> snapshot(
			int maxClientTickSamples,
			int maxMenuSamples,
			int maxClickedSamples,
			boolean includeMenuEntries,
			int menuEntryLimit)
	{
		long now = System.currentTimeMillis();
		int effectiveMenuEntryLimit = Math.max(0, menuEntryLimit <= 0 ? DEFAULT_MENU_ENTRY_LIMIT : menuEntryLimit);
		Map<String, Object> payload = new LinkedHashMap<>();
		Map<String, Object> clientTick = trimmedSample(latestClientTick, includeMenuEntries, effectiveMenuEntryLimit);
		Map<String, Object> postMenuSort = trimmedSample(latestPostMenuSort, includeMenuEntries, effectiveMenuEntryLimit);
		Map<String, Object> clicked = trimmedSample(latestMenuOptionClicked, includeMenuEntries, effectiveMenuEntryLimit);
		Map<String, Object> metadata = latestMetadataSample(clientTick, postMenuSort, clicked);

		payload.put("schema", SCHEMA);
		payload.put("clientTick", longValue(metadata, "clientTick"));
		payload.put("wallTimeMillis", longValue(metadata, "wallTimeMillis"));
		payload.put("monotonicTimeNanos", longValue(metadata, "monotonicTimeNanos"));
		payload.put("gameTickAtSample", longValue(metadata, "gameTickAtSample"));
		payload.put("gameState", metadata == null ? null : metadata.get("gameState"));
		payload.put("sampleSource", metadata == null ? null : metadata.get("sampleSource"));
		payload.put("sourceEvent", metadata == null ? null : metadata.get("sourceEvent"));
		payload.put("sessionId", metadata == null ? null : metadata.get("sessionId"));
		payload.put("clientProcessId", metadata == null ? null : metadata.get("clientProcessId"));
		payload.put("mouse", mousePayload(clientTick, postMenuSort, clicked));
		payload.put("postMenuSort", postMenuSort);
		payload.put("hoverMenu", postMenuSort);
		payload.put("lastMenuOptionClicked", clicked);
		payload.put("latency", latencyPayload(now));

		if (maxClientTickSamples > 0)
		{
			payload.put("clientTickTail", tail(clientTickSamples, maxClientTickSamples, includeMenuEntries, effectiveMenuEntryLimit));
		}
		if (maxMenuSamples > 0)
		{
			payload.put("postMenuSortTail", tail(postMenuSortSamples, maxMenuSamples, includeMenuEntries, effectiveMenuEntryLimit));
		}
		if (maxClickedSamples > 0)
		{
			payload.put("clickedTail", tail(clickedSamples, maxClickedSamples, includeMenuEntries, effectiveMenuEntryLimit));
		}

		return payload;
	}

	private int appendBounded(ArrayDeque<Map<String, Object>> target, Map<String, Object> sample)
	{
		int dropped = 0;
		while (target.size() >= sampleCap)
		{
			target.removeFirst();
			dropped++;
		}
		target.addLast(sample);
		return dropped;
	}

	private Map<String, Object> latencyPayload(long now)
	{
		Map<String, Object> latency = new LinkedHashMap<>();
		latency.put("ageMillis", ageMillis(latestClientTick, now));
		latency.put("postMenuSortAgeMillis", ageMillis(latestPostMenuSort, now));
		latency.put("lastClickAgeMillis", ageMillis(latestMenuOptionClicked, now));
		Long gameTick = firstLong(latestClientTick, latestPostMenuSort, latestMenuOptionClicked, "gameTickAtSample");
		Long clientTick = firstLong(latestClientTick, latestPostMenuSort, latestMenuOptionClicked, "clientTick");
		latency.put("clientTicksSinceGameTick", gameTick == null || clientTick == null ? null : Math.max(0L, clientTick - gameTick));
		latency.put("samplesBuffered", clientTickSamples.size() + postMenuSortSamples.size() + clickedSamples.size());
		latency.put("clientTickSamplesBuffered", clientTickSamples.size());
		latency.put("postMenuSortSamplesBuffered", postMenuSortSamples.size());
		latency.put("clickedSamplesBuffered", clickedSamples.size());
		latency.put("droppedSamples", droppedClientTickSamples + droppedPostMenuSortSamples + droppedClickedSamples);
		latency.put("droppedClientTickSamples", droppedClientTickSamples);
		latency.put("droppedPostMenuSortSamples", droppedPostMenuSortSamples);
		latency.put("droppedClickedSamples", droppedClickedSamples);
		return latency;
	}

	private Long ageMillis(Map<String, Object> sample, long now)
	{
		Long wallTime = longValue(sample, "wallTimeMillis");
		return wallTime == null ? null : Math.max(0L, now - wallTime);
	}

	private List<Map<String, Object>> tail(
			ArrayDeque<Map<String, Object>> samples,
			int requested,
			boolean includeMenuEntries,
			int menuEntryLimit)
	{
		int limit = Math.max(0, Math.min(sampleCap, requested));
		List<Map<String, Object>> items = new ArrayList<>();
		if (limit <= 0 || samples.isEmpty())
		{
			return items;
		}
		int skip = Math.max(0, samples.size() - limit);
		int index = 0;
		for (Map<String, Object> sample : samples)
		{
			if (index++ < skip)
			{
				continue;
			}
			items.add(trimmedSample(sample, includeMenuEntries, menuEntryLimit));
		}
		return items;
	}

	private Map<String, Object> mousePayload(Map<String, Object>... samples)
	{
		Map<String, Object> mouse = new LinkedHashMap<>();
		mouse.put("canvasX", firstLong(samples, "mouseCanvasX"));
		mouse.put("canvasY", firstLong(samples, "mouseCanvasY"));
		mouse.put("isInCanvas", firstBoolean(samples, "isInCanvas"));
		mouse.put("lastMoveWallTimeMillis", firstLong(samples, "lastMoveWallTimeMillis"));
		mouse.put("lastMoveClientTick", firstLong(samples, "lastMoveClientTick"));
		return mouse;
	}

	private Map<String, Object> latestMetadataSample(Map<String, Object>... samples)
	{
		Map<String, Object> best = null;
		Long bestTime = null;
		for (Map<String, Object> sample : samples)
		{
			if (sample == null)
			{
				continue;
			}
			Long wallTime = longValue(sample, "wallTimeMillis");
			if (best == null || (wallTime != null && (bestTime == null || wallTime >= bestTime)))
			{
				best = sample;
				bestTime = wallTime;
			}
		}
		return best;
	}

	private Map<String, Object> trimmedSample(Map<String, Object> sample, boolean includeMenuEntries, int menuEntryLimit)
	{
		Map<String, Object> copy = copySample(sample);
		if (copy == null)
		{
			return null;
		}
		Object entries = copy.get("entries");
		if (!includeMenuEntries)
		{
			copy.remove("entries");
		}
		else if (entries instanceof List)
		{
			List<?> list = (List<?>) entries;
			int limit = Math.max(0, menuEntryLimit);
			if (list.size() > limit)
			{
				copy.put("entries", new ArrayList<>(list.subList(0, limit)));
			}
		}
		return copy;
	}

	private Map<String, Object> copySample(Map<String, Object> sample)
	{
		return sample == null ? null : new LinkedHashMap<>(sample);
	}

	private Long firstLong(Map<String, Object>[] samples, String key)
	{
		for (Map<String, Object> sample : samples)
		{
			Long value = longValue(sample, key);
			if (value != null)
			{
				return value;
			}
		}
		return null;
	}

	private Long firstLong(Map<String, Object> first, Map<String, Object> second, Map<String, Object> third, String key)
	{
		return firstLong(new Map[] {first, second, third}, key);
	}

	private String firstString(Map<String, Object> first, Map<String, Object> second, Map<String, Object> third, String key)
	{
		for (Map<String, Object> sample : new Map[] {first, second, third})
		{
			if (sample != null && sample.get(key) != null)
			{
				return String.valueOf(sample.get(key));
			}
		}
		return null;
	}

	private Boolean firstBoolean(Map<String, Object>[] samples, String key)
	{
		for (Map<String, Object> sample : samples)
		{
			if (sample != null && sample.get(key) instanceof Boolean)
			{
				return (Boolean) sample.get(key);
			}
		}
		return null;
	}

	private Long longValue(Map<String, Object> sample, String key)
	{
		if (sample == null)
		{
			return null;
		}
		Object value = sample.get(key);
		return value instanceof Number ? ((Number) value).longValue() : null;
	}
}

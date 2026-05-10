package com.osrstelemetry;

import com.google.gson.Gson;
import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class LivePacketWriter implements Closeable
{
	public static final String LIVE_PACKETS_DIR = "live_packets";
	public static final String INDEX_FILE_NAME = "live_packet_index.json";
	public static final String LATEST_SEGMENT_FILE_NAME = "latest_segment.txt";
	public static final String INDEX_SCHEMA = "live_packet_index.v1";
	private static final String LIVE_PACKET_FILE_PREFIX = "live";
	private static final String LIVE_PACKET_FILE_SUFFIX = ".ndjson";
	private static final int INDEX_WRITE_INTERVAL_PACKETS = 25;

	private static class SegmentMetadata
	{
		String path;
		Long firstSequence;
		Long lastSequence;
		Long firstTick;
		Long lastTick;
		long bytes;
		Map<String, Long> packetCountsByType = new LinkedHashMap<>();
		boolean active;
		String openedAtUtc;
		String closedAtUtc;

		void record(LivePacket packet, long lineBytes, boolean activeSegment)
		{
			if (firstSequence == null)
			{
				firstSequence = packet.sequence;
			}

			if (firstTick == null)
			{
				firstTick = packet.tick;
			}

			lastSequence = packet.sequence;
			lastTick = packet.tick;
			bytes += lineBytes;
			active = activeSegment;
			packetCountsByType.put(
					packet.packetType,
					packetCountsByType.getOrDefault(packet.packetType, 0L) + 1L);
		}
	}

	private static class LivePacketIndex
	{
		String schema;
		String sessionPath;
		boolean enabled;
		String activeSegment;
		String latestSegment;
		List<SegmentMetadata> segments;
		Long latestTick;
		Long latestSequence;
		long totalBytes;
		long retentionBytes;
		long retentionSegments;
		long retentionTicks;
		long prunedCount;
		String generatedAtUtc;
	}

	private final String sessionId;
	private final Path sessionDir;
	private final Path livePacketsDir;
	private final Path indexFile;
	private final Path latestSegmentFile;
	private final Gson gson;
	private final LinkedBlockingQueue<LivePacket> queue;
	private final long maxSegmentBytes;
	private final long retentionBytes;
	private final long retentionTicks;
	private final long retentionSegments;
	private final AtomicLong sequence = new AtomicLong();
	private final AtomicLong droppedPackets = new AtomicLong();
	private final AtomicLong writtenPackets = new AtomicLong();
	private final AtomicLong writeErrors = new AtomicLong();
	private final AtomicLong prunedSegments = new AtomicLong();
	private final List<SegmentMetadata> segments = new ArrayList<>();

	private volatile boolean running = false;
	private Thread worker;
	private BufferedWriter writer;
	private Path currentSegment;
	private SegmentMetadata currentSegmentMetadata;
	private int segmentIndex = 1;
	private long currentSegmentBytes;
	private long latestTick = -1;
	private long lastWriteMillis = -1;
	private long packetsSinceIndexWrite;

	public LivePacketWriter(
			String sessionId,
			Path sessionDir,
			Gson gson,
			int maxSegmentMb,
			long retentionTicks,
			long retentionBytes,
			long retentionSegments,
			int queueSize)
	{
		this.sessionId = sessionId;
		this.sessionDir = sessionDir;
		this.livePacketsDir = sessionDir.resolve(LIVE_PACKETS_DIR);
		this.indexFile = livePacketsDir.resolve(INDEX_FILE_NAME);
		this.latestSegmentFile = livePacketsDir.resolve(LATEST_SEGMENT_FILE_NAME);
		this.gson = gson;
		this.maxSegmentBytes = Math.max(1L, maxSegmentMb) * 1024L * 1024L;
		this.retentionTicks = Math.max(0L, retentionTicks);
		this.retentionBytes = Math.max(0L, retentionBytes);
		this.retentionSegments = Math.max(0L, retentionSegments);
		this.queue = new LinkedBlockingQueue<>(Math.max(1, queueSize));
	}

	public void start() throws IOException
	{
		Files.createDirectories(livePacketsDir);
		segmentIndex = nextSegmentIndex();
		openSegment();
		writeIndexAndLatestPointer();
		running = true;
		worker = new Thread(this::runWriterLoop, "telemetry-live-packet-writer");
		worker.setDaemon(true);
		worker.start();
	}

	public boolean enqueue(String packetType, long tick, String timestampUtc, Object payload)
	{
		if (!running || packetType == null || timestampUtc == null)
		{
			return false;
		}

		LivePacket packet = createPacket(packetType, tick, timestampUtc, payload);

		if (!queue.offer(packet))
		{
			long dropped = droppedPackets.incrementAndGet();

			if (dropped == 1 || dropped % 1000 == 0)
			{
				log.warn("Compact live packet queue full; dropped {} packets", dropped);
			}

			return false;
		}

		return true;
	}

	LivePacket createPacket(String packetType, long tick, String timestampUtc, Object payload)
	{
		return new LivePacket(packetType, sessionId, tick, sequence.incrementAndGet(), timestampUtc, payload);
	}

	void writePacketForTest(LivePacket packet) throws IOException
	{
		if (writer == null)
		{
			Files.createDirectories(livePacketsDir);
			segmentIndex = nextSegmentIndex();
			openSegment();
		}

		writePacket(packet);
	}

	public int getQueueDepth()
	{
		return queue.size();
	}

	public long getDroppedPackets()
	{
		return droppedPackets.get();
	}

	public long getWrittenPackets()
	{
		return writtenPackets.get();
	}

	public long getWriteErrors()
	{
		return writeErrors.get();
	}

	public long getLastWriteMillis()
	{
		return lastWriteMillis;
	}

	public long getSegmentCount()
	{
		return segments.size();
	}

	public long getTotalBytes()
	{
		long total = 0L;

		for (SegmentMetadata segment : segments)
		{
			total += segment.bytes;
		}

		return total;
	}

	public long getPrunedSegments()
	{
		return prunedSegments.get();
	}

	public long getRetentionBytes()
	{
		return retentionBytes;
	}

	public long getRetentionSegments()
	{
		return retentionSegments;
	}

	public String getActiveSegmentName()
	{
		return currentSegment == null ? null : currentSegment.getFileName().toString();
	}

	public Path getLivePacketsDir()
	{
		return livePacketsDir;
	}

	private void runWriterLoop()
	{
		try
		{
			while (running || !queue.isEmpty())
			{
				LivePacket packet = running ? queue.poll(250, TimeUnit.MILLISECONDS) : queue.poll();

				if (packet != null)
				{
					try
					{
						writePacket(packet);
					}
					catch (IOException e)
					{
						writeErrors.incrementAndGet();
						log.warn("Compact live packet write failed", e);
					}
				}
			}
		}
		catch (InterruptedException e)
		{
			drainQueue();
			Thread.currentThread().interrupt();
		}
		finally
		{
			drainQueue();
			writeIndexQuietly();
			closeWriter();
		}
	}

	private void drainQueue()
	{
		LivePacket packet;

		while ((packet = queue.poll()) != null)
		{
			try
			{
				writePacket(packet);
			}
			catch (IOException e)
			{
				writeErrors.incrementAndGet();
				log.warn("Compact live packet write failed while draining", e);
				return;
			}
		}
	}

	private void writePacket(LivePacket packet) throws IOException
	{
		long startMillis = System.currentTimeMillis();
		String json = gson.toJson(packet);
		long bytes = writeJsonLine(json);
		currentSegmentBytes += bytes;
		latestTick = Math.max(latestTick, packet.tick);
		currentSegmentMetadata.record(packet, bytes, true);
		writtenPackets.incrementAndGet();
		lastWriteMillis = System.currentTimeMillis() - startMillis;
		packetsSinceIndexWrite++;

		if (currentSegmentBytes >= maxSegmentBytes)
		{
			rotateSegment();
			return;
		}

		if (packetsSinceIndexWrite >= INDEX_WRITE_INTERVAL_PACKETS)
		{
			runRetention();
			writeIndexAndLatestPointer();
			packetsSinceIndexWrite = 0;
		}
	}

	private long writeJsonLine(String json) throws IOException
	{
		writer.write(json);
		writer.newLine();
		writer.flush();
		return json.getBytes(StandardCharsets.UTF_8).length + System.lineSeparator().getBytes(StandardCharsets.UTF_8).length;
	}

	private void rotateSegment() throws IOException
	{
		if (currentSegmentMetadata != null)
		{
			currentSegmentMetadata.active = false;
			currentSegmentMetadata.closedAtUtc = Instant.now().toString();
		}

		closeWriter();
		segmentIndex++;
		openSegment();
		runRetention();
		writeIndexAndLatestPointer();
		packetsSinceIndexWrite = 0;
	}

	private void openSegment() throws IOException
	{
		currentSegment = livePacketsDir.resolve(segmentFileName(segmentIndex));
		writer = Files.newBufferedWriter(
				currentSegment,
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.APPEND);
		currentSegmentBytes = Files.size(currentSegment);
		currentSegmentMetadata = new SegmentMetadata();
		currentSegmentMetadata.path = currentSegment.getFileName().toString();
		currentSegmentMetadata.bytes = currentSegmentBytes;
		currentSegmentMetadata.active = true;
		currentSegmentMetadata.openedAtUtc = Instant.now().toString();
		segments.add(currentSegmentMetadata);
	}

	private int nextSegmentIndex() throws IOException
	{
		int next = 1;

		try (DirectoryStream<Path> stream = Files.newDirectoryStream(livePacketsDir, LIVE_PACKET_FILE_PREFIX + "-*" + LIVE_PACKET_FILE_SUFFIX))
		{
			for (Path path : stream)
			{
				String name = path.getFileName().toString();
				int dash = name.indexOf('-');
				int dot = name.lastIndexOf('.');

				if (dash < 0 || dot <= dash)
				{
					continue;
				}

				try
				{
					next = Math.max(next, Integer.parseInt(name.substring(dash + 1, dot)) + 1);
				}
				catch (NumberFormatException ignored)
				{
					// Ignore unrelated files that happen to match the glob.
				}
			}
		}

		return next;
	}

	private String segmentFileName(int index)
	{
		return String.format("%s-%06d%s", LIVE_PACKET_FILE_PREFIX, index, LIVE_PACKET_FILE_SUFFIX);
	}

	private void runRetention()
	{
		try
		{
			pruneByTick();
			pruneBySegmentCount();
			pruneByBytes();
		}
		catch (IOException e)
		{
			writeErrors.incrementAndGet();
			log.warn("Compact live packet retention failed", e);
		}
	}

	private void pruneByTick()
	{
		if (retentionTicks <= 0 || latestTick < 0)
		{
			return;
		}

		long oldestAllowedTick = latestTick - retentionTicks;

		for (SegmentMetadata segment : new ArrayList<>(segments))
		{
			if (segment.active || segment.lastTick == null || segment.lastTick >= oldestAllowedTick)
			{
				continue;
			}

			deleteSegment(segment);
		}
	}

	private void pruneBySegmentCount()
	{
		if (retentionSegments <= 0)
		{
			return;
		}

		while (segments.size() > retentionSegments)
		{
			SegmentMetadata oldest = oldestCompletedSegment();

			if (oldest == null)
			{
				return;
			}

			deleteSegment(oldest);
		}
	}

	private void pruneByBytes() throws IOException
	{
		if (retentionBytes <= 0)
		{
			return;
		}

		long totalBytes = getTotalBytes();

		while (totalBytes > retentionBytes)
		{
			SegmentMetadata oldest = oldestCompletedSegment();

			if (oldest == null)
			{
				return;
			}

			long size = oldest.bytes;

			if (!deleteSegment(oldest))
			{
				return;
			}

			totalBytes -= size;
		}
	}

	private SegmentMetadata oldestCompletedSegment()
	{
		return segments.stream()
				.filter(segment -> !segment.active)
				.min(Comparator.comparing(segment -> segment.firstSequence == null ? Long.MAX_VALUE : segment.firstSequence))
				.orElse(null);
	}

	private boolean deleteSegment(SegmentMetadata segment)
	{
		if (segment == null || segment.active)
		{
			return false;
		}

		Path path = livePacketsDir.resolve(segment.path).normalize();

		if (!path.startsWith(livePacketsDir.normalize()) || !isLiveSegmentFile(path))
		{
			log.warn("Refusing to prune compact live packet file outside live packet directory: {}", path);
			return false;
		}

		try
		{
			boolean deleted = Files.deleteIfExists(path);

			if (deleted)
			{
				segments.remove(segment);
				prunedSegments.incrementAndGet();
			}

			return deleted;
		}
		catch (IOException e)
		{
			log.debug("Failed to prune compact live packet segment {}", path, e);
			return false;
		}
	}

	private boolean isLiveSegmentFile(Path path)
	{
		String name = path.getFileName().toString();
		return name.startsWith(LIVE_PACKET_FILE_PREFIX + "-") && name.endsWith(LIVE_PACKET_FILE_SUFFIX);
	}

	private void writeIndexAndLatestPointer() throws IOException
	{
		writeIndex();
		writeLatestPointer();
	}

	private void writeIndex() throws IOException
	{
		LivePacketIndex index = new LivePacketIndex();
		index.schema = INDEX_SCHEMA;
		index.sessionPath = sessionDir.toString();
		index.enabled = true;
		index.activeSegment = getActiveSegmentName();
		index.latestSegment = getActiveSegmentName();
		index.segments = new ArrayList<>(segments);
		index.latestTick = latestTick >= 0 ? latestTick : null;
		index.latestSequence = sequence.get() > 0 ? sequence.get() : null;
		index.totalBytes = getTotalBytes();
		index.retentionBytes = retentionBytes;
		index.retentionSegments = retentionSegments;
		index.retentionTicks = retentionTicks;
		index.prunedCount = prunedSegments.get();
		index.generatedAtUtc = Instant.now().toString();

		Files.writeString(
				indexFile,
				gson.toJson(index),
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.TRUNCATE_EXISTING);
	}

	private void writeLatestPointer() throws IOException
	{
		String latestSegment = getActiveSegmentName();

		if (latestSegment == null)
		{
			return;
		}

		Files.writeString(
				latestSegmentFile,
				latestSegment + System.lineSeparator(),
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.TRUNCATE_EXISTING);
	}

	private void writeIndexQuietly()
	{
		try
		{
			writeIndexAndLatestPointer();
		}
		catch (IOException e)
		{
			writeErrors.incrementAndGet();
			log.warn("Failed to write compact live packet index", e);
		}
	}

	private void closeWriter()
	{
		if (writer == null)
		{
			return;
		}

		try
		{
			writer.close();
		}
		catch (IOException e)
		{
			log.warn("Failed to close compact live packet writer", e);
		}
		finally
		{
			writer = null;
		}
	}

	@Override
	public void close()
	{
		running = false;

		if (worker != null)
		{
			worker.interrupt();
			worker = null;
			return;
		}

		writeIndexQuietly();
		closeWriter();
	}
}

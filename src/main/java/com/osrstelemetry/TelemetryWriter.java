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
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class TelemetryWriter implements Closeable
{
	private static final String STREAM_TICKS = "ticks";
	private static final String STREAM_EVENTS = "events";
	private static final String SCHEMA_VERSION = "0.1.0";
	private static final int QUEUE_CAPACITY = 100_000;

	private static class QueuedLine
	{
		final String stream;
		final String json;

		QueuedLine(String stream, String json)
		{
			this.stream = stream;
			this.json = json;
		}
	}

	private static class Manifest
	{
		String sessionId;
		String startedAtUtc;
		String endedAtUtc;
		String schemaVersion;
		boolean active;
		String currentTickSegment;
		String currentEventSegment;
		int tickSegmentIndex;
		int eventSegmentIndex;
		long tickCount;
		long eventCount;
		long droppedRecords;
		String lastUpdatedUtc;
	}

	private final Gson gson;
	private final LinkedBlockingQueue<QueuedLine> queue = new LinkedBlockingQueue<>(QUEUE_CAPACITY);
	private final Path sessionsRoot;
	private final Path sessionDir;
	private final Path ticksDir;
	private final Path eventsDir;
	private final Path manifestFile;
	private final String sessionId;
	private final long maxSegmentBytes;
	private final boolean retentionEnabled;
	private final long maxTelemetryBytes;
	private final long cleanupIntervalMillis;
	private final boolean preservePinnedSessions;
	private final boolean allowDeletingClosedSegmentsFromActiveSession;
	private final AtomicLong droppedRecords = new AtomicLong();
	private final Manifest manifest = new Manifest();

	private volatile boolean running = false;
	private Thread worker;
	private BufferedWriter tickWriter;
	private BufferedWriter eventWriter;
	private Path currentTickSegment;
	private Path currentEventSegment;
	private long currentTickBytes;
	private long currentEventBytes;
	private long nextCleanupAtMillis;

	public TelemetryWriter(
			String outputDirectory,
			Gson gson,
			int maxSegmentMb,
			boolean retentionEnabled,
			int maxTelemetryGb,
			int cleanupIntervalSeconds,
			boolean preservePinnedSessions,
			boolean allowDeletingClosedSegmentsFromActiveSession)
	{
		this.gson = gson;
		this.sessionsRoot = Path.of(outputDirectory);
		this.sessionId = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
		this.sessionDir = sessionsRoot.resolve(sessionId);
		this.ticksDir = sessionDir.resolve(STREAM_TICKS);
		this.eventsDir = sessionDir.resolve(STREAM_EVENTS);
		this.manifestFile = sessionDir.resolve("manifest.json");
		this.maxSegmentBytes = Math.max(1L, maxSegmentMb) * 1024L * 1024L;
		this.retentionEnabled = retentionEnabled;
		this.maxTelemetryBytes = Math.max(1L, maxTelemetryGb) * 1024L * 1024L * 1024L;
		this.cleanupIntervalMillis = Duration.ofSeconds(Math.max(1L, cleanupIntervalSeconds)).toMillis();
		this.preservePinnedSessions = preservePinnedSessions;
		this.allowDeletingClosedSegmentsFromActiveSession = allowDeletingClosedSegmentsFromActiveSession;
	}

	public void start() throws IOException
	{
		Files.createDirectories(ticksDir);
		Files.createDirectories(eventsDir);

		manifest.sessionId = sessionId;
		manifest.startedAtUtc = Instant.now().toString();
		manifest.schemaVersion = SCHEMA_VERSION;
		manifest.active = true;
		manifest.tickSegmentIndex = 1;
		manifest.eventSegmentIndex = 1;

		openTickSegment();
		openEventSegment();
		writeManifest();

		running = true;
		nextCleanupAtMillis = System.currentTimeMillis() + cleanupIntervalMillis;
		worker = new Thread(this::runWriterLoop, "telemetry-writer");
		worker.setDaemon(true);
		worker.start();

		log.info("Telemetry session started: {}", sessionDir);
	}

	public void enqueueTick(String json)
	{
		enqueue(new QueuedLine(STREAM_TICKS, json));
	}

	public void enqueueEvent(String json)
	{
		enqueue(new QueuedLine(STREAM_EVENTS, json));
	}

	public int getQueueSize()
	{
		return queue.size();
	}

	public long getDroppedRecords()
	{
		return droppedRecords.get();
	}

	private void enqueue(QueuedLine line)
	{
		if (!queue.offer(line))
		{
			long dropped = droppedRecords.incrementAndGet();

			if (dropped == 1 || dropped % 1000 == 0)
			{
				log.warn("Telemetry writer queue full; dropped {} records", dropped);
			}
		}
	}

	private void runWriterLoop()
	{
		try
		{
			while (running || !queue.isEmpty())
			{
				QueuedLine line = running ? queue.poll(250, TimeUnit.MILLISECONDS) : queue.poll();

				if (line != null)
				{
					writeLine(line);
				}

				maybeRunRetention();
			}
		}
		catch (InterruptedException e)
		{
			drainQueue();
			Thread.currentThread().interrupt();
		}
		catch (IOException e)
		{
			log.warn("Telemetry writer failed", e);
		}
		finally
		{
			drainQueue();
			closeWriter(tickWriter, "ticks");
			closeWriter(eventWriter, "events");
			manifest.active = false;
			manifest.endedAtUtc = Instant.now().toString();
			manifest.droppedRecords = droppedRecords.get();
			writeManifestQuietly();
		}
	}

	private void writeLine(QueuedLine line) throws IOException
	{
		if (STREAM_TICKS.equals(line.stream))
		{
			writeTick(line.json);
		}
		else if (STREAM_EVENTS.equals(line.stream))
		{
			writeEvent(line.json);
		}
	}

	private void writeTick(String json) throws IOException
	{
		long bytes = writeJsonLine(tickWriter, json);
		currentTickBytes += bytes;
		manifest.tickCount++;
		manifest.droppedRecords = droppedRecords.get();
		writeManifest();

		if (currentTickBytes >= maxSegmentBytes)
		{
			rotateTickSegment();
		}
	}

	private void writeEvent(String json) throws IOException
	{
		long bytes = writeJsonLine(eventWriter, json);
		currentEventBytes += bytes;
		manifest.eventCount++;
		manifest.droppedRecords = droppedRecords.get();
		writeManifest();

		if (currentEventBytes >= maxSegmentBytes)
		{
			rotateEventSegment();
		}
	}

	private long writeJsonLine(BufferedWriter writer, String json) throws IOException
	{
		writer.write(json);
		writer.newLine();
		writer.flush();
		return json.getBytes(StandardCharsets.UTF_8).length + System.lineSeparator().getBytes(StandardCharsets.UTF_8).length;
	}

	private void openTickSegment() throws IOException
	{
		currentTickSegment = ticksDir.resolve(segmentFileName(STREAM_TICKS, manifest.tickSegmentIndex));
		tickWriter = Files.newBufferedWriter(
				currentTickSegment,
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.APPEND);
		currentTickBytes = Files.size(currentTickSegment);
		manifest.currentTickSegment = sessionDir.relativize(currentTickSegment).toString().replace('\\', '/');
	}

	private void openEventSegment() throws IOException
	{
		currentEventSegment = eventsDir.resolve(segmentFileName(STREAM_EVENTS, manifest.eventSegmentIndex));
		eventWriter = Files.newBufferedWriter(
				currentEventSegment,
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.APPEND);
		currentEventBytes = Files.size(currentEventSegment);
		manifest.currentEventSegment = sessionDir.relativize(currentEventSegment).toString().replace('\\', '/');
	}

	private void rotateTickSegment() throws IOException
	{
		closeWriter(tickWriter, "ticks");
		manifest.tickSegmentIndex++;
		openTickSegment();
		writeManifest();
	}

	private void rotateEventSegment() throws IOException
	{
		closeWriter(eventWriter, "events");
		manifest.eventSegmentIndex++;
		openEventSegment();
		writeManifest();
	}

	private String segmentFileName(String stream, int index)
	{
		return String.format("%s-%06d.jsonl", stream, index);
	}

	@Override
	public void close()
	{
		running = false;

		if (worker != null)
		{
			worker.interrupt();
			worker = null;
		}
	}

	private void drainQueue()
	{
		QueuedLine line;

		while ((line = queue.poll()) != null)
		{
			try
			{
				writeLine(line);
			}
			catch (IOException e)
			{
				log.warn("Telemetry writer failed while draining queue", e);
				return;
			}
		}
	}

	private void writeManifest() throws IOException
	{
		manifest.lastUpdatedUtc = Instant.now().toString();
		manifest.droppedRecords = droppedRecords.get();
		Files.writeString(
				manifestFile,
				gson.toJson(manifest),
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.TRUNCATE_EXISTING);
	}

	private void writeManifestQuietly()
	{
		try
		{
			writeManifest();
		}
		catch (IOException e)
		{
			log.warn("Failed to write telemetry manifest", e);
		}
	}

	private void maybeRunRetention()
	{
		if (!retentionEnabled)
		{
			return;
		}

		long now = System.currentTimeMillis();

		if (now < nextCleanupAtMillis)
		{
			return;
		}

		nextCleanupAtMillis = now + cleanupIntervalMillis;
		runRetention();
	}

	private void runRetention()
	{
		try
		{
			long totalSize = directorySize(sessionsRoot);

			if (totalSize <= maxTelemetryBytes)
			{
				return;
			}

			deleteOldClosedSegments(totalSize);
			totalSize = directorySize(sessionsRoot);

			if (totalSize <= maxTelemetryBytes)
			{
				return;
			}

			deleteOldCompletedSessions(totalSize);
			totalSize = directorySize(sessionsRoot);

			if (totalSize > maxTelemetryBytes)
			{
				log.warn("Telemetry retention cap still exceeded; only protected or active open files may remain");
			}
		}
		catch (IOException e)
		{
			log.warn("Telemetry retention cleanup failed", e);
		}
	}

	private void deleteOldClosedSegments(long startingSize) throws IOException
	{
		List<Path> candidates = new ArrayList<>();

		for (Path session : listDirectories(sessionsRoot))
		{
			if (isPinned(session))
			{
				continue;
			}

			boolean activeSession = session.equals(sessionDir);

			if (!activeSession && isActiveSession(session))
			{
				continue;
			}

			if (activeSession && !allowDeletingClosedSegmentsFromActiveSession)
			{
				continue;
			}

			Path ticks = session.resolve(STREAM_TICKS);
			Path events = session.resolve(STREAM_EVENTS);
			addClosedSegmentCandidates(candidates, ticks, currentTickSegment);
			addClosedSegmentCandidates(candidates, events, currentEventSegment);
		}

		candidates.sort(Comparator.comparingLong(this::lastModifiedMillis));
		deleteUntilUnderCap(candidates, startingSize);
	}

	private void addClosedSegmentCandidates(List<Path> candidates, Path dir, Path currentOpenSegment) throws IOException
	{
		if (!Files.isDirectory(dir))
		{
			return;
		}

		try (DirectoryStream<Path> stream = Files.newDirectoryStream(dir, "*.jsonl"))
		{
			for (Path path : stream)
			{
				if (!path.equals(currentOpenSegment))
				{
					candidates.add(path);
				}
			}
		}
	}

	private void deleteOldCompletedSessions(long startingSize) throws IOException
	{
		List<Path> candidates = new ArrayList<>();

		for (Path session : listDirectories(sessionsRoot))
		{
			if (session.equals(sessionDir) || isPinned(session) || isActiveSession(session))
			{
				continue;
			}

			candidates.add(session);
		}

		candidates.sort(Comparator.comparingLong(this::lastModifiedMillis));
		deleteUntilUnderCap(candidates, startingSize);
	}

	private void deleteUntilUnderCap(List<Path> candidates, long startingSize) throws IOException
	{
		long totalSize = startingSize;

		for (Path candidate : candidates)
		{
			if (totalSize <= maxTelemetryBytes)
			{
				return;
			}

			long size = Files.isDirectory(candidate) ? directorySize(candidate) : Files.size(candidate);
			deletePath(candidate);
			totalSize -= size;
		}
	}

	private boolean isPinned(Path session)
	{
		return preservePinnedSessions && Files.exists(session.resolve("pinned.flag"));
	}

	private boolean isActiveSession(Path session)
	{
		if (session.equals(sessionDir))
		{
			return true;
		}

		Path manifest = session.resolve("manifest.json");

		if (!Files.exists(manifest))
		{
			return false;
		}

		try
		{
			String body = Files.readString(manifest, StandardCharsets.UTF_8);
			return body.contains("\"active\":true");
		}
		catch (IOException e)
		{
			return false;
		}
	}

	private List<Path> listDirectories(Path root) throws IOException
	{
		List<Path> dirs = new ArrayList<>();

		if (!Files.isDirectory(root))
		{
			return dirs;
		}

		try (DirectoryStream<Path> stream = Files.newDirectoryStream(root))
		{
			for (Path path : stream)
			{
				if (Files.isDirectory(path))
				{
					dirs.add(path);
				}
			}
		}

		return dirs;
	}

	private long directorySize(Path root) throws IOException
	{
		if (!Files.exists(root))
		{
			return 0;
		}

		try (Stream<Path> stream = Files.walk(root))
		{
			return stream
					.filter(Files::isRegularFile)
					.mapToLong(this::fileSize)
					.sum();
		}
	}

	private long fileSize(Path path)
	{
		try
		{
			return Files.size(path);
		}
		catch (IOException e)
		{
			return 0;
		}
	}

	private long lastModifiedMillis(Path path)
	{
		try
		{
			return Files.getLastModifiedTime(path).toMillis();
		}
		catch (IOException e)
		{
			return 0;
		}
	}

	private void deletePath(Path path) throws IOException
	{
		if (!Files.exists(path))
		{
			return;
		}

		if (Files.isDirectory(path))
		{
			try (Stream<Path> stream = Files.walk(path))
			{
				List<Path> paths = stream.sorted(Comparator.reverseOrder()).collect(Collectors.toList());

				for (Path child : paths)
				{
					Files.deleteIfExists(child);
				}
			}
		}
		else
		{
			Files.deleteIfExists(path);
		}
	}

	private void closeWriter(BufferedWriter writer, String stream)
	{
		if (writer == null)
		{
			return;
		}

		try
		{
			writer.flush();
			writer.close();
		}
		catch (IOException e)
		{
			log.warn("Failed to close telemetry {} writer", stream, e);
		}
	}
}

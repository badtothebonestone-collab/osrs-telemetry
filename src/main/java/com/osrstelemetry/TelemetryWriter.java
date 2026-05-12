package com.osrstelemetry;

import com.google.gson.Gson;
import java.awt.image.BufferedImage;
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
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.stream.ImageOutputStream;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class TelemetryWriter implements Closeable
{
	private static final String STREAM_TICKS = "ticks";
	private static final String STREAM_EVENTS = "events";
	private static final String STREAM_FRAME_INDEX = "frame_index";
	private static final String FRAMES_DIR = "frames";
	private static final String FRAME_INDEX_FILE = "frame_index.jsonl";
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

	private static class QueuedFrame
	{
		final long tickId;
		final String relativePath;
		final BufferedImage image;
		final String captureSource;
		final String requestedAtUtc;
		final String capturedAtUtc;
		final String enqueuedAtUtc;

		QueuedFrame(
				long tickId,
				String relativePath,
				BufferedImage image,
				String captureSource,
				String requestedAtUtc,
				String capturedAtUtc,
				String enqueuedAtUtc)
		{
			this.tickId = tickId;
			this.relativePath = relativePath;
			this.image = image;
			this.captureSource = captureSource;
			this.requestedAtUtc = requestedAtUtc;
			this.capturedAtUtc = capturedAtUtc;
			this.enqueuedAtUtc = enqueuedAtUtc;
		}
	}

	private static class FrameIndexRecord
	{
		String schemaVersion;
		long tickId;
		String framePath;
		String captureSource;
		String status;
		String requestedAtUtc;
		String capturedAtUtc;
		String enqueuedAtUtc;
		String writtenAtUtc;
		Long captureLatencyMs;
		Long queueLatencyMs;
		Long writeLatencyMs;
		Long totalLatencyMs;
		Integer width;
		Integer height;
		Long sizeBytes;
		Long droppedFrameCount;
		String error;
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
		long frameCount;
		long droppedFrameCount;
		long deletedFrameCount;
		String recordingMode;
		boolean rawTickRecordingEnabled;
		boolean rawEventRecordingEnabled;
		boolean frameRecordingEnabled;
		boolean rawRecordingEnabled;
		boolean compactPacketRecordingEnabled;
		long rawTicksWritten;
		long rawTicksSuppressedByMode;
		long rawEventsWritten;
		long rawEventsSuppressedByMode;
		long framesWritten;
		long framesSuppressedByMode;
		boolean compactLivePacketsEnabled;
		boolean compactLivePacketFilesEnabled;
		boolean compactLiveStreamEnabled;
		String compactLiveStreamHost;
		int compactLiveStreamPort;
		int compactLiveStreamQueueSize;
		boolean compactLiveStreamCircuitBreakerEnabled;
		long compactLiveStreamMaxWriteMillisConfigured;
		boolean compactLiveStreamRunning;
		int compactLiveStreamClientCount;
		long compactLiveStreamPacketsOffered;
		long compactLiveStreamPacketsWritten;
		long compactLiveStreamPacketsDropped;
		long compactLiveStreamPacketsDroppedNoClients;
		long compactLiveStreamPacketsDroppedByCircuitBreaker;
		long compactLiveStreamWriteErrors;
		long compactLiveStreamAcceptedClients;
		long compactLiveStreamDisconnectedClients;
		long compactLiveStreamLastWriteMillis;
		long compactLiveStreamMaxWriteMillisObserved;
		boolean compactLiveStreamCircuitBreakerTripped;
		String compactLiveStreamCircuitBreakerReason;
		String compactLiveStreamDisabledUntilUtc;
		long compactLiveStreamCircuitBreakerTrips;
		Map<String, Long> compactLiveStreamPacketsOfferedByType;
		Map<String, Long> compactLiveStreamPacketsSentByType;
		Map<String, Long> compactLiveStreamPacketsDroppedByType;
		Map<String, Long> compactLiveStreamLatestOfferedTickByType;
		Map<String, Long> compactLiveStreamLatestTickByType;
		String livePacketDir;
		String livePacketSchema;
		int livePacketSegmentMb;
		long compactLiveRetentionTicks;
		long compactLiveRetentionBytes;
		long compactLiveRetentionSegments;
		int compactLiveQueueSize;
		long livePacketsWritten;
		long livePacketsDropped;
		long livePacketWriteErrors;
		long livePacketLastWriteMillis;
		long livePacketSegmentCount;
		long livePacketTotalBytes;
		long livePacketSegmentsPruned;
		long livePacketRetentionBytes;
		long livePacketRetentionSegments;
		String livePacketActiveSegment;
		boolean liveCacheEnabled;
		long liveCacheUpdates;
		long liveCacheUpdateErrors;
		List<String> liveCachePayloadTypes;
		long liveCacheLatestTick;
		long liveCacheLatestSequence;
		long liveCacheEstimatedBytes;
		int screenshotEveryTicks;
		String screenshotFormat;
		int maxFrameStorageMb;
		int frameCleanupIntervalSeconds;
		String frameCaptureMode;
		boolean allowScreenRectangleFallback;
		String lastUpdatedUtc;
	}

	private final Gson gson;
	private final LinkedBlockingQueue<QueuedLine> queue = new LinkedBlockingQueue<>(QUEUE_CAPACITY);
	private final LinkedBlockingQueue<QueuedFrame> frameQueue;
	private final Path sessionsRoot;
	private final Path sessionDir;
	private final Path ticksDir;
	private final Path eventsDir;
	private final Path framesDir;
	private final Path frameIndexFile;
	private final Path dictionariesDir;
	private final Path itemsDictionaryFile;
	private final Path npcsDictionaryFile;
	private final Path objectsDictionaryFile;
	private final Path manifestFile;
	private final String sessionId;
	private final long maxSegmentBytes;
	private final boolean retentionEnabled;
	private final long maxTelemetryBytes;
	private final long cleanupIntervalMillis;
	private final boolean preservePinnedSessions;
	private final boolean allowDeletingClosedSegmentsFromActiveSession;
	private final TelemetryRecordingMode recordingMode;
	private final boolean rawTickRecordingEnabled;
	private final boolean rawEventRecordingEnabled;
	private final boolean frameRecordingEnabled;
	private final int screenshotEveryTicks;
	private final String screenshotFormat;
	private final float jpegQuality;
	private final boolean deleteOldFrames;
	private final long maxFrameStorageBytes;
	private final long frameCleanupIntervalMillis;
	private final String frameCaptureMode;
	private final boolean allowScreenRectangleFallback;
	private final boolean compactLivePacketsEnabled;
	private final boolean compactLivePacketFilesEnabled;
	private final boolean compactLiveStreamEnabled;
	private final String compactLiveStreamHost;
	private final int compactLiveStreamPort;
	private final int compactLiveStreamQueueSize;
	private final boolean compactLiveStreamCircuitBreakerEnabled;
	private final int compactLiveStreamMaxWriteMillis;
	private final int compactLiveStreamDisableSeconds;
	private final int compactLiveSegmentMb;
	private final long compactLiveRetentionTicks;
	private final long compactLiveRetentionBytes;
	private final long compactLiveRetentionSegments;
	private final int compactLiveQueueSize;
	private final PluginLiveCache liveCache;
	private final AtomicLong droppedRecords = new AtomicLong();
	private final AtomicLong droppedFrameCount = new AtomicLong();
	private final AtomicLong deletedFrameCount = new AtomicLong();
	private final AtomicLong rawTicksSuppressedByMode = new AtomicLong();
	private final AtomicLong rawEventsSuppressedByMode = new AtomicLong();
	private final AtomicLong framesSuppressedByMode = new AtomicLong();
	private final AtomicBoolean dictionariesDirty = new AtomicBoolean(false);
	private final Map<Integer, String> itemDictionary = new ConcurrentHashMap<>();
	private final Map<Integer, String> npcDictionary = new ConcurrentHashMap<>();
	private final Map<Integer, String> objectDictionary = new ConcurrentHashMap<>();
	private final Manifest manifest = new Manifest();
	private LivePacketWriter livePacketWriter;
	private CompactLiveStreamPublisher compactLiveStreamPublisher;

	private volatile boolean running = false;
	private Thread worker;
	private BufferedWriter tickWriter;
	private BufferedWriter eventWriter;
	private BufferedWriter frameIndexWriter;
	private long livePacketStartupErrors;
	private long liveStreamStartupErrors;
	private Path currentTickSegment;
	private Path currentEventSegment;
	private long currentTickBytes;
	private long currentEventBytes;
	private long nextCleanupAtMillis;
	private long nextFrameCleanupAtMillis;
	private long nextDictionaryFlushAtMillis;
	private volatile Path currentFrameWrite;

	public TelemetryWriter(
			String outputDirectory,
			Gson gson,
			int maxSegmentMb,
			boolean retentionEnabled,
			int maxTelemetryGb,
			int cleanupIntervalSeconds,
			boolean preservePinnedSessions,
			boolean allowDeletingClosedSegmentsFromActiveSession,
			TelemetryRecordingMode recordingMode,
			boolean rawTickRecordingEnabled,
			boolean rawEventRecordingEnabled,
			boolean frameRecordingEnabled,
			int screenshotEveryTicks,
			String screenshotFormat,
			double jpegQuality,
			int maxFrameStorageMb,
			int frameCleanupIntervalSeconds,
			boolean deleteOldFrames,
			int maxFrameQueueSize,
			String frameCaptureMode,
			boolean allowScreenRectangleFallback,
			boolean compactLivePacketFilesEnabled,
			int compactLiveSegmentMb,
			long compactLiveRetentionTicks,
			long compactLiveRetentionBytes,
			long compactLiveRetentionSegments,
			int compactLiveQueueSize,
			boolean compactLiveStreamEnabled,
			String compactLiveStreamHost,
			int compactLiveStreamPort,
			int compactLiveStreamQueueSize,
			boolean compactLiveStreamCircuitBreakerEnabled,
			int compactLiveStreamMaxWriteMillis,
			int compactLiveStreamDisableSeconds,
			PluginLiveCache liveCache)
	{
		this.gson = gson;
		this.frameQueue = new LinkedBlockingQueue<>(Math.max(1, maxFrameQueueSize));
		this.sessionsRoot = Path.of(outputDirectory);
		this.sessionId = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
		this.sessionDir = sessionsRoot.resolve(sessionId);
		this.ticksDir = sessionDir.resolve(STREAM_TICKS);
		this.eventsDir = sessionDir.resolve(STREAM_EVENTS);
		this.framesDir = sessionDir.resolve(FRAMES_DIR);
		this.frameIndexFile = sessionDir.resolve(FRAME_INDEX_FILE);
		this.dictionariesDir = sessionDir.resolve("dictionaries");
		this.itemsDictionaryFile = dictionariesDir.resolve("items.json");
		this.npcsDictionaryFile = dictionariesDir.resolve("npcs.json");
		this.objectsDictionaryFile = dictionariesDir.resolve("objects.json");
		this.manifestFile = sessionDir.resolve("manifest.json");
		this.maxSegmentBytes = Math.max(1L, maxSegmentMb) * 1024L * 1024L;
		this.retentionEnabled = retentionEnabled;
		this.maxTelemetryBytes = Math.max(1L, maxTelemetryGb) * 1024L * 1024L * 1024L;
		this.cleanupIntervalMillis = Duration.ofSeconds(Math.max(1L, cleanupIntervalSeconds)).toMillis();
		this.preservePinnedSessions = preservePinnedSessions;
		this.allowDeletingClosedSegmentsFromActiveSession = allowDeletingClosedSegmentsFromActiveSession;
		this.recordingMode = recordingMode == null ? TelemetryRecordingMode.LIVE_COMPACT_ONLY : recordingMode;
		this.rawTickRecordingEnabled = rawTickRecordingEnabled;
		this.rawEventRecordingEnabled = rawEventRecordingEnabled;
		this.frameRecordingEnabled = frameRecordingEnabled;
		this.screenshotEveryTicks = Math.max(0, screenshotEveryTicks);
		this.screenshotFormat = normalizeScreenshotFormat(screenshotFormat);
		this.jpegQuality = (float) Math.max(0.0, Math.min(1.0, jpegQuality));
		this.deleteOldFrames = deleteOldFrames;
		this.maxFrameStorageBytes = Math.max(1L, maxFrameStorageMb) * 1024L * 1024L;
		this.frameCleanupIntervalMillis = Duration.ofSeconds(Math.max(1L, frameCleanupIntervalSeconds)).toMillis();
		this.frameCaptureMode = normalizeFrameCaptureMode(frameCaptureMode);
		this.allowScreenRectangleFallback = allowScreenRectangleFallback;
		this.compactLivePacketFilesEnabled = compactLivePacketFilesEnabled;
		this.compactLiveStreamEnabled = compactLiveStreamEnabled;
		this.compactLivePacketsEnabled = compactLivePacketFilesEnabled || compactLiveStreamEnabled || liveCache != null;
		this.compactLiveStreamHost = compactLiveStreamHost == null || compactLiveStreamHost.isBlank() ? "127.0.0.1" : compactLiveStreamHost.trim();
		this.compactLiveStreamPort = Math.max(0, Math.min(65535, compactLiveStreamPort));
		this.compactLiveStreamQueueSize = Math.max(1, compactLiveStreamQueueSize);
		this.compactLiveStreamCircuitBreakerEnabled = compactLiveStreamCircuitBreakerEnabled;
		this.compactLiveStreamMaxWriteMillis = Math.max(1, compactLiveStreamMaxWriteMillis);
		this.compactLiveStreamDisableSeconds = Math.max(1, compactLiveStreamDisableSeconds);
		this.liveCache = liveCache;
		this.compactLiveSegmentMb = Math.max(1, compactLiveSegmentMb);
		this.compactLiveRetentionTicks = Math.max(0L, compactLiveRetentionTicks);
		this.compactLiveRetentionBytes = Math.max(0L, compactLiveRetentionBytes);
		this.compactLiveRetentionSegments = Math.max(0L, compactLiveRetentionSegments);
		this.compactLiveQueueSize = Math.max(1, compactLiveQueueSize);
	}

	public void start() throws IOException
	{
		Files.createDirectories(sessionDir);
		if (rawTickRecordingEnabled)
		{
			Files.createDirectories(ticksDir);
		}
		if (rawEventRecordingEnabled)
		{
			Files.createDirectories(eventsDir);
		}
		if (frameRecordingEnabled)
		{
			Files.createDirectories(framesDir);
		}

		manifest.sessionId = sessionId;
		manifest.startedAtUtc = Instant.now().toString();
		manifest.schemaVersion = SCHEMA_VERSION;
		manifest.active = true;
		manifest.tickSegmentIndex = rawTickRecordingEnabled ? 1 : 0;
		manifest.eventSegmentIndex = rawEventRecordingEnabled ? 1 : 0;
		manifest.recordingMode = this.recordingMode.name();
		manifest.rawTickRecordingEnabled = rawTickRecordingEnabled;
		manifest.rawEventRecordingEnabled = rawEventRecordingEnabled;
		manifest.frameRecordingEnabled = frameRecordingEnabled;
		manifest.rawRecordingEnabled = rawTickRecordingEnabled || rawEventRecordingEnabled;
		manifest.compactPacketRecordingEnabled = isCompactLivePacketsEnabled();
		manifest.compactLivePacketFilesEnabled = compactLivePacketFilesEnabled;
		manifest.compactLiveStreamEnabled = compactLiveStreamEnabled;
		manifest.compactLiveStreamHost = this.compactLiveStreamHost;
		manifest.compactLiveStreamPort = this.compactLiveStreamPort;
		manifest.compactLiveStreamQueueSize = this.compactLiveStreamQueueSize;
		manifest.compactLiveStreamCircuitBreakerEnabled = this.compactLiveStreamCircuitBreakerEnabled;
		manifest.compactLiveStreamMaxWriteMillisConfigured = this.compactLiveStreamMaxWriteMillis;
		manifest.screenshotEveryTicks = screenshotEveryTicks;
		manifest.screenshotFormat = this.screenshotFormat;
		manifest.maxFrameStorageMb = (int) (maxFrameStorageBytes / (1024L * 1024L));
		manifest.frameCleanupIntervalSeconds = (int) (frameCleanupIntervalMillis / 1000L);
		manifest.frameCaptureMode = frameCaptureMode;
		manifest.allowScreenRectangleFallback = allowScreenRectangleFallback;
		manifest.compactLivePacketsEnabled = compactLivePacketsEnabled;
		manifest.livePacketDir = compactLivePacketFilesEnabled ? LivePacketWriter.LIVE_PACKETS_DIR : null;
		manifest.livePacketSchema = compactLivePacketsEnabled ? LivePacket.ENVELOPE_SCHEMA : null;
		manifest.livePacketSegmentMb = compactLiveSegmentMb;
		manifest.compactLiveRetentionTicks = compactLiveRetentionTicks;
		manifest.compactLiveRetentionBytes = compactLiveRetentionBytes;
		manifest.compactLiveRetentionSegments = compactLiveRetentionSegments;
		manifest.compactLiveQueueSize = compactLiveQueueSize;
		manifest.liveCacheEnabled = isLiveCacheEnabled();
		manifest.liveCacheUpdates = getLiveCacheUpdates();
		manifest.liveCacheUpdateErrors = getLiveCacheUpdateErrors();
		manifest.liveCachePayloadTypes = getLiveCachePayloadTypes();
		manifest.liveCacheLatestTick = getLiveCacheLatestTick();
		manifest.liveCacheLatestSequence = getLiveCacheLatestSequence();
		manifest.liveCacheEstimatedBytes = getLiveCacheEstimatedBytes();

		if (rawTickRecordingEnabled)
		{
			openTickSegment();
		}
		if (rawEventRecordingEnabled)
		{
			openEventSegment();
		}
		if (frameRecordingEnabled)
		{
			openFrameIndex();
		}
		if (compactLiveStreamEnabled)
		{
			try
			{
				compactLiveStreamPublisher = new CompactLiveStreamPublisher(
						sessionId,
						gson,
						this.compactLiveStreamHost,
						this.compactLiveStreamPort,
						this.compactLiveStreamQueueSize,
						this.compactLiveStreamCircuitBreakerEnabled,
						this.compactLiveStreamMaxWriteMillis,
						this.compactLiveStreamDisableSeconds);
				compactLiveStreamPublisher.start();
			}
			catch (IOException e)
			{
				compactLiveStreamPublisher = null;
				liveStreamStartupErrors++;
				log.warn("Compact live stream failed to start; continuing with other telemetry outputs", e);
			}
		}
		if (compactLivePacketFilesEnabled)
		{
			try
			{
				livePacketWriter = new LivePacketWriter(
						sessionId,
						sessionDir,
						gson,
					compactLiveSegmentMb,
					compactLiveRetentionTicks,
					compactLiveRetentionBytes,
					compactLiveRetentionSegments,
					compactLiveQueueSize);
				livePacketWriter.start();
			}
			catch (IOException e)
			{
				livePacketWriter = null;
				livePacketStartupErrors++;
				log.warn("Compact live packet writer failed to start; continuing with raw telemetry only", e);
			}
		}
		writeManifest();

		running = true;
		nextCleanupAtMillis = System.currentTimeMillis() + cleanupIntervalMillis;
		nextFrameCleanupAtMillis = System.currentTimeMillis() + frameCleanupIntervalMillis;
		nextDictionaryFlushAtMillis = System.currentTimeMillis() + 5000L;
		worker = new Thread(this::runWriterLoop, "telemetry-writer");
		worker.setDaemon(true);
		worker.start();

		log.info("Telemetry session started: {}", sessionDir);
	}

	public void enqueueTick(String json)
	{
		if (!rawTickRecordingEnabled)
		{
			recordRawTickSuppressedByMode();
			return;
		}
		enqueue(new QueuedLine(STREAM_TICKS, json));
	}

	public void enqueueEvent(String json)
	{
		if (!rawEventRecordingEnabled)
		{
			recordRawEventSuppressedByMode();
			return;
		}
		enqueue(new QueuedLine(STREAM_EVENTS, json));
	}

	public boolean enqueueLivePacket(String packetType, long tick, String timestampUtc, Object payload)
	{
		LivePacketWriter liveWriter = livePacketWriter;
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		PluginLiveCache cache = liveCache;
		boolean accepted = false;

		if (cache != null)
		{
			accepted = cache.update(packetType, tick, timestampUtc, payload);
		}

		if (streamPublisher != null)
		{
			accepted = streamPublisher.enqueue(packetType, tick, timestampUtc, payload) || accepted;
		}

		if (liveWriter != null)
		{
			accepted = liveWriter.enqueue(packetType, tick, timestampUtc, payload) || accepted;
		}

		return accepted;
	}

	public boolean isCompactLivePacketsEnabled()
	{
		return livePacketWriter != null || compactLiveStreamPublisher != null || liveCache != null;
	}

	public boolean isLiveCacheEnabled()
	{
		return liveCache != null;
	}

	public boolean isCompactLivePacketFilesEnabled()
	{
		return livePacketWriter != null;
	}

	public boolean isCompactLiveStreamEnabled()
	{
		return compactLiveStreamPublisher != null;
	}

	public String getRecordingMode()
	{
		return recordingMode.name();
	}

	public boolean isRawTickRecordingEnabled()
	{
		return rawTickRecordingEnabled;
	}

	public boolean isRawEventRecordingEnabled()
	{
		return rawEventRecordingEnabled;
	}

	public boolean isRawRecordingEnabled()
	{
		return rawTickRecordingEnabled || rawEventRecordingEnabled;
	}

	public boolean isFrameRecordingEnabled()
	{
		return frameRecordingEnabled;
	}

	public int getScreenshotEveryTicks()
	{
		return screenshotEveryTicks;
	}

	public void recordRawTickSuppressedByMode()
	{
		rawTicksSuppressedByMode.incrementAndGet();
	}

	public void recordRawEventSuppressedByMode()
	{
		rawEventsSuppressedByMode.incrementAndGet();
	}

	public void recordFrameSuppressedByMode()
	{
		framesSuppressedByMode.incrementAndGet();
	}

	Path getSessionDir()
	{
		return sessionDir;
	}

	public boolean enqueueFrame(String relativePath, BufferedImage image)
	{
		return enqueueFrame(-1L, relativePath, image, null, null, null);
	}

	public boolean enqueueFrame(
			long tickId,
			String relativePath,
			BufferedImage image,
			String captureSource,
			String requestedAtUtc,
			String capturedAtUtc)
	{
		if (!frameRecordingEnabled)
		{
			recordFrameSuppressedByMode();
			return false;
		}
		if (!running || relativePath == null || image == null)
		{
			return false;
		}

		String enqueuedAtUtc = Instant.now().toString();

		if (!frameQueue.offer(new QueuedFrame(
				tickId,
				relativePath,
				image,
				captureSource,
				requestedAtUtc,
				capturedAtUtc,
				enqueuedAtUtc)))
		{
			long dropped = droppedFrameCount.incrementAndGet();
			enqueueFrameIndex(frameIndexRecord(
					tickId,
					relativePath,
					captureSource,
					"DROPPED_QUEUE_FULL",
					requestedAtUtc,
					capturedAtUtc,
					enqueuedAtUtc,
					null,
					null,
					image.getWidth(),
					image.getHeight(),
					null,
					null));

			if (dropped == 1 || dropped % 100 == 0)
			{
				log.warn("Telemetry frame queue full; dropped {} frames", dropped);
			}

			return false;
		}

		return true;
	}

	public void recordFrameIndex(
			long tickId,
			String relativePath,
			String captureSource,
			String status,
			String requestedAtUtc,
			String capturedAtUtc,
			String error)
	{
		if (!frameRecordingEnabled)
		{
			return;
		}
		enqueueFrameIndex(frameIndexRecord(
				tickId,
				relativePath,
				captureSource,
				status,
				requestedAtUtc,
				capturedAtUtc,
				null,
				null,
				null,
				null,
				null,
				null,
				error));
	}

	public int getQueueSize()
	{
		return queue.size();
	}

	public long getDroppedRecords()
	{
		return droppedRecords.get();
	}

	public long getDroppedFrameCount()
	{
		return droppedFrameCount.get();
	}

	public long getRawTicksWritten()
	{
		return manifest.tickCount;
	}

	public long getRawTicksSuppressedByMode()
	{
		return rawTicksSuppressedByMode.get();
	}

	public long getRawEventsWritten()
	{
		return manifest.eventCount;
	}

	public long getRawEventsSuppressedByMode()
	{
		return rawEventsSuppressedByMode.get();
	}

	public long getFramesWritten()
	{
		return manifest.frameCount;
	}

	public long getFramesSuppressedByMode()
	{
		return framesSuppressedByMode.get();
	}

	public int getLivePacketQueueDepth()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0 : liveWriter.getQueueDepth();
	}

	public long getLivePacketsDropped()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0L : liveWriter.getDroppedPackets();
	}

	public long getLivePacketsWritten()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0L : liveWriter.getWrittenPackets();
	}

	public long getLivePacketWriteErrors()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return livePacketStartupErrors + (liveWriter == null ? 0L : liveWriter.getWriteErrors());
	}

	public long getLivePacketLastWriteMillis()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? -1L : liveWriter.getLastWriteMillis();
	}

	public long getLivePacketSegmentCount()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0L : liveWriter.getSegmentCount();
	}

	public long getLivePacketTotalBytes()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0L : liveWriter.getTotalBytes();
	}

	public long getLivePacketSegmentsPruned()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? 0L : liveWriter.getPrunedSegments();
	}

	public String getLivePacketActiveSegment()
	{
		LivePacketWriter liveWriter = livePacketWriter;
		return liveWriter == null ? null : liveWriter.getActiveSegmentName();
	}

	public long getLiveCacheUpdates()
	{
		return liveCache == null ? 0L : liveCache.getUpdates();
	}

	public long getLiveCacheUpdateErrors()
	{
		return liveCache == null ? 0L : liveCache.getUpdateErrors();
	}

	public List<String> getLiveCachePayloadTypes()
	{
		return liveCache == null ? List.of() : liveCache.packetTypes();
	}

	public long getLiveCacheLatestTick()
	{
		return liveCache == null ? -1L : liveCache.getLatestTick();
	}

	public long getLiveCacheLatestSequence()
	{
		return liveCache == null ? -1L : liveCache.getLatestSequence();
	}

	public long getLiveCacheEstimatedBytes()
	{
		return liveCache == null ? 0L : liveCache.getEstimatedBytes();
	}

	public Map<String, Object> getLiveCacheHealth()
	{
		return liveCache == null ? Map.of() : liveCache.health();
	}

	public int getCompactLiveStreamQueueDepth()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0 : streamPublisher.getQueueDepth();
	}

	public int getCompactLiveStreamClientCount()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0 : streamPublisher.getClientCount();
	}

	public long getCompactLiveStreamPacketsOffered()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getOfferedPackets();
	}

	public long getCompactLiveStreamPacketsWritten()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getStreamedPackets();
	}

	public long getCompactLiveStreamPacketsDropped()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getDroppedPackets();
	}

	public long getCompactLiveStreamPacketsDroppedNoClients()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getDroppedNoClients();
	}

	public long getCompactLiveStreamPacketsDroppedByCircuitBreaker()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getDroppedByCircuitBreaker();
	}

	public long getCompactLiveStreamWriteErrors()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return liveStreamStartupErrors + (streamPublisher == null ? 0L : streamPublisher.getWriteErrors());
	}

	public long getCompactLiveStreamAcceptedClients()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getAcceptedClients();
	}

	public long getCompactLiveStreamDisconnectedClients()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getDisconnectedClients();
	}

	public long getCompactLiveStreamLastWriteMillis()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? -1L : streamPublisher.getLastWriteMillis();
	}

	public long getCompactLiveStreamMaxWriteMillisObserved()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getMaxWriteMillisObserved();
	}

	public boolean isCompactLiveStreamCircuitBreakerTripped()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher != null && streamPublisher.isCircuitBreakerTripped();
	}

	public String getCompactLiveStreamCircuitBreakerReason()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? null : streamPublisher.getCircuitBreakerReason();
	}

	public String getCompactLiveStreamDisabledUntilUtc()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? null : streamPublisher.getDisabledUntilUtc();
	}

	public long getCompactLiveStreamCircuitBreakerTrips()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? 0L : streamPublisher.getCircuitBreakerTrips();
	}

	public Map<String, Long> getCompactLiveStreamPacketsByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getStreamedPacketsByType();
	}

	public Map<String, Long> getCompactLiveStreamPacketsOfferedByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getOfferedPacketsByType();
	}

	public Map<String, Long> getCompactLiveStreamPacketsSentByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getStreamedPacketsByType();
	}

	public Map<String, Long> getCompactLiveStreamPacketsDroppedByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getDroppedPacketsByType();
	}

	public Map<String, Long> getCompactLiveStreamLatestOfferedTickByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getLatestOfferedTickByType();
	}

	public Map<String, Long> getCompactLiveStreamLatestTickByType()
	{
		CompactLiveStreamPublisher streamPublisher = compactLiveStreamPublisher;
		return streamPublisher == null ? Map.of() : streamPublisher.getLatestSentTickByType();
	}

	public void rememberItem(int id, String name)
	{
		rememberDictionaryEntry(itemDictionary, id, name);
	}

	public void rememberNpc(int id, String name)
	{
		rememberDictionaryEntry(npcDictionary, id, name);
	}

	public void rememberObject(int id, String name)
	{
		rememberDictionaryEntry(objectDictionary, id, name);
	}

	private void rememberDictionaryEntry(Map<Integer, String> dictionary, int id, String name)
	{
		if (id < 0 || name == null || name.isBlank() || "null".equalsIgnoreCase(name))
		{
			return;
		}

		if (dictionary.putIfAbsent(id, name) == null)
		{
			dictionariesDirty.set(true);
		}
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
			while (running || !queue.isEmpty() || !frameQueue.isEmpty())
			{
				QueuedLine line = running ? queue.poll(250, TimeUnit.MILLISECONDS) : queue.poll();

				if (line != null)
				{
					writeLine(line);
				}

				QueuedFrame frame;
				while ((frame = frameQueue.poll()) != null)
				{
					writeFrame(frame);
				}

				maybeRunRetention();
				maybeRunFrameCleanup();
				maybeFlushDictionaries();
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
			closeWriter(frameIndexWriter, "frame index");
			closeLivePacketWriter();
			manifest.active = false;
			manifest.endedAtUtc = Instant.now().toString();
			manifest.droppedRecords = droppedRecords.get();
			flushDictionariesQuietly();
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
		else if (STREAM_FRAME_INDEX.equals(line.stream))
		{
			writeFrameIndex(line.json);
		}
	}

	private void writeFrame(QueuedFrame frame) throws IOException
	{
		Path path = sessionDir.resolve(frame.relativePath).normalize();
		long writeStartedMillis = System.currentTimeMillis();

		if (!path.startsWith(framesDir))
		{
			log.warn("Refusing to write telemetry frame outside frames directory: {}", frame.relativePath);
			enqueueFrameIndex(frameIndexRecord(
					frame.tickId,
					frame.relativePath,
					frame.captureSource,
					"WRITE_REJECTED",
					frame.requestedAtUtc,
					frame.capturedAtUtc,
					frame.enqueuedAtUtc,
					null,
					null,
					frame.image.getWidth(),
					frame.image.getHeight(),
					null,
					"frame path escapes frames directory"));
			return;
		}

		currentFrameWrite = path;

		try
		{
			Files.createDirectories(path.getParent());
			writeImage(path, frame.image);
			String writtenAtUtc = Instant.now().toString();
			manifest.frameCount++;
			manifest.framesWritten = manifest.frameCount;
			manifest.droppedFrameCount = droppedFrameCount.get();
			enqueueFrameIndex(frameIndexRecord(
					frame.tickId,
					frame.relativePath,
					frame.captureSource,
					"WRITTEN",
					frame.requestedAtUtc,
					frame.capturedAtUtc,
					frame.enqueuedAtUtc,
					writtenAtUtc,
					System.currentTimeMillis() - writeStartedMillis,
					frame.image.getWidth(),
					frame.image.getHeight(),
					Files.size(path),
					null));
			writeManifest();
		}
		catch (IOException e)
		{
			enqueueFrameIndex(frameIndexRecord(
					frame.tickId,
					frame.relativePath,
					frame.captureSource,
					"WRITE_FAILED",
					frame.requestedAtUtc,
					frame.capturedAtUtc,
					frame.enqueuedAtUtc,
					Instant.now().toString(),
					System.currentTimeMillis() - writeStartedMillis,
					frame.image.getWidth(),
					frame.image.getHeight(),
					null,
					e.toString()));
			throw e;
		}
		finally
		{
			currentFrameWrite = null;
		}
	}

	private void writeImage(Path path, BufferedImage image) throws IOException
	{
		if ("png".equals(screenshotFormat))
		{
			ImageIO.write(image, "png", path.toFile());
			return;
		}

		BufferedImage rgb = image.getType() == BufferedImage.TYPE_INT_RGB
				? image
				: new BufferedImage(image.getWidth(), image.getHeight(), BufferedImage.TYPE_INT_RGB);

		if (rgb != image)
		{
			java.awt.Graphics graphics = rgb.getGraphics();

			try
			{
				graphics.drawImage(image, 0, 0, null);
			}
			finally
			{
				graphics.dispose();
			}
		}

		Iterator<ImageWriter> writers = ImageIO.getImageWritersByFormatName("jpg");

		if (!writers.hasNext())
		{
			ImageIO.write(rgb, "jpg", path.toFile());
			return;
		}

		ImageWriter writer = writers.next();

		try (ImageOutputStream output = ImageIO.createImageOutputStream(path.toFile()))
		{
			ImageWriteParam params = writer.getDefaultWriteParam();

			if (params.canWriteCompressed())
			{
				params.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
				params.setCompressionQuality(jpegQuality);
			}

			writer.setOutput(output);
			writer.write(null, new IIOImage(rgb, null, null), params);
		}
		finally
		{
			writer.dispose();
		}
	}

	private void writeTick(String json) throws IOException
	{
		if (tickWriter == null)
		{
			recordRawTickSuppressedByMode();
			return;
		}
		long bytes = writeJsonLine(tickWriter, json);
		currentTickBytes += bytes;
		manifest.tickCount++;
		manifest.rawTicksWritten = manifest.tickCount;
		manifest.droppedRecords = droppedRecords.get();
		writeManifest();

		if (currentTickBytes >= maxSegmentBytes)
		{
			rotateTickSegment();
		}
	}

	private void writeEvent(String json) throws IOException
	{
		if (eventWriter == null)
		{
			recordRawEventSuppressedByMode();
			return;
		}
		long bytes = writeJsonLine(eventWriter, json);
		currentEventBytes += bytes;
		manifest.eventCount++;
		manifest.rawEventsWritten = manifest.eventCount;
		manifest.droppedRecords = droppedRecords.get();
		writeManifest();

		if (currentEventBytes >= maxSegmentBytes)
		{
			rotateEventSegment();
		}
	}

	private void writeFrameIndex(String json) throws IOException
	{
		if (frameIndexWriter == null)
		{
			return;
		}
		writeJsonLine(frameIndexWriter, json);
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

	private void openFrameIndex() throws IOException
	{
		frameIndexWriter = Files.newBufferedWriter(
				frameIndexFile,
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.APPEND);
	}

	private void rotateTickSegment() throws IOException
	{
		if (!rawTickRecordingEnabled)
		{
			return;
		}
		closeWriter(tickWriter, "ticks");
		manifest.tickSegmentIndex++;
		openTickSegment();
		writeManifest();
	}

	private void rotateEventSegment() throws IOException
	{
		if (!rawEventRecordingEnabled)
		{
			return;
		}
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
		closeLivePacketWriter();
		closeCompactLiveStreamPublisher();

		if (worker != null)
		{
			worker.interrupt();
			worker = null;
		}
	}

	private void closeLivePacketWriter()
	{
		if (livePacketWriter == null)
		{
			return;
		}

		livePacketWriter.close();
	}

	private void closeCompactLiveStreamPublisher()
	{
		if (compactLiveStreamPublisher == null)
		{
			return;
		}

		compactLiveStreamPublisher.close();
		compactLiveStreamPublisher = null;
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

		QueuedFrame frame;

		while ((frame = frameQueue.poll()) != null)
		{
			try
			{
				writeFrame(frame);
			}
			catch (IOException e)
			{
				log.warn("Telemetry writer failed while draining frame queue", e);
				return;
			}
		}

		while ((line = queue.poll()) != null)
		{
			try
			{
				writeLine(line);
			}
			catch (IOException e)
			{
				log.warn("Telemetry writer failed while draining frame diagnostics", e);
				return;
			}
		}
	}

	private void enqueueFrameIndex(FrameIndexRecord record)
	{
		if (record == null || !frameRecordingEnabled)
		{
			return;
		}

		enqueue(new QueuedLine(STREAM_FRAME_INDEX, gson.toJson(record)));
	}

	private FrameIndexRecord frameIndexRecord(
			long tickId,
			String relativePath,
			String captureSource,
			String status,
			String requestedAtUtc,
			String capturedAtUtc,
			String enqueuedAtUtc,
			String writtenAtUtc,
			Long writeLatencyMs,
			Integer width,
			Integer height,
			Long sizeBytes,
			String error)
	{
		FrameIndexRecord record = new FrameIndexRecord();
		record.schemaVersion = SCHEMA_VERSION;
		record.tickId = tickId;
		record.framePath = relativePath;
		record.captureSource = captureSource;
		record.status = status;
		record.requestedAtUtc = requestedAtUtc;
		record.capturedAtUtc = capturedAtUtc;
		record.enqueuedAtUtc = enqueuedAtUtc;
		record.writtenAtUtc = writtenAtUtc;
		record.captureLatencyMs = millisBetween(requestedAtUtc, capturedAtUtc);
		record.queueLatencyMs = millisBetween(enqueuedAtUtc, writtenAtUtc);
		record.writeLatencyMs = writeLatencyMs;
		record.totalLatencyMs = millisBetween(requestedAtUtc, writtenAtUtc);
		record.width = width;
		record.height = height;
		record.sizeBytes = sizeBytes;
		record.droppedFrameCount = droppedFrameCount.get();
		record.error = error;
		return record;
	}

	private Long millisBetween(String start, String end)
	{
		if (start == null || end == null)
		{
			return null;
		}

		try
		{
			return Duration.between(Instant.parse(start), Instant.parse(end)).toMillis();
		}
		catch (RuntimeException e)
		{
			return null;
		}
	}

	private void writeManifest() throws IOException
	{
		manifest.lastUpdatedUtc = Instant.now().toString();
		manifest.droppedRecords = droppedRecords.get();
		manifest.droppedFrameCount = droppedFrameCount.get();
		manifest.deletedFrameCount = deletedFrameCount.get();
		manifest.rawTicksWritten = manifest.tickCount;
		manifest.rawTicksSuppressedByMode = rawTicksSuppressedByMode.get();
		manifest.rawEventsWritten = manifest.eventCount;
		manifest.rawEventsSuppressedByMode = rawEventsSuppressedByMode.get();
		manifest.framesWritten = manifest.frameCount;
		manifest.framesSuppressedByMode = framesSuppressedByMode.get();
		manifest.rawRecordingEnabled = rawTickRecordingEnabled || rawEventRecordingEnabled;
		manifest.compactPacketRecordingEnabled = isCompactLivePacketsEnabled();
		manifest.compactLivePacketFilesEnabled = compactLivePacketFilesEnabled;
		manifest.compactLiveStreamEnabled = compactLiveStreamEnabled;
		manifest.compactLiveStreamRunning = compactLiveStreamPublisher != null;
		manifest.compactLiveStreamClientCount = getCompactLiveStreamClientCount();
		manifest.compactLiveStreamPacketsOffered = getCompactLiveStreamPacketsOffered();
		manifest.compactLiveStreamPacketsWritten = getCompactLiveStreamPacketsWritten();
		manifest.compactLiveStreamPacketsDropped = getCompactLiveStreamPacketsDropped();
		manifest.compactLiveStreamPacketsDroppedNoClients = getCompactLiveStreamPacketsDroppedNoClients();
		manifest.compactLiveStreamPacketsDroppedByCircuitBreaker = getCompactLiveStreamPacketsDroppedByCircuitBreaker();
		manifest.compactLiveStreamWriteErrors = getCompactLiveStreamWriteErrors();
		manifest.compactLiveStreamAcceptedClients = getCompactLiveStreamAcceptedClients();
		manifest.compactLiveStreamDisconnectedClients = getCompactLiveStreamDisconnectedClients();
		manifest.compactLiveStreamLastWriteMillis = getCompactLiveStreamLastWriteMillis();
		manifest.compactLiveStreamMaxWriteMillisObserved = getCompactLiveStreamMaxWriteMillisObserved();
		manifest.compactLiveStreamCircuitBreakerTripped = isCompactLiveStreamCircuitBreakerTripped();
		manifest.compactLiveStreamCircuitBreakerReason = getCompactLiveStreamCircuitBreakerReason();
		manifest.compactLiveStreamDisabledUntilUtc = getCompactLiveStreamDisabledUntilUtc();
		manifest.compactLiveStreamCircuitBreakerTrips = getCompactLiveStreamCircuitBreakerTrips();
		manifest.compactLiveStreamPacketsOfferedByType = getCompactLiveStreamPacketsOfferedByType();
		manifest.compactLiveStreamPacketsSentByType = getCompactLiveStreamPacketsSentByType();
		manifest.compactLiveStreamPacketsDroppedByType = getCompactLiveStreamPacketsDroppedByType();
		manifest.compactLiveStreamLatestOfferedTickByType = getCompactLiveStreamLatestOfferedTickByType();
		manifest.compactLiveStreamLatestTickByType = getCompactLiveStreamLatestTickByType();
		manifest.livePacketsWritten = getLivePacketsWritten();
		manifest.livePacketsDropped = getLivePacketsDropped();
		manifest.livePacketWriteErrors = getLivePacketWriteErrors();
		manifest.livePacketLastWriteMillis = getLivePacketLastWriteMillis();
		manifest.livePacketSegmentCount = getLivePacketSegmentCount();
		manifest.livePacketTotalBytes = getLivePacketTotalBytes();
		manifest.livePacketSegmentsPruned = getLivePacketSegmentsPruned();
		manifest.livePacketRetentionBytes = compactLiveRetentionBytes;
		manifest.livePacketRetentionSegments = compactLiveRetentionSegments;
		manifest.livePacketActiveSegment = getLivePacketActiveSegment();
		manifest.liveCacheEnabled = isLiveCacheEnabled();
		manifest.liveCacheUpdates = getLiveCacheUpdates();
		manifest.liveCacheUpdateErrors = getLiveCacheUpdateErrors();
		manifest.liveCachePayloadTypes = getLiveCachePayloadTypes();
		manifest.liveCacheLatestTick = getLiveCacheLatestTick();
		manifest.liveCacheLatestSequence = getLiveCacheLatestSequence();
		manifest.liveCacheEstimatedBytes = getLiveCacheEstimatedBytes();
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

	private void maybeRunFrameCleanup()
	{
		if (!deleteOldFrames)
		{
			return;
		}

		long now = System.currentTimeMillis();

		if (now < nextFrameCleanupAtMillis)
		{
			return;
		}

		nextFrameCleanupAtMillis = now + frameCleanupIntervalMillis;
		deleteOldFrames();
	}

	private void deleteOldFrames()
	{
		if (!deleteOldFrames)
		{
			return;
		}

		try
		{
			long frameSize = directorySize(framesDir);

			if (frameSize <= maxFrameStorageBytes)
			{
				return;
			}

			List<Path> candidates = new ArrayList<>();

			try (DirectoryStream<Path> stream = Files.newDirectoryStream(framesDir))
			{
				for (Path path : stream)
				{
					if (Files.isRegularFile(path) && !path.equals(currentFrameWrite))
					{
						candidates.add(path);
					}
				}
			}

			candidates.sort(Comparator.comparingLong(this::lastModifiedMillis));
			boolean deletedAny = false;

			for (Path candidate : candidates)
			{
				if (frameSize <= maxFrameStorageBytes)
				{
					break;
				}

				long size = fileSize(candidate);
				if (safeDelete(candidate))
				{
					deletedAny = true;
					deletedFrameCount.incrementAndGet();
					manifest.deletedFrameCount = deletedFrameCount.get();
					frameSize -= size;
				}
			}

			if (deletedAny)
			{
				writeManifestQuietly();
			}
		}
		catch (IOException e)
		{
			log.warn("Telemetry frame cleanup failed", e);
		}
	}

	private void maybeFlushDictionaries()
	{
		long now = System.currentTimeMillis();

		if (now < nextDictionaryFlushAtMillis)
		{
			return;
		}

		nextDictionaryFlushAtMillis = now + 5000L;

		if (!dictionariesDirty.compareAndSet(true, false))
		{
			return;
		}

		flushDictionariesQuietly();
	}

	private void flushDictionariesQuietly()
	{
		try
		{
			Files.createDirectories(dictionariesDir);
			writeDictionary(itemsDictionaryFile, itemDictionary);
			writeDictionary(npcsDictionaryFile, npcDictionary);
			writeDictionary(objectsDictionaryFile, objectDictionary);
		}
		catch (IOException e)
		{
			dictionariesDirty.set(true);
			log.warn("Failed to flush telemetry dictionaries", e);
		}
	}

	private void writeDictionary(Path file, Map<Integer, String> dictionary) throws IOException
	{
		Files.writeString(
				file,
				gson.toJson(dictionary),
				StandardCharsets.UTF_8,
				StandardOpenOption.CREATE,
				StandardOpenOption.TRUNCATE_EXISTING);
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
			Path frames = session.resolve(FRAMES_DIR);
			addClosedSegmentCandidates(candidates, ticks, currentTickSegment);
			addClosedSegmentCandidates(candidates, events, currentEventSegment);
			addFrameCandidates(candidates, frames);
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

	private void addFrameCandidates(List<Path> candidates, Path dir) throws IOException
	{
		if (!Files.isDirectory(dir))
		{
			return;
		}

		try (DirectoryStream<Path> stream = Files.newDirectoryStream(dir))
		{
			for (Path path : stream)
			{
				if (Files.isRegularFile(path) && !path.equals(currentFrameWrite))
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

	private boolean safeDelete(Path path)
	{
		try
		{
			return Files.deleteIfExists(path);
		}
		catch (IOException e)
		{
			log.debug("Failed to delete old telemetry frame {}", path, e);
			return false;
		}
	}

	private String normalizeScreenshotFormat(String format)
	{
		if (format == null)
		{
			return "jpg";
		}

		String normalized = format.trim().toLowerCase();
		return "png".equals(normalized) ? "png" : "jpg";
	}

	private String normalizeFrameCaptureMode(String mode)
	{
		if (mode == null)
		{
			return "RUNELITE_ONLY";
		}

		String normalized = mode.trim().toUpperCase();
		return "SCREEN_RECTANGLE".equals(normalized) ? "SCREEN_RECTANGLE" : "RUNELITE_ONLY";
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

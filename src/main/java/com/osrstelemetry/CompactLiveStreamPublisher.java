package com.osrstelemetry;

import com.google.gson.Gson;
import java.io.Closeable;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.StandardSocketOptions;
import java.nio.ByteBuffer;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class CompactLiveStreamPublisher implements Closeable
{
	private static final int POLL_TIMEOUT_MILLIS = 50;
	private static final int MAX_NON_BLOCKING_WRITE_ATTEMPTS = 4;

	private final String sessionId;
	private final Gson gson;
	private final String host;
	private final int port;
	private final int queueCapacity;
	private final boolean circuitBreakerEnabled;
	private final long maxWriteMillis;
	private final long disableMillis;
	private final LinkedBlockingQueue<LivePacket> queue;
	private final AtomicLong sequence = new AtomicLong();
	private final AtomicLong offeredPackets = new AtomicLong();
	private final AtomicLong streamedPackets = new AtomicLong();
	private final AtomicLong droppedPackets = new AtomicLong();
	private final AtomicLong droppedNoClients = new AtomicLong();
	private final AtomicLong droppedByCircuitBreaker = new AtomicLong();
	private final AtomicLong writeErrors = new AtomicLong();
	private final AtomicLong acceptedClients = new AtomicLong();
	private final AtomicLong disconnectedClients = new AtomicLong();
	private final AtomicLong circuitBreakerTrips = new AtomicLong();
	private final AtomicLong maxWriteMillisObserved = new AtomicLong();
	private final AtomicInteger clientCount = new AtomicInteger();
	private final Map<String, AtomicLong> offeredPacketsByType = new ConcurrentHashMap<>();
	private final Map<String, AtomicLong> streamedPacketsByType = new ConcurrentHashMap<>();
	private final Map<String, AtomicLong> droppedPacketsByType = new ConcurrentHashMap<>();
	private final Map<String, Long> latestOfferedTickByType = new ConcurrentHashMap<>();
	private final Map<String, Long> latestSentTickByType = new ConcurrentHashMap<>();
	private final List<SocketChannel> clients = new ArrayList<>();

	private volatile boolean running;
	private volatile long disabledUntilMillis;
	private volatile String circuitBreakerReason;
	private volatile String disabledUntilUtc;
	private Thread worker;
	private ServerSocketChannel serverChannel;
	private int boundPort;
	private long lastWriteMillis = -1L;

	public CompactLiveStreamPublisher(
			String sessionId,
			Gson gson,
			String host,
			int port,
			int queueSize,
			boolean circuitBreakerEnabled,
			long maxWriteMillis,
			long disableSeconds)
	{
		this.sessionId = sessionId;
		this.gson = gson;
		this.host = normalizeHost(host);
		this.port = Math.max(0, Math.min(65535, port));
		this.queueCapacity = Math.max(1, queueSize);
		this.queue = new LinkedBlockingQueue<>(queueCapacity);
		this.circuitBreakerEnabled = circuitBreakerEnabled;
		this.maxWriteMillis = Math.max(1L, maxWriteMillis);
		this.disableMillis = Math.max(1L, disableSeconds) * 1000L;
	}

	public void start() throws IOException
	{
		InetAddress bindAddress = loopbackAddress(host);
		serverChannel = ServerSocketChannel.open();
		serverChannel.configureBlocking(false);
		serverChannel.setOption(StandardSocketOptions.SO_REUSEADDR, true);
		serverChannel.bind(new InetSocketAddress(bindAddress, port));
		boundPort = serverChannel.socket().getLocalPort();
		running = true;
		worker = new Thread(this::runLoop, "telemetry-compact-live-stream");
		worker.setDaemon(true);
		worker.start();
	}

	public boolean enqueue(String packetType, long tick, String timestampUtc, Object payload)
	{
		if (!running || packetType == null || timestampUtc == null)
		{
			return false;
		}

		recordOffered(packetType, tick);

		if (isCircuitOpen())
		{
			recordCircuitDrop(packetType);
			return false;
		}

		if (clientCount.get() <= 0)
		{
			recordNoClientDrop(packetType);
			return false;
		}

		if (circuitBreakerEnabled && queue.size() >= highWatermark())
		{
			tripCircuitBreaker("stream queue high watermark reached");
			recordCircuitDrop(packetType);
			return false;
		}

		LivePacket packet = new LivePacket(packetType, sessionId, tick, sequence.incrementAndGet(), timestampUtc, payload);
		if (!queue.offer(packet))
		{
			recordQueueDrop(packetType);
			return false;
		}

		return true;
	}

	public int getQueueDepth()
	{
		return queue.size();
	}

	public int getClientCount()
	{
		return clientCount.get();
	}

	public int getBoundPort()
	{
		return boundPort;
	}

	public String getHost()
	{
		return host;
	}

	public int getConfiguredPort()
	{
		return port;
	}

	public long getOfferedPackets()
	{
		return offeredPackets.get();
	}

	public long getStreamedPackets()
	{
		return streamedPackets.get();
	}

	public long getDroppedPackets()
	{
		return droppedPackets.get();
	}

	public long getDroppedNoClients()
	{
		return droppedNoClients.get();
	}

	public long getDroppedByCircuitBreaker()
	{
		return droppedByCircuitBreaker.get();
	}

	public long getWriteErrors()
	{
		return writeErrors.get();
	}

	public long getAcceptedClients()
	{
		return acceptedClients.get();
	}

	public long getDisconnectedClients()
	{
		return disconnectedClients.get();
	}

	public long getLastWriteMillis()
	{
		return lastWriteMillis;
	}

	public boolean isCircuitBreakerTripped()
	{
		return isCircuitOpen();
	}

	public String getCircuitBreakerReason()
	{
		return circuitBreakerReason;
	}

	public String getDisabledUntilUtc()
	{
		return isCircuitOpen() ? disabledUntilUtc : null;
	}

	public long getCircuitBreakerTrips()
	{
		return circuitBreakerTrips.get();
	}

	public long getMaxWriteMillisObserved()
	{
		return maxWriteMillisObserved.get();
	}

	public Map<String, Long> getOfferedPacketsByType()
	{
		return counterSnapshot(offeredPacketsByType);
	}

	public Map<String, Long> getStreamedPacketsByType()
	{
		return counterSnapshot(streamedPacketsByType);
	}

	public Map<String, Long> getDroppedPacketsByType()
	{
		return counterSnapshot(droppedPacketsByType);
	}

	public Map<String, Long> getLatestOfferedTickByType()
	{
		return longSnapshot(latestOfferedTickByType);
	}

	public Map<String, Long> getLatestSentTickByType()
	{
		return longSnapshot(latestSentTickByType);
	}

	private void runLoop()
	{
		try
		{
			while (running || !queue.isEmpty())
			{
				acceptClientsIfReady();
				LivePacket packet = queue.poll();
				if (packet == null)
				{
					packet = running ? queue.poll(POLL_TIMEOUT_MILLIS, TimeUnit.MILLISECONDS) : queue.poll();
				}
				if (packet != null)
				{
					writePacket(packet);
				}
			}
		}
		catch (InterruptedException e)
		{
			Thread.currentThread().interrupt();
		}
		finally
		{
			closeServer();
			closeClients();
		}
	}

	private void acceptClientsIfReady()
	{
		ServerSocketChannel server = serverChannel;
		if (server == null)
		{
			return;
		}

		while (running)
		{
			try
			{
				SocketChannel client = server.accept();
				if (client == null)
				{
					return;
				}
				client.configureBlocking(false);
				client.setOption(StandardSocketOptions.TCP_NODELAY, true);
				synchronized (clients)
				{
					clients.add(client);
					clientCount.set(clients.size());
				}
				acceptedClients.incrementAndGet();
			}
			catch (IOException e)
			{
				if (running)
				{
					writeErrors.incrementAndGet();
					log.debug("Compact live stream accept failed", e);
				}
				return;
			}
		}
	}

	private void writePacket(LivePacket packet)
	{
		if (isCircuitOpen())
		{
			recordCircuitDrop(packet.packetType);
			return;
		}

		long started = System.nanoTime();
		byte[] line = (gson.toJson(packet) + "\n").getBytes(StandardCharsets.UTF_8);
		boolean delivered = false;
		List<SocketChannel> snapshot;
		List<SocketChannel> stale = new ArrayList<>();

		synchronized (clients)
		{
			snapshot = new ArrayList<>(clients);
		}

		for (SocketChannel client : snapshot)
		{
			if (writeNonBlocking(client, line))
			{
				delivered = true;
			}
			else
			{
				stale.add(client);
				writeErrors.incrementAndGet();
			}
		}

		removeAndCloseStaleClients(stale);

		long elapsedMillis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started);
		lastWriteMillis = elapsedMillis;
		maxWriteMillisObserved.updateAndGet(previous -> Math.max(previous, elapsedMillis));

		if (elapsedMillis > maxWriteMillis)
		{
			tripCircuitBreaker("stream write exceeded " + maxWriteMillis + " ms");
		}

		if (delivered)
		{
			streamedPackets.incrementAndGet();
			incrementType(streamedPacketsByType, packet.packetType);
			latestSentTickByType.put(packet.packetType, packet.tick);
		}
		else
		{
			recordNoClientDrop(packet.packetType);
		}
	}

	private boolean writeNonBlocking(SocketChannel client, byte[] line)
	{
		ByteBuffer buffer = ByteBuffer.wrap(line);
		int attempts = 0;

		try
		{
			while (buffer.hasRemaining() && attempts < MAX_NON_BLOCKING_WRITE_ATTEMPTS)
			{
				int written = client.write(buffer);
				if (written <= 0)
				{
					break;
				}
				attempts++;
			}
			return !buffer.hasRemaining();
		}
		catch (IOException e)
		{
			return false;
		}
	}

	private void removeAndCloseStaleClients(List<SocketChannel> stale)
	{
		if (stale.isEmpty())
		{
			return;
		}

		synchronized (clients)
		{
			clients.removeAll(stale);
			clientCount.set(clients.size());
		}

		for (SocketChannel client : stale)
		{
			closeClient(client);
		}
	}

	private void recordOffered(String packetType, long tick)
	{
		offeredPackets.incrementAndGet();
		incrementType(offeredPacketsByType, packetType);
		latestOfferedTickByType.put(packetType, tick);
	}

	private void recordQueueDrop(String packetType)
	{
		long dropped = droppedPackets.incrementAndGet();
		incrementType(droppedPacketsByType, packetType);
		if (dropped == 1 || dropped % 1000 == 0)
		{
			log.warn("Compact live stream queue full; dropped {} packets", dropped);
		}
	}

	private void recordNoClientDrop(String packetType)
	{
		droppedNoClients.incrementAndGet();
		droppedPackets.incrementAndGet();
		incrementType(droppedPacketsByType, packetType);
	}

	private void recordCircuitDrop(String packetType)
	{
		droppedByCircuitBreaker.incrementAndGet();
		droppedPackets.incrementAndGet();
		incrementType(droppedPacketsByType, packetType);
	}

	private void tripCircuitBreaker(String reason)
	{
		if (!circuitBreakerEnabled)
		{
			return;
		}

		long now = System.currentTimeMillis();
		long disabledUntil = now + disableMillis;
		if (disabledUntil > disabledUntilMillis)
		{
			disabledUntilMillis = disabledUntil;
			disabledUntilUtc = Instant.ofEpochMilli(disabledUntil).toString();
			circuitBreakerReason = reason;
			circuitBreakerTrips.incrementAndGet();
			log.warn("Compact live stream circuit breaker tripped: {}", reason);
		}
	}

	private boolean isCircuitOpen()
	{
		return circuitBreakerEnabled && System.currentTimeMillis() < disabledUntilMillis;
	}

	private int highWatermark()
	{
		return Math.max(1, (int) Math.ceil(queueCapacity * 0.90));
	}

	private static void incrementType(Map<String, AtomicLong> counters, String packetType)
	{
		if (packetType == null)
		{
			return;
		}
		counters.computeIfAbsent(packetType, ignored -> new AtomicLong()).incrementAndGet();
	}

	private static Map<String, Long> counterSnapshot(Map<String, AtomicLong> counters)
	{
		Map<String, Long> snapshot = new LinkedHashMap<>();
		counters.keySet().stream().sorted().forEach(packetType ->
				snapshot.put(packetType, counters.get(packetType).get()));
		return snapshot;
	}

	private static Map<String, Long> longSnapshot(Map<String, Long> values)
	{
		Map<String, Long> snapshot = new LinkedHashMap<>();
		values.keySet().stream().sorted().forEach(packetType ->
				snapshot.put(packetType, values.get(packetType)));
		return snapshot;
	}

	private static String normalizeHost(String configured)
	{
		if (configured == null || configured.isBlank())
		{
			return "127.0.0.1";
		}
		return configured.trim();
	}

	private static InetAddress loopbackAddress(String configured) throws IOException
	{
		String normalized = normalizeHost(configured);
		if ("localhost".equalsIgnoreCase(normalized))
		{
			return InetAddress.getLoopbackAddress();
		}
		if ("127.0.0.1".equals(normalized) || "::1".equals(normalized) || "0:0:0:0:0:0:0:1".equals(normalized))
		{
			InetAddress address = InetAddress.getByName(normalized);
			if (address.isLoopbackAddress())
			{
				return address;
			}
		}
		throw new IOException("Compact live stream host must be a loopback literal or localhost: " + configured);
	}

	private void closeServer()
	{
		ServerSocketChannel server = serverChannel;
		serverChannel = null;
		if (server == null)
		{
			return;
		}

		try
		{
			server.close();
		}
		catch (IOException e)
		{
			log.debug("Failed to close compact live stream server", e);
		}
	}

	private void closeClients()
	{
		List<SocketChannel> snapshot;
		synchronized (clients)
		{
			snapshot = new ArrayList<>(clients);
			clients.clear();
			clientCount.set(0);
		}

		for (SocketChannel client : snapshot)
		{
			closeClient(client);
		}
	}

	private void closeClient(SocketChannel client)
	{
		try
		{
			client.close();
		}
		catch (IOException e)
		{
			log.debug("Failed to close compact live stream client", e);
		}
		finally
		{
			disconnectedClients.incrementAndGet();
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
		}
	}
}

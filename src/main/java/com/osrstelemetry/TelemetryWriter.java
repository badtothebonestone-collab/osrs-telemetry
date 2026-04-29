package com.osrstelemetry;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class TelemetryWriter implements Closeable
{
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

    private final LinkedBlockingQueue<QueuedLine> queue = new LinkedBlockingQueue<>(100_000);
    private final Path sessionDir;
    private final Path tickFile;
    private final Path eventFile;
    private final AtomicLong droppedRecords = new AtomicLong();

    private volatile boolean running = false;
    private Thread worker;
    private BufferedWriter tickWriter;
    private BufferedWriter eventWriter;

    public TelemetryWriter(String outputDirectory)
    {
        String sessionId = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
        this.sessionDir = Path.of(outputDirectory, sessionId);
        this.tickFile = sessionDir.resolve("ticks.jsonl");
        this.eventFile = sessionDir.resolve("events.jsonl");
    }

    public void start() throws IOException
    {
        Files.createDirectories(sessionDir);

        tickWriter = Files.newBufferedWriter(tickFile, StandardCharsets.UTF_8);
        eventWriter = Files.newBufferedWriter(eventFile, StandardCharsets.UTF_8);

        running = true;
        worker = new Thread(this::runWriterLoop, "telemetry-writer");
        worker.setDaemon(true);
        worker.start();

        System.out.println("Telemetry session started: " + sessionDir);
    }

    public void enqueueTick(String json)
    {
        enqueue(new QueuedLine("ticks", json));
    }

    public void enqueueEvent(String json)
    {
        enqueue(new QueuedLine("events", json));
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

                if (line == null)
                {
                    continue;
                }

                if ("ticks".equals(line.stream))
                {
                    tickWriter.write(line.json);
                    tickWriter.newLine();
                    tickWriter.flush();
                }
                else if ("events".equals(line.stream))
                {
                    eventWriter.write(line.json);
                    eventWriter.newLine();
                    eventWriter.flush();
                }
            }

            tickWriter.flush();
            eventWriter.flush();
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
            closeWriter(tickWriter, "ticks");
            closeWriter(eventWriter, "events");
        }
    }

    @Override
    public void close() throws IOException
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

    private void writeLine(QueuedLine line) throws IOException
    {
        if ("ticks".equals(line.stream))
        {
            tickWriter.write(line.json);
            tickWriter.newLine();
            tickWriter.flush();
        }
        else if ("events".equals(line.stream))
        {
            eventWriter.write(line.json);
            eventWriter.newLine();
            eventWriter.flush();
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

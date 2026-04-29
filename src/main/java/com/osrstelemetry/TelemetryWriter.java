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

public class TelemetryWriter implements Closeable
{
    public void enqueue(String json) {
    }

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
        queue.offer(new QueuedLine("ticks", json));
    }

    public void enqueueEvent(String json)
    {
        queue.offer(new QueuedLine("events", json));
    }

    private void runWriterLoop()
    {
        try
        {
            while (running || !queue.isEmpty())
            {
                QueuedLine line = queue.poll(250, TimeUnit.MILLISECONDS);

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
        catch (Exception e)
        {
            e.printStackTrace();
        }
    }

    @Override
    public void close() throws IOException
    {
        running = false;

        if (worker != null)
        {
            try
            {
                worker.join(2000);
            }
            catch (InterruptedException e)
            {
                Thread.currentThread().interrupt();
            }
        }

        if (tickWriter != null)
        {
            tickWriter.flush();
            tickWriter.close();
        }

        if (eventWriter != null)
        {
            eventWriter.flush();
            eventWriter.close();
        }
    }
}
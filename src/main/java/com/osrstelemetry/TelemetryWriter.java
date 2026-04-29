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
    private final LinkedBlockingQueue<String> queue = new LinkedBlockingQueue<>(100_000);
    private final Path sessionDir;
    private final Path tickFile;

    private volatile boolean running = false;
    private Thread worker;
    private BufferedWriter writer;

    public TelemetryWriter(String outputDirectory)
    {
        String sessionId = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
        this.sessionDir = Path.of(outputDirectory, "sessions", sessionId);
        this.tickFile = sessionDir.resolve("ticks.jsonl");
    }

    public void start() throws IOException
    {
        Files.createDirectories(sessionDir);

        writer = Files.newBufferedWriter(
                tickFile,
                StandardCharsets.UTF_8
        );

        running = true;
        worker = new Thread(this::runWriterLoop, "telemetry-writer");
        worker.setDaemon(true);
        worker.start();

        System.out.println("Telemetry session started: " + sessionDir);
    }

    public void enqueue(String json)
    {
        queue.offer(json);
    }

    private void runWriterLoop()
    {
        try
        {
            while (running || !queue.isEmpty())
            {
                String line = queue.poll(250, TimeUnit.MILLISECONDS);

                if (line != null)
                {
                    writer.write(line);
                    writer.newLine();
                    writer.flush();
                }
            }

            writer.flush();
        }
        catch (InterruptedException e)
        {
            Thread.currentThread().interrupt();
        }
        catch (Exception e)
        {
            e.printStackTrace();
        }
        finally
        {
            try
            {
                writer.close();
            }
            catch (IOException e)
            {
                e.printStackTrace();
            }
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
}

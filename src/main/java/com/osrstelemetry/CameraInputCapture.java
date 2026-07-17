package com.osrstelemetry;

import java.awt.Canvas;
import java.awt.event.InputEvent;
import java.awt.event.KeyEvent;
import java.awt.event.MouseEvent;
import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.function.LongSupplier;
import net.runelite.client.input.KeyListener;
import net.runelite.client.input.MouseListener;

/**
 * Privacy-bounded observation of camera controls inside the RuneLite canvas.
 * This listener never consumes or changes an input event and deliberately does
 * not inspect key characters, typed events, modifiers, or non-camera buttons.
 * Observation is disabled by default and requires a short renewable lease from
 * an explicit demonstration snapshot request.
 */
final class CameraInputCapture implements KeyListener, MouseListener
{
	static final String SCHEMA = "plugin_camera_input.v1";
	static final long CAPTURE_LEASE_MILLIS = 2_000L;
	private static final long CAPTURE_LEASE_NANOS =
			TimeUnit.MILLISECONDS.toNanos(CAPTURE_LEASE_MILLIS);
	private static final List<String> CAMERA_POSE_NUMERIC_FIELDS = List.of(
			"clientTick",
			"gameTick",
			"cameraX",
			"cameraY",
			"cameraZ",
			"cameraPitch",
			"cameraYaw",
			"viewportWidth",
			"viewportHeight",
			"viewportXOffset",
			"viewportYOffset",
			"canvasWidth",
			"canvasHeight",
			"cameraYawTarget",
			"cameraPitchTarget",
			"zoom3d");

	private final Consumer<Map<String, Object>> sink;
	private final Runnable onLeaseStarted;
	private final LongSupplier wallClockMillis;
	private final LongSupplier monotonicNanos;
	private final Map<Integer, Long> pressedAtNanos = new HashMap<>();
	private volatile Context context = Context.blocked();
	private long captureLeaseExpiresAtNanos;
	private boolean closed;
	private boolean middleHeld;
	private long middlePressedAtNanos;
	private int middleStartX;
	private int middleStartY;
	private int middleLastX;
	private int middleLastY;
	private double middlePathDistance;
	private int middleDragSamples;

	CameraInputCapture(Consumer<Map<String, Object>> sink)
	{
		this(sink, null, System::currentTimeMillis, System::nanoTime);
	}

	CameraInputCapture(
			Consumer<Map<String, Object>> sink,
			Runnable onLeaseStarted,
			LongSupplier wallClockMillis,
			LongSupplier monotonicNanos)
	{
		this.sink = sink;
		this.onLeaseStarted = onLeaseStarted;
		this.wallClockMillis = wallClockMillis == null ? System::currentTimeMillis : wallClockMillis;
		this.monotonicNanos = monotonicNanos == null ? System::nanoTime : monotonicNanos;
	}

	/** Starts or renews the fixed, bounded demonstration-capture lease. */
	public synchronized void renewLease()
	{
		if (closed)
		{
			return;
		}
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		if (captureLeaseExpiresAtNanos == 0L && onLeaseStarted != null)
		{
			// Clear the prior demonstration lane before any new event can pass.
			onLeaseStarted.run();
		}
		captureLeaseExpiresAtNanos = saturatedAdd(nowNanos, CAPTURE_LEASE_NANOS);
	}

	/** Explicitly ends capture and closes any in-flight key or drag episode. */
	public synchronized void disableLease()
	{
		disableLease(monotonicNow());
	}

	synchronized void updateContext(Context next)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		Context replacement = next == null ? Context.blocked() : next;
		if (context.allowed && (!replacement.allowed || !sameProvenance(context, replacement)))
		{
			// Close against the old provenance. A blocked replacement can lack a
			// session/canvas, and a new allowed session must not inherit holds.
			cancelActiveInputs(context, nowNanos);
		}
		context = replacement;
	}

	synchronized void close()
	{
		disableLease(monotonicNow());
		context = Context.blocked();
		closed = true;
	}

	@Override
	public synchronized void keyPressed(KeyEvent event)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		String control = keyControl(event == null ? KeyEvent.VK_UNDEFINED : event.getKeyCode());
		if (control == null || !accepts(event, nowNanos))
		{
			return;
		}
		if (pressedAtNanos.putIfAbsent(event.getKeyCode(), nowNanos) == null)
		{
			emit(basePayload(context, "key", "press", control, event, nowNanos));
		}
	}

	@Override
	public synchronized void keyReleased(KeyEvent event)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		String control = keyControl(event == null ? KeyEvent.VK_UNDEFINED : event.getKeyCode());
		if (control == null || !accepts(event, nowNanos))
		{
			return;
		}
		Long started = pressedAtNanos.remove(event.getKeyCode());
		if (started == null)
		{
			return;
		}
		Map<String, Object> payload = basePayload(
				context, "key", "release", control, event, nowNanos);
		payload.put("holdDurationMillis", elapsedNanosMillis(started, nowNanos));
		emit(payload);
	}

	@Override
	public void keyTyped(KeyEvent event)
	{
		// Intentionally ignored. Typed characters are outside the telemetry boundary.
	}

	@Override
	public synchronized void focusLost()
	{
		updateContext(Context.blocked());
	}

	@Override
	public synchronized MouseEvent mousePressed(MouseEvent event)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		if (event != null && event.getButton() == MouseEvent.BUTTON2 && accepts(event, nowNanos))
		{
			middleHeld = true;
			middlePressedAtNanos = nowNanos;
			middleStartX = middleLastX = event.getX();
			middleStartY = middleLastY = event.getY();
			middlePathDistance = 0.0d;
			middleDragSamples = 0;
			Map<String, Object> payload = basePayload(
					context, "middle_drag", "press", "MIDDLE", event, nowNanos);
			addCanvasPoint(payload, event);
			emit(payload);
		}
		return event;
	}

	@Override
	public synchronized MouseEvent mouseDragged(MouseEvent event)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		if (middleHeld && accepts(event, nowNanos))
		{
			int x = event.getX();
			int y = event.getY();
			int dx = x - middleLastX;
			int dy = y - middleLastY;
			if (dx != 0 || dy != 0)
			{
				middlePathDistance += Math.hypot(dx, dy);
				middleLastX = x;
				middleLastY = y;
				middleDragSamples++;
				Map<String, Object> payload = basePayload(
						context, "middle_drag", "drag", "MIDDLE", event, nowNanos);
				addCanvasPoint(payload, event);
				payload.put("deltaX", dx);
				payload.put("deltaY", dy);
				emit(payload);
			}
		}
		return event;
	}

	@Override
	public synchronized MouseEvent mouseReleased(MouseEvent event)
	{
		long nowNanos = monotonicNow();
		expireLeaseIfNeeded(nowNanos);
		if (middleHeld
				&& event != null
				&& event.getButton() == MouseEvent.BUTTON2
				&& accepts(event, nowNanos))
		{
			Map<String, Object> payload = basePayload(
					context, "middle_drag", "release", "MIDDLE", event, nowNanos);
			addCanvasPoint(payload, event);
			addMiddleSummary(payload, nowNanos);
			emit(payload);
			middleHeld = false;
		}
		return event;
	}

	@Override
	public MouseEvent mouseClicked(MouseEvent event)
	{
		return event;
	}

	@Override
	public MouseEvent mouseEntered(MouseEvent event)
	{
		return event;
	}

	@Override
	public MouseEvent mouseExited(MouseEvent event)
	{
		return event;
	}

	@Override
	public MouseEvent mouseMoved(MouseEvent event)
	{
		return event;
	}

	private boolean accepts(InputEvent event, long nowNanos)
	{
		Context current = context;
		return event != null
				&& leaseActive(nowNanos)
				&& current.allowed
				&& current.canvas != null
				&& event.getComponent() == current.canvas;
	}

	private void expireLeaseIfNeeded(long nowNanos)
	{
		if (captureLeaseExpiresAtNanos != 0L && !leaseActive(nowNanos))
		{
			disableLease(nowNanos);
		}
	}

	private boolean leaseActive(long nowNanos)
	{
		return captureLeaseExpiresAtNanos != 0L && nowNanos < captureLeaseExpiresAtNanos;
	}

	private void disableLease(long nowNanos)
	{
		cancelActiveInputs(context, nowNanos);
		captureLeaseExpiresAtNanos = 0L;
	}

	private void cancelActiveInputs(Context current, long nowNanos)
	{
		for (Map.Entry<Integer, Long> pressed : pressedAtNanos.entrySet())
		{
			String control = keyControl(pressed.getKey());
			if (control != null)
			{
				Map<String, Object> payload = basePayload(
						current, "key", "cancel", control, null, nowNanos);
				payload.put("holdDurationMillis", elapsedNanosMillis(pressed.getValue(), nowNanos));
				emit(payload);
			}
		}
		pressedAtNanos.clear();
		if (middleHeld)
		{
			Map<String, Object> payload = basePayload(
					current, "middle_drag", "cancel", "MIDDLE", null, nowNanos);
			payload.put("canvasX", middleLastX);
			payload.put("canvasY", middleLastY);
			addMiddleSummary(payload, nowNanos);
			emit(payload);
			middleHeld = false;
		}
	}

	private void addMiddleSummary(Map<String, Object> payload, long nowNanos)
	{
		payload.put("holdDurationMillis", elapsedNanosMillis(middlePressedAtNanos, nowNanos));
		payload.put("totalDeltaX", middleLastX - middleStartX);
		payload.put("totalDeltaY", middleLastY - middleStartY);
		payload.put("pathDistancePixels", middlePathDistance);
		payload.put("dragSampleCount", middleDragSamples);
	}

	private Map<String, Object> basePayload(
			Context current,
			String inputKind,
			String phase,
			String control,
			InputEvent event,
			long monotonicTimeNanos)
	{
		long wallTimeMillis = wallClockMillis.getAsLong();
		Map<String, Object> payload = new LinkedHashMap<>();
		payload.put("schema", SCHEMA);
		payload.put("sampleSource", "CameraInput");
		payload.put("sourceEvent", "CameraInput");
		payload.put("inputKind", inputKind);
		payload.put("phase", phase);
		payload.put("control", control);
		payload.put("timestampUtc", Instant.ofEpochMilli(wallTimeMillis).toString());
		payload.put("wallTimeMillis", wallTimeMillis);
		payload.put("monotonicTimeNanos", monotonicTimeNanos);
		payload.put("awtEventWhenMillis", event == null ? null : event.getWhen());
		payload.put("clientTick", current.clientTick);
		payload.put("gameTickAtSample", current.gameTick);
		payload.put("gameState", current.gameState);
		payload.put("sessionId", current.sessionId);
		payload.put("clientProcessId", current.clientProcessId);
		payload.put("cameraPose", current.cameraPose == null
				? null
				: new LinkedHashMap<>(current.cameraPose));
		return payload;
	}

	private void addCanvasPoint(Map<String, Object> payload, MouseEvent event)
	{
		payload.put("canvasX", event.getX());
		payload.put("canvasY", event.getY());
	}

	private void emit(Map<String, Object> payload)
	{
		if (sink != null)
		{
			sink.accept(payload);
		}
	}

	private long monotonicNow()
	{
		return monotonicNanos.getAsLong();
	}

	private static boolean sameProvenance(Context first, Context second)
	{
		return first.canvas == second.canvas
				&& first.clientProcessId == second.clientProcessId
				&& Objects.equals(first.sessionId, second.sessionId);
	}

	private static long saturatedAdd(long first, long second)
	{
		if (second > 0L && first > Long.MAX_VALUE - second)
		{
			return Long.MAX_VALUE;
		}
		return first + second;
	}

	private static long elapsedNanosMillis(long startedAtNanos, long endedAtNanos)
	{
		if (endedAtNanos <= startedAtNanos)
		{
			return 0L;
		}
		return TimeUnit.NANOSECONDS.toMillis(endedAtNanos - startedAtNanos);
	}

	static String keyControl(int keyCode)
	{
		switch (keyCode)
		{
			case KeyEvent.VK_W:
				return "W";
			case KeyEvent.VK_A:
				return "A";
			case KeyEvent.VK_S:
				return "S";
			case KeyEvent.VK_D:
				return "D";
			case KeyEvent.VK_UP:
				return "UP";
			case KeyEvent.VK_LEFT:
				return "LEFT";
			case KeyEvent.VK_DOWN:
				return "DOWN";
			case KeyEvent.VK_RIGHT:
				return "RIGHT";
			default:
				return null;
		}
	}

	private static Map<String, Object> strictCameraPose(Map<String, Object> source)
	{
		if (source == null)
		{
			return null;
		}
		Map<String, Object> pose = new LinkedHashMap<>();
		copyString(source, pose, "schema");
		for (String field : CAMERA_POSE_NUMERIC_FIELDS)
		{
			Object value = source.get(field);
			if (value instanceof Number)
			{
				pose.put(field, value);
			}
		}
		copyString(source, pose, "geometryFrameId");
		return pose.isEmpty() ? null : pose;
	}

	private static void copyString(Map<String, Object> source, Map<String, Object> target, String field)
	{
		Object value = source.get(field);
		if (value instanceof String)
		{
			target.put(field, value);
		}
	}

	static final class Context
	{
		final boolean allowed;
		final Canvas canvas;
		final long clientTick;
		final long gameTick;
		final String gameState;
		final String sessionId;
		final long clientProcessId;
		final Map<String, Object> cameraPose;

		Context(
				boolean allowed,
				Canvas canvas,
				long clientTick,
				long gameTick,
				String gameState,
				String sessionId,
				long clientProcessId,
				Map<String, Object> cameraPose)
		{
			this.allowed = allowed;
			this.canvas = canvas;
			this.clientTick = clientTick;
			this.gameTick = gameTick;
			this.gameState = gameState;
			this.sessionId = sessionId;
			this.clientProcessId = clientProcessId;
			Map<String, Object> strictPose = strictCameraPose(cameraPose);
			this.cameraPose = strictPose == null ? null : Map.copyOf(strictPose);
		}

		static Context blocked()
		{
			return new Context(false, null, 0L, 0L, null, null, ProcessHandle.current().pid(), null);
		}
	}
}

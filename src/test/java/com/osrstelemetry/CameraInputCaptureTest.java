package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import java.awt.Canvas;
import java.awt.event.KeyEvent;
import java.awt.event.MouseEvent;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.Test;

public class CameraInputCaptureTest
{
	@Test
	public void recordsOnlyWhitelistedEffectiveCameraKeysWithoutConsumingEvents()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = capture(canvas, samples, true);
		KeyEvent wPress = key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_W);
		KeyEvent wRelease = key(canvas, KeyEvent.KEY_RELEASED, KeyEvent.VK_W);
		KeyEvent enter = key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_ENTER);

		capture.keyPressed(wPress);
		capture.keyPressed(enter);
		capture.keyReleased(wRelease);

		assertFalse(wPress.isConsumed());
		assertFalse(wRelease.isConsumed());
		assertFalse(enter.isConsumed());
		assertEquals(2, samples.size());
		assertEquals("W", samples.get(0).get("control"));
		assertEquals("press", samples.get(0).get("phase"));
		assertEquals("release", samples.get(1).get("phase"));
		assertTrue(((Number) samples.get(1).get("holdDurationMillis")).longValue() >= 0L);
		for (Map<String, Object> sample : samples)
		{
			assertFalse(sample.containsKey("keyChar"));
			assertFalse(sample.containsKey("text"));
			assertFalse(sample.containsKey("modifiers"));
		}
	}

	@Test
	public void recordsEveryWasdAndArrowCameraControl()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = capture(canvas, samples, true);
		int[] keyCodes = {
				KeyEvent.VK_W,
				KeyEvent.VK_A,
				KeyEvent.VK_S,
				KeyEvent.VK_D,
				KeyEvent.VK_UP,
				KeyEvent.VK_LEFT,
				KeyEvent.VK_DOWN,
				KeyEvent.VK_RIGHT
		};
		List<String> controls = List.of(
				"W", "A", "S", "D", "UP", "LEFT", "DOWN", "RIGHT");

		for (int keyCode : keyCodes)
		{
			capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, keyCode));
			capture.keyReleased(key(canvas, KeyEvent.KEY_RELEASED, keyCode));
		}

		assertEquals(controls.size() * 2, samples.size());
		for (int index = 0; index < controls.size(); index++)
		{
			assertEquals(controls.get(index), samples.get(index * 2).get("control"));
			assertEquals("press", samples.get(index * 2).get("phase"));
			assertEquals(controls.get(index), samples.get(index * 2 + 1).get("control"));
			assertEquals("release", samples.get(index * 2 + 1).get("phase"));
		}
	}

	@Test
	public void recordsOnlyMiddlePressDragReleaseAndReturnsOriginalEvents()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = capture(canvas, samples, true);
		MouseEvent left = mouse(canvas, MouseEvent.MOUSE_PRESSED, 10, 10, MouseEvent.BUTTON1);
		MouseEvent press = mouse(canvas, MouseEvent.MOUSE_PRESSED, 20, 30, MouseEvent.BUTTON2);
		MouseEvent drag = mouse(canvas, MouseEvent.MOUSE_DRAGGED, 28, 34, MouseEvent.NOBUTTON);
		MouseEvent release = mouse(canvas, MouseEvent.MOUSE_RELEASED, 28, 34, MouseEvent.BUTTON2);

		assertSame(left, capture.mousePressed(left));
		assertSame(press, capture.mousePressed(press));
		assertSame(drag, capture.mouseDragged(drag));
		assertSame(release, capture.mouseReleased(release));

		assertFalse(left.isConsumed());
		assertFalse(press.isConsumed());
		assertEquals(3, samples.size());
		assertEquals("middle_drag", samples.get(0).get("inputKind"));
		assertEquals("drag", samples.get(1).get("phase"));
		assertEquals(8, samples.get(1).get("deltaX"));
		assertEquals(4, samples.get(1).get("deltaY"));
		assertEquals(8, samples.get(2).get("totalDeltaX"));
		assertEquals(4, samples.get(2).get("totalDeltaY"));
	}

	@Test
	public void wrongCanvasOrClosedPrivacyGateProducesNoSamplesAndGateLossCancelsHold()
	{
		Canvas canvas = new Canvas();
		Canvas other = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = capture(canvas, samples, true);

		capture.keyPressed(key(other, KeyEvent.KEY_PRESSED, KeyEvent.VK_LEFT));
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_LEFT));
		capture.updateContext(context(canvas, false));
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_RIGHT));

		assertEquals(2, samples.size());
		assertEquals("press", samples.get(0).get("phase"));
		assertEquals("cancel", samples.get(1).get("phase"));
	}

	@Test
	public void captureIsDisabledUntilRenewedAndExpiryOrExplicitDisableCancelsHolds()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		AtomicInteger leaseStarts = new AtomicInteger();
		AtomicLong wallMillis = new AtomicLong(10_000L);
		AtomicLong nanos = new AtomicLong(1_000_000_000L);
		CameraInputCapture capture = new CameraInputCapture(
				samples::add,
				leaseStarts::incrementAndGet,
				wallMillis::get,
				nanos::get);
		capture.updateContext(context(canvas, true));

		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_W));
		assertTrue(samples.isEmpty());

		capture.renewLease();
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_W));
		assertEquals(1, leaseStarts.get());
		assertEquals("press", samples.get(0).get("phase"));

		nanos.addAndGet((CameraInputCapture.CAPTURE_LEASE_MILLIS + 1L) * 1_000_000L);
		capture.updateContext(context(canvas, true));
		assertEquals("cancel", samples.get(1).get("phase"));
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_A));
		assertEquals(2, samples.size());

		capture.renewLease();
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_A));
		capture.disableLease();
		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_D));
		assertEquals(2, leaseStarts.get());
		assertEquals(List.of("press", "cancel", "press", "cancel"), List.of(
				samples.get(0).get("phase"),
				samples.get(1).get("phase"),
				samples.get(2).get("phase"),
				samples.get(3).get("phase")));
	}

	@Test
	public void allowedProvenanceChangesCancelRatherThanCarryActiveHolds()
	{
		Canvas firstCanvas = new Canvas();
		Canvas secondCanvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		AtomicLong nanos = new AtomicLong(1_000_000_000L);
		CameraInputCapture capture = new CameraInputCapture(
				samples::add, null, () -> 10_000L, nanos::get);
		capture.updateContext(context(firstCanvas, true, "session-1", 1234L));
		capture.renewLease();

		capture.keyPressed(key(firstCanvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_W));
		capture.updateContext(context(firstCanvas, true, "session-2", 1234L));
		capture.keyPressed(key(firstCanvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_A));
		capture.updateContext(context(firstCanvas, true, "session-2", 5678L));
		capture.keyPressed(key(firstCanvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_S));
		capture.updateContext(context(secondCanvas, true, "session-2", 5678L));

		assertEquals(6, samples.size());
		assertEquals(List.of("press", "cancel", "press", "cancel", "press", "cancel"), List.of(
				samples.get(0).get("phase"),
				samples.get(1).get("phase"),
				samples.get(2).get("phase"),
				samples.get(3).get("phase"),
				samples.get(4).get("phase"),
				samples.get(5).get("phase")));
		assertEquals("session-1", samples.get(1).get("sessionId"));
		assertEquals(1234L, samples.get(3).get("clientProcessId"));
	}

	@Test
	@SuppressWarnings("unchecked")
	public void samplesRetainAwtTimeUseMonotonicHoldDurationAndFilterPoseFields()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		AtomicLong wallMillis = new AtomicLong(50_000L);
		AtomicLong nanos = new AtomicLong(1_000_000_000L);
		CameraInputCapture capture = new CameraInputCapture(
				samples::add, null, wallMillis::get, nanos::get);
		capture.updateContext(new CameraInputCapture.Context(
				true,
				canvas,
				12L,
				7L,
				"LOGGED_IN",
				"test-session",
				1234L,
				Map.of(
						"schema", "camera_pose.v1",
						"cameraYaw", 100,
						"cameraPitch", 300,
						"geometryFrameId", "geometry-test",
						"unexpected", "must-not-escape")));
		capture.renewLease();
		KeyEvent press = new KeyEvent(
				canvas, KeyEvent.KEY_PRESSED, 12_345L, 0, KeyEvent.VK_LEFT, KeyEvent.CHAR_UNDEFINED);
		KeyEvent release = new KeyEvent(
				canvas, KeyEvent.KEY_RELEASED, 12_399L, 0, KeyEvent.VK_LEFT, KeyEvent.CHAR_UNDEFINED);

		capture.keyPressed(press);
		wallMillis.set(40_000L);
		nanos.addAndGet(37_900_000L);
		capture.keyReleased(release);

		assertEquals(12_345L, samples.get(0).get("awtEventWhenMillis"));
		assertEquals(12_399L, samples.get(1).get("awtEventWhenMillis"));
		assertEquals(37L, samples.get(1).get("holdDurationMillis"));
		assertEquals(Set.of(
				"schema", "sampleSource", "sourceEvent", "inputKind", "phase", "control",
				"timestampUtc", "wallTimeMillis", "monotonicTimeNanos", "awtEventWhenMillis",
				"clientTick", "gameTickAtSample", "gameState", "sessionId", "clientProcessId",
				"cameraPose"), samples.get(0).keySet());
		Map<String, Object> pose = (Map<String, Object>) samples.get(0).get("cameraPose");
		assertEquals(Set.of("schema", "cameraYaw", "cameraPitch", "geometryFrameId"), pose.keySet());
		assertFalse(pose.containsKey("unexpected"));
	}

	private CameraInputCapture capture(
			Canvas canvas,
			List<Map<String, Object>> samples,
			boolean allowed)
	{
		CameraInputCapture capture = new CameraInputCapture(samples::add);
		capture.updateContext(context(canvas, allowed));
		if (allowed)
		{
			capture.renewLease();
		}
		return capture;
	}

	private CameraInputCapture.Context context(Canvas canvas, boolean allowed)
	{
		return context(canvas, allowed, "test-session", 1234L);
	}

	private CameraInputCapture.Context context(
			Canvas canvas,
			boolean allowed,
			String sessionId,
			long processId)
	{
		return new CameraInputCapture.Context(
				allowed,
				canvas,
				12L,
				7L,
				"LOGGED_IN",
				sessionId,
				processId,
				Map.of(
						"schema", "camera_pose.v1",
						"cameraYaw", 100,
						"cameraPitch", 300,
						"geometryFrameId", "geometry-test"));
	}

	private KeyEvent key(Canvas canvas, int id, int keyCode)
	{
		return new KeyEvent(
				canvas,
				id,
				System.currentTimeMillis(),
				0,
				keyCode,
				KeyEvent.CHAR_UNDEFINED);
	}

	private MouseEvent mouse(Canvas canvas, int id, int x, int y, int button)
	{
		return new MouseEvent(
				canvas,
				id,
				System.currentTimeMillis(),
				0,
				x,
				y,
				1,
				false,
				button);
	}
}

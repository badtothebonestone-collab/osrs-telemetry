package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import java.awt.Canvas;
import java.awt.event.KeyEvent;
import java.awt.event.MouseEvent;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class CameraInputCapturePrivacyContractTest
{
	@Test
	public void sourceUsesOnlyCanvasLocalWhitelistedCameraEvidence() throws Exception
	{
		String source = Files.readString(
				Path.of("src/main/java/com/osrstelemetry/CameraInputCapture.java"),
				StandardCharsets.UTF_8);

		for (String token : List.of(
				"GetAsyncKeyState",
				"GlobalScreen",
				"RegisterRawInputDevices",
				"SetWindowsHookEx",
				"addAWTEventListener",
				"getKeyChar(",
				"org.jnativehook",
				"MouseEvent.BUTTON1",
				"MouseEvent.BUTTON3"))
		{
			assertFalse(token, source.contains(token));
		}
		assertTrue(source.contains("event.getComponent() == current.canvas"));
		assertTrue(source.contains("MouseEvent.BUTTON2"));
		assertEquals(8, occurrences(source, "case KeyEvent.VK_"));
		for (String control : List.of("W", "A", "S", "D", "UP", "LEFT", "DOWN", "RIGHT"))
		{
			assertTrue(control, source.contains("return \"" + control + "\";"));
		}

		int typedStart = source.indexOf("public void keyTyped(KeyEvent event)");
		int typedEnd = source.indexOf("\n\t@Override", typedStart + 1);
		assertTrue(typedStart >= 0 && typedEnd > typedStart);
		String typedBody = source.substring(typedStart, typedEnd);
		assertFalse(typedBody.contains("emit("));
		assertFalse(typedBody.contains("basePayload("));
	}

	@Test
	public void pluginAppliesLoadedSceneFocusTextAndBankPinGates() throws Exception
	{
		String source = Files.readString(
				Path.of("src/main/java/com/osrstelemetry/TelemetryPlugin.java"),
				StandardCharsets.UTF_8);

		for (String required : List.of(
				"keyManager.registerKeyListener(cameraInputCapture)",
				"mouseManager.registerMouseListener(cameraInputCapture)",
				"keyManager.unregisterKeyListener(cameraInputCapture)",
				"mouseManager.unregisterMouseListener(cameraInputCapture)",
				"cameraInputCapture.updateContext",
				"client.getGameState() != GameState.LOGGED_IN",
				"client.getLocalPlayer() == null",
				"!canvas.isFocusOwner()",
				"textInputActive(",
				"client.getFocusedInputFieldWidget() != null",
				"client.getVarcIntValue(VarClientInt.INPUT_TYPE))",
				"InterfaceID.BankpinKeypad.UNIVERSE"))
		{
			assertTrue(required, source.contains(required));
		}
	}

	@Test
	public void arbitraryKeysTypedCharactersAndNonMiddleButtonsAreNotRecorded()
	{
		Canvas canvas = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = new CameraInputCapture(samples::add);
		capture.updateContext(allowedContext(canvas));
		capture.renewLease();

		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_B, 'b'));
		capture.keyReleased(key(canvas, KeyEvent.KEY_RELEASED, KeyEvent.VK_B, 'b'));
		capture.keyTyped(key(canvas, KeyEvent.KEY_TYPED, KeyEvent.VK_UNDEFINED, 'w'));
		MouseEvent left = mouse(canvas, MouseEvent.MOUSE_PRESSED, MouseEvent.BUTTON1, 10, 20);
		assertSame(left, capture.mousePressed(left));

		assertTrue(samples.isEmpty());

		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_W, 'w'));
		capture.keyReleased(key(canvas, KeyEvent.KEY_RELEASED, KeyEvent.VK_W, 'w'));
		MouseEvent middle = mouse(canvas, MouseEvent.MOUSE_PRESSED, MouseEvent.BUTTON2, 12, 24);
		assertSame(middle, capture.mousePressed(middle));

		assertEquals(3, samples.size());
		assertEquals("W", samples.get(0).get("control"));
		assertEquals("press", samples.get(0).get("phase"));
		assertEquals("release", samples.get(1).get("phase"));
		assertEquals("MIDDLE", samples.get(2).get("control"));
		assertEquals("middle_drag", samples.get(2).get("inputKind"));
	}

	@Test
	public void blockedOrForeignCanvasEventsAreNotRecorded()
	{
		Canvas canvas = new Canvas();
		Canvas foreign = new Canvas();
		List<Map<String, Object>> samples = new ArrayList<>();
		CameraInputCapture capture = new CameraInputCapture(samples::add);

		capture.keyPressed(key(canvas, KeyEvent.KEY_PRESSED, KeyEvent.VK_A, 'a'));
		capture.mousePressed(mouse(canvas, MouseEvent.MOUSE_PRESSED, MouseEvent.BUTTON2, 1, 2));
		capture.updateContext(allowedContext(canvas));
		capture.renewLease();
		capture.keyPressed(key(foreign, KeyEvent.KEY_PRESSED, KeyEvent.VK_A, 'a'));
		capture.mousePressed(mouse(foreign, MouseEvent.MOUSE_PRESSED, MouseEvent.BUTTON2, 1, 2));

		assertTrue(samples.isEmpty());
	}

	private static CameraInputCapture.Context allowedContext(Canvas canvas)
	{
		return new CameraInputCapture.Context(
				true,
				canvas,
				10L,
				20L,
				"LOGGED_IN",
				"session",
				1234L,
				Map.of("cameraYaw", 512, "cameraPitch", 256));
	}

	private static KeyEvent key(Canvas canvas, int id, int keyCode, char keyChar)
	{
		return new KeyEvent(canvas, id, System.currentTimeMillis(), 0, keyCode, keyChar);
	}

	private static MouseEvent mouse(
			Canvas canvas,
			int id,
			int button,
			int x,
			int y)
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

	private static int occurrences(String source, String token)
	{
		int count = 0;
		int index = 0;
		while ((index = source.indexOf(token, index)) >= 0)
		{
			count++;
			index += token.length();
		}
		return count;
	}
}

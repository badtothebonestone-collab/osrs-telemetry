package com.osrstelemetry;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.lang.reflect.Proxy;
import net.runelite.api.Point;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import org.junit.Test;

public class TelemetryPluginDialogueTest
{
	private final TelemetryPlugin plugin = new TelemetryPlugin();

	@Test
	public void chatmenuIdentityCapturesArbitraryPromptAndOptionsWithoutTaskTextRules()
	{
		Widget first = widget(component(219, 2), true, "3. Travel north", 10, 20, 120, 18);
		Widget second = widget(component(219, 3), true, "Stay here", 10, 40, 120, 18);
		Widget prompt = widget(component(219, 4), true, "Where do you want to travel?", 10, 2, 180, 18);
		Widget container = widget(
				InterfaceID.Chatmenu.OPTIONS,
				true,
				"",
				0,
				0,
				200,
				100,
				first,
				second);
		Widget universe = widget(
				InterfaceID.Chatmenu.UNIVERSE,
				true,
				"",
				0,
				0,
				200,
				100,
				prompt,
				container);

		TickSnapshot.DialogueStateSnapshot state = plugin.dialogueStateFromWidgets(
				universe,
				container,
				new TelemetryPlugin.DialogueContinueSurface[0],
				91L,
				1234L);

		assertEquals("dialogue_state.v1", state.schema);
		assertTrue(state.active);
		assertEquals("options", state.type);
		assertEquals("Where do you want to travel?", state.promptText);
		assertEquals(2, state.options.length);
		assertEquals("3. Travel north", state.options[0].text);
		assertEquals("3", state.options[0].key);
		assertEquals(1, state.options[0].index);
		assertEquals(Integer.valueOf(219), state.options[0].widgetGroup);
		assertEquals(Integer.valueOf(2), state.options[0].widgetChild);
		assertEquals(10, state.options[0].bounds.x);
		assertEquals("Stay here", state.options[1].text);
		assertEquals("2", state.options[1].key);
		assertEquals(Boolean.TRUE, state.canUseNumberKeys);
		assertEquals(Boolean.FALSE, state.canUseSpaceContinue);
		assertEquals("runelite_dialogue_widget_ids", state.source);
		assertArrayEquals(new Integer[] {219}, state.widgetRootIds);
		assertEquals(Long.valueOf(91L), state.latestClientTick);
		assertEquals(Long.valueOf(1234L), state.wallTimeMillis);
	}

	@Test
	public void climbTextWithoutAnAuthoritativeDialogueSurfaceIsIgnored()
	{
		Widget unrelatedText = widget(component(999, 1), true, "Climb up or down?", 0, 0, 100, 20);
		Widget hiddenContinue = widget(component(999, 2), false, "Click here to continue", 0, 20, 100, 20);

		TickSnapshot.DialogueStateSnapshot state = plugin.dialogueStateFromWidgets(
				null,
				null,
				new TelemetryPlugin.DialogueContinueSurface[] {
						new TelemetryPlugin.DialogueContinueSurface(unrelatedText, hiddenContinue)
				},
				1L,
				2L);

		assertFalse(state.active);
		assertEquals("unknown", state.type);
		assertEquals("", state.promptText);
		assertEquals(0, state.options.length);
	}

	@Test
	public void hiddenChatmenuFailsClosedEvenWhenItsChildrenLookActionable()
	{
		Widget option = widget(component(219, 2), true, "1. Climb up", 0, 0, 100, 20);
		Widget container = widget(
				InterfaceID.Chatmenu.OPTIONS,
				true,
				"Climb up or down?",
				0,
				0,
				100,
				40,
				option);
		Widget hiddenUniverse = widget(
				InterfaceID.Chatmenu.UNIVERSE,
				false,
				"",
				0,
				0,
				100,
				40,
				container);

		TickSnapshot.DialogueStateSnapshot state = plugin.dialogueStateFromWidgets(
				hiddenUniverse,
				container,
				new TelemetryPlugin.DialogueContinueSurface[0],
				1L,
				2L);

		assertFalse(state.active);
		assertEquals("unknown", state.type);
	}

	@Test
	public void knownContinueIdentityCapturesNarrativeTextWithoutLiteralMatching()
	{
		Widget text = widget(InterfaceID.ChatLeft.TEXT, true, "The road is clear.", 10, 10, 180, 30);
		Widget continueWidget = widget(InterfaceID.ChatLeft.CONTINUE, true, "Next", 10, 45, 80, 18);

		TickSnapshot.DialogueStateSnapshot state = plugin.dialogueStateFromWidgets(
				null,
				null,
				new TelemetryPlugin.DialogueContinueSurface[] {
						new TelemetryPlugin.DialogueContinueSurface(text, continueWidget)
				},
				8L,
				9L);

		assertTrue(state.active);
		assertEquals("click_to_continue", state.type);
		assertEquals("The road is clear.", state.promptText);
		assertEquals(Boolean.FALSE, state.canUseNumberKeys);
		assertEquals(Boolean.TRUE, state.canUseSpaceContinue);
		assertArrayEquals(new Integer[] {231}, state.widgetRootIds);
	}

	@Test
	public void multipleVisibleContinueSurfacesFailClosed()
	{
		TelemetryPlugin.DialogueContinueSurface left = new TelemetryPlugin.DialogueContinueSurface(
				widget(InterfaceID.ChatLeft.TEXT, true, "Left", 0, 0, 10, 10),
				widget(InterfaceID.ChatLeft.CONTINUE, true, "Continue", 0, 10, 10, 10));
		TelemetryPlugin.DialogueContinueSurface right = new TelemetryPlugin.DialogueContinueSurface(
				widget(InterfaceID.ChatRight.TEXT, true, "Right", 0, 0, 10, 10),
				widget(InterfaceID.ChatRight.CONTINUE, true, "Continue", 0, 10, 10, 10));

		TickSnapshot.DialogueStateSnapshot state = plugin.dialogueStateFromWidgets(
				null,
				null,
				new TelemetryPlugin.DialogueContinueSurface[] {left, right},
				1L,
				2L);

		assertFalse(state.active);
		assertEquals("unknown", state.type);
	}

	private static int component(int group, int child)
	{
		return group << 16 | child;
	}

	private static Widget widget(
			int id,
			boolean visible,
			String text,
			int x,
			int y,
			int width,
			int height,
			Widget... children)
	{
		Widget[] childCopy = children == null ? null : children.clone();
		return (Widget) Proxy.newProxyInstance(
				Widget.class.getClassLoader(),
				new Class<?>[] {Widget.class},
				(proxy, method, args) -> {
					String name = method.getName();
					if ("equals".equals(name))
					{
						return proxy == args[0];
					}
					if ("hashCode".equals(name))
					{
						return System.identityHashCode(proxy);
					}
					if ("toString".equals(name))
					{
						return "WidgetProxy(" + id + ")";
					}
					switch (name)
					{
						case "getId":
							return id;
						case "getIndex":
							return id & 0xFFFF;
						case "isHidden":
							return !visible;
						case "getText":
							return text;
						case "getName":
							return "";
						case "getCanvasLocation":
							return new Point(x, y);
						case "getWidth":
							return width;
						case "getHeight":
							return height;
						case "getChildren":
						case "getDynamicChildren":
						case "getNestedChildren":
							return childCopy == null ? null : childCopy.clone();
						case "getStaticChildren":
							return new Widget[0];
						default:
							return defaultValue(method.getReturnType());
					}
				});
	}

	private static Object defaultValue(Class<?> type)
	{
		if (!type.isPrimitive())
		{
			return null;
		}
		if (type == boolean.class)
		{
			return false;
		}
		if (type == char.class)
		{
			return '\0';
		}
		if (type == byte.class)
		{
			return (byte) 0;
		}
		if (type == short.class)
		{
			return (short) 0;
		}
		if (type == int.class)
		{
			return 0;
		}
		if (type == long.class)
		{
			return 0L;
		}
		if (type == float.class)
		{
			return 0.0F;
		}
		if (type == double.class)
		{
			return 0.0D;
		}
		return null;
	}
}

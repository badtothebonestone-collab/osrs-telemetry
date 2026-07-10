from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from osrs_bot.action import ArduinoActionInterface
from osrs_bot.arduino import ArduinoHIDBackend, ArduinoHIDError
from osrs_bot.model import (
    Action,
    ActionKind,
    DialogueOption,
    InventoryObservation,
    MenuEntry,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.safety import SafetyGate


POINT = ScreenPoint(110, 110)


def observation(
    *,
    menus: tuple[MenuEntry, ...],
    tick: int = 10,
    menu_open: bool = False,
    menu_bounds: ScreenBounds | None = None,
    menu_point: ScreenPoint = POINT,
) -> Observation:
    tree = NearbyObject(
        key="tree-1",
        object_id=1276,
        name="Tree",
        kind="GAME_OBJECT",
        actions=("Chop down",),
        location=WorldPoint(3193, 3244, 0),
        distance=1,
        geometry=TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            canvas_point=ScreenPoint(60, 60),
            screen_point=POINT,
            screen_bounds=ScreenBounds(100, 100, 30, 30),
        ),
        scene_x=49,
        scene_y=52,
        resource_candidate=True,
    )
    return Observation(
        player=PlayerObservation(),
        location=WorldPoint(3192, 3244, 0),
        plane=0,
        inventory=InventoryObservation(known=True),
        nearby_objects=(tree,),
        menus=menus,
        widgets=WidgetObservation(bank_known=True),
        canvas_bounds=ScreenBounds(50, 50, 500, 400),
        game_state="LOGGED_IN",
        timestamp=datetime.now(timezone.utc),
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id="session-1",
        menu_client_tick=1000 + tick,
        menu_mouse_screen_point=menu_point,
        menu_open=menu_open,
        menu_bounds=menu_bounds,
        client_focused=True,
        client_process_id=1234,
    )


def tree_action() -> Action:
    return Action(
        kind=ActionKind.INTERACT_OBJECT,
        label="chop ordinary tree",
        source_tick=10,
        option="Chop down",
        target_key="tree-1",
        target_name="Tree",
        target_id=1276,
        screen_point=POINT,
        source_menu_client_tick=1010,
        target_param0=49,
        target_param1=52,
        source_session_id="session-1",
    )


class FakeBackend:
    def __init__(self, fail_at: str | None = None) -> None:
        self.calls: list[str] = []
        self.buttons: list[str] = []
        self.fail_at = fail_at
        self.move_kwargs: dict[str, object] = {}

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(name)

    def connect(self) -> None:
        self._call("connect")

    def configure_movement_safety(self, **_: object) -> None:
        self._call("configure")

    def arm(self) -> None:
        self._call("arm")

    def move_to_absolute(self, *_: object, **kwargs: object) -> None:
        self.move_kwargs = kwargs
        self._call("move")

    def mouse_down(self, **kwargs: object) -> None:
        self.buttons.append(str(kwargs.get("button")))
        self._call("mouse_down")

    def mouse_up(self, **_: object) -> None:
        self._call("mouse_up")

    def press(self, _: str) -> None:
        self._call("press")

    def assert_foreground(self, _: object, **__: object) -> None:
        self._call("foreground")

    def stop_all(self) -> None:
        self._call("stop_all")

    def disarm(self) -> None:
        self._call("disarm")

    def close(self) -> None:
        self._call("close")

    def status(self) -> dict[str, object]:
        return {"calls": list(self.calls)}


class ArduinoActionInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pre = observation(menus=())
        self.hover = observation(
            menus=(MenuEntry("Chop down", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52),),
            tick=11,
        )

    def interface(self, backend: FakeBackend, post: Observation) -> ArduinoActionInterface:
        return ArduinoActionInterface(
            backend,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: post,
            sleep=lambda _: None,
        )

    def test_pointer_action_requires_fresh_hover_then_confirms_cleanup(self) -> None:
        backend = FakeBackend()
        result = self.interface(backend, self.hover).execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertTrue(result.stop_all_confirmed)
        self.assertTrue(result.disarm_confirmed)
        self.assertEqual(1, backend.move_kwargs["tolerance_px"])
        self.assertEqual(
            ["connect", "configure", "arm", "move", "foreground", "mouse_down", "mouse_up", "stop_all", "disarm", "close"],
            backend.calls,
        )

    def test_hover_mismatch_blocks_click_and_still_cleans_up(self) -> None:
        backend = FakeBackend()
        no_hover = observation(menus=(), tick=11)
        result = self.interface(backend, no_hover).execute(tree_action(), self.pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("hover_menu_mismatch", result.reason)
        self.assertNotIn("mouse_down", backend.calls)
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])

    def test_waits_boundedly_for_exact_hover_menu_after_pointer_arrives(self) -> None:
        backend = FakeBackend()
        samples = iter((observation(menus=(), tick=11), self.hover))
        interface = ArduinoActionInterface(
            backend,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=2,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertIn("mouse_down", backend.calls)

    def test_selects_one_exact_lower_context_entry_and_cleans_up(self) -> None:
        backend = FakeBackend()
        generic = MenuEntry(
            "Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52
        )
        exact = MenuEntry(
            "Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52
        )
        candidate = observation(menus=(generic, exact), tick=11)
        menu_bounds = ScreenBounds(80, 80, 200, 100)
        row_bounds = ScreenBounds(81, 114, 199, 15)
        opened_entries = (
            replace(generic, row_bounds=ScreenBounds(81, 99, 199, 15)),
            replace(exact, row_bounds=row_bounds),
        )
        opened = observation(
            menus=opened_entries,
            tick=12,
            menu_open=True,
            menu_bounds=menu_bounds,
        )
        row = observation(
            menus=opened_entries,
            tick=13,
            menu_open=True,
            menu_bounds=menu_bounds,
            menu_point=row_bounds.center,
        )
        samples = iter((candidate, opened, row))
        interface = ArduinoActionInterface(
            backend,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(["right", "left"], backend.buttons)
        self.assertEqual(2, backend.calls.count("move"))
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])

    def test_context_cleanup_escape_is_skipped_after_focus_changes(self) -> None:
        class FocusChangesBackend(FakeBackend):
            def __init__(self) -> None:
                super().__init__()
                self.foreground_checks = 0

            def assert_foreground(self, _: object, **__: object) -> None:
                self.foreground_checks += 1
                self.calls.append("foreground")
                if self.foreground_checks == 2:
                    raise RuntimeError("focus changed")

        backend = FocusChangesBackend()
        generic = MenuEntry(
            "Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52
        )
        exact = MenuEntry(
            "Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52
        )
        candidate = observation(menus=(generic, exact), tick=11)
        still_closed = observation(menus=(generic, exact), tick=12)
        samples = iter((candidate, still_closed))
        interface = ArduinoActionInterface(
            backend,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=1,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("context_menu_not_open", result.reason)
        self.assertEqual(["right"], backend.buttons)
        self.assertNotIn("press", backend.calls)
        self.assertEqual(2, backend.foreground_checks)
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])

    def test_failed_cleanup_overrides_a_blocked_post_move_result(self) -> None:
        no_hover = observation(menus=(), tick=11)
        for cleanup_call in ("stop_all", "disarm"):
            with self.subTest(cleanup_call=cleanup_call):
                backend = FakeBackend(fail_at=cleanup_call)
                result = self.interface(backend, no_hover).execute(
                    tree_action(), self.pre
                )

                self.assertEqual("ERROR", result.status)
                self.assertIn("cleanup_not_confirmed", result.reason)
                self.assertIn("hover_menu_mismatch", result.reason)
                self.assertNotIn("mouse_down", backend.calls)

    def test_preflight_block_never_connects_hardware(self) -> None:
        backend = FakeBackend()
        stale = replace(self.pre, fresh=False)
        result = self.interface(backend, self.hover).execute(tree_action(), stale)

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual([], backend.calls)

    def test_backend_failure_still_runs_stop_and_disarm(self) -> None:
        backend = FakeBackend(fail_at="mouse_down")
        result = self.interface(backend, self.hover).execute(tree_action(), self.pre)

        self.assertEqual("ERROR", result.status)
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])

    def test_real_backend_never_hides_failed_disarm_ack(self) -> None:
        backend = ArduinoHIDBackend(port="COM-test", serial_lock_enabled=False)
        backend._serial = object()  # exercise the connected disarm contract
        backend._status.armed = True

        def send(command: str, **_: object) -> str:
            if command == "DISARM":
                raise TimeoutError("no ack")
            return "ACK"

        backend._send = send  # type: ignore[method-assign]

        with self.assertRaises(ArduinoHIDError):
            backend.disarm()
        self.assertTrue(backend._status.stop_all_sent)
        self.assertIn("no ack", backend._status.last_error or "")

    def test_key_action_rechecks_dialogue_and_foreground_after_arm(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(DialogueOption(1, "1", "Climb up the stairs."),),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        fresh = replace(
            self.hover,
            widgets=replace(widgets, dialogue_client_tick=501),
        )
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose climb up",
            10,
            option="Climb up the stairs.",
            target_key="dialogue:1",
            target_name="Climb up the stairs.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
        )
        backend = FakeBackend(fail_at="foreground")

        result = self.interface(backend, fresh).execute(
            action, replace(self.pre, widgets=widgets)
        )

        self.assertEqual("ERROR", result.status)
        self.assertNotIn("press", backend.calls)
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])

    def test_key_action_waits_boundedly_for_a_new_dialogue_sample(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(DialogueOption(1, "1", "Climb up the stairs."),),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        stale = replace(self.hover, widgets=widgets)
        fresh = replace(
            self.hover, widgets=replace(widgets, dialogue_client_tick=501)
        )
        samples = iter((stale, stale, stale, fresh))
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose climb up",
            10,
            option="Climb up the stairs.",
            target_key="dialogue:1",
            target_name="Climb up the stairs.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
        )
        backend = FakeBackend()
        interface = ArduinoActionInterface(
            backend,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
        )

        result = interface.execute(action, replace(self.pre, widgets=widgets))

        self.assertEqual("SENT", result.status)
        self.assertIn("press", backend.calls)

    def test_bank_escape_waits_for_a_new_bank_tick_then_cleans_up(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )
        pre = replace(
            self.pre,
            location=WorldPoint(3208, 3220, 2),
            plane=2,
            widgets=widgets,
        )
        fresh = replace(pre, tick=11)
        action = Action(
            ActionKind.PRESS_KEY,
            "Close bank with Escape",
            10,
            option="Close bank",
            target_key="close_bank_keyboard",
            target_name="Close bank",
            target_id=0,
            key="escape",
            source_session_id="session-1",
        )
        backend = FakeBackend()

        result = self.interface(backend, fresh).execute(action, pre)

        self.assertEqual("SENT", result.status)
        self.assertIn("press", backend.calls)
        self.assertEqual(["stop_all", "disarm", "close"], backend.calls[-3:])


if __name__ == "__main__":
    unittest.main()

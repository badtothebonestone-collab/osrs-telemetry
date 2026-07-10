from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from osrs_bot.model import (
    Action,
    ActionKind,
    DialogueOption,
    InventoryItem,
    InventoryObservation,
    MenuEntry,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.safety import SafetyGate


TREE_POINT = ScreenPoint(320, 240)
CANVAS = ScreenBounds(0, 0, 765, 503)


def tree(geometry: TargetGeometry | None = None) -> NearbyObject:
    return NearbyObject(
        key="tree:1276:3200:3200:0",
        object_id=1276,
        name="Tree",
        kind="game_object",
        actions=("Chop down",),
        location=WorldPoint(3200, 3200, 0),
        distance=3,
        geometry=geometry
        or TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=TREE_POINT,
            screen_bounds=ScreenBounds(300, 220, 40, 40),
        ),
        scene_x=49,
        scene_y=52,
        resource_candidate=True,
    )


def inventory(*item_ids: int, known: bool = True) -> InventoryObservation:
    items = tuple(
        InventoryItem(slot=index, item_id=item_id, quantity=1)
        for index, item_id in enumerate(item_ids)
    )
    return InventoryObservation(
        items=items,
        occupied_slots=len(items),
        free_slots=28 - len(items),
        known=known,
    )


def observation(
    *,
    tick: int = 100,
    menus: tuple[MenuEntry, ...] = (),
    nearby_objects: tuple[NearbyObject, ...] | None = None,
    items: InventoryObservation | None = None,
    widgets: WidgetObservation | None = None,
    menu_client_tick: int | None = None,
    menu_point: ScreenPoint | None = TREE_POINT,
    menu_open: bool = False,
    menu_bounds: ScreenBounds | None = None,
) -> Observation:
    return Observation(
        player=PlayerObservation(),
        location=WorldPoint(3197, 3200, 0),
        plane=0,
        inventory=items or inventory(1511),
        nearby_objects=nearby_objects if nearby_objects is not None else (tree(),),
        menus=menus,
        widgets=widgets or WidgetObservation(bank_known=True),
        canvas_bounds=CANVAS,
        game_state="LOGGED_IN",
        timestamp=datetime.now(timezone.utc),
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id="session-1",
        menu_client_tick=1000 + tick if menu_client_tick is None else menu_client_tick,
        menu_mouse_screen_point=menu_point,
        menu_open=menu_open,
        menu_bounds=menu_bounds,
        client_focused=True,
        client_process_id=1234,
    )


def tree_action(**changes: object) -> Action:
    values = {
        "kind": ActionKind.INTERACT_OBJECT,
        "label": "Chop ordinary tree",
        "source_tick": 100,
        "option": "Chop down",
        "target_key": "tree:1276:3200:3200:0",
        "target_name": "Tree",
        "target_id": 1276,
        "screen_point": TREE_POINT,
        "source_menu_client_tick": 1100,
        "target_param0": 49,
        "target_param1": 52,
        "source_session_id": "session-1",
    }
    values.update(changes)
    return Action(**values)


def exact_hover() -> MenuEntry:
    return MenuEntry(
        option="Chop down",
        target="Tree",
        entry_type="GAME_OBJECT_FIRST_OPTION",
        identifier=1276,
        param0=49,
        param1=52,
    )


def walk_target() -> NearbyObject:
    return replace(
        tree(),
        key="route:castle_west_approach",
        object_id=0,
        name="route:castle_west_approach",
        kind="NAVIGATION_TILE",
        actions=("Walk here",),
        resource_candidate=False,
        route_candidate=True,
    )


def walk_action() -> Action:
    return tree_action(
        kind=ActionKind.WALK,
        label="Walk route step",
        option="Walk here",
        target_key="route:castle_west_approach",
        target_name="route:castle_west_approach",
        target_id=0,
    )


def walk_hover() -> MenuEntry:
    return MenuEntry(
        option="Walk here",
        target="",
        entry_type="WALK",
        identifier=0,
        param0=49,
        param1=52,
    )


class SafetyGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SafetyGate(max_observation_age_seconds=2.0)

    def test_validates_pre_move_and_exact_post_move_hover_separately(self) -> None:
        self.assertTrue(self.gate.validate_pre_move(tree_action(), observation()).allowed)

        post = observation(tick=101, menus=(exact_hover(),))
        result = self.gate.validate_post_move(tree_action(), post)

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "post_move_safe")

    def test_context_menu_requires_one_exact_bounded_lower_row(self) -> None:
        generic = MenuEntry(
            "Examine", "Tree", "EXAMINE_OBJECT", 1276, 49, 52
        )
        expected = exact_hover()
        candidate = observation(tick=101, menus=(generic, expected))

        self.assertTrue(
            self.gate.validate_context_candidate(tree_action(), candidate).allowed
        )

        row_bounds = ScreenBounds(100, 120, 180, 24)
        open_menu = observation(
            tick=102,
            menus=(generic, replace(expected, row_bounds=row_bounds)),
            menu_open=True,
            menu_bounds=ScreenBounds(90, 80, 200, 100),
        )
        self.assertTrue(
            self.gate.validate_context_menu(
                tree_action(), open_menu, minimum_menu_client_tick=1101
            ).allowed
        )

        row_point = row_bounds.center
        row_hover = replace(
            open_menu,
            tick=103,
            menu_client_tick=1103,
            menu_mouse_screen_point=row_point,
        )
        self.assertTrue(
            self.gate.validate_context_menu(
                tree_action(), row_hover,
                minimum_menu_client_tick=1102,
                row_point=row_point,
            ).allowed
        )

    def test_rejects_unusable_observations(self) -> None:
        base = observation()
        cases = {
            "non-pass": replace(base, status="WARN"),
            "snapshot stale": replace(base, fresh=False),
            "cache stale": replace(base, cache_wall_clock_fresh=False),
            "not loaded": replace(base, game_state="LOGIN_SCREEN"),
            "missing capability": replace(base, missing_capabilities=("inventory",)),
            "not focused": replace(base, client_focused=False),
            "process unknown": replace(base, client_process_id=None),
            "bank state unknown": replace(
                base, widgets=replace(base.widgets, bank_known=False)
            ),
            "too old": replace(
                base, timestamp=datetime.now(timezone.utc) - timedelta(seconds=10)
            ),
            "future dated": replace(
                base, timestamp=datetime.now(timezone.utc) + timedelta(hours=1)
            ),
            "bank pin": replace(
                base, widgets=replace(base.widgets, bank_pin_open=True)
            ),
        }
        for label, candidate in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    self.gate.validate_pre_move(tree_action(), candidate).allowed
                )

    def test_future_dated_observation_has_an_explicit_block_reason(self) -> None:
        candidate = replace(
            observation(),
            timestamp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = self.gate.validate_pre_move(tree_action(), candidate)

        self.assertFalse(result.allowed)
        self.assertEqual("observation_timestamp_future", result.reason)

    def test_pre_move_requires_source_tick_and_post_move_cannot_regress(self) -> None:
        self.assertEqual(
            self.gate.validate_pre_move(tree_action(), observation(tick=101)).reason,
            "tick_mismatch",
        )
        self.assertEqual(
            self.gate.validate_post_move(tree_action(), observation(tick=99)).reason,
            "tick_mismatch",
        )

    def test_action_cannot_cross_plugin_sessions(self) -> None:
        changed = replace(observation(), session_id="session-2")

        self.assertEqual(
            "session_changed",
            self.gate.validate_pre_move(tree_action(), changed).reason,
        )

    def test_rejects_missing_target(self) -> None:
        self.assertEqual(
            self.gate.validate_pre_move(
                tree_action(target_key="missing"), observation()
            ).reason,
            "target_missing",
        )

    def test_rejects_incomplete_or_mismatched_exact_target(self) -> None:
        cases = {
            "missing option": tree_action(option=None),
            "wrong option": tree_action(option="Cut"),
            "wrong name": tree_action(target_name="Oak"),
            "wrong id": tree_action(target_id=1278),
        }
        for label, action in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    self.gate.validate_pre_move(action, observation()).allowed
                )

    def test_rejects_unusable_geometry(self) -> None:
        base = tree().geometry
        cases = {
            "unavailable": replace(base, available=False),
            "offscreen": replace(base, on_screen=False),
            "hidden": replace(base, visible=False),
            "not actionable": replace(base, actionable=False),
            "missing point": replace(base, screen_point=None),
        }
        for label, geometry in cases.items():
            with self.subTest(label=label):
                candidate = observation(nearby_objects=(tree(geometry),))
                self.assertFalse(
                    self.gate.validate_pre_move(tree_action(), candidate).allowed
                )

    def test_only_verified_screen_point_is_executable(self) -> None:
        result = self.gate.validate_pre_move(
            tree_action(screen_point=ScreenPoint(321, 240)), observation()
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "screen_point_not_verified")

    def test_canvas_point_is_not_accepted_as_executable_screen_point(self) -> None:
        geometry = replace(
            tree().geometry,
            canvas_point=ScreenPoint(450, 300),
            screen_point=TREE_POINT,
        )
        candidate = observation(nearby_objects=(tree(geometry),))

        result = self.gate.validate_pre_move(
            tree_action(screen_point=geometry.canvas_point), candidate
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "screen_point_not_verified")

    def test_rejects_invalid_and_out_of_bounds_points(self) -> None:
        negative = ScreenPoint(-1, 20)
        negative_geometry = replace(
            tree().geometry,
            screen_point=negative,
            screen_bounds=None,
        )
        outside = ScreenPoint(800, 240)
        outside_geometry = replace(
            tree().geometry,
            screen_point=outside,
            screen_bounds=None,
        )
        cases = {
            "negative": (negative, negative_geometry, CANVAS),
            "outside canvas": (outside, outside_geometry, CANVAS),
            "missing canvas": (TREE_POINT, tree().geometry, None),
        }
        for label, (point, geometry, canvas) in cases.items():
            with self.subTest(label=label):
                candidate = replace(
                    observation(nearby_objects=(tree(geometry),)),
                    canvas_bounds=canvas,
                )
                self.assertFalse(
                    self.gate.validate_pre_move(
                        tree_action(screen_point=point), candidate
                    ).allowed
                )

    def test_rejects_point_outside_target_bounds(self) -> None:
        geometry = replace(
            tree().geometry,
            screen_bounds=ScreenBounds(10, 10, 20, 20),
        )
        candidate = observation(nearby_objects=(tree(geometry),))

        result = self.gate.validate_pre_move(tree_action(), candidate)

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "screen_point_outside_target")

    def test_post_move_requires_exact_option_name_and_id_in_hover_menu(self) -> None:
        cases = {
            "missing": (),
            "wrong option": (replace(exact_hover(), option="Walk here"),),
            "wrong name": (replace(exact_hover(), target="Oak"),),
            "wrong id": (replace(exact_hover(), identifier=1278),),
        }
        for label, menus in cases.items():
            with self.subTest(label=label):
                result = self.gate.validate_post_move(
                    tree_action(), observation(tick=101, menus=menus)
                )
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "hover_menu_mismatch")

    def test_post_move_requires_expected_entry_to_be_top_default(self) -> None:
        wrong_top = MenuEntry("Walk here", "", "WALK", 0)
        post = observation(tick=101, menus=(wrong_top, exact_hover()))

        result = self.gate.validate_post_move(tree_action(), post)

        self.assertFalse(result.allowed)
        self.assertEqual("hover_menu_mismatch", result.reason)

    def test_post_move_requires_new_menu_sample_at_pointer(self) -> None:
        same_sample = observation(
            tick=101, menus=(exact_hover(),), menu_client_tick=1100
        )
        wrong_pointer = observation(
            tick=101, menus=(exact_hover(),), menu_point=ScreenPoint(10, 10)
        )

        self.assertEqual(
            "menu_sample_not_newer",
            self.gate.validate_post_move(tree_action(), same_sample).reason,
        )
        self.assertEqual(
            "hover_pointer_mismatch",
            self.gate.validate_post_move(tree_action(), wrong_pointer).reason,
        )

        open_menu = observation(
            tick=101, menus=(exact_hover(),), menu_open=True
        )
        self.assertEqual(
            "context_menu_open",
            self.gate.validate_post_move(tree_action(), open_menu).reason,
        )

    def test_post_move_rechecks_freshness(self) -> None:
        post = replace(
            observation(tick=101, menus=(exact_hover(),)),
            cache_wall_clock_fresh=False,
        )

        result = self.gate.validate_post_move(tree_action(), post)

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "observation_stale")

    def test_walk_requires_a_resolved_virtual_target_and_exact_hover(self) -> None:
        action = walk_action()
        pre = observation(nearby_objects=(walk_target(),))
        post = observation(
            tick=101,
            menus=(walk_hover(),),
            nearby_objects=(walk_target(),),
        )

        self.assertTrue(self.gate.validate_pre_move(action, pre).allowed)
        self.assertTrue(self.gate.validate_post_move(action, post).allowed)
        self.assertFalse(
            self.gate.validate_pre_move(
                replace(action, target_key=None), pre
            ).allowed
        )

    def test_walk_hover_allows_dynamic_display_target_but_requires_walk_type(self) -> None:
        player_under_cursor = replace(
            walk_hover(), target="Player (level-3)", identifier=886
        )
        post = observation(
            tick=101,
            menus=(player_under_cursor,),
            nearby_objects=(walk_target(),),
        )

        self.assertTrue(self.gate.validate_post_move(walk_action(), post).allowed)

        wrong_type = replace(player_under_cursor, entry_type="PLAYER_THIRD_OPTION")
        post = replace(post, menus=(wrong_type,))
        result = self.gate.validate_post_move(walk_action(), post)

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "hover_menu_mismatch")

    def test_walk_hover_uses_projected_geometry_not_canvas_menu_params(self) -> None:
        canvas_coordinates = replace(walk_hover(), param0=355, param1=314)
        post = observation(
            tick=101,
            menus=(canvas_coordinates,),
            nearby_objects=(walk_target(),),
        )

        result = self.gate.validate_post_move(walk_action(), post)

        self.assertTrue(result.allowed)

    def test_deposit_inventory_allows_only_known_logs(self) -> None:
        point = ScreenPoint(600, 400)
        target = WidgetTarget(
            name="Deposit inventory",
            visible=True,
            screen_point=point,
            screen_bounds=ScreenBounds(580, 380, 40, 40),
        )
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            deposit_inventory=target,
        )
        action = Action(
            kind=ActionKind.CLICK_WIDGET,
            label="Deposit logs",
            source_tick=100,
            target_key="deposit_inventory",
            option="Deposit inventory",
            target_name="Deposit inventory",
            screen_point=point,
            source_menu_client_tick=1100,
            source_session_id="session-1",
        )

        safe = replace(
            observation(items=inventory(1511, 1511), widgets=widgets),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )
        unsafe = replace(
            observation(items=inventory(1511, 1351), widgets=widgets),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )

        self.assertTrue(self.gate.validate_pre_move(action, safe).allowed)
        result = self.gate.validate_pre_move(action, unsafe)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "unsafe_deposit_inventory")

    def test_deposit_inventory_rejects_unknown_or_empty_inventory(self) -> None:
        point = ScreenPoint(600, 400)
        target = WidgetTarget("Deposit inventory", True, point)
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            deposit_inventory=target,
        )
        action = Action(
            kind=ActionKind.CLICK_WIDGET,
            label="Deposit logs",
            source_tick=100,
            target_key="deposit_inventory",
            option="Deposit inventory",
            target_name="Deposit inventory",
            screen_point=point,
            source_menu_client_tick=1100,
            source_session_id="session-1",
        )

        for candidate in (inventory(known=False), inventory()):
            with self.subTest(known=candidate.known):
                self.assertFalse(
                    self.gate.validate_pre_move(
                        action,
                        replace(
                            observation(items=candidate, widgets=widgets),
                            location=WorldPoint(3208, 3220, 2),
                            plane=2,
                        ),
                    ).allowed
                )

    def test_widget_post_move_does_not_require_object_hover_menu(self) -> None:
        point = ScreenPoint(700, 40)
        close = WidgetTarget("Close bank", True, point)
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            close_bank=close,
        )
        action = Action(
            kind=ActionKind.CLICK_WIDGET,
            label="Close bank",
            source_tick=100,
            target_key="close_bank",
            option="Close bank",
            target_name="Close bank",
            screen_point=point,
            source_menu_client_tick=1100,
            source_session_id="session-1",
        )

        result = self.gate.validate_post_move(
            action,
            replace(
                observation(tick=101, widgets=widgets, menu_point=point),
                location=WorldPoint(3208, 3220, 2),
                plane=2,
            ),
        )

        self.assertTrue(result.allowed)

    def test_stair_dialogue_allows_only_the_exact_numbered_choice(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(
                DialogueOption(1, "1", "Climb up the stairs."),
                DialogueOption(2, "2", "Climb down the stairs."),
            ),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose climb up",
            100,
            option="Climb up the stairs.",
            target_key="dialogue:1",
            target_name="Climb up the stairs.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
        )

        self.assertTrue(
            self.gate.validate_pre_move(action, observation(widgets=widgets)).allowed
        )
        self.assertEqual(
            "dialogue_option_mismatch",
            self.gate.validate_pre_move(
                replace(action, key="2"), observation(widgets=widgets)
            ).reason,
        )

    def test_bank_escape_requires_exact_live_keyboard_close_support(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )
        action = Action(
            ActionKind.PRESS_KEY,
            "Close bank with Escape",
            100,
            option="Close bank",
            target_key="close_bank_keyboard",
            target_name="Close bank",
            target_id=0,
            key="escape",
            source_session_id="session-1",
        )
        before = replace(
            observation(tick=100, widgets=widgets),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )
        after = replace(before, tick=101)

        self.assertTrue(self.gate.validate_pre_move(action, before).allowed)
        self.assertEqual(
            "bank_sample_not_newer",
            self.gate.validate_post_move(action, before).reason,
        )
        self.assertTrue(self.gate.validate_post_move(action, after).allowed)
        self.assertEqual(
            "unsafe_key",
            self.gate.validate_pre_move(replace(action, key="enter"), before).reason,
        )
        unavailable = replace(
            before,
            widgets=replace(widgets, keyboard_close_possible=False),
        )
        self.assertEqual(
            "bank_keyboard_close_unavailable",
            self.gate.validate_pre_move(action, unavailable).reason,
        )


if __name__ == "__main__":
    unittest.main()

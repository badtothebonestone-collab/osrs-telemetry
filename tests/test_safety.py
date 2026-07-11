from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from osrs_bot.model import (
    Action,
    ActionKind,
    DialogueOption,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryConstraint,
    InventoryItem,
    InventoryObservation,
    MenuEntry,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    TaskConstraints,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.safety import (
    SafetyCheck,
    SafetyEvaluation,
    SafetyGate,
    SafetyResult,
)


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
    timestamp = datetime.now(timezone.utc)
    session_id = "session-1"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
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
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id=session_id,
        menu_client_tick=1000 + tick if menu_client_tick is None else menu_client_tick,
        menu_mouse_screen_point=menu_point,
        menu_open=menu_open,
        menu_bounds=menu_bounds,
        client_focused=True,
        client_process_id=process_id,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=process_id,
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

    def test_post_move_separates_canonical_aim_from_settled_pointer(self) -> None:
        settled = ScreenPoint(TREE_POINT.x + 3, TREE_POINT.y)
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=settled,
        )

        evaluation = self.gate.evaluate_post_move(
            tree_action(), post, settled_pointer=settled
        )

        self.assertTrue(evaluation.result.allowed)
        self.assertIn(
            SafetyCheck(
                "post_move.settled_pointer", "settled_pointer_safe", True
            ),
            evaluation.checks,
        )
        self.assertIn(
            SafetyCheck(
                "post_move.action_invariants", "screen_point_safe", True
            ),
            evaluation.checks,
        )

    def test_settled_pointer_must_remain_inside_fresh_target_bounds(self) -> None:
        outside = ScreenPoint(TREE_POINT.x + 3, TREE_POINT.y)
        narrow = replace(
            tree(),
            geometry=replace(
                tree().geometry,
                screen_bounds=ScreenBounds(300, 220, 21, 40),
            ),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=outside,
            nearby_objects=(narrow,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=outside
        )

        self.assertFalse(result.allowed)
        self.assertEqual("screen_point_outside_target", result.reason)

    def test_settled_pointer_cannot_exceed_verified_coordinator_region(self) -> None:
        outside = ScreenPoint(TREE_POINT.x + 4, TREE_POINT.y)
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=outside,
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=outside
        )

        self.assertFalse(result.allowed)
        self.assertEqual(
            "settled_pointer_outside_verified_region", result.reason
        )

    def test_missing_object_bounds_allow_only_the_exact_canonical_point(self) -> None:
        offset = ScreenPoint(TREE_POINT.x + 1, TREE_POINT.y)
        point_only = replace(
            tree(),
            geometry=replace(tree().geometry, screen_bounds=None),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=offset,
            nearby_objects=(point_only,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=offset
        )

        self.assertFalse(result.allowed)
        self.assertEqual("target_bounds_missing", result.reason)

    def test_missing_widget_bounds_allow_only_the_exact_canonical_point(self) -> None:
        point = ScreenPoint(700, 40)
        offset = ScreenPoint(point.x + 1, point.y)
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
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint("bank", 2, True)
            ),
        )
        post = replace(
            observation(tick=101, widgets=widgets, menu_point=offset),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )

        result = self.gate.validate_post_move(
            action, post, settled_pointer=offset
        )

        self.assertFalse(result.allowed)
        self.assertEqual("target_bounds_missing", result.reason)

    def test_post_move_allows_only_bounded_fresh_canonical_aim_drift(self) -> None:
        settled = ScreenPoint(TREE_POINT.x + 1, TREE_POINT.y)
        drifted = replace(
            tree(),
            geometry=replace(tree().geometry, screen_point=settled),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=settled,
            nearby_objects=(drifted,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=settled
        )

        self.assertTrue(result.allowed)
        self.assertEqual("post_move_safe", result.reason)

        evaluation = self.gate.evaluate_post_move(
            tree_action(), post, settled_pointer=settled
        )
        self.assertIn(
            SafetyCheck(
                "post_move.action_invariants",
                "screen_point_drift_bounded",
                True,
            ),
            evaluation.checks,
        )

        pre_result = self.gate.validate_pre_move(
            tree_action(),
            observation(
                tick=100,
                menus=(exact_hover(),),
                menu_point=settled,
                nearby_objects=(drifted,),
            ),
        )
        self.assertFalse(pre_result.allowed)
        self.assertEqual("screen_point_not_verified", pre_result.reason)

    def test_post_move_rejects_canonical_aim_drift_beyond_tolerance(self) -> None:
        drifted_point = ScreenPoint(TREE_POINT.x + 4, TREE_POINT.y)
        settled = ScreenPoint(TREE_POINT.x + 2, TREE_POINT.y)
        drifted = replace(
            tree(),
            geometry=replace(tree().geometry, screen_point=drifted_point),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=settled,
            nearby_objects=(drifted,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=settled
        )

        self.assertFalse(result.allowed)
        self.assertEqual("screen_point_not_verified", result.reason)

    def test_source_and_fresh_tolerances_cannot_stack_to_six_pixels(self) -> None:
        settled = ScreenPoint(TREE_POINT.x - 3, TREE_POINT.y)
        fresh = ScreenPoint(TREE_POINT.x + 3, TREE_POINT.y)
        drifted = replace(
            tree(),
            geometry=replace(tree().geometry, screen_point=fresh),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=settled,
            nearby_objects=(drifted,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=settled
        )

        self.assertFalse(result.allowed)
        self.assertEqual(
            "settled_pointer_outside_fresh_region", result.reason
        )

    def test_walk_uses_the_same_bounded_reprojection_contract(self) -> None:
        fresh = ScreenPoint(TREE_POINT.x + 3, TREE_POINT.y)
        settled = ScreenPoint(TREE_POINT.x + 2, TREE_POINT.y)
        drifted = replace(
            walk_target(),
            geometry=replace(walk_target().geometry, screen_point=fresh),
        )
        post = observation(
            tick=101,
            menus=(walk_hover(),),
            menu_point=settled,
            nearby_objects=(drifted,),
        )

        result = self.gate.validate_post_move(
            walk_action(), post, settled_pointer=settled
        )

        self.assertTrue(result.allowed)
        self.assertEqual("post_move_safe", result.reason)

    def test_widget_uses_the_same_bounded_reprojection_contract(self) -> None:
        source = ScreenPoint(700, 40)
        fresh = ScreenPoint(source.x + 3, source.y)
        settled = ScreenPoint(source.x + 2, source.y)
        close = WidgetTarget(
            "Close bank",
            True,
            fresh,
            ScreenBounds(690, 30, 30, 30),
        )
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
            screen_point=source,
            source_menu_client_tick=1100,
            source_session_id="session-1",
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint("bank", 2, True)
            ),
        )
        post = replace(
            observation(tick=101, widgets=widgets, menu_point=settled),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )

        result = self.gate.validate_post_move(
            action, post, settled_pointer=settled
        )

        self.assertTrue(result.allowed)
        self.assertEqual("post_move_safe", result.reason)

    def test_context_candidate_uses_bounded_reprojection_contract(self) -> None:
        fresh = ScreenPoint(TREE_POINT.x + 3, TREE_POINT.y)
        settled = ScreenPoint(TREE_POINT.x + 2, TREE_POINT.y)
        drifted = replace(
            tree(),
            geometry=replace(tree().geometry, screen_point=fresh),
        )
        generic = MenuEntry(
            "Examine", "Tree", "EXAMINE_OBJECT", 1276, 49, 52
        )
        post = observation(
            tick=101,
            menus=(generic, exact_hover()),
            menu_point=settled,
            nearby_objects=(drifted,),
        )

        result = self.gate.validate_context_candidate(
            tree_action(), post, settled_pointer=settled
        )

        self.assertTrue(result.allowed)
        self.assertEqual("context_candidate_safe", result.reason)

    def test_point_only_target_cannot_reproject_after_pointer_settle(self) -> None:
        fresh = ScreenPoint(TREE_POINT.x + 1, TREE_POINT.y)
        point_only = replace(
            tree(),
            geometry=replace(
                tree().geometry,
                screen_point=fresh,
                screen_bounds=None,
            ),
        )
        post = observation(
            tick=101,
            menus=(exact_hover(),),
            menu_point=TREE_POINT,
            nearby_objects=(point_only,),
        )

        result = self.gate.validate_post_move(
            tree_action(), post, settled_pointer=TREE_POINT
        )

        self.assertFalse(result.allowed)
        self.assertEqual("target_bounds_missing", result.reason)

    def test_pre_move_evaluation_records_stable_order_and_drives_wrapper(self) -> None:
        action = tree_action()
        sample = observation()

        evaluation = self.gate.evaluate_pre_move(action, sample)

        self.assertIsInstance(evaluation, SafetyEvaluation)
        self.assertEqual(
            [
                ("pre_move.observation", "observation_safe", True),
                ("pre_move.session", "session_bound", True),
                ("pre_move.source_tick", "tick_bound", True),
                ("pre_move.source_menu_sample", "menu_sample_bound", True),
                ("pre_move.action_invariants", "screen_point_safe", True),
                (
                    "pre_move.task_constraints",
                    "task_constraints_satisfied",
                    True,
                ),
                ("pre_move.complete", "pre_move_safe", True),
            ],
            [(check.stage, check.code, check.allowed) for check in evaluation.checks],
        )
        self.assertEqual(evaluation.result, self.gate.validate_pre_move(action, sample))

    def test_evaluation_stops_at_the_exact_failed_check(self) -> None:
        evaluation = self.gate.evaluate_pre_move(
            tree_action(), replace(observation(), source_coherent=False)
        )

        self.assertEqual(SafetyResult(False, "source_incoherent"), evaluation.result)
        self.assertEqual(
            (SafetyCheck("pre_move.observation", "source_incoherent", False),),
            evaluation.checks,
        )

    def test_post_and_context_evaluations_use_distinct_stable_stages(self) -> None:
        action = tree_action()
        post = observation(tick=101, menus=(exact_hover(),))
        post_evaluation = self.gate.evaluate_post_move(action, post)
        context_evaluation = self.gate.evaluate_context_candidate(action, post)

        self.assertEqual("post_move_safe", post_evaluation.result.reason)
        self.assertEqual("post_move.observation", post_evaluation.checks[0].stage)
        self.assertEqual("post_move.complete", post_evaluation.checks[-1].stage)
        self.assertEqual(
            "context_candidate.observation", context_evaluation.checks[0].stage
        )
        self.assertEqual(
            "context_candidate.exact_lower_entry",
            context_evaluation.checks[-1].stage,
        )
        self.assertEqual(
            "context_option_not_unique_lower_entry",
            context_evaluation.result.reason,
        )

    def test_safety_evidence_is_deeply_immutable_and_validated(self) -> None:
        evaluation = self.gate.evaluate_pre_move(tree_action(), observation())

        self.assertFalse(hasattr(evaluation, "__dict__"))
        self.assertFalse(hasattr(evaluation.checks[0], "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            evaluation.checks[0].allowed = False  # type: ignore[misc]
        with self.assertRaises(ValueError):
            SafetyEvaluation(
                SafetyResult(True, "safe"),
                (SafetyCheck("test", "different", True),),
            )

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
            frame_id="test-frame-103",
            geometry_frame_id="test-frame-103",
            menu_source_tick=103,
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

    def test_unrelated_object_action_does_not_require_bank_state(self) -> None:
        candidate = replace(
            observation(), widgets=WidgetObservation(bank_known=False)
        )

        result = self.gate.validate_pre_move(tree_action(), candidate)

        self.assertTrue(result.allowed)

    def test_rejects_incoherent_source_frame_explicitly(self) -> None:
        result = self.gate.validate_pre_move(
            tree_action(), replace(observation(), source_coherent=False)
        )

        self.assertFalse(result.allowed)
        self.assertEqual("source_incoherent", result.reason)

    def test_rejects_stale_or_unbound_menu_provenance(self) -> None:
        base = observation()
        cases = {
            "stale flag": (
                replace(base, menu_fresh=False),
                "menu_evidence_stale",
            ),
            "stale timestamp": (
                replace(
                    base,
                    menu_timestamp=base.timestamp - timedelta(seconds=10),
                ),
                "menu_evidence_too_old",
            ),
            "source tick mismatch": (
                replace(base, menu_source_tick=base.tick - 1),
                "menu_source_tick_mismatch",
            ),
            "session mismatch": (
                replace(base, menu_session_id="session-2"),
                "menu_session_mismatch",
            ),
            "process mismatch": (
                replace(base, menu_process_id=4321),
                "menu_process_mismatch",
            ),
        }

        for label, (candidate, expected_reason) in cases.items():
            with self.subTest(label=label):
                result = self.gate.validate_pre_move(tree_action(), candidate)
                self.assertFalse(result.allowed)
                self.assertEqual(expected_reason, result.reason)

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
            task_constraints=TaskConstraints(
                inventory=InventoryConstraint(frozenset({1511})),
                interface=InterfaceConstraint("bank", 2, True, require_readable=True),
            ),
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
            task_constraints=TaskConstraints(
                inventory=InventoryConstraint(frozenset({1511})),
                interface=InterfaceConstraint("bank", 2, True, require_readable=True),
            ),
        )

        for candidate, reason in (
            (inventory(known=False), "inventory_unknown"),
            (inventory(), "constrained_inventory_empty"),
        ):
            with self.subTest(known=candidate.known, reason=reason):
                result = self.gate.validate_pre_move(
                    action,
                    replace(
                        observation(items=candidate, widgets=widgets),
                        location=WorldPoint(3208, 3220, 2),
                        plane=2,
                    ),
                )
                self.assertFalse(result.allowed)
                self.assertEqual(reason, result.reason)

        missing_constraint = replace(
            action,
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint("bank", 2, True, require_readable=True)
            ),
        )
        result = self.gate.validate_pre_move(
            missing_constraint,
            replace(
                observation(items=inventory(1511), widgets=widgets),
                location=WorldPoint(3208, 3220, 2),
                plane=2,
            ),
        )
        self.assertEqual("inventory_constraint_missing", result.reason)

        missing_interface = replace(
            action,
            task_constraints=TaskConstraints(
                inventory=InventoryConstraint(frozenset({1511}))
            ),
        )
        result = self.gate.validate_pre_move(
            missing_interface,
            replace(
                observation(items=inventory(1511), widgets=widgets),
                location=WorldPoint(3208, 3220, 2),
                plane=2,
            ),
        )
        self.assertEqual("interface_constraint_missing", result.reason)

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
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint("bank", 2, True)
            ),
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

    def test_dialogue_constraint_allows_only_the_exact_numbered_choice(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Which passage should be entered?",
            dialogue_options=(
                DialogueOption(1, "1", "Enter eastern passage."),
                DialogueOption(2, "2", "Enter western passage."),
            ),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose eastern passage",
            100,
            option="Enter eastern passage.",
            target_key="dialogue:1",
            target_name="Enter eastern passage.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
            task_constraints=TaskConstraints(
                dialogue=DialogueOptionConstraint(
                    "passage", "Enter eastern passage.", 1, "1"
                )
            ),
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
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(
                    "bank", 2, True, require_keyboard_close=True
                )
            ),
        )
        before = replace(
            observation(tick=100, widgets=widgets),
            location=WorldPoint(3208, 3220, 2),
            plane=2,
        )
        after = replace(before, tick=101)

        self.assertTrue(self.gate.validate_pre_move(action, before).allowed)
        self.assertEqual(
            "interface_sample_not_newer",
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
            "interface_keyboard_close_unavailable",
            self.gate.validate_pre_move(action, unavailable).reason,
        )


if __name__ == "__main__":
    unittest.main()

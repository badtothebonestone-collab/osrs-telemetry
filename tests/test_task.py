from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from osrs_bot.model import (
    DialogueOption,
    LOG_ITEM_ID,
    ActionKind,
    InventoryItem,
    InventoryObservation,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    TaskPhase,
    VerificationKind,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.task import (
    BANK_ANCHOR,
    BANK_OBJECT_ID,
    CHOP_DEADLINE_TICKS,
    CLOSE_WIDGET_NAME,
    DEPOSIT_WIDGET_NAME,
    ROUTE_TO_BANK,
    ROUTE_TO_TREES,
    TREE_AREA,
    WoodcutBankTask,
)


SCREEN = ScreenPoint(1400, 2200)
GEOMETRY = TargetGeometry(
    available=True,
    on_screen=True,
    visible=True,
    actionable=True,
    canvas_point=ScreenPoint(400, 200),
    screen_point=SCREEN,
)


def inventory(
    *, logs: int = 0, other_ids: tuple[int, ...] = (), full: bool = False, known: bool = True
) -> InventoryObservation:
    items = []
    if logs:
        items.append(InventoryItem(0, LOG_ITEM_ID, logs, "Logs"))
    for slot, item_id in enumerate(other_ids, start=1):
        items.append(InventoryItem(slot, item_id, 1, "Other"))
    occupied = 28 if full else len(items)
    return InventoryObservation(
        items=tuple(items),
        occupied_slots=occupied,
        free_slots=0 if full else 28 - occupied,
        known=known,
    )


def observation(
    *,
    location: WorldPoint = TREE_AREA,
    inv: InventoryObservation | None = None,
    objects: tuple[NearbyObject, ...] = (),
    widgets: WidgetObservation | None = None,
    tick: int = 10,
    fresh: bool = True,
) -> Observation:
    return Observation(
        player=PlayerObservation(),
        location=location,
        plane=location.plane,
        inventory=inv or inventory(),
        nearby_objects=objects,
        menus=(),
        widgets=widgets or WidgetObservation(bank_known=True),
        canvas_bounds=ScreenBounds(1000, 2000, 800, 600),
        game_state="LOGGED_IN",
        timestamp=datetime.now(timezone.utc),
        tick=tick,
        status="PASS",
        fresh=fresh,
        cache_wall_clock_fresh=fresh,
        scene_playable=True,
        session_id="session-1",
        menu_client_tick=500,
        client_focused=True,
        client_process_id=1234,
    )


def scene_object(
    key: str,
    object_id: int,
    name: str,
    action: str,
    location: WorldPoint,
    *,
    geometry: TargetGeometry = GEOMETRY,
    resource: bool = False,
    route: bool = False,
    service: bool = False,
) -> NearbyObject:
    return NearbyObject(
        key=key,
        object_id=object_id,
        name=name,
        kind="GAME_OBJECT",
        actions=(action,),
        location=location,
        distance=1,
        geometry=geometry,
        scene_x=49,
        scene_y=52,
        resource_candidate=resource,
        route_candidate=route,
        service_candidate=service,
    )


def tree(**overrides: object) -> NearbyObject:
    values = {
        "key": "tree:1276",
        "object_id": 1276,
        "name": "Tree",
        "action": "Chop down",
        "location": WorldPoint(3195, 3248, 0),
        "geometry": GEOMETRY,
        "resource": True,
    }
    values.update(overrides)
    return scene_object(**values)


def route_tile(step) -> NearbyObject:
    return NearbyObject(
        key=step.target_key,
        object_id=0,
        name=step.target_key,
        kind="NAVIGATION_TILE",
        actions=("Walk here",),
        location=step.location,
        distance=8,
        geometry=GEOMETRY,
        scene_x=49,
        scene_y=52,
        route_candidate=True,
    )


def route_object(step, **overrides: object) -> NearbyObject:
    values = {
        "key": f"live:{step.step_id}",
        "object_id": step.object_id,
        "name": step.object_name,
        "action": step.action,
        "location": step.location,
        "route": True,
    }
    values.update(overrides)
    return scene_object(**values)


def bank_object(**overrides: object) -> NearbyObject:
    values = {
        "key": "live:bank-booth",
        "object_id": BANK_OBJECT_ID,
        "name": "Bank booth",
        "action": "Bank",
        "location": BANK_ANCHOR,
        "route": True,
        "service": True,
    }
    values.update(overrides)
    return scene_object(**values)


def bank_widgets(*, open: bool = True, readable: bool = True) -> WidgetObservation:
    return WidgetObservation(
        bank_known=True,
        bank_open=open,
        bank_readable=readable,
        deposit_inventory=WidgetTarget(DEPOSIT_WIDGET_NAME, True, SCREEN),
        close_bank=WidgetTarget(CLOSE_WIDGET_NAME, True, SCREEN),
    )


def stair_dialogue() -> WidgetObservation:
    return WidgetObservation(
        dialogue_active=True,
        dialogue_type="options",
        dialogue_prompt="Climb up or down the stairs?",
        dialogue_options=(
            DialogueOption(1, "1", "Climb up the stairs."),
            DialogueOption(2, "2", "Climb down the stairs."),
        ),
        dialogue_number_keys=True,
        dialogue_client_tick=600,
    )


class WoodcutBankTaskTests(unittest.TestCase):
    def test_find_tree_requires_exact_name_action_and_screen_geometry(self) -> None:
        invalid_geometry = TargetGeometry(
            available=True, on_screen=True, visible=True, actionable=True
        )
        point_outside_bounds = replace(
            GEOMETRY,
            screen_bounds=ScreenBounds(1000, 2000, 10, 10),
        )
        cases = (
            tree(object_id=1278),
            tree(name="Oak tree"),
            tree(action="Chop"),
            tree(geometry=invalid_geometry),
            tree(geometry=point_outside_bounds),
            tree(resource=False),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                task = WoodcutBankTask()
                decision = task.decide(observation(objects=(candidate,)))
                self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
                self.assertEqual(ActionKind.WAIT, decision.action.kind)

        task = WoodcutBankTask()
        decision = task.decide(observation(objects=(tree(),)))
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertEqual("tree:1276", task.progress.target_key)

    def test_find_tree_skips_aim_point_occluded_by_another_tree(self) -> None:
        occluded = tree(
            key="tree:occluded",
            location=WorldPoint(3196, 3248, 0),
            geometry=replace(
                GEOMETRY,
                screen_point=ScreenPoint(1300, 2200),
                screen_bounds=ScreenBounds(1250, 2150, 100, 100),
            ),
        )
        clear = tree(
            key="tree:clear",
            location=WorldPoint(3197, 3248, 0),
            geometry=replace(
                GEOMETRY,
                screen_point=ScreenPoint(1400, 2200),
                screen_bounds=ScreenBounds(1200, 2100, 250, 200),
            ),
        )

        task = WoodcutBankTask()
        task.decide(observation(objects=(occluded, clear)))

        self.assertEqual("tree:clear", task.progress.target_key)
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)

    def test_stale_scene_and_unknown_inventory_never_emit_actions(self) -> None:
        task = WoodcutBankTask()
        stale = task.decide(observation(objects=(tree(),), fresh=False))
        self.assertEqual(ActionKind.WAIT, stale.action.kind)
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)

        unknown = task.decide(
            observation(objects=(tree(),), inv=inventory(known=False))
        )
        self.assertEqual(ActionKind.WAIT, unknown.action.kind)
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)

    def test_read_only_decision_does_not_require_foreground_focus(self) -> None:
        task = WoodcutBankTask()
        state = replace(
            observation(objects=(tree(),)),
            client_focused=False,
            client_process_id=None,
        )

        task.decide(state)
        decision = task.decide(state)

        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual("Chop down", decision.action.option)

    def test_chop_sets_external_log_verification_and_advances_on_result(self) -> None:
        task = WoodcutBankTask()
        first = observation(objects=(tree(),), tick=20)
        task.decide(first)
        decision = task.decide(first)

        self.assertEqual(TaskPhase.VERIFY_LOGS, decision.phase)
        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual("Chop down", decision.action.option)
        self.assertEqual(SCREEN, decision.action.screen_point)
        self.assertEqual(VerificationKind.LOG_GAINED, task.progress.pending.kind)
        self.assertEqual(0, task.progress.pending.before_log_count)
        self.assertEqual(20 + CHOP_DEADLINE_TICKS, task.progress.pending.deadline_tick)

        waiting = task.decide(observation(objects=(tree(),), tick=21))
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)
        task.apply_verification(True, "ordinary log count increased")
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIsNone(task.progress.pending)

        full = task.decide(observation(inv=inventory(logs=28, full=True), tick=22))
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, full.phase)
        self.assertEqual(ActionKind.WAIT, full.action.kind)

    def test_failed_external_verification_blocks_terminally(self) -> None:
        task = WoodcutBankTask()
        state = observation(objects=(tree(),))
        task.decide(state)
        task.decide(state)
        task.apply_verification(False, "deadline expired without log gain")

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIn("deadline expired", task.progress.failures[-1])
        self.assertEqual(ActionKind.WAIT, task.decide(state).action.kind)

    def test_only_current_walk_step_is_requested_and_strictly_accepted(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        approach = WorldPoint(3195, 3252, 0)
        self.assertEqual(((step.target_key, step.location),), task.requested_tile_projections())

        missing = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
            )
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, missing.phase)
        self.assertEqual(ActionKind.WAIT, missing.action.kind)

        invalid = route_tile(step)
        invalid = NearbyObject(**{**invalid.__dict__, "object_id": 99})
        blocked = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
                objects=(invalid,),
            )
        )
        self.assertEqual(TaskPhase.BLOCKED, blocked.phase)
        self.assertEqual(ActionKind.WAIT, blocked.action.kind)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        unavailable = replace(route_tile(step), geometry=TargetGeometry())
        waiting = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
                objects=(unavailable,),
            )
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, waiting.phase)
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        decision = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
                objects=(route_tile(step),),
            )
        )
        self.assertEqual(ActionKind.WALK, decision.action.kind)
        self.assertEqual(SCREEN, decision.action.screen_point)
        self.assertEqual(VerificationKind.MOVED_CLOSER, task.progress.pending.kind)
        task.apply_verification(True, "distance decreased")
        self.assertEqual(0, task.progress.route_index)

        arrived = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                tick=20,
            )
        )
        self.assertEqual(ActionKind.WAIT, arrived.action.kind)
        self.assertEqual(0, task.progress.route_index)
        settled = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                tick=24,
            )
        )
        self.assertEqual(ActionKind.WAIT, settled.action.kind)
        self.assertEqual(1, task.progress.route_index)

    def test_stairs_require_exact_id_action_plane_and_external_plane_proof(self) -> None:
        stair_index = next(index for index, item in enumerate(ROUTE_TO_BANK) if not item.is_walk)
        step = ROUTE_TO_BANK[stair_index]
        for bad_target in (
            route_object(step, object_id=step.object_id + 1),
            route_object(step, action="Climb-down"),
            route_object(step, location=WorldPoint(step.location.x, step.location.y, 1)),
        ):
            with self.subTest(target=bad_target):
                task = WoodcutBankTask()
                task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
                task.progress.route_index = stair_index
                decision = task.decide(
                    observation(
                        location=step.location,
                        inv=inventory(logs=28, full=True),
                        objects=(bad_target,),
                    )
                )
                self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, decision.phase)
                self.assertEqual(ActionKind.WAIT, decision.action.kind)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = stair_index
        decision = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                objects=(replace(
                    route_object(step),
                    actions=("Climb", "Climb-up", "Climb-down"),
                ),),
            )
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual(step.action, decision.action.option)
        self.assertEqual(1, decision.action.verification.expected_plane)
        self.assertEqual(
            VerificationKind.ROUTE_TRANSITION_READY,
            decision.action.verification.kind,
        )
        self.assertEqual((), task.requested_tile_projections())
        task.apply_verification(True, "plane_changed")
        self.assertEqual(stair_index + 1, task.progress.route_index)

        second = ROUTE_TO_BANK[stair_index + 1]
        task.decide(
            observation(
                location=second.location,
                inv=inventory(logs=28, full=True),
                objects=(route_object(second),),
            )
        )
        task.apply_verification(True, "plane_changed")
        self.assertEqual(stair_index + 2, task.progress.route_index)

    def test_generic_climb_uses_exact_direction_dialogue(self) -> None:
        stair_index = next(index for index, item in enumerate(ROUTE_TO_BANK) if not item.is_walk)
        step = ROUTE_TO_BANK[stair_index]
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = stair_index
        task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                objects=(route_object(step, action="Climb"),),
            )
        )

        task.apply_verification(True, "dialogue_open")
        choice = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                widgets=stair_dialogue(),
                tick=11,
            )
        )

        self.assertEqual(TaskPhase.STAIR_DIALOGUE, choice.phase)
        self.assertEqual(ActionKind.PRESS_KEY, choice.action.kind)
        self.assertEqual("1", choice.action.key)
        self.assertEqual("Climb up the stairs.", choice.action.target_name)
        task.apply_verification(True, "plane_changed")
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(stair_index + 1, task.progress.route_index)

    def test_open_bank_requires_exact_lumbridge_booth_and_verification(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.OPEN_BANK
        invalid = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                objects=(bank_object(object_id=10355),),
            )
        )
        self.assertEqual(TaskPhase.BLOCKED, invalid.phase)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.OPEN_BANK
        decision = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                objects=(bank_object(route=False),),
            )
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual(BANK_OBJECT_ID, decision.action.target_id)
        self.assertEqual(VerificationKind.BANK_OPEN, task.progress.pending.kind)
        task.apply_verification(True, "readable bank opened")
        self.assertEqual(TaskPhase.DEPOSIT_LOGS, task.progress.phase)

    def test_bank_pin_and_non_log_inventory_fail_closed(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.OPEN_BANK
        pin = WidgetObservation(bank_known=True, bank_pin_open=True)
        result = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                widgets=pin,
            )
        )
        self.assertEqual(TaskPhase.BLOCKED, result.phase)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.DEPOSIT_LOGS
        mixed = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=27, other_ids=(995,), full=True),
                widgets=bank_widgets(),
            )
        )
        self.assertEqual(TaskPhase.BLOCKED, mixed.phase)
        self.assertEqual(ActionKind.WAIT, mixed.action.kind)

    def test_deposit_all_logs_then_close_bank_via_verified_actions(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.DEPOSIT_LOGS
        decision = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                widgets=bank_widgets(),
            )
        )
        self.assertEqual(TaskPhase.VERIFY_DEPOSIT, decision.phase)
        self.assertEqual(ActionKind.CLICK_WIDGET, decision.action.kind)
        self.assertEqual(DEPOSIT_WIDGET_NAME, decision.action.target_key)
        self.assertEqual(VerificationKind.LOGS_DEPOSITED, task.progress.pending.kind)
        task.apply_verification(True, "inventory has no logs")
        self.assertEqual(TaskPhase.CLOSE_BANK, task.progress.phase)

        close = task.decide(
            observation(location=BANK_ANCHOR, widgets=bank_widgets(), tick=11)
        )
        self.assertEqual(ActionKind.CLICK_WIDGET, close.action.kind)
        self.assertEqual(CLOSE_WIDGET_NAME, close.action.target_key)
        self.assertEqual(VerificationKind.BANK_CLOSED, task.progress.pending.kind)
        task.apply_verification(True, "bank widget closed")
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(0, task.progress.route_index)

    def test_already_open_and_already_closed_are_simple_phase_evidence(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.OPEN_BANK
        opened = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                widgets=bank_widgets(),
            )
        )
        self.assertEqual(TaskPhase.DEPOSIT_LOGS, opened.phase)

        task.progress.phase = TaskPhase.CLOSE_BANK
        closed = task.decide(
            observation(location=BANK_ANCHOR, widgets=bank_widgets(open=False))
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, closed.phase)

    def test_close_bank_uses_verified_escape_when_button_geometry_is_absent(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CLOSE_BANK
        widgets = replace(
            bank_widgets(),
            close_bank=WidgetTarget(CLOSE_WIDGET_NAME, True),
            keyboard_close_possible=True,
        )

        decision = task.decide(
            observation(location=BANK_ANCHOR, widgets=widgets, tick=11)
        )

        self.assertEqual(ActionKind.PRESS_KEY, decision.action.kind)
        self.assertEqual("escape", decision.action.key)
        self.assertEqual("close_bank_keyboard", decision.action.target_key)
        self.assertEqual(VerificationKind.BANK_CLOSED, task.progress.pending.kind)

    def test_verified_return_phase_does_not_reopen_inventory_uncertainty(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CLOSE_BANK
        widgets = replace(
            bank_widgets(),
            close_bank=None,
            keyboard_close_possible=True,
        )

        decision = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(known=False),
                widgets=widgets,
                tick=11,
            )
        )

        self.assertEqual(ActionKind.PRESS_KEY, decision.action.kind)
        self.assertNotIn("inventory", decision.reason)

    def test_unknown_bank_capture_cannot_prove_closed_or_emit_bank_input(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CLOSE_BANK

        decision = task.decide(
            observation(
                location=BANK_ANCHOR,
                widgets=WidgetObservation(bank_known=False, bank_open=False),
            )
        )

        self.assertEqual(TaskPhase.CLOSE_BANK, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertIn("not observable", decision.reason)

    def test_fixed_return_route_completes_exactly_one_cycle(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_TREES

        self.assertEqual("Bottom-floor", ROUTE_TO_TREES[2].action)

        for step in ROUTE_TO_TREES:
            expected = ((step.target_key, step.location),) if step.is_walk else ()
            self.assertEqual(expected, task.requested_tile_projections())
            if step.is_walk:
                decision = task.decide(observation(location=step.location))
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
            else:
                decision = task.decide(
                    observation(location=step.location, objects=(route_object(step),))
                )
                self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
                task.apply_verification(True, "plane_changed")

        self.assertEqual(TaskPhase.COMPLETE, task.progress.phase)
        self.assertEqual(1, task.progress.cycles_completed)
        terminal = task.decide(observation(location=TREE_AREA, objects=(tree(),)))
        self.assertEqual(TaskPhase.COMPLETE, terminal.phase)
        self.assertEqual(ActionKind.WAIT, terminal.action.kind)

    def test_verification_phase_without_pending_is_blocked(self) -> None:
        for phase in (TaskPhase.VERIFY_LOGS, TaskPhase.VERIFY_DEPOSIT):
            with self.subTest(phase=phase):
                task = WoodcutBankTask()
                task.progress.phase = phase
                result = task.decide(observation())
                self.assertEqual(TaskPhase.BLOCKED, result.phase)

    def test_apply_verification_without_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no action verification"):
            WoodcutBankTask().apply_verification(True, "impossible")


if __name__ == "__main__":
    unittest.main()

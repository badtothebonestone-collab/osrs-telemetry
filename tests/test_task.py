from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from osrs_bot.definition import LUMBRIDGE_WEST_TREES_V1
from osrs_bot.model import (
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    DialogueOption,
    ActionKind,
    InventoryItem,
    InventoryObservation,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    VerificationKind,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.profile import DEFAULT_BINDING
from osrs_bot.task import TaskPhase, WoodcutBankTask
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationResult,
    VerificationStatus,
)


DEFINITION = LUMBRIDGE_WEST_TREES_V1
TREE_AREA = DEFINITION.resource.work_area.anchor
BANK_ANCHOR = DEFINITION.bank.anchor
TREE_OBJECT_ID = next(iter(DEFINITION.resource.selector.object_ids))
BANK_OBJECT_ID = next(iter(DEFINITION.bank.selector.object_ids))
LOG_ITEM_ID = next(iter(DEFINITION.resource.produced_item_ids))
ROUTE_TO_BANK = DEFINITION.route_to_bank.steps
ROUTE_TO_TREES = DEFINITION.route_to_resource.steps
CHOP_DEADLINE_TICKS = DEFINITION.verification.resource_deadline_ticks
CLOSE_WIDGET_NAME = CLOSE_BANK_WIDGET_KEY
DEPOSIT_WIDGET_NAME = DEPOSIT_INVENTORY_WIDGET_KEY


def verification_pass(
    kind: OutcomeKind, reason: str = "verified", tick: int = 11
) -> VerificationResult:
    return VerificationResult(
        VerificationStatus.PASS,
        reason,
        Outcome(kind, tick),
    )


def verification_fail(reason: str) -> VerificationResult:
    return VerificationResult(VerificationStatus.FAIL, reason)


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
    timestamp = datetime.now(timezone.utc)
    session_id = "session-1"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
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
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=fresh,
        cache_wall_clock_fresh=fresh,
        scene_playable=True,
        session_id=session_id,
        menu_client_tick=500,
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
        "key": f"resource:{TREE_OBJECT_ID}",
        "object_id": TREE_OBJECT_ID,
        "name": DEFINITION.resource.selector.name,
        "action": DEFINITION.resource.selector.action,
        "location": WorldPoint(3195, 3248, 0),
        "geometry": GEOMETRY,
        "resource": False,
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
        route_candidate=False,
    )


def route_object(step, **overrides: object) -> NearbyObject:
    values = {
        "key": f"live:{step.step_id}",
        "object_id": step.object_id,
        "name": step.object_name,
        "action": step.action,
        "location": step.location,
        "route": False,
    }
    values.update(overrides)
    return scene_object(**values)


def bank_object(**overrides: object) -> NearbyObject:
    values = {
        "key": "live:bank-booth",
        "object_id": BANK_OBJECT_ID,
        "name": DEFINITION.bank.selector.name,
        "action": DEFINITION.bank.selector.action,
        "location": BANK_ANCHOR,
        "route": False,
        "service": False,
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
    def test_task_uses_the_validated_default_binding(self) -> None:
        task = WoodcutBankTask()

        self.assertIs(task.binding, DEFAULT_BINDING)
        self.assertIs(task.definition, DEFINITION)
        snapshot = task.snapshot()
        self.assertEqual("woodcut_bank", snapshot.task_id)
        self.assertEqual(DEFINITION.definition_id, snapshot.definition_id)
        self.assertEqual(DEFAULT_BINDING.profile.profile_id, snapshot.profile_id)
        self.assertEqual("cycles", snapshot.progress.label)
        self.assertEqual(0, snapshot.progress.current)
        self.assertEqual(DEFAULT_BINDING.profile.cycle_goal, snapshot.progress.total)

        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 2
        route_snapshot = task.snapshot()
        self.assertEqual(DEFINITION.route_to_bank.route_id, route_snapshot.progress.label)
        self.assertEqual(2, route_snapshot.progress.current)
        self.assertEqual(len(ROUTE_TO_BANK), route_snapshot.progress.total)

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
        self.assertEqual(tree().key, task.progress.target_key)
        self.assertFalse(tree().resource_candidate)

    def test_tree_rejection_codes_are_stable_and_geometry_is_exact(self) -> None:
        bad = tree(
            key="tree:bad",
            object_id=1278,
            name="Oak tree",
            action="Chop",
            geometry=TargetGeometry(),
        )
        state = observation(objects=(bad,), tick=42)

        decision = WoodcutBankTask().decide(state)

        self.assertIsNone(decision.evidence.selected)
        self.assertEqual((), decision.evidence.eligible)
        self.assertEqual(1, len(decision.evidence.rejected))
        rejected = decision.evidence.rejected[0]
        self.assertEqual("tree:bad", rejected.target.key)
        self.assertEqual(42, rejected.target.source_tick)
        self.assertEqual("test-frame-42", rejected.target.geometry_frame_id)
        self.assertIsNone(rejected.target.point)
        self.assertIsNone(rejected.target.bounds)
        self.assertEqual(
            (
                "object_id_not_supported",
                "name_mismatch",
                "action_unavailable",
                "geometry_unavailable",
                "off_screen",
                "not_visible",
                "not_actionable",
                "screen_point_unavailable",
            ),
            rejected.rejection_codes,
        )

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
        decision = task.decide(observation(objects=(occluded, clear), tick=37))

        self.assertEqual("tree:clear", task.progress.target_key)
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual("tree:clear", decision.evidence.selected.key)
        self.assertEqual(
            ("tree:clear",),
            tuple(target.key for target in decision.evidence.eligible),
        )
        self.assertEqual(
            ("tree:occluded",),
            tuple(item.target.key for item in decision.evidence.rejected),
        )
        self.assertEqual(
            ("aim_point_occluded",),
            decision.evidence.rejected[0].rejection_codes,
        )
        selected = decision.evidence.selected
        self.assertEqual(DEFINITION.resource.selector.action, selected.action)
        self.assertEqual(37, selected.source_tick)
        self.assertEqual("test-frame-37", selected.geometry_frame_id)
        self.assertEqual(clear.geometry.screen_point, selected.point)
        self.assertEqual(clear.geometry.screen_bounds, selected.bounds)

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

        self.assertEqual(TaskPhase.VERIFY_LOGS.value, decision.state)
        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual("Chop down", decision.action.option)
        self.assertEqual(SCREEN, decision.action.screen_point)
        self.assertEqual(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            task.progress.pending.kind,
        )
        self.assertEqual(0, task.progress.pending.before_quantity)
        self.assertEqual(20 + CHOP_DEADLINE_TICKS, task.progress.pending.deadline_tick)

        waiting = task.decide(observation(objects=(tree(),), tick=21))
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)
        task.apply_verification(
            verification_pass(OutcomeKind.ITEM_QUANTITY_INCREASED)
        )
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIsNone(task.progress.pending)

        full = task.decide(observation(inv=inventory(logs=28, full=True), tick=22))
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK.value, full.state)
        self.assertEqual(ActionKind.WAIT, full.action.kind)

    def test_failed_external_verification_blocks_terminally(self) -> None:
        task = WoodcutBankTask()
        state = observation(objects=(tree(),))
        task.decide(state)
        task.decide(state)
        task.apply_verification(
            verification_fail("deadline expired without log gain")
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIn("deadline expired", task.progress.failures[-1])
        self.assertEqual(ActionKind.WAIT, task.decide(state).action.kind)

    def test_only_current_walk_step_is_requested_and_strictly_accepted(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        approach = WorldPoint(3195, 3252, 0)
        self.assertEqual(
            ((step.target_key, step.location),),
            task.observation_request().tile_projections,
        )

        missing = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
            )
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK.value, missing.state)
        self.assertEqual(ActionKind.WAIT, missing.action.kind)

        invalid = route_tile(step)
        invalid = replace(invalid, object_id=99)
        blocked = task.decide(
            observation(
                location=approach,
                inv=inventory(logs=28, full=True),
                objects=(invalid,),
            )
        )
        self.assertEqual(TaskPhase.BLOCKED.value, blocked.state)
        self.assertEqual(ActionKind.WAIT, blocked.action.kind)
        self.assertEqual(step.target_key, blocked.evidence.rejected[0].target.key)
        self.assertEqual(
            ("object_id_mismatch",),
            blocked.evidence.rejected[0].rejection_codes,
        )
        blocked_progress = task.snapshot().progress
        self.assertEqual(DEFINITION.route_to_bank.route_id, blocked_progress.label)
        self.assertEqual(0, blocked_progress.current)
        self.assertEqual(len(ROUTE_TO_BANK), blocked_progress.total)

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
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK.value, waiting.state)
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)
        self.assertEqual(
            (
                "geometry_unavailable",
                "off_screen",
                "not_visible",
                "not_actionable",
                "screen_point_unavailable",
            ),
            waiting.evidence.rejected[0].rejection_codes,
        )

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
        self.assertEqual(step.target_key, decision.evidence.selected.key)
        self.assertEqual((step.target_key,), tuple(
            target.key for target in decision.evidence.eligible
        ))
        self.assertEqual("Walk here", decision.evidence.selected.action)
        self.assertEqual(SCREEN, decision.evidence.selected.point)
        self.assertEqual("test-frame-10", decision.evidence.selected.geometry_frame_id)
        self.assertEqual(VerificationKind.MOVED_CLOSER, task.progress.pending.kind)
        task.apply_verification(verification_pass(OutcomeKind.MOVED_CLOSER))
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

    def test_route_object_evidence_uses_the_actual_ranked_selection(self) -> None:
        index = next(
            index for index, step in enumerate(ROUTE_TO_BANK) if not step.is_walk
        )
        step = ROUTE_TO_BANK[index]
        invalid = route_object(
            step,
            key="route:invalid",
            object_id=step.object_id + 1,
        )
        selected = route_object(step, key="route:selected")
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = index

        decision = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                objects=(invalid, selected),
                tick=51,
            )
        )

        self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
        self.assertEqual("route:selected", decision.action.target_key)
        self.assertEqual("route:selected", decision.evidence.selected.key)
        self.assertEqual(
            ("route:selected",),
            tuple(target.key for target in decision.evidence.eligible),
        )
        self.assertEqual(
            ("route:invalid",),
            tuple(item.target.key for item in decision.evidence.rejected),
        )
        self.assertEqual(
            ("object_id_mismatch",),
            decision.evidence.rejected[0].rejection_codes,
        )
        self.assertEqual(decision.action.option, decision.evidence.selected.action)
        self.assertEqual(51, decision.evidence.selected.source_tick)
        self.assertEqual(SCREEN, decision.evidence.selected.point)

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
                self.assertEqual(TaskPhase.NAVIGATE_TO_BANK.value, decision.state)
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
            VerificationKind.ROUTE_TRANSITION,
            decision.action.verification.kind,
        )
        self.assertEqual((), task.observation_request().tile_projections)
        task.apply_verification(verification_pass(OutcomeKind.PLANE_CHANGED))
        self.assertEqual(stair_index + 1, task.progress.route_index)

        second = ROUTE_TO_BANK[stair_index + 1]
        task.decide(
            observation(
                location=second.location,
                inv=inventory(logs=28, full=True),
                objects=(route_object(second),),
            )
        )
        task.apply_verification(verification_pass(OutcomeKind.PLANE_CHANGED))
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

        task.apply_verification(
            verification_pass(OutcomeKind.DIALOGUE_OPTION_APPEARED)
        )
        choice = task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                widgets=stair_dialogue(),
                tick=11,
            )
        )

        self.assertEqual(TaskPhase.STAIR_DIALOGUE.value, choice.state)
        self.assertEqual(ActionKind.PRESS_KEY, choice.action.kind)
        self.assertEqual("1", choice.action.key)
        self.assertEqual("Climb up the stairs.", choice.action.target_name)
        self.assertEqual("dialogue:1", choice.evidence.selected.key)
        self.assertEqual("Climb up the stairs.", choice.evidence.selected.action)
        self.assertEqual(
            ("dialogue:1",),
            tuple(target.key for target in choice.evidence.eligible),
        )
        self.assertEqual(
            ("dialogue:2",),
            tuple(item.target.key for item in choice.evidence.rejected),
        )
        self.assertEqual(
            ("text_mismatch",), choice.evidence.rejected[0].rejection_codes
        )
        self.assertEqual(
            "Climb up the stairs.",
            choice.action.task_constraints.dialogue.option_text,
        )
        task.apply_verification(verification_pass(OutcomeKind.PLANE_CHANGED))
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
        self.assertEqual(TaskPhase.BLOCKED.value, invalid.state)
        self.assertEqual(
            ("object_id_not_supported",),
            invalid.evidence.rejected[0].rejection_codes,
        )

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
        self.assertEqual("live:bank-booth", decision.evidence.selected.key)
        self.assertEqual(DEFINITION.bank.selector.action, decision.evidence.selected.action)
        self.assertEqual(SCREEN, decision.evidence.selected.point)
        self.assertFalse(decision.action.task_constraints.interface.expected_open)
        self.assertEqual(
            VerificationKind.INTERFACE_OPENED,
            task.progress.pending.kind,
        )
        task.apply_verification(verification_pass(OutcomeKind.INTERFACE_OPENED))
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
        self.assertEqual(TaskPhase.BLOCKED.value, result.state)

        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.DEPOSIT_LOGS
        mixed = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=27, other_ids=(995,), full=True),
                widgets=bank_widgets(),
            )
        )
        self.assertEqual(TaskPhase.BLOCKED.value, mixed.state)
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
        self.assertEqual(TaskPhase.VERIFY_DEPOSIT.value, decision.state)
        self.assertEqual(ActionKind.CLICK_WIDGET, decision.action.kind)
        self.assertEqual(DEPOSIT_WIDGET_NAME, decision.action.target_key)
        self.assertEqual(DEPOSIT_WIDGET_NAME, decision.evidence.selected.key)
        self.assertEqual("Deposit inventory", decision.evidence.selected.action)
        self.assertEqual(SCREEN, decision.evidence.selected.point)
        self.assertEqual(
            frozenset({LOG_ITEM_ID}),
            decision.action.task_constraints.inventory.allowed_item_ids,
        )
        self.assertTrue(decision.action.task_constraints.interface.require_readable)
        self.assertEqual(
            VerificationKind.ITEM_QUANTITY_EQUALS,
            task.progress.pending.kind,
        )
        task.apply_verification(verification_pass(OutcomeKind.ITEM_QUANTITY_EQUALS))
        self.assertEqual(TaskPhase.CLOSE_BANK, task.progress.phase)

        close = task.decide(
            observation(location=BANK_ANCHOR, widgets=bank_widgets(), tick=11)
        )
        self.assertEqual(ActionKind.CLICK_WIDGET, close.action.kind)
        self.assertEqual(CLOSE_WIDGET_NAME, close.action.target_key)
        self.assertEqual(
            VerificationKind.INTERFACE_CLOSED,
            task.progress.pending.kind,
        )
        task.apply_verification(verification_pass(OutcomeKind.INTERFACE_CLOSED))
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
        self.assertEqual(TaskPhase.DEPOSIT_LOGS.value, opened.state)

        task.progress.phase = TaskPhase.CLOSE_BANK
        closed = task.decide(
            observation(location=BANK_ANCHOR, widgets=bank_widgets(open=False))
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES.value, closed.state)

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
        self.assertTrue(
            decision.action.task_constraints.interface.require_keyboard_close
        )
        self.assertEqual("close_bank_keyboard", decision.evidence.selected.key)
        self.assertEqual(("close_bank_keyboard",), tuple(
            target.key for target in decision.evidence.eligible
        ))
        self.assertEqual((CLOSE_WIDGET_NAME,), tuple(
            item.target.key for item in decision.evidence.rejected
        ))
        self.assertEqual(
            ("screen_point_unavailable",),
            decision.evidence.rejected[0].rejection_codes,
        )
        self.assertEqual(
            VerificationKind.INTERFACE_CLOSED,
            task.progress.pending.kind,
        )

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
            self.assertEqual(expected, task.observation_request().tile_projections)
            if step.is_walk:
                decision = task.decide(observation(location=step.location))
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
            else:
                decision = task.decide(
                    observation(location=step.location, objects=(route_object(step),))
                )
                self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
                task.apply_verification(
                    verification_pass(OutcomeKind.PLANE_CHANGED)
                )

        self.assertEqual(TaskPhase.COMPLETE, task.progress.phase)
        self.assertEqual(1, task.progress.cycles_completed)
        terminal = task.decide(observation(location=TREE_AREA, objects=(tree(),)))
        self.assertEqual(TaskPhase.COMPLETE.value, terminal.state)
        self.assertEqual(ActionKind.WAIT, terminal.action.kind)

    def test_verification_phase_without_pending_is_blocked(self) -> None:
        for phase in (TaskPhase.VERIFY_LOGS, TaskPhase.VERIFY_DEPOSIT):
            with self.subTest(phase=phase):
                task = WoodcutBankTask()
                task.progress.phase = phase
                result = task.decide(observation())
                self.assertEqual(TaskPhase.BLOCKED.value, result.state)

    def test_apply_verification_without_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no action verification"):
            WoodcutBankTask().apply_verification(
                verification_pass(OutcomeKind.ITEM_QUANTITY_INCREASED)
            )


if __name__ == "__main__":
    unittest.main()

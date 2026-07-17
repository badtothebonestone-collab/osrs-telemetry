from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from osrs_bot.application import EngineApplication
from osrs_bot.definition import (
    LUMBRIDGE_SWAMP_COPPER_V1,
    LUMBRIDGE_WEST_TREES_V1,
    StopConditionKind,
    list_builtin_definitions,
)
from osrs_bot.model import (
    ActionKind,
    EquipmentObservation,
    InventoryItem,
    InventoryObservation,
    NearbyObject,
    Observation,
    PlayerObservation,
    SceneCensusEvidence,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    VerificationKind,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.profile import BoundProfile, Profile
from osrs_bot.task import (
    GATHER_BANK_TASK_ID,
    GatherBankTask,
    TaskPhase,
    WoodcutBankTask,
)
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationResult,
    VerificationStatus,
)


MINING = LUMBRIDGE_SWAMP_COPPER_V1
WOODCUT = LUMBRIDGE_WEST_TREES_V1
COPPER_ORE_ID = 436
BRONZE_PICKAXE_ID = 1265
COPPER_ROCK_ID = min(MINING.resource.selector.object_ids)
BASE_TIME = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
SCREEN_POINT = ScreenPoint(1400, 2200)
EXACT_GEOMETRY = TargetGeometry(
    available=True,
    on_screen=True,
    visible=True,
    actionable=True,
    canvas_point=ScreenPoint(400, 200),
    screen_point=SCREEN_POINT,
)


def mining_binding(**overrides: object) -> BoundProfile:
    values: dict[str, object] = {
        "profile_id": "test_lumbridge_copper",
        "definition_id": MINING.definition_id,
        "cycle_goal": 2,
    }
    values.update(overrides)
    return BoundProfile(Profile(**values), MINING)  # type: ignore[arg-type]


def woodcut_binding() -> BoundProfile:
    return BoundProfile(
        Profile(
            profile_id="test_lumbridge_woodcut",
            definition_id=WOODCUT.definition_id,
            cycle_goal=2,
        ),
        WOODCUT,
    )


def equipment(*item_ids: int, known: bool = True) -> EquipmentObservation:
    if not known:
        return EquipmentObservation()
    items = tuple(
        InventoryItem(slot=slot, item_id=item_id, quantity=1, name="Tool")
        for slot, item_id in enumerate(item_ids)
    )
    return EquipmentObservation(
        items=items,
        occupied_slots=len(items),
        free_slots=14 - len(items),
        known=True,
    )


def inventory(
    *items: InventoryItem,
    occupied_slots: int | None = None,
    free_slots: int | None = None,
    known: bool = True,
) -> InventoryObservation:
    occupied = len(items) if occupied_slots is None else occupied_slots
    free = 28 - occupied if free_slots is None else free_slots
    return InventoryObservation(
        items=tuple(items),
        occupied_slots=occupied,
        free_slots=free,
        known=known,
    )


def resource_object(
    definition=MINING,
    *,
    object_id: int | None = None,
    name: str | None = None,
    action: str | None = None,
    location: WorldPoint | None = None,
    geometry: TargetGeometry = EXACT_GEOMETRY,
) -> NearbyObject:
    selector = definition.resource.selector
    resolved_id = min(selector.object_ids) if object_id is None else object_id
    resolved_action = selector.action if action is None else action
    anchor = definition.resource.work_area.anchor
    return NearbyObject(
        key=f"resource:{resolved_id}:{name or selector.name}:{resolved_action}",
        object_id=resolved_id,
        name=selector.name if name is None else name,
        kind="GAME_OBJECT",
        actions=(resolved_action,),
        location=(
            WorldPoint(anchor.x + 1, anchor.y, anchor.plane)
            if location is None
            else location
        ),
        distance=1,
        geometry=geometry,
        scene_x=49,
        scene_y=52,
    )


def observation(
    *,
    location: WorldPoint | None = None,
    inv: InventoryObservation | None = None,
    equipped: EquipmentObservation | None = None,
    objects: tuple[NearbyObject, ...] = (),
    widgets: WidgetObservation | None = None,
    timestamp: datetime = BASE_TIME,
    tick: int = 10,
) -> Observation:
    resolved_location = location or MINING.resource.work_area.anchor
    frame_id = f"mining-test-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=resolved_location,
        plane=resolved_location.plane,
        inventory=inv if inv is not None else inventory(),
        nearby_objects=objects,
        menus=(),
        widgets=widgets or WidgetObservation(bank_known=True),
        canvas_bounds=ScreenBounds(1000, 2000, 800, 600),
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id="mining-test-session",
        menu_client_tick=500 + tick,
        client_focused=True,
        client_process_id=2468,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id="mining-test-session",
        menu_process_id=2468,
        camera_yaw=0,
        camera_pitch=1024,
        scene_census=SceneCensusEvidence(
            metadata_present=True,
            complete=True,
            scene_coverage_complete=True,
            authoritative_absence_eligible=True,
            priority_absence_eligible=True,
        ),
        equipment=(
            equipped
            if equipped is not None
            else equipment(BRONZE_PICKAXE_ID)
        ),
    )


class GatheringPlatformTests(unittest.TestCase):
    def test_unknown_equipment_waits_without_an_input_proposal(self) -> None:
        task = GatherBankTask(mining_binding())

        decision = task.decide(
            observation(
                equipped=equipment(known=False),
                objects=(resource_object(),),
            )
        )

        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertIn("equipment is not observable", decision.reason)
        self.assertEqual(TaskPhase.FIND_RESOURCE, task.progress.phase)
        self.assertIsNone(task.progress.pending)

    def test_known_missing_required_pickaxe_blocks(self) -> None:
        task = GatherBankTask(mining_binding())

        decision = task.decide(
            observation(equipped=equipment(), objects=(resource_object(),))
        )

        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIn("required equipment", decision.reason)
        self.assertIsNone(task.progress.pending)

    def test_exact_copper_rock_uses_shared_interaction_and_verification_path(self) -> None:
        mining_task = GatherBankTask(mining_binding())
        rock = resource_object()

        selected = mining_task.decide(observation(objects=(rock,), tick=10))
        proposed = mining_task.decide(observation(objects=(rock,), tick=11))

        self.assertEqual(ActionKind.WAIT, selected.action.kind)
        self.assertEqual(TaskPhase.VERIFY_YIELD, mining_task.progress.phase)
        self.assertEqual(ActionKind.INTERACT_OBJECT, proposed.action.kind)
        self.assertEqual("Mine", proposed.action.option)
        self.assertEqual(COPPER_ROCK_ID, proposed.action.target_id)
        self.assertEqual(rock.key, proposed.action.target_key)
        self.assertEqual(SCREEN_POINT, proposed.action.screen_point)
        self.assertIsNotNone(proposed.action.verification)
        assert proposed.action.verification is not None
        self.assertEqual(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            proposed.action.verification.kind,
        )
        self.assertEqual(COPPER_ORE_ID, proposed.action.verification.item_id)
        self.assertEqual(0, proposed.action.verification.before_quantity)

        woodcut_task = GatherBankTask(woodcut_binding())
        tree = resource_object(WOODCUT)
        woodcut_task.decide(
            observation(
                location=WOODCUT.resource.work_area.anchor,
                equipped=equipment(),
                objects=(tree,),
                tick=20,
            )
        )
        woodcut_proposal = woodcut_task.decide(
            observation(
                location=WOODCUT.resource.work_area.anchor,
                equipped=equipment(),
                objects=(tree,),
                tick=21,
            )
        )
        self.assertEqual(proposed.action.kind, woodcut_proposal.action.kind)
        assert woodcut_proposal.action.verification is not None
        self.assertEqual(
            proposed.action.verification.kind,
            woodcut_proposal.action.verification.kind,
        )

    def test_wrong_identity_action_plane_and_inventory_evidence_fail_closed(self) -> None:
        mismatches = (
            resource_object(object_id=10079),
            resource_object(name="Copper rocks"),
            resource_object(action="Prospect"),
        )
        for candidate in mismatches:
            with self.subTest(candidate=candidate.key):
                task = GatherBankTask(mining_binding())
                decision = task.decide(observation(objects=(candidate,)))
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
                self.assertIsNone(task.progress.pending)
                self.assertNotEqual(TaskPhase.VERIFY_YIELD, task.progress.phase)

        wrong_plane = WorldPoint(
            MINING.resource.work_area.anchor.x,
            MINING.resource.work_area.anchor.y,
            1,
        )
        plane_task = GatherBankTask(mining_binding())
        plane_decision = plane_task.decide(
            observation(
                location=wrong_plane,
                objects=(
                    resource_object(
                        location=WorldPoint(wrong_plane.x + 1, wrong_plane.y, 1)
                    ),
                ),
            )
        )
        self.assertEqual(ActionKind.WAIT, plane_decision.action.kind)
        self.assertEqual(TaskPhase.BLOCKED, plane_task.progress.phase)
        self.assertIn("outside the supported work area", plane_decision.reason)

        contradictory = inventory(
            occupied_slots=28,
            free_slots=0,
        )
        inventory_task = GatherBankTask(mining_binding())
        inventory_decision = inventory_task.decide(
            observation(inv=contradictory, objects=(resource_object(),))
        )
        self.assertEqual(ActionKind.WAIT, inventory_decision.action.kind)
        self.assertEqual(TaskPhase.BLOCKED, inventory_task.progress.phase)
        self.assertIn("bank threshold without", inventory_decision.reason)

        disallowed = inventory(
            InventoryItem(slot=0, item_id=1511, quantity=1, name="Logs")
        )
        disallowed_task = GatherBankTask(mining_binding())
        disallowed_decision = disallowed_task.decide(
            observation(inv=disallowed, objects=(resource_object(),))
        )
        self.assertEqual(ActionKind.WAIT, disallowed_decision.action.kind)
        self.assertEqual(TaskPhase.BLOCKED, disallowed_task.progress.phase)
        self.assertIn("inventory violates", disallowed_decision.reason)

    def test_minimal_mining_replay_records_exact_verified_quantity(self) -> None:
        task = GatherBankTask(
            mining_binding(cycle_goal=5, item_quantity_goal=2)
        )
        rock = resource_object()

        first = task.decide(observation(objects=(rock,), tick=30))
        second = task.decide(observation(objects=(rock,), tick=31))
        self.assertEqual(ActionKind.WAIT, first.action.kind)
        self.assertEqual(ActionKind.INTERACT_OBJECT, second.action.kind)

        task.apply_verification(
            VerificationResult(
                VerificationStatus.PASS,
                "copper quantity increased by exact verified delta",
                Outcome(
                    OutcomeKind.ITEM_QUANTITY_INCREASED,
                    observed_tick=32,
                    item_id=COPPER_ORE_ID,
                    item_quantity_delta=2,
                ),
            )
        )

        self.assertEqual(2, task.progress.items_gathered)
        self.assertEqual(TaskPhase.FIND_RESOURCE, task.progress.phase)
        metrics = {metric.label: metric for metric in task.snapshot().metrics}
        self.assertEqual(2, metrics["items_gathered"].current)
        self.assertEqual(2, metrics["items_gathered"].total)

        completed = task.decide(
            observation(
                inv=inventory(
                    InventoryItem(
                        slot=0,
                        item_id=COPPER_ORE_ID,
                        quantity=2,
                        name="Copper ore",
                    )
                ),
                objects=(rock,),
                tick=33,
            )
        )
        self.assertEqual(ActionKind.WAIT, completed.action.kind)
        self.assertEqual(TaskPhase.COMPLETE, task.progress.phase)
        self.assertIn("item quantity goal", completed.reason)

    def test_scheduled_start_waits_and_composed_stop_conditions_are_independent(self) -> None:
        scheduled = GatherBankTask(
            mining_binding(start_at_utc=BASE_TIME + timedelta(minutes=5))
        )
        early = scheduled.decide(observation(objects=(resource_object(),)))
        self.assertEqual(ActionKind.WAIT, early.action.kind)
        self.assertIn("scheduled start", early.reason)
        self.assertIsNone(scheduled._run_started_at_utc)

        binding = mining_binding(
            cycle_goal=2,
            item_quantity_goal=3,
            stop_when_inventory_full=True,
        )
        self.assertEqual(
            frozenset(
                {
                    StopConditionKind.CYCLES,
                    StopConditionKind.ITEM_QUANTITY,
                    StopConditionKind.INVENTORY_FULL,
                }
            ),
            binding.profile.stop_conditions,
        )

        cycle_task = GatherBankTask(binding)
        cycle_task.progress.cycles_completed = 2
        cycle_stop = cycle_task.decide(observation(objects=(resource_object(),)))
        self.assertEqual(TaskPhase.COMPLETE, cycle_task.progress.phase)
        self.assertIn("cycle goal", cycle_stop.reason)

        quantity_task = GatherBankTask(binding)
        quantity_task.progress.items_gathered = 3
        quantity_stop = quantity_task.decide(
            observation(objects=(resource_object(),), tick=11)
        )
        self.assertEqual(TaskPhase.COMPLETE, quantity_task.progress.phase)
        self.assertIn("item quantity goal", quantity_stop.reason)

        full_items = tuple(
            InventoryItem(
                slot=slot,
                item_id=COPPER_ORE_ID,
                quantity=1,
                name="Copper ore",
            )
            for slot in range(28)
        )
        full_task = GatherBankTask(binding)
        full_stop = full_task.decide(
            observation(
                inv=inventory(*full_items, occupied_slots=28, free_slots=0),
                objects=(resource_object(),),
                tick=12,
            )
        )
        self.assertEqual(TaskPhase.COMPLETE, full_task.progress.phase)
        self.assertIn("inventory-full goal", full_stop.reason)

    def test_restart_reconciliation_accepts_known_route_state_only_when_enabled(self) -> None:
        bank_location = MINING.bank.anchor
        bank_state = observation(
            location=bank_location,
            widgets=WidgetObservation(bank_known=True, bank_open=False),
        )

        enabled = GatherBankTask(mining_binding(reconcile_on_start=True))
        enabled_decision = enabled.decide(bank_state)
        self.assertEqual(ActionKind.WAIT, enabled_decision.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_RESOURCE, enabled.progress.phase)
        self.assertEqual(
            "fresh_observation_reconciled",
            enabled.snapshot().lifecycle.reconciliation_status,
        )
        self.assertIn("return-route start", enabled_decision.reason)

        disabled = GatherBankTask(mining_binding(reconcile_on_start=False))
        disabled_decision = disabled.decide(bank_state)
        self.assertEqual(ActionKind.WAIT, disabled_decision.action.kind)
        self.assertEqual(TaskPhase.BLOCKED, disabled.progress.phase)
        self.assertEqual(
            "disabled_start_state_rejected",
            disabled.snapshot().lifecycle.reconciliation_status,
        )
        self.assertIn("reconciliation is disabled", disabled_decision.reason)

    def test_catalog_has_woodcut_and_mining_through_one_task(self) -> None:
        definitions = list_builtin_definitions()
        definition_ids = {definition.definition_id for definition in definitions}
        self.assertEqual(
            {WOODCUT.definition_id, MINING.definition_id},
            definition_ids,
        )

        tasks = EngineApplication.list_tasks()
        self.assertEqual(1, len(tasks))
        self.assertEqual(GATHER_BANK_TASK_ID, tasks[0].task_id)
        self.assertEqual(definition_ids, set(tasks[0].definition_ids))
        self.assertIs(WoodcutBankTask, GatherBankTask)
        self.assertIsInstance(GatherBankTask(woodcut_binding()), GatherBankTask)
        self.assertIsInstance(GatherBankTask(mining_binding()), GatherBankTask)


if __name__ == "__main__":
    unittest.main()

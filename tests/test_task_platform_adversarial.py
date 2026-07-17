from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from osrs_bot.definition import (
    LUMBRIDGE_SWAMP_COPPER_V1,
    TargetPolicy,
    TaskCapability,
    TaskType,
)
from osrs_bot.model import (
    CLOSE_BANK_WIDGET_KEY,
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
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.profile import BoundProfile, Profile
from osrs_bot.task import GatherBankTask, TaskPhase
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationResult,
    VerificationStatus,
)


DEFINITION = LUMBRIDGE_SWAMP_COPPER_V1
RESOURCE_ID = min(DEFINITION.resource.selector.object_ids)
ORE_ID = next(iter(DEFINITION.resource.produced_item_ids))
PICKAXE_ID = min(DEFINITION.equipment.required_any_of_item_ids)
BASE_TIME = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
CANVAS = ScreenBounds(1000, 2000, 800, 600)


def binding(definition=DEFINITION, **overrides: object) -> BoundProfile:
    values: dict[str, object] = {
        "profile_id": "adversarial_mining",
        "definition_id": definition.definition_id,
        "cycle_goal": 2,
    }
    values.update(overrides)
    return BoundProfile(Profile(**values), definition)  # type: ignore[arg-type]


def inventory(*, ore: int = 0, full: bool = False) -> InventoryObservation:
    items = (
        (InventoryItem(slot=0, item_id=ORE_ID, quantity=ore, name="Copper ore"),)
        if ore
        else ()
    )
    occupied = 28 if full else len(items)
    return InventoryObservation(
        items=items,
        occupied_slots=occupied,
        free_slots=0 if full else 28 - occupied,
        known=True,
    )


def equipped_pickaxe() -> EquipmentObservation:
    return EquipmentObservation(
        items=(InventoryItem(0, PICKAXE_ID, 1, "Pickaxe"),),
        occupied_slots=1,
        free_slots=13,
        known=True,
    )


def geometry(index: int = 0) -> TargetGeometry:
    point = ScreenPoint(
        1080 + 140 * (index % 6),
        2100 + 120 * (index % 4),
    )
    return TargetGeometry(
        available=True,
        on_screen=True,
        visible=True,
        actionable=True,
        canvas_point=ScreenPoint(point.x - CANVAS.x, point.y - CANVAS.y),
        screen_point=point,
    )


def resource(
    key: str,
    *,
    offset: int = 1,
    target_geometry: TargetGeometry | None = None,
    location: WorldPoint | None = None,
) -> NearbyObject:
    anchor = DEFINITION.resource.work_area.anchor
    resolved_location = location or WorldPoint(
        anchor.x + offset,
        anchor.y,
        anchor.plane,
    )
    return NearbyObject(
        key=key,
        object_id=RESOURCE_ID,
        name=DEFINITION.resource.selector.name,
        kind="GAME_OBJECT",
        actions=(DEFINITION.resource.selector.action,),
        location=resolved_location,
        distance=offset,
        geometry=target_geometry or geometry(offset),
        scene_x=48 + offset,
        scene_y=52,
    )


def bank_object() -> NearbyObject:
    selector = DEFINITION.bank.selector
    return NearbyObject(
        key="bank:exact-lumbridge-booth",
        object_id=min(selector.object_ids),
        name=selector.name,
        kind="GAME_OBJECT",
        actions=(selector.action,),
        location=DEFINITION.bank.anchor,
        distance=1,
        geometry=geometry(),
        scene_x=50,
        scene_y=50,
    )


def complete_census() -> SceneCensusEvidence:
    return SceneCensusEvidence(
        metadata_present=True,
        complete=True,
        authoritative_absence_eligible=True,
        priority_absence_eligible=True,
        scene_coverage_complete=True,
    )


def incomplete_census() -> SceneCensusEvidence:
    return SceneCensusEvidence(
        metadata_present=True,
        complete=False,
        authoritative_absence_eligible=False,
        priority_absence_eligible=False,
        scene_coverage_complete=False,
        response_cap_hit=True,
    )


def bank_widgets(*, open: bool = False) -> WidgetObservation:
    return WidgetObservation(
        bank_known=True,
        bank_open=open,
        bank_readable=open,
        keyboard_close_possible=open,
        close_bank=(
            WidgetTarget(CLOSE_BANK_WIDGET_KEY, True, ScreenPoint(1500, 2250))
            if open
            else None
        ),
    )


def observation(
    *,
    location: WorldPoint | None = None,
    inv: InventoryObservation | None = None,
    objects: tuple[NearbyObject, ...] = (),
    widgets: WidgetObservation | None = None,
    census: SceneCensusEvidence | None = None,
    equipment_fact: EquipmentObservation | None = None,
    tick: int = 10,
    timestamp: datetime = BASE_TIME,
) -> Observation:
    resolved_location = location or DEFINITION.resource.work_area.anchor
    frame_id = f"adversarial-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=resolved_location,
        plane=resolved_location.plane,
        inventory=inv if inv is not None else inventory(),
        nearby_objects=objects,
        menus=(),
        widgets=widgets or bank_widgets(),
        canvas_bounds=CANVAS,
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id="adversarial-session",
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
        menu_session_id="adversarial-session",
        menu_process_id=2468,
        camera_yaw=0,
        camera_pitch=1024,
        scene_census=census or complete_census(),
        equipment=(
            equipped_pickaxe() if equipment_fact is None else equipment_fact
        ),
    )


def verification_pass(
    kind: OutcomeKind,
    *,
    tick: int,
    item_delta: int | None = None,
) -> VerificationResult:
    outcome = (
        Outcome(
            kind,
            tick,
            item_id=ORE_ID,
            item_quantity_delta=item_delta,
        )
        if item_delta is not None
        else Outcome(kind, tick)
    )
    return VerificationResult(VerificationStatus.PASS, "verified", outcome)


class TaskPlatformAdversarialTests(unittest.TestCase):
    def test_bank_unavailable_wait_is_bounded_and_exact_return_resets_budget(self) -> None:
        exhausted = GatherBankTask(binding())
        exhausted.progress.phase = TaskPhase.OPEN_BANK

        first = exhausted.decide(
            observation(location=DEFINITION.bank.anchor, inv=inventory(ore=1), tick=10)
        )
        second = exhausted.decide(
            observation(location=DEFINITION.bank.anchor, inv=inventory(ore=1), tick=11)
        )
        terminal = exhausted.decide(
            observation(location=DEFINITION.bank.anchor, inv=inventory(ore=1), tick=12)
        )

        self.assertEqual(ActionKind.WAIT, first.action.kind)
        self.assertIn("bounded re-observation 1/2", first.reason)
        self.assertEqual(ActionKind.WAIT, second.action.kind)
        self.assertIn("bounded re-observation 2/2", second.reason)
        self.assertEqual(TaskPhase.BLOCKED, exhausted.progress.phase)
        self.assertIn("remained unavailable", terminal.reason)
        self.assertIsNone(exhausted.progress.pending)

        recovered = GatherBankTask(binding())
        recovered.progress.phase = TaskPhase.OPEN_BANK
        recovered.decide(
            observation(location=DEFINITION.bank.anchor, inv=inventory(ore=1), tick=20)
        )
        exact = recovered.decide(
            observation(
                location=DEFINITION.bank.anchor,
                inv=inventory(ore=1),
                objects=(bank_object(),),
                tick=21,
            )
        )

        self.assertEqual(ActionKind.INTERACT_OBJECT, exact.action.kind)
        self.assertEqual(VerificationKind.INTERFACE_OPENED, exact.action.verification.kind)
        self.assertEqual(0, recovered._bank_unavailable_frames)
        self.assertIsNot(TaskPhase.BLOCKED, recovered.progress.phase)

    def test_mining_restart_matrix_never_awards_unverified_cycle_credit(self) -> None:
        full_at_resource_anchor = GatherBankTask(binding())
        full_at_resource_anchor.decide(
            observation(inv=inventory(ore=28, full=True), tick=9)
        )
        self.assertTrue(
            full_at_resource_anchor._restart_reconciled_without_cycle_credit
        )
        full_at_resource_anchor.progress.phase = TaskPhase.NAVIGATE_TO_RESOURCE
        full_at_resource_anchor._finish_route()
        self.assertEqual(0, full_at_resource_anchor.progress.cycles_completed)
        self.assertFalse(
            full_at_resource_anchor._restart_reconciled_without_cycle_credit
        )

        route_restart = GatherBankTask(binding())
        bank_route_location = DEFINITION.route_to_bank.steps[5].location
        resumed = route_restart.decide(
            observation(
                location=bank_route_location,
                inv=inventory(ore=28, full=True),
            )
        )
        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, route_restart.progress.phase)
        self.assertTrue(route_restart._restart_reconciled_without_cycle_credit)
        self.assertEqual(0, route_restart.progress.cycles_completed)
        self.assertEqual(0, route_restart.progress.inventories_banked)

        open_with_full_inventory = GatherBankTask(binding())
        open_with_full_inventory.decide(
            observation(
                location=DEFINITION.bank.anchor,
                inv=inventory(ore=28, full=True),
                widgets=bank_widgets(open=True),
                tick=20,
            )
        )
        self.assertEqual(
            TaskPhase.NAVIGATE_TO_BANK,
            open_with_full_inventory.progress.phase,
        )
        self.assertEqual(0, open_with_full_inventory.progress.cycles_completed)
        self.assertEqual(0, open_with_full_inventory.progress.inventories_banked)

        empty_deposit_restart = GatherBankTask(binding())
        initial = empty_deposit_restart.decide(
            observation(
                location=DEFINITION.bank.anchor,
                inv=inventory(),
                widgets=bank_widgets(open=True),
                tick=30,
            )
        )
        close = empty_deposit_restart.decide(
            observation(
                location=DEFINITION.bank.anchor,
                inv=inventory(),
                widgets=bank_widgets(open=True),
                tick=31,
            )
        )
        self.assertEqual(ActionKind.WAIT, initial.action.kind)
        self.assertEqual(ActionKind.CLICK_WIDGET, close.action.kind)
        self.assertEqual(VerificationKind.INTERFACE_CLOSED, close.action.verification.kind)
        self.assertTrue(
            empty_deposit_restart._restart_reconciled_without_cycle_credit
        )

        empty_deposit_restart.apply_verification(
            verification_pass(OutcomeKind.INTERFACE_CLOSED, tick=32)
        )
        empty_deposit_restart.progress.route_index = len(
            DEFINITION.route_to_resource.steps
        )
        empty_deposit_restart.decide(
            observation(
                location=DEFINITION.resource.work_area.anchor,
                inv=inventory(),
                tick=33,
            )
        )
        self.assertEqual(TaskPhase.FIND_RESOURCE, empty_deposit_restart.progress.phase)
        self.assertEqual(0, empty_deposit_restart.progress.cycles_completed)
        self.assertEqual(0, empty_deposit_restart.progress.inventories_banked)
        self.assertFalse(
            empty_deposit_restart._restart_reconciled_without_cycle_credit
        )

        disabled = GatherBankTask(binding(reconcile_on_start=False))
        overlapping_return_step = DEFINITION.route_to_resource.steps[-2].location
        disabled.decide(
            observation(
                location=overlapping_return_step,
                inv=inventory(),
                tick=34,
            )
        )
        self.assertEqual(TaskPhase.FIND_RESOURCE, disabled.progress.phase)
        self.assertEqual(
            "disabled_clean_start_confirmed",
            disabled.snapshot().lifecycle.reconciliation_status,
        )

    def test_reached_stop_closes_open_bank_despite_unknown_inventory_and_equipment(self) -> None:
        task = GatherBankTask(binding(stop_at_utc=BASE_TIME))

        decision = task.decide(
            observation(
                location=DEFINITION.bank.anchor,
                inv=InventoryObservation(),
                equipment_fact=EquipmentObservation(),
                widgets=bank_widgets(open=True),
                tick=35,
                timestamp=BASE_TIME,
            )
        )

        self.assertEqual(ActionKind.CLICK_WIDGET, decision.action.kind)
        self.assertEqual(VerificationKind.INTERFACE_CLOSED, decision.action.verification.kind)
        self.assertTrue(task._stop_after_bank_close)

    def test_stop_condition_waits_for_pending_causal_verification(self) -> None:
        stop_at = BASE_TIME + timedelta(seconds=1)
        task = GatherBankTask(binding(stop_at_utc=stop_at))
        rock = resource("rock:stop-race")

        task.decide(observation(objects=(rock,), tick=40, timestamp=BASE_TIME))
        proposed = task.decide(
            observation(
                objects=(rock,),
                tick=41,
                timestamp=BASE_TIME + timedelta(milliseconds=500),
            )
        )
        pending = task.progress.pending
        raced = task.decide(
            observation(
                objects=(rock,),
                tick=42,
                timestamp=BASE_TIME + timedelta(seconds=2),
            )
        )

        self.assertEqual(ActionKind.INTERACT_OBJECT, proposed.action.kind)
        self.assertIsNotNone(pending)
        self.assertIs(pending, task.progress.pending)
        self.assertEqual(TaskPhase.VERIFY_YIELD, task.progress.phase)
        self.assertIn("waiting for external action verification", raced.reason)
        self.assertIsNone(task.progress.completion_reason)

        task.apply_verification(
            verification_pass(
                OutcomeKind.ITEM_QUANTITY_INCREASED,
                tick=42,
                item_delta=1,
            )
        )
        stopped = task.decide(
            observation(
                inv=inventory(ore=1),
                tick=43,
                timestamp=BASE_TIME + timedelta(seconds=2),
            )
        )
        self.assertEqual(1, task.progress.items_gathered)
        self.assertEqual(TaskPhase.COMPLETE, task.progress.phase)
        self.assertIn("absolute stop time", stopped.reason)

    def test_target_policy_caps_rank_and_diagnostic_work(self) -> None:
        capped_definition = replace(
            DEFINITION,
            target_policy=TargetPolicy(
                max_candidates=2,
                max_rejection_evidence=3,
                incomplete_omission_wait_frames=1,
                query_radius_tiles=4,
            ),
        )
        task = GatherBankTask(binding(capped_definition))
        rocks = tuple(resource(f"rock:cap:{index}", offset=index + 1) for index in range(6))

        selected = task.decide(observation(objects=rocks, tick=50))
        metrics = task.last_resource_selection_metrics

        self.assertEqual(2, len(selected.evidence.eligible))
        self.assertEqual(3, len(selected.evidence.rejected))
        self.assertTrue(
            all(
                rejected.rejection_codes == ("candidate_budget_exceeded",)
                for rejected in selected.evidence.rejected
            )
        )
        self.assertEqual(
            {
                "scene_objects": 6,
                "indexed_candidates": 6,
                "identity_evaluations": 6,
                "ambiguity_queries": 6,
                "ranked_candidates": 2,
                "rejection_evidence": 3,
            },
            metrics,
        )

    def test_fresh_geometry_can_change_but_immutable_identity_cannot(self) -> None:
        stable = resource("rock:stable", offset=2, target_geometry=geometry(0))
        competitor = resource("rock:closer", offset=1, target_geometry=geometry(1))
        task = GatherBankTask(binding())
        task.decide(observation(objects=(stable,), tick=60))

        moved_geometry = replace(stable, geometry=geometry(4))
        activated = task.decide(
            observation(objects=(competitor, moved_geometry), tick=61)
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, activated.action.kind)
        self.assertEqual(stable.key, activated.action.target_key)
        self.assertEqual(moved_geometry.geometry.screen_point, activated.action.screen_point)

        contradictory_task = GatherBankTask(binding())
        contradictory_task.decide(observation(objects=(stable,), tick=70))
        moved_identity = replace(
            stable,
            location=WorldPoint(
                stable.location.x + 1,
                stable.location.y,
                stable.location.plane,
            ),
            geometry=geometry(5),
        )
        blocked = contradictory_task.decide(
            observation(objects=(moved_identity,), tick=71)
        )
        self.assertEqual(TaskPhase.BLOCKED, contradictory_task.progress.phase)
        self.assertIn("contradictory immutable identity", blocked.reason)
        self.assertIsNone(contradictory_task.progress.pending)

    def test_incomplete_census_continuity_budget_is_definition_bounded(self) -> None:
        bounded_definition = replace(
            DEFINITION,
            target_policy=replace(
                DEFINITION.target_policy,
                incomplete_omission_wait_frames=1,
            ),
            recovery=replace(
                DEFINITION.recovery,
                max_target_incomplete_frames=1,
            ),
        )
        task = GatherBankTask(binding(bounded_definition))
        locked = resource("rock:bounded-omission")
        task.decide(observation(objects=(locked,), tick=80))

        retained = task.decide(
            observation(objects=(), census=incomplete_census(), tick=81)
        )
        exhausted = task.decide(
            observation(objects=(), census=incomplete_census(), tick=82)
        )

        self.assertEqual(TaskPhase.INTERACT_RESOURCE, task.progress.blocked_from_phase)
        self.assertIn("continuity frame 1/1", retained.reason)
        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIn("wait budget", exhausted.reason)
        self.assertEqual(
            ("target_omission_wait_exhausted",),
            exhausted.evidence.rejected[0].rejection_codes,
        )
        self.assertEqual(locked.key, task.snapshot().target_continuity.locked_target_key)

    def test_future_capability_families_fail_binding_with_exact_diagnostics(self) -> None:
        cases = (
            ("fallback", TaskType.GATHERING, (TaskCapability.FALLBACK_BANKS,)),
            ("withdrawal", TaskType.GATHERING, (TaskCapability.BANK_WITHDRAWAL,)),
            ("npc", TaskType.GATHERING, (TaskCapability.NPC_INTERACTION_GEOMETRY,)),
            (
                "combat",
                TaskType.COMBAT,
                (
                    TaskCapability.COMBAT_STATE_OBSERVATION,
                    TaskCapability.COMBAT_TARGETING,
                ),
            ),
            (
                "quest",
                TaskType.QUEST,
                (
                    TaskCapability.QUEST_STATE_PROVIDER,
                    TaskCapability.QUEST_STEP_PRECONDITIONS,
                    TaskCapability.QUEST_ITEM_ORCHESTRATION,
                ),
            ),
        )
        for label, task_type, unsupported in cases:
            with self.subTest(label=label):
                definition = replace(
                    DEFINITION,
                    task_type=task_type,
                    capabilities=(
                        DEFINITION.capabilities | frozenset(unsupported)
                    ),
                )
                expected = sorted(item.value for item in unsupported)
                with self.assertRaises(ValueError) as raised:
                    binding(definition, profile_id=f"boundary_{label}")
                if task_type is TaskType.GATHERING:
                    self.assertEqual(
                        f"definition requires unsupported capabilities: {expected}",
                        str(raised.exception),
                    )
                else:
                    self.assertEqual(
                        "runtime supports only gathering task definitions; "
                        f"got task_type {task_type.value!r}",
                        str(raised.exception),
                    )

    def test_scheduled_run_window_cannot_exceed_definition_horizon(self) -> None:
        bounded_definition = replace(
            DEFINITION,
            lifecycle=replace(
                DEFINITION.lifecycle,
                maximum_duration_seconds=30.0,
            ),
        )
        with self.assertRaises(ValueError) as raised:
            binding(
                bounded_definition,
                start_at_utc=BASE_TIME,
                stop_at_utc=BASE_TIME + timedelta(seconds=31),
            )
        self.assertEqual(
            "scheduled run window exceeds the definition's maximum duration",
            str(raised.exception),
        )


if __name__ == "__main__":
    unittest.main()

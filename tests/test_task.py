from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from itertools import permutations

from osrs_bot.behavior import (
    BehaviorConfig,
    BehaviorPolicy,
    CameraFramingDecision,
    point_in_polygon,
)
from osrs_bot.camera import desired_camera_yaw
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
    SceneCensusEvidence,
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
from osrs_bot.task_contract import CameraAcquisitionState
from osrs_bot.verification import (
    CameraPoseResult,
    Outcome,
    OutcomeKind,
    VerificationFailureKind,
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
MOVEMENT_DEADLINE_TICKS = DEFINITION.verification.movement_deadline_ticks
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


def camera_verification_pass(
    *,
    direction: str,
    before_yaw: int,
    after_yaw: int,
    before_pitch: int = 1024,
    after_pitch: int = 1024,
    before_geometry_frame_id: str,
    after_geometry_frame_id: str,
    tick: int,
) -> VerificationResult:
    yaw_delta = (
        after_yaw - before_yaw + 16_384 // 2
    ) % 16_384 - 16_384 // 2
    return VerificationResult(
        VerificationStatus.PASS,
        "camera_pose_changed",
        Outcome(
            OutcomeKind.CAMERA_POSE_CHANGED,
            tick,
            CameraPoseResult(
                camera_key=direction,
                before_yaw=before_yaw,
                after_yaw=after_yaw,
                yaw_delta=yaw_delta,
                before_pitch=before_pitch,
                after_pitch=after_pitch,
                pitch_delta=after_pitch - before_pitch,
                before_geometry_frame_id=before_geometry_frame_id,
                after_geometry_frame_id=after_geometry_frame_id,
            ),
        ),
    )


def verification_fail(
    reason: str,
    failure_kind: VerificationFailureKind = VerificationFailureKind.RUNTIME_FAILURE,
) -> VerificationResult:
    return VerificationResult(
        VerificationStatus.FAIL,
        reason,
        failure_kind=failure_kind,
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
NAV_GEOMETRY = replace(
    GEOMETRY,
    scene_supported=True,
    collision_supported=True,
    shortcut_clear=True,
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
    camera_yaw: int | None = 0,
    camera_pitch: int | None = 1024,
    camera_zoom: int | None = None,
    geometry_frame_id: str | None = None,
    viewport_bounds: ScreenBounds | None = None,
    client_window_bounds: ScreenBounds | None = None,
    text_input_active: bool | None = None,
    scene_census: SceneCensusEvidence | None = None,
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
        viewport_bounds=viewport_bounds,
        client_window_bounds=client_window_bounds,
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
        geometry_frame_id=geometry_frame_id or frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=process_id,
        camera_yaw=camera_yaw,
        camera_pitch=camera_pitch,
        camera_zoom=camera_zoom,
        text_input_active=text_input_active,
        scene_census=(
            scene_census
            if scene_census is not None
            else SceneCensusEvidence(
                metadata_present=True,
                complete=True,
                scene_coverage_complete=True,
                authoritative_absence_eligible=True,
                priority_absence_eligible=True,
            )
        ),
    )


def scene_object(
    key: str,
    object_id: int,
    name: str,
    action: str,
    location: WorldPoint,
    *,
    geometry: TargetGeometry = GEOMETRY,
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
    )


def tree(**overrides: object) -> NearbyObject:
    values = {
        "key": f"resource:{TREE_OBJECT_ID}",
        "object_id": TREE_OBJECT_ID,
        "name": DEFINITION.resource.selector.name,
        "action": DEFINITION.resource.selector.action,
        "location": WorldPoint(3195, 3248, 0),
        "geometry": GEOMETRY,
    }
    values.update(overrides)
    return scene_object(**values)


def route_tile(step, *, geometry: TargetGeometry = GEOMETRY) -> NearbyObject:
    return NearbyObject(
        key=step.target_key,
        object_id=0,
        name=step.target_key,
        kind="NAVIGATION_TILE",
        actions=("Walk here",),
        location=step.location,
        distance=8,
        geometry=geometry,
        scene_x=49,
        scene_y=52,
    )


def route_object(step, **overrides: object) -> NearbyObject:
    values = {
        "key": f"live:{step.step_id}",
        "object_id": step.object_id,
        "name": step.object_name,
        "action": step.action,
        "location": step.location,
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
        self.assertIs(snapshot.progress, snapshot.cycle_progress)
        self.assertIsNone(snapshot.route_step)
        self.assertIsNone(snapshot.route_progress)

        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 2
        route_snapshot = task.snapshot()
        self.assertEqual(DEFINITION.route_to_bank.route_id, route_snapshot.progress.label)
        self.assertEqual(2, route_snapshot.progress.current)
        self.assertEqual(len(ROUTE_TO_BANK), route_snapshot.progress.total)
        self.assertIs(route_snapshot.progress, route_snapshot.route_progress)
        self.assertEqual(ROUTE_TO_BANK[2].step_id, route_snapshot.route_step)
        self.assertEqual("cycles", route_snapshot.cycle_progress.label)
        self.assertEqual(0, route_snapshot.cycle_progress.current)
        self.assertEqual(
            DEFAULT_BINDING.profile.cycle_goal,
            route_snapshot.cycle_progress.total,
        )

    def test_find_tree_requires_exact_identity_but_defers_geometry_to_camera_acquisition(self) -> None:
        invalid_geometry = TargetGeometry(
            available=True, on_screen=True, visible=True, actionable=True
        )
        point_outside_bounds = replace(
            GEOMETRY,
            screen_bounds=ScreenBounds(1000, 2000, 10, 10),
        )
        identity_mismatches = (
            tree(object_id=1278),
            tree(name="Oak tree"),
            tree(action="Chop"),
        )
        for candidate in identity_mismatches:
            with self.subTest(candidate=candidate):
                task = WoodcutBankTask()
                decision = task.decide(observation(objects=(candidate,)))
                self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
                self.assertEqual(ActionKind.WAIT, decision.action.kind)

        for geometry in (invalid_geometry, point_outside_bounds):
            with self.subTest(geometry=geometry):
                task = WoodcutBankTask()
                candidate = tree(geometry=geometry)
                decision = task.decide(observation(objects=(candidate,)))
                self.assertEqual(TaskPhase.CHOP, task.progress.phase)
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
                followup = task.decide(observation(objects=(candidate,), tick=11))
                self.assertEqual(ActionKind.WAIT, followup.action.kind)
                self.assertIn("fresh actionable resource geometry", followup.reason)

        task = WoodcutBankTask()
        decision = task.decide(observation(objects=(tree(),)))
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertEqual(tree().key, task.progress.target_key)

    def test_find_tree_prefers_action_ready_framing_over_nearer_reframe(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        closer_edge_polygon = (
            ScreenPoint(1004, 2180),
            ScreenPoint(1079, 2180),
            ScreenPoint(1079, 2299),
            ScreenPoint(1004, 2299),
        )
        closer_edge = tree(
            key="tree:near-edge",
            location=WorldPoint(TREE_AREA.x + 1, TREE_AREA.y, TREE_AREA.plane),
            geometry=TargetGeometry(
                available=True,
                on_screen=True,
                visible=True,
                actionable=True,
                screen_point=ScreenPoint(1012, 2240),
                screen_bounds=ScreenBounds(1004, 2180, 76, 120),
                geometry_source="clickbox",
                screen_polygon=closer_edge_polygon,
                visible_area_ratio=1.0,
            ),
        )
        farther_ready_polygon = (
            ScreenPoint(1240, 2150),
            ScreenPoint(1399, 2150),
            ScreenPoint(1399, 2359),
            ScreenPoint(1240, 2359),
        )
        farther_ready = tree(
            key="tree:farther-ready",
            location=WorldPoint(TREE_AREA.x + 4, TREE_AREA.y, TREE_AREA.plane),
            geometry=TargetGeometry(
                available=True,
                on_screen=True,
                visible=True,
                actionable=True,
                screen_point=ScreenPoint(1320, 2250),
                screen_bounds=ScreenBounds(1240, 2150, 160, 210),
                geometry_source="clickbox",
                screen_polygon=farther_ready_polygon,
                visible_area_ratio=1.0,
            ),
        )

        task = WoodcutBankTask()
        selected = task.decide(
            observation(
                objects=(closer_edge, farther_ready),
                viewport_bounds=viewport,
                geometry_frame_id="rank-ready-over-near-edge",
            )
        )

        self.assertEqual(farther_ready.key, task.progress.target_key)
        self.assertEqual(farther_ready.key, selected.evidence.selected.key)
        self.assertEqual(
            (farther_ready.key, closer_edge.key),
            tuple(item.key for item in selected.evidence.eligible),
        )

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
        self.assertEqual(bad.location, rejected.target.world_location)
        self.assertEqual(bad.distance, rejected.target.distance)
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

    def test_polygon_tree_uses_safe_area_when_canonical_point_overlaps(self) -> None:
        primary_polygon = (
            ScreenPoint(1200, 2100),
            ScreenPoint(1420, 2100),
            ScreenPoint(1420, 2320),
            ScreenPoint(1200, 2320),
        )
        primary = tree(
            key="tree:a-primary",
            geometry=TargetGeometry(
                available=True,
                on_screen=True,
                visible=True,
                actionable=True,
                screen_point=ScreenPoint(1300, 2200),
                screen_bounds=ScreenBounds(1200, 2100, 221, 221),
                geometry_source="clickbox",
                screen_polygon=primary_polygon,
                visible_area_ratio=1.0,
            ),
        )
        blocker_bounds = ScreenBounds(1270, 2170, 61, 61)
        blocker = tree(
            key="tree:z-blocker",
            location=WorldPoint(3198, 3248, 0),
            geometry=replace(
                GEOMETRY,
                screen_point=ScreenPoint(1300, 2200),
                screen_bounds=blocker_bounds,
            ),
        )

        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=9127))
        )
        decision = task.decide(observation(objects=(primary, blocker), tick=38))

        self.assertEqual("tree:a-primary", decision.evidence.selected.key)
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = "tree:a-primary"
        chop = task.decide(observation(objects=(primary, blocker), tick=39))
        self.assertEqual(ActionKind.INTERACT_OBJECT, chop.action.kind)
        self.assertTrue(point_in_polygon(chop.action.screen_point, primary_polygon))
        self.assertFalse(blocker_bounds.contains(chop.action.screen_point))
        self.assertIn(
            "competing_target_overlap",
            chop.evidence.targeting.rejected_reasons,
        )

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

    def test_chop_without_safe_inset_point_does_not_orphan_verification_phase(self) -> None:
        polygon = (
            ScreenPoint(1300, 2140),
            ScreenPoint(1499, 2140),
            ScreenPoint(1499, 2359),
            ScreenPoint(1300, 2359),
        )
        shape = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1400, 2250),
            screen_bounds=ScreenBounds(1300, 2140, 200, 220),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=1.0,
        )
        selected = tree(
            key="resource:a-selected",
            geometry=shape,
        )
        # A second exact Tree covering the whole selected clickbox makes every
        # otherwise-valid inset point ambiguous.  This mirrors the live frame
        # where camera recovery exposed geometry but no safe candidate could
        # be emitted.
        competitor = tree(
            key="resource:b-overlapping",
            location=WorldPoint(3196, 3248, 0),
            geometry=shape,
        )
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=2026071311))
        )
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = selected.key

        no_aim = task.decide(
            observation(objects=(selected, competitor), tick=5970)
        )

        self.assertEqual(ActionKind.WAIT, no_aim.action.kind)
        self.assertIn("safe inset aim candidate", no_aim.reason)
        self.assertEqual(TaskPhase.CHOP.value, no_aim.state)
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertIsNone(task.progress.pending)

        # If the final log appears asynchronously while aim selection waits,
        # the fresh inventory observation must select the bank route instead
        # of tripping the orphan-verification invariant.
        filled = task.decide(
            observation(
                inv=inventory(logs=28, full=True),
                objects=(selected, competitor),
                tick=5971,
            )
        )

        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK.value, filled.state)
        self.assertEqual(ActionKind.WAIT, filled.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertIsNone(task.progress.pending)
        self.assertEqual([], task.progress.failures)

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

    def test_one_typed_resource_no_yield_failure_reselects_then_second_blocks(self) -> None:
        task = WoodcutBankTask()
        failed = tree(
            key="resource:no-yield",
            location=WorldPoint(3195, 3248, 0),
        )
        alternate = tree(
            key="resource:alternate-yield",
            location=WorldPoint(3197, 3249, 0),
        )
        state = observation(objects=(failed, alternate), tick=20)
        task.decide(state)
        task.decide(state)

        no_yield = verification_fail(
            "diagnostic resource timeout text",
            VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE,
        )
        task.apply_verification(no_yield)

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual([], task.progress.failures)
        replanned = task.decide(replace(state, tick=21))
        self.assertEqual(alternate.key, task.progress.target_key)
        self.assertEqual(
            ("resource_no_yield",),
            next(
                rejected.rejection_codes
                for rejected in replanned.evidence.rejected
                if rejected.target.key == failed.key
            ),
        )

        task.decide(replace(state, tick=21))
        task.apply_verification(no_yield)

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIn(
            "diagnostic resource timeout text",
            task.progress.failures[-1],
        )

    def test_successful_log_gain_resets_resource_no_yield_retry(self) -> None:
        task = WoodcutBankTask()
        state = observation(objects=(tree(),), tick=20)
        task.decide(state)
        task.decide(state)
        task.apply_verification(
            verification_fail(
                "item_quantity_unchanged_at_deadline",
                VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE,
            )
        )
        task.decide(replace(state, tick=21))
        task.decide(replace(state, tick=22))
        task.decide(replace(state, tick=22))
        task.apply_verification(
            verification_pass(OutcomeKind.ITEM_QUANTITY_INCREASED)
        )

        task.decide(replace(state, tick=23))
        task.decide(replace(state, tick=23))
        task.apply_verification(
            verification_fail(
                "item_quantity_unchanged_at_deadline",
                VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE,
            )
        )

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual([], task.progress.failures)

    def test_unsent_chop_proposal_discards_pending_target_for_fresh_selection(self) -> None:
        task = WoodcutBankTask()
        failed = tree(
            key="resource:failed",
            location=WorldPoint(3195, 3248, 0),
        )
        alternate = tree(
            key="resource:alternate",
            location=WorldPoint(3197, 3249, 0),
        )
        state = observation(objects=(failed, alternate), tick=20)
        task.decide(state)
        task.decide(state)

        task.discard_pending_action("hover target changed before activation")

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIsNone(task.progress.pending)
        self.assertEqual([], task.progress.failures)

        replanned = task.decide(replace(state, tick=21))

        self.assertEqual(TaskPhase.CHOP.value, replanned.state)
        self.assertEqual(alternate.key, task.progress.target_key)
        rejected = {
            candidate.target.key: candidate.rejection_codes
            for candidate in replanned.evidence.rejected
        }
        self.assertEqual(
            ("preactivation_target_invalidated",),
            rejected[failed.key],
        )

    def test_cursor_invalidated_chop_discards_stale_target_then_recognizes_fresh(self) -> None:
        task = WoodcutBankTask()
        selected = tree(key="resource:cursor-retry")
        state = observation(objects=(selected,), tick=20)
        task.decide(state)
        proposal = task.decide(state)
        self.assertEqual(selected.key, proposal.action.target_key)

        task.discard_pending_action(
            "cursor_changed_after_pointer_validation",
            target_invalidated=False,
        )

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIsNone(task.progress.target_key)
        self.assertIsNone(task.progress.pending)
        refreshed = replace(state, tick=21)
        recognized = task.decide(refreshed)
        self.assertEqual(TaskPhase.CHOP.value, recognized.state)
        self.assertEqual(selected.key, task.progress.target_key)
        replanned = task.decide(refreshed)
        self.assertEqual(selected.key, replanned.action.target_key)
        self.assertEqual(TaskPhase.VERIFY_LOGS, task.progress.phase)

    def test_discard_requires_a_pending_action_and_nonempty_reason(self) -> None:
        task = WoodcutBankTask()

        with self.assertRaises(ValueError):
            task.discard_pending_action(" ")
        with self.assertRaises(RuntimeError):
            task.discard_pending_action("target changed")

    def test_walk_request_stops_at_next_barrier_and_current_is_strictly_accepted(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        approach = WorldPoint(3195, 3252, 0)
        self.assertEqual(
            tuple(
                (candidate.target_key, candidate.location)
                for candidate in ROUTE_TO_BANK[:2]
            ),
            task.observation_request().tile_projections,
        )
        first_transition = next(step for step in ROUTE_TO_BANK if not step.is_walk)
        self.assertEqual(
            (first_transition.object_id,),
            task.observation_request().priority_object_ids,
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
        self.assertEqual(
            10 + MOVEMENT_DEADLINE_TICKS,
            task.progress.pending.deadline_tick,
        )
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

    def test_observation_priority_ids_follow_current_definition_context(self) -> None:
        task = WoodcutBankTask()
        self.assertEqual((TREE_OBJECT_ID,), task.observation_request().priority_object_ids)

        task.progress.phase = TaskPhase.OPEN_BANK
        self.assertEqual((BANK_OBJECT_ID,), task.observation_request().priority_object_ids)

        task.progress.phase = TaskPhase.DEPOSIT_LOGS
        self.assertEqual((), task.observation_request().priority_object_ids)

        task.progress.phase = TaskPhase.NAVIGATE_TO_TREES
        first_transition_index = next(
            index for index, step in enumerate(ROUTE_TO_TREES) if not step.is_walk
        )
        first_transition = ROUTE_TO_TREES[first_transition_index]
        self.assertEqual(
            (first_transition.object_id,),
            task.observation_request().priority_object_ids,
        )
        task.progress.phase = TaskPhase.STAIR_DIALOGUE
        task.progress.resume_phase = TaskPhase.NAVIGATE_TO_TREES
        task.progress.route_index = first_transition_index
        self.assertEqual(
            (first_transition.object_id,),
            task.observation_request().priority_object_ids,
        )

    def test_walk_lookahead_requests_through_barrier_and_selects_sixteen_tiles(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=7, route_turn_limit_degrees=85.0)
            )
        )
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 4
        source = ROUTE_TO_BANK[3].location
        requested_steps = ROUTE_TO_BANK[4:10]

        self.assertEqual(
            tuple((step.target_key, step.location) for step in requested_steps),
            task.observation_request().tile_projections,
        )
        self.assertLessEqual(len(task.observation_request().tile_projections), 16)

        decision = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=tuple(
                    route_tile(step, geometry=NAV_GEOMETRY)
                    for step in requested_steps
                ),
            )
        )

        selected = ROUTE_TO_BANK[9]
        self.assertEqual(ActionKind.WALK, decision.action.kind)
        self.assertEqual(selected.target_key, decision.action.target_key)
        self.assertEqual(9, task.progress.route_index)
        self.assertIsNotNone(task.last_route_selection)
        self.assertEqual(9, task.last_route_selection.selected_index)
        self.assertEqual(16.0, task.last_route_selection.requested_tile_distance)
        self.assertEqual((4, 5, 6, 7, 8), task.last_route_selection.skipped_guidance_indices)
        route_evidence = decision.evidence.route
        self.assertIsNotNone(route_evidence)
        self.assertEqual(len(requested_steps), len(route_evidence.projected_route_points))
        self.assertEqual(len(requested_steps), len(route_evidence.projected_route_labels))
        self.assertEqual(SCREEN, route_evidence.selected_screen_point)
        self.assertEqual(5, len(route_evidence.skipped_route_points))
        self.assertGreaterEqual(len(route_evidence.mandatory_route_points), 1)

        task.apply_verification(verification_pass(OutcomeKind.MOVED_CLOSER))
        task.decide(
            observation(
                location=ROUTE_TO_BANK[4].location,
                inv=inventory(logs=28, full=True),
                tick=20,
            )
        )
        self.assertIsNone(task.last_route_actual_progress_delta)
        task.decide(
            observation(
                location=selected.location,
                inv=inventory(logs=28, full=True),
                tick=21,
            )
        )
        self.assertIsNone(task.last_route_actual_progress_delta)
        task.decide(
            observation(
                location=selected.location,
                inv=inventory(logs=28, full=True),
                tick=25,
            )
        )
        self.assertGreater(task.last_route_actual_progress_delta, 12.0)
        self.assertAlmostEqual(
            task.last_route_actual_progress_delta,
            task.last_route_progress.progress_delta,
        )
        self.assertEqual(10, task.progress.route_index)

    def test_route_behavior_config_bounds_request_and_selection(self) -> None:
        config = BehaviorConfig(
            seed=9,
            route_lookahead_points=12,
            route_max_click_tiles=10,
            route_open_click_floor_tiles=8,
            route_corridor_radius_tiles=2.5,
            route_turn_limit_degrees=85.0,
        )
        task = WoodcutBankTask(behavior=BehaviorPolicy(config))
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 4
        source = ROUTE_TO_BANK[3].location
        requested_steps = ROUTE_TO_BANK[4:10]

        limits = task._route_selection_limits()
        self.assertEqual(10.0, limits.max_click_distance_tiles)
        self.assertEqual(8.0, limits.open_click_floor_tiles)
        self.assertEqual(2.5, limits.corridor_limit_tiles)
        self.assertEqual(85.0, limits.max_skipped_turn_degrees)
        self.assertEqual(
            tuple((step.target_key, step.location) for step in requested_steps),
            task.observation_request().tile_projections,
        )

        decision = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=tuple(
                    route_tile(step, geometry=NAV_GEOMETRY)
                    for step in requested_steps
                ),
            )
        )

        self.assertEqual(ActionKind.WALK, decision.action.kind)
        self.assertEqual(ROUTE_TO_BANK[6].target_key, decision.action.target_key)
        self.assertEqual(9.0, task.last_route_selection.requested_tile_distance)

    def test_route_behavior_config_limits_projection_request_count(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=11, route_lookahead_points=2)
            )
        )
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 3

        self.assertEqual(
            tuple(
                (step.target_key, step.location)
                for step in ROUTE_TO_BANK[3:5]
            ),
            task.observation_request().tile_projections,
        )

    def test_route_request_continues_past_stale_current_turn_to_next_barrier(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 0

        requested = task.observation_request().tile_projections

        self.assertEqual(
            tuple(
                (step.target_key, step.location)
                for step in ROUTE_TO_BANK[:2]
            ),
            requested,
        )

    def test_live_tree_lane_position_uses_bounded_route_reentry_correction(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=20260713))
        )
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 0
        reentry = ROUTE_TO_BANK[0]

        decision = task.decide(
            observation(
                location=WorldPoint(3194, 3248, 0),
                inv=inventory(logs=28, full=True),
                objects=(
                    route_tile(
                        reentry,
                        geometry=replace(NAV_GEOMETRY, shortcut_clear=False),
                    ),
                ),
            )
        )

        self.assertEqual(ActionKind.WALK, decision.action.kind)
        self.assertEqual(reentry.target_key, decision.action.target_key)
        self.assertEqual(4.0, task.last_route_selection.requested_tile_distance)
        self.assertEqual(
            "route_reentry_correction_required",
            task.last_route_selection.fallback_reason,
        )
        self.assertEqual(
            "route_reentry_correction_required",
            decision.evidence.route.fallback_reason,
        )

    def test_unknown_or_invalid_future_collision_retains_short_correction(self) -> None:
        for label, future_geometry in (
            ("unknown", GEOMETRY),
            ("invalid", replace(NAV_GEOMETRY, collision_supported=False)),
        ):
            with self.subTest(label=label):
                task = WoodcutBankTask()
                task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
                task.progress.route_index = 4
                source = ROUTE_TO_BANK[3].location
                requested_steps = ROUTE_TO_BANK[4:10]
                objects = tuple(
                    route_tile(
                        step,
                        geometry=NAV_GEOMETRY if index == 4 else future_geometry,
                    )
                    for index, step in enumerate(requested_steps, start=4)
                )

                decision = task.decide(
                    observation(
                        location=source,
                        inv=inventory(logs=28, full=True),
                        objects=objects,
                    )
                )

                self.assertEqual(ActionKind.WALK, decision.action.kind)
                self.assertEqual(ROUTE_TO_BANK[4].target_key, decision.action.target_key)
                self.assertEqual(4, task.progress.route_index)
                self.assertEqual(3.0, task.last_route_selection.requested_tile_distance)
                self.assertEqual(
                    "short_correction_required",
                    task.last_route_selection.fallback_reason,
                )
                if label == "invalid":
                    far_rejection = next(
                        item
                        for item in task.last_route_selection.rejections
                        if item.route_index == 9
                    )
                    self.assertIn("collision_unsupported", far_rejection.reasons)

    def test_shortcut_fact_rejects_far_point_without_losing_forward_progress(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 4
        source = ROUTE_TO_BANK[3].location
        requested_steps = ROUTE_TO_BANK[4:10]
        objects = tuple(
            route_tile(
                step,
                geometry=(
                    replace(NAV_GEOMETRY, shortcut_clear=False)
                    if index == 9
                    else NAV_GEOMETRY
                ),
            )
            for index, step in enumerate(requested_steps, start=4)
        )

        decision = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=objects,
            )
        )

        self.assertEqual(ActionKind.WALK, decision.action.kind)
        self.assertEqual(8, task.progress.route_index)
        self.assertGreater(task.last_route_selection.requested_tile_distance, 4.0)
        far_rejection = next(
            item
            for item in task.last_route_selection.rejections
            if item.route_index == 9
        )
        self.assertIn("shortcut_unsupported", far_rejection.reasons)
        self.assertEqual(
            (
                (
                    "south_corridor_entry",
                    ("shortcut_unsupported",),
                ),
            ),
            tuple(
                (item.step_id, item.rejection_codes)
                for item in decision.evidence.route.candidate_rejections
            ),
        )

    def test_camera_recovery_recomputes_fresh_route_candidate_support(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 4
        source = ROUTE_TO_BANK[3].location
        requested_steps = ROUTE_TO_BANK[4:10]
        camera_only = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=False,
            canvas_point=ScreenPoint(900, 700),
            screen_point=ScreenPoint(1950, 2800),
            scene_supported=True,
            collision_supported=True,
            shortcut_clear=True,
        )
        before_camera = tuple(
            route_tile(
                step,
                geometry=camera_only if index == 9 else NAV_GEOMETRY,
            )
            for index, step in enumerate(requested_steps, start=4)
        )

        waiting = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=before_camera,
                tick=10,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            )
        )
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)
        self.assertEqual(9, task.last_route_selection.selected_index)
        self.assertEqual(4, task.progress.route_index)
        camera = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=before_camera,
                tick=11,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        task.apply_verification(verification_pass(OutcomeKind.CAMERA_POSE_CHANGED))

        after_camera = tuple(
            route_tile(
                step,
                geometry=(
                    replace(NAV_GEOMETRY, collision_supported=False)
                    if index == 9
                    else NAV_GEOMETRY
                ),
            )
            for index, step in enumerate(requested_steps, start=4)
        )
        walk = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=after_camera,
                tick=13,
                camera_yaw=9_000,
                geometry_frame_id="camera-frame-9000",
            )
        )

        self.assertEqual(ActionKind.WALK, walk.action.kind)
        self.assertEqual(8, task.last_route_selection.selected_index)
        self.assertEqual(8, task.progress.route_index)

    def test_fresh_task_reconciles_empty_inventory_on_exact_return_route(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3214, 3228, 0)

        resumed = task.decide(observation(location=location, inv=inventory()))

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(6, task.progress.route_index)
        self.assertIsNone(task.progress.pending)
        self.assertIsNone(task.progress.target_key)
        self.assertEqual(
            "reobserved empty inventory on the validated return route",
            resumed.reason,
        )

        arrived = task.decide(
            observation(location=location, inv=inventory(), tick=11)
        )
        self.assertEqual(ActionKind.WAIT, arrived.action.kind)
        self.assertEqual(7, task.progress.route_index)

        task.progress.route_index = len(ROUTE_TO_TREES)
        completed_resume = task.decide(
            observation(location=TREE_AREA, inv=inventory(), tick=12)
        )
        self.assertEqual(ActionKind.WAIT, completed_resume.action.kind)
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual(0, task.progress.cycles_completed)
        self.assertEqual(0, task.progress.route_index)

    def test_fresh_task_prefers_exact_return_route_inside_broad_work_area(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3210, 3228, 0)

        resumed = task.decide(observation(location=location, inv=inventory()))

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(
            next(
                index
                for index, step in enumerate(ROUTE_TO_TREES)
                if step.step_id == "ground_corridor_east_1"
            ),
            task.progress.route_index,
        )
        self.assertEqual(
            "reobserved empty inventory on the validated return route",
            resumed.reason,
        )

    def test_fresh_task_recovers_from_bounded_polyline_route_deviation(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3206, 3217, 0)

        resumed = task.decide(observation(location=location, inv=inventory()))

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(
            next(
                index
                for index, step in enumerate(ROUTE_TO_TREES)
                if step.step_id == "south_corridor_return"
            ),
            task.progress.route_index,
        )
        self.assertEqual(
            "reobserved empty inventory on the validated return route",
            resumed.reason,
        )

    def test_fresh_task_reconciles_empty_inventory_with_open_bank_in_radius(self) -> None:
        task = WoodcutBankTask()
        bank_area = WorldPoint(
            BANK_ANCHOR.x,
            BANK_ANCHOR.y - 1,
            BANK_ANCHOR.plane,
        )
        bank_open = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )

        resumed = task.decide(
            observation(
                location=bank_area,
                inv=inventory(),
                widgets=bank_open,
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.CLOSE_BANK, task.progress.phase)
        self.assertTrue(task._restart_reconciled_without_cycle_credit)
        self.assertEqual(
            "reobserved empty inventory with an open bank at the validated "
            "bank interaction area",
            resumed.reason,
        )

        close = task.decide(
            observation(
                location=bank_area,
                inv=inventory(),
                widgets=bank_open,
                tick=11,
            )
        )

        self.assertEqual(ActionKind.PRESS_KEY, close.action.kind)
        self.assertEqual("escape", close.action.key)
        self.assertEqual(VerificationKind.INTERFACE_CLOSED, task.progress.pending.kind)

        task.apply_verification(
            verification_pass(OutcomeKind.INTERFACE_CLOSED, tick=12)
        )
        task.progress.route_index = len(ROUTE_TO_TREES)
        completed_resume = task.decide(
            observation(location=TREE_AREA, inv=inventory(), tick=13)
        )

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual(0, task.progress.cycles_completed)
        self.assertFalse(task._restart_reconciled_without_cycle_credit)

    def test_open_bank_anchor_restart_near_misses_remain_fail_closed(self) -> None:
        bank_open = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )
        cases = (
            ("unknown inventory", BANK_ANCHOR, inventory(known=False), bank_open),
            ("occupied inventory", BANK_ANCHOR, inventory(logs=1), bank_open),
            (
                "bank unknown",
                BANK_ANCHOR,
                inventory(),
                replace(bank_open, bank_known=False),
            ),
            (
                "bank unknown at route overlap",
                ROUTE_TO_TREES[0].location,
                inventory(),
                replace(bank_open, bank_known=False, bank_open=False),
            ),
            (
                "wrong plane",
                WorldPoint(BANK_ANCHOR.x, BANK_ANCHOR.y, BANK_ANCHOR.plane - 1),
                inventory(),
                bank_open,
            ),
            (
                "outside bank radius",
                WorldPoint(
                    BANK_ANCHOR.x + DEFINITION.bank.interaction_radius + 1,
                    BANK_ANCHOR.y,
                    BANK_ANCHOR.plane,
                ),
                inventory(),
                bank_open,
            ),
        )

        for label, location, inv, widgets in cases:
            with self.subTest(label=label):
                task = WoodcutBankTask()

                decision = task.decide(
                    observation(
                        location=location,
                        inv=inv,
                        widgets=widgets,
                    )
                )

                self.assertIsNot(TaskPhase.CLOSE_BANK, task.progress.phase)
                self.assertIsNot(
                    TaskPhase.NAVIGATE_TO_TREES,
                    task.progress.phase,
                )
                self.assertFalse(task._restart_reconciled_without_cycle_credit)
                self.assertIsNot(ActionKind.PRESS_KEY, decision.action.kind)
                self.assertIsNot(ActionKind.CLICK_WIDGET, decision.action.kind)

    def test_fresh_task_reconciles_empty_inventory_at_closed_bank_start(self) -> None:
        task = WoodcutBankTask()
        bank_closed = WidgetObservation(
            bank_known=True,
            bank_open=False,
        )

        resumed = task.decide(
            observation(
                location=WorldPoint(
                    BANK_ANCHOR.x,
                    BANK_ANCHOR.y - 1,
                    BANK_ANCHOR.plane,
                ),
                inv=inventory(),
                widgets=bank_closed,
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(0, task.progress.route_index)
        self.assertTrue(task._restart_reconciled_without_cycle_credit)
        self.assertEqual(
            "reobserved empty inventory at the validated bank return-route start",
            resumed.reason,
        )

        task.progress.route_index = len(ROUTE_TO_TREES)
        completed_resume = task.decide(
            observation(location=TREE_AREA, inv=inventory(), tick=11)
        )

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual(0, task.progress.cycles_completed)
        self.assertFalse(task._restart_reconciled_without_cycle_credit)

    def test_open_bank_route_overlap_closes_before_route_resume(self) -> None:
        task = WoodcutBankTask()
        bank_open = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )

        resumed = task.decide(
            observation(
                location=ROUTE_TO_TREES[0].location,
                inv=inventory(),
                widgets=bank_open,
            )
        )
        close = task.decide(
            observation(
                location=ROUTE_TO_TREES[0].location,
                inv=inventory(),
                widgets=bank_open,
                tick=11,
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.CLOSE_BANK, task.progress.phase)
        self.assertEqual(0, task.progress.route_index)
        self.assertTrue(task._restart_reconciled_without_cycle_credit)
        self.assertEqual(ActionKind.PRESS_KEY, close.action.kind)
        self.assertEqual("escape", close.action.key)

    def test_open_bank_anchor_restart_rejects_pin_before_input(self) -> None:
        for label, location, bank_open in (
            ("open bank anchor", BANK_ANCHOR, True),
            ("closed route overlap", ROUTE_TO_TREES[0].location, False),
        ):
            with self.subTest(label=label):
                task = WoodcutBankTask()
                bank_pin = WidgetObservation(
                    bank_known=True,
                    bank_open=bank_open,
                    bank_pin_open=True,
                    bank_readable=bank_open,
                    keyboard_close_possible=bank_open,
                )

                blocked = task.decide(
                    observation(
                        location=location,
                        inv=inventory(),
                        widgets=bank_pin,
                    )
                )

                self.assertEqual(ActionKind.WAIT, blocked.action.kind)
                self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
                self.assertFalse(
                    task._restart_reconciled_without_cycle_credit
                )
                self.assertEqual(
                    "bank PIN handling is out of scope",
                    blocked.reason,
                )

    def test_unreadable_open_bank_at_anchor_may_only_close(self) -> None:
        task = WoodcutBankTask()
        unreadable = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=False,
            keyboard_close_possible=True,
        )

        resumed = task.decide(
            observation(location=BANK_ANCHOR, inv=inventory(), widgets=unreadable)
        )
        close = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(),
                widgets=unreadable,
                tick=11,
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(ActionKind.PRESS_KEY, close.action.kind)
        self.assertEqual("escape", close.action.key)
        self.assertEqual(VerificationKind.INTERFACE_CLOSED, task.progress.pending.kind)

    def test_return_route_reconciliation_uses_furthest_matching_step(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3206, 3227, 2)
        expected = max(
            index
            for index, step in enumerate(ROUTE_TO_TREES)
            if location.plane == step.location.plane
            and location.distance_to(step.location) <= step.arrival_radius
        )

        task.decide(observation(location=location, inv=inventory()))

        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, task.progress.phase)
        self.assertEqual(expected, task.progress.route_index)

    def test_fresh_task_reconciles_full_inventory_on_exact_bank_route(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3205, 3215, 2)
        expected = max(
            index
            for index, step in enumerate(ROUTE_TO_BANK)
            if location.plane == step.location.plane
            and location.distance_to(step.location) <= step.arrival_radius
        )

        resumed = task.decide(
            observation(
                location=location,
                inv=inventory(logs=28, full=True),
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(expected, task.progress.route_index)
        self.assertIsNone(task.progress.pending)
        self.assertIsNone(task.progress.target_key)
        self.assertEqual(
            "reobserved full inventory on the validated bank route",
            resumed.reason,
        )

        arrived = task.decide(
            observation(
                location=location,
                inv=inventory(logs=28, full=True),
                tick=11,
            )
        )
        self.assertEqual(ActionKind.WAIT, arrived.action.kind)
        self.assertEqual(expected + 1, task.progress.route_index)

        task.progress.phase = TaskPhase.NAVIGATE_TO_TREES
        task.progress.route_index = len(ROUTE_TO_TREES)
        completed_resume = task.decide(
            observation(location=TREE_AREA, inv=inventory(), tick=12)
        )
        self.assertEqual(ActionKind.WAIT, completed_resume.action.kind)
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual(0, task.progress.cycles_completed)
        self.assertEqual(0, task.progress.route_index)

    def test_fresh_task_reconciles_full_inventory_on_overlapping_bank_route(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3198, 3228, 0)
        expected = next(
            index
            for index, step in enumerate(ROUTE_TO_BANK)
            if step.step_id == "west_wall_descent_2"
        )

        resumed = task.decide(
            observation(
                location=location,
                inv=inventory(logs=28, full=True),
            )
        )

        self.assertEqual(ActionKind.WAIT, resumed.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(expected, task.progress.route_index)
        self.assertTrue(task._restart_reconciled_without_cycle_credit)
        self.assertEqual(
            "reobserved full inventory on the validated bank route",
            resumed.reason,
        )

    def test_verified_final_log_inside_work_area_keeps_normal_cycle_credit(self) -> None:
        task = WoodcutBankTask()
        location = ROUTE_TO_BANK[0].location
        target = tree(location=location)
        state = observation(
            location=location,
            inv=inventory(logs=27),
            objects=(target,),
            tick=20,
        )
        task.decide(state)
        task.decide(state)
        task.apply_verification(
            verification_pass(OutcomeKind.ITEM_QUANTITY_INCREASED, tick=21)
        )

        full = task.decide(
            observation(
                location=location,
                inv=inventory(logs=28, full=True),
                tick=22,
            )
        )

        self.assertEqual(
            "inventory is full; fixed bank route selected",
            full.reason,
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(0, task.progress.route_index)
        self.assertFalse(task._restart_reconciled_without_cycle_credit)

    def test_bank_route_reconciliation_uses_furthest_matching_step(self) -> None:
        task = WoodcutBankTask()
        location = WorldPoint(3205, 3213, 2)
        expected = max(
            index
            for index, step in enumerate(ROUTE_TO_BANK)
            if location.plane == step.location.plane
            and location.distance_to(step.location) <= step.arrival_radius
        )

        task.decide(
            observation(
                location=location,
                inv=inventory(logs=28, full=True),
            )
        )

        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(expected, task.progress.route_index)

    def test_bank_anchor_restart_advances_directly_to_open_bank(self) -> None:
        task = WoodcutBankTask()
        task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
            )
        )

        self.assertEqual(len(ROUTE_TO_BANK) - 1, task.progress.route_index)

        arrived = task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                tick=11,
            )
        )

        self.assertEqual(ActionKind.WAIT, arrived.action.kind)
        self.assertEqual(TaskPhase.OPEN_BANK, task.progress.phase)

    def test_bank_route_reconciliation_rejects_partial_or_off_route_state(self) -> None:
        for label, location, inv in (
            (
                "partial logs",
                WorldPoint(3205, 3215, 2),
                inventory(logs=27),
            ),
            (
                "off route",
                WorldPoint(3300, 3300, 2),
                inventory(logs=28, full=True),
            ),
        ):
            with self.subTest(label=label):
                task = WoodcutBankTask()
                task.decide(observation(location=location, inv=inv))

                self.assertFalse(task._restart_reconciled_without_cycle_credit)
                if label == "off route":
                    self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
                    self.assertEqual(0, task.progress.route_index)
                else:
                    self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)

        wrong_plane = WoodcutBankTask()
        wrong_plane.decide(
            observation(
                location=WorldPoint(3205, 3215, 1),
                inv=inventory(logs=28, full=True),
            )
        )
        self.assertFalse(wrong_plane._restart_reconciled_without_cycle_credit)
        self.assertEqual(0, wrong_plane.progress.route_index)

    def test_return_route_reconciliation_rejects_ambiguous_inventory_or_location(self) -> None:
        for label, location, inv in (
            (
                "partial logs",
                WorldPoint(3214, 3228, 0),
                inventory(logs=1),
            ),
            ("off route", WorldPoint(3300, 3300, 0), inventory()),
            ("wrong plane", WorldPoint(3215, 3228, 1), inventory()),
        ):
            with self.subTest(label=label):
                task = WoodcutBankTask()
                decision = task.decide(observation(location=location, inv=inv))

                self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
                self.assertEqual("player is outside the supported work area", decision.reason)

    def test_route_projection_camera_recovery_is_typed_bounded_and_resumable(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        source = WorldPoint(3195, 3248, 0)
        target = route_tile(step, geometry=TargetGeometry())

        first = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=10,
                camera_yaw=0,
                geometry_frame_id="camera-frame-0",
            )
        )
        self.assertEqual(ActionKind.WAIT, first.action.kind)
        self.assertIn("stable route projection", first.reason)

        camera = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=11,
                camera_yaw=0,
                geometry_frame_id="camera-frame-0",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        self.assertEqual("left", camera.action.key)
        self.assertEqual("left", camera.evidence.camera.action)
        self.assertGreaterEqual(camera.action.key_hold_millis, 80)
        self.assertLessEqual(camera.action.key_hold_millis, 250)
        self.assertEqual(
            camera.action.key_hold_millis,
            camera.action.task_constraints.camera.hold_millis,
        )
        self.assertEqual(
            VerificationKind.CAMERA_POSE_CHANGED,
            camera.action.verification.kind,
        )
        self.assertEqual(step.target_key, camera.action.target_key)
        self.assertEqual(step.location, camera.action.task_constraints.camera.target_location)
        self.assertEqual(0, task.progress.route_index)

        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=12)
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(0, task.progress.route_index)

        walk = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=(route_tile(step),),
                tick=13,
                camera_yaw=9_000,
                geometry_frame_id="camera-frame-9000",
            )
        )
        self.assertEqual(ActionKind.WALK, walk.action.kind)
        self.assertEqual(0, task._camera_recovery_attempts)

    def test_exact_offscreen_tree_is_camera_acquired_before_activation(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=2026071301))
        )
        hidden = tree(geometry=TargetGeometry())
        selected = task.decide(observation(objects=(hidden,), tick=10))

        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(hidden.key, task.progress.target_key)
        self.assertEqual(ActionKind.WAIT, selected.action.kind)

        stable = task.decide(
            observation(
                objects=(hidden,),
                tick=11,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
                geometry_frame_id="tree-hidden-11",
            )
        )
        self.assertEqual(ActionKind.WAIT, stable.action.kind)
        self.assertIn("stable proactive framing", stable.reason)

        camera = task.decide(
            observation(
                objects=(hidden,),
                tick=12,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
                geometry_frame_id="tree-hidden-12",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        self.assertEqual("interaction", camera.evidence.camera.framing_context)
        self.assertEqual(1, camera.evidence.camera.correction_attempt)
        self.assertEqual(
            camera.action.key_hold_millis,
            camera.evidence.camera.cumulative_hold_millis,
        )
        self.assertEqual("tree-hidden-12", camera.evidence.camera.geometry_frame_id)

        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=13)
        )
        activation = task.decide(
            observation(
                objects=(tree(),),
                tick=14,
                camera_yaw=8_000,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
                geometry_frame_id="tree-visible-14",
            )
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, activation.action.kind)
        self.assertEqual(hidden.key, activation.action.target_key)
        self.assertEqual(1, activation.evidence.camera.correction_attempt)
        self.assertGreater(activation.evidence.camera.cumulative_hold_millis, 0)
        self.assertEqual("tree-visible-14", activation.evidence.camera.geometry_frame_id)

    def test_visible_actionable_tree_ranks_before_nearer_hidden_tree(self) -> None:
        task = WoodcutBankTask()
        hidden = tree(
            key="resource:hidden-near",
            location=TREE_AREA,
            geometry=TargetGeometry(),
        )
        visible = tree(
            key="resource:visible-farther",
            location=WorldPoint(TREE_AREA.x + 1, TREE_AREA.y, TREE_AREA.plane),
        )

        eligible, rejected = task._classify_trees(
            observation(objects=(hidden, visible))
        )

        self.assertEqual((visible, hidden), eligible)
        self.assertEqual((), rejected)

    def test_exhausted_hidden_tree_reselects_an_exact_visible_alternate(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=2026071302, camera_max_corrections=1)
            )
        )
        hidden = tree(
            key="resource:hidden-exhausted",
            location=TREE_AREA,
            geometry=TargetGeometry(),
        )
        visible = tree(
            key="resource:visible-alternate",
            location=WorldPoint(TREE_AREA.x + 1, TREE_AREA.y, TREE_AREA.plane),
        )
        viewport = ScreenBounds(1000, 2000, 800, 600)
        task.decide(observation(objects=(hidden,), tick=10, viewport_bounds=viewport))
        task.decide(observation(objects=(hidden,), tick=11, viewport_bounds=viewport))
        camera = task.decide(
            observation(objects=(hidden,), tick=12, viewport_bounds=viewport)
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=13)
        )

        fallback = task.decide(
            observation(
                objects=(hidden, visible), tick=14, viewport_bounds=viewport
            )
        )

        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, fallback.action.kind)
        self.assertIsNotNone(fallback.evidence.camera)
        self.assertEqual(1, fallback.evidence.camera.correction_attempt)
        self.assertEqual(
            ("camera_framing_exhausted",),
            fallback.evidence.rejected[0].rejection_codes,
        )

        selected = task.decide(
            observation(
                objects=(hidden, visible), tick=15, viewport_bounds=viewport
            )
        )
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(visible.key, task.progress.target_key)
        self.assertEqual(visible.key, selected.evidence.selected.key)
        self.assertTrue(
            any(
                item.target.key == hidden.key
                and item.rejection_codes == ("camera_framing_exhausted",)
                for item in selected.evidence.rejected
            )
        )

    def test_exhausted_tree_without_alternate_remains_blocked(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=2026071303, camera_max_corrections=1)
            )
        )
        hidden = tree(geometry=TargetGeometry())
        viewport = ScreenBounds(1000, 2000, 800, 600)
        task.decide(observation(objects=(hidden,), tick=10, viewport_bounds=viewport))
        task.decide(observation(objects=(hidden,), tick=11, viewport_bounds=viewport))
        camera = task.decide(
            observation(objects=(hidden,), tick=12, viewport_bounds=viewport)
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=13)
        )
        blocked = task.decide(
            observation(objects=(hidden,), tick=14, viewport_bounds=viewport)
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertIsNotNone(blocked.evidence.camera)
        self.assertEqual(1, blocked.evidence.camera.correction_attempt)

    def test_exact_offscreen_bank_and_stair_enter_camera_acquisition(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)

        bank_task = WoodcutBankTask()
        bank_task.progress.phase = TaskPhase.OPEN_BANK
        hidden_bank = bank_object(geometry=TargetGeometry())
        bank_wait = bank_task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                objects=(hidden_bank,),
                widgets=WidgetObservation(bank_known=True),
                tick=30,
                viewport_bounds=viewport,
            )
        )
        self.assertEqual(ActionKind.WAIT, bank_wait.action.kind)
        self.assertIn("stable proactive framing", bank_wait.reason)
        bank_camera = bank_task.decide(
            observation(
                location=BANK_ANCHOR,
                inv=inventory(logs=28, full=True),
                objects=(hidden_bank,),
                widgets=WidgetObservation(bank_known=True),
                tick=31,
                viewport_bounds=viewport,
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, bank_camera.action.kind)
        self.assertEqual(hidden_bank.key, bank_camera.action.target_key)

        route_index = next(
            index for index, step in enumerate(ROUTE_TO_BANK) if not step.is_walk
        )
        step = ROUTE_TO_BANK[route_index]
        stair_task = WoodcutBankTask()
        stair_task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        stair_task.progress.route_index = route_index
        hidden_stair = route_object(step, geometry=TargetGeometry())
        stair_wait = stair_task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                objects=(hidden_stair,),
                tick=40,
                viewport_bounds=viewport,
            )
        )
        self.assertEqual(ActionKind.WAIT, stair_wait.action.kind)
        self.assertIn("stable proactive framing", stair_wait.reason)
        stair_camera = stair_task.decide(
            observation(
                location=step.location,
                inv=inventory(logs=28, full=True),
                objects=(hidden_stair,),
                tick=41,
                viewport_bounds=viewport,
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, stair_camera.action.kind)
        self.assertEqual(hidden_stair.key, stair_camera.action.target_key)

    def test_route_camera_evidence_includes_fresh_lookahead_envelope(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        first_step, turn_step = ROUTE_TO_BANK[:2]
        first = route_tile(
            first_step,
            geometry=replace(NAV_GEOMETRY, screen_point=ScreenPoint(1400, 2250)),
        )
        turn = route_tile(
            turn_step,
            geometry=replace(NAV_GEOMETRY, screen_point=ScreenPoint(1560, 2100)),
        )
        state = observation(
            location=WorldPoint(3195, 3248, 0),
            inv=inventory(logs=28, full=True),
            objects=(first, turn),
            tick=50,
            viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            geometry_frame_id="route-lookahead-50",
        )

        framing = task._classify_route_camera(state, first_step, first)

        self.assertIsNotNone(framing)
        assert framing is not None
        self.assertEqual("route", framing.framing_context)
        self.assertEqual("route-lookahead-50", framing.geometry_frame_id)
        self.assertIn(first.geometry.screen_point, framing.lookahead_points)
        self.assertIn(turn.geometry.screen_point, framing.lookahead_points)
        self.assertIsNotNone(framing.lookahead_bounds)

    def test_camera_yaw_deadband_prevents_small_oscillating_correction(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=49, camera_yaw_deadband_units=32)
            )
        )
        source = WorldPoint(0, 0, 0)
        target = WorldPoint(0, -1, 0)

        self.assertEqual(22, task._camera_yaw_error(source, target, 8170))
        self.assertIsNone(
            task._camera_correction_direction(
                source,
                target,
                8170,
                None,
                None,
            )
        )

    def test_route_camera_reframe_records_nonempty_rejection_reason(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        target = route_tile(step, geometry=NAV_GEOMETRY)
        framing = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1100, 2100, 400, 300),
            target_point=SCREEN,
            action="left",
            hold_millis=100,
            route_direction_bias="south",
            correction_distance_px=120.0,
        )

        decision = task._recover_route_projection(
            observation(
                location=WorldPoint(3198, 3228, 0),
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=10,
            ),
            step,
            target,
            framing=framing,
        )

        self.assertEqual(ActionKind.WAIT, decision.action.kind)
        self.assertEqual(
            ("camera_reframe_required",),
            decision.evidence.rejected[0].rejection_codes,
        )

    def test_route_projection_camera_recovery_exhausts_after_configured_verified_turns(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        source = WorldPoint(3195, 3248, 0)
        target = route_tile(step, geometry=TargetGeometry())
        tick = 10
        limit = task.behavior.config.camera_max_corrections
        wait = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=tick,
                camera_yaw=0,
            )
        )
        self.assertEqual(ActionKind.WAIT, wait.action.kind)
        tick += 1
        for attempt in range(limit):
            camera = task.decide(
                observation(
                    location=source,
                    inv=inventory(logs=28, full=True),
                    objects=(target,),
                    tick=tick,
                    camera_yaw=(attempt * 1_000) % 16_384,
                )
            )
            self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
            task.apply_verification(
                verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=tick + 1)
            )
            tick += 2

        self.assertEqual(limit, task._camera_recovery_attempts)
        blocked = task.decide(
            observation(
                location=source,
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=tick,
                camera_yaw=8_000,
            )
        )
        self.assertEqual(TaskPhase.BLOCKED.value, blocked.state)
        self.assertIn("camera recovery exhausted", blocked.reason)

    def test_camera_turn_direction_uses_shortest_fixed_point_arc(self) -> None:
        source = WorldPoint(3195, 3248, 0)
        target = WorldPoint(3200, 3238, 0)

        self.assertEqual("left", WoodcutBankTask._camera_turn_direction(source, target, 0))
        self.assertEqual("right", WoodcutBankTask._camera_turn_direction(source, target, 9_000))
        self.assertEqual("left", WoodcutBankTask._camera_turn_direction(source, target, 10_000))

    def test_vertical_framing_moves_projection_toward_region(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(seed=47, camera_yaw_deadband_units=32)
            )
        )
        source = WorldPoint(0, 0, 0)
        target = WorldPoint(0, -1, 0)
        desired = ScreenBounds(1100, 2200, 400, 300)
        above = CameraFramingDecision(
            classification="barely_visible",
            desired_region=desired,
            target_point=ScreenPoint(1300, 2100),
            action="reframe",
            hold_millis=100,
            route_direction_bias="south",
            correction_distance_px=100.0,
        )
        below = replace(above, target_point=ScreenPoint(1300, 2600))

        self.assertEqual(
            "down",
            task._camera_correction_direction(
                source,
                target,
                8191,
                1024,
                above,
            ),
        )
        self.assertEqual(
            "up",
            task._camera_correction_direction(
                source,
                target,
                8193,
                1024,
                below,
            ),
        )

    def test_screen_edge_cause_directs_yaw_before_world_bearing(self) -> None:
        task = WoodcutBankTask()
        source = WorldPoint(0, 0, 0)
        target = WorldPoint(0, -1, 0)
        framing = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1100, 2200, 400, 300),
            target_point=ScreenPoint(1300, 2350),
            action="reframe",
            hold_millis=100,
            route_direction_bias="south",
            correction_distance_px=60.0,
            screen_correction_x_px=60.0,
        )

        self.assertEqual(
            "right",
            task._camera_correction_direction(
                source,
                target,
                8192,
                1024,
                framing,
            ),
        )
        self.assertEqual(
            "left",
            task._camera_correction_direction(
                source,
                target,
                8192,
                1024,
                replace(framing, screen_correction_x_px=-60.0),
            ),
        )

    def test_not_visible_camera_uses_world_bearing_not_clipped_screen_vector(self) -> None:
        task = WoodcutBankTask()
        source = WorldPoint(3197, 3237, 0)
        target = WorldPoint(3193, 3244, 0)
        framing = CameraFramingDecision(
            classification="not_visible",
            desired_region=ScreenBounds(1439, 285, 1253, 865),
            target_point=ScreenPoint(2973, 605),
            action="reframe",
            hold_millis=238,
            route_direction_bias="north_west",
            correction_distance_px=469.0,
            screen_correction_x_px=-469.0,
            yaw_error_units=-2416,
        )

        self.assertEqual(-2416, task._camera_yaw_error(source, target, 3770))
        self.assertEqual(
            "left",
            task._camera_correction_direction(
                source,
                target,
                3770,
                1024,
                framing,
            ),
        )

    def test_live_left_edge_correction_keeps_rightward_screen_motion(self) -> None:
        task = WoodcutBankTask()
        source = WorldPoint(3184, 3234, 0)
        target = WorldPoint(3189, 3229, 0)
        framing = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1439, 285, 1253, 865),
            target_point=ScreenPoint(1286, 638),
            action="reframe",
            hold_millis=120,
            route_direction_bias="south_east",
            correction_distance_px=153.0,
            screen_correction_x_px=153.0,
            yaw_error_units=1809,
        )

        self.assertEqual(1809, task._camera_yaw_error(source, target, 8431))
        self.assertEqual(
            "right",
            task._camera_correction_direction(
                source,
                target,
                8431,
                1024,
                framing,
            ),
        )

    def test_signed_vertical_screen_correction_uses_causal_pitch_direction(self) -> None:
        task = WoodcutBankTask()
        source = WorldPoint(0, 0, 0)
        target = WorldPoint(0, -1, 0)
        framing = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1100, 2200, 400, 300),
            target_point=ScreenPoint(1300, 2350),
            action="reframe",
            hold_millis=100,
            route_direction_bias="south",
            correction_distance_px=56.0,
            screen_correction_y_px=56.0,
        )

        # A positive screen correction means the projected shape must move
        # downward.  Live RuneLite evidence shows DOWN decreases pitch and
        # moves scene geometry down; UP has the opposite effect.
        self.assertEqual(
            "down",
            task._camera_correction_direction(
                source,
                target,
                8192,
                1024,
                framing,
            ),
        )
        self.assertEqual(
            "up",
            task._camera_correction_direction(
                source,
                target,
                8192,
                1024,
                replace(framing, screen_correction_y_px=-56.0),
            ),
        )

    def test_coarse_yaw_then_ineffective_fine_pitch_stops_without_third_action(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=48))
        )
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            canvas_point=ScreenPoint(500, 590),
            screen_point=ScreenPoint(1500, 2590),
            screen_bounds=ScreenBounds(1450, 2540, 100, 60),
            visible_area_ratio=1.0,
            scene_supported=True,
            collision_supported=True,
            shortcut_clear=True,
        )
        state = dict(
            location=WorldPoint(3194, 3248, 0),
            inv=inventory(logs=28, full=True),
            objects=(route_tile(step, geometry=geometry),),
            viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            camera_yaw=327,
            camera_pitch=1024,
        )

        first = task.decide(observation(tick=10, **state))
        self.assertEqual(ActionKind.WAIT, first.action.kind)
        coarse = task.decide(observation(tick=11, **state))
        self.assertEqual(ActionKind.CAMERA_HOLD, coarse.action.kind)
        self.assertIn(coarse.action.key, {"left", "right"})

        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=12)
        )

        fine = task.decide(observation(tick=13, **state))
        self.assertEqual(ActionKind.CAMERA_HOLD, fine.action.kind)
        self.assertIn(fine.action.key, {"up", "down"})

        task.apply_verification(
            verification_fail(
                "condition_unmet_at_deadline",
                VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
            )
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, task.progress.phase)
        self.assertEqual(step.step_id, task._camera_pitch_suppressed_step_id)

        blocked = task.decide(observation(tick=14, **state))
        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertNotEqual(ActionKind.PRESS_KEY, blocked.action.kind)
        self.assertEqual(2, task._camera_recovery_attempts)
        self.assertTrue(
            task._camera_response_model.pitch_direction_blocked(
                fine.action.key,
                1024,
            )
        )

    def test_camera_progress_accepts_clipped_shape_motion_toward_region(self) -> None:
        task = WoodcutBankTask()
        baseline = CameraFramingDecision(
            classification="obscured_or_contradictory",
            desired_region=ScreenBounds(1252, 405, 1253, 865),
            target_point=ScreenPoint(2006, 355),
            action="reframe",
            hold_millis=90,
            route_direction_bias="north_east",
            correction_distance_px=56.0,
            source_tick=10,
            geometry_frame_id="tree-progress-10",
            edge_clearance_px=-12.0,
            yaw_error_units=7,
            screen_correction_y_px=56.0,
        )

        for tick, target_y in ((11, 361), (12, 373), (13, 394)):
            task._camera_progress_baseline = baseline
            current = replace(
                baseline,
                target_point=ScreenPoint(2009, target_y),
                source_tick=tick,
                geometry_frame_id=f"tree-progress-{tick}",
            )

            self.assertTrue(task._consume_camera_framing_progress(current))
            self.assertEqual(0, task._camera_non_improving_corrections)
            self.assertFalse(task._camera_framing_progress_stalled())
            baseline = current

    def test_camera_progress_ignores_duplicate_geometry_frame(self) -> None:
        task = WoodcutBankTask()
        baseline = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1252, 405, 1253, 865),
            target_point=ScreenPoint(1587, 309),
            action="reframe",
            hold_millis=90,
            route_direction_bias="north_east",
            correction_distance_px=96.0,
            source_tick=10,
            geometry_frame_id="unchanged-pitch-limit",
            edge_clearance_px=-12.0,
            yaw_error_units=1135,
            screen_correction_y_px=96.0,
        )
        task._camera_progress_baseline = baseline
        task._camera_non_improving_corrections = 1

        result = task._consume_camera_framing_progress(
            replace(baseline, source_tick=11)
        )

        self.assertIsNone(result)
        self.assertIs(baseline, task._camera_progress_baseline)
        self.assertEqual(1, task._camera_non_improving_corrections)

    def test_unsent_camera_action_discards_progress_baseline(self) -> None:
        task = WoodcutBankTask()
        hidden = tree(geometry=TargetGeometry())
        viewport = ScreenBounds(1000, 2000, 800, 600)
        task.decide(observation(objects=(hidden,), tick=10, viewport_bounds=viewport))
        task.decide(observation(objects=(hidden,), tick=11, viewport_bounds=viewport))
        camera = task.decide(
            observation(objects=(hidden,), tick=12, viewport_bounds=viewport)
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        self.assertIsNotNone(task._camera_progress_baseline)

        task.discard_pending_action("camera proposal was safely unsent")

        self.assertIsNone(task.progress.pending)
        self.assertIsNone(task._camera_progress_baseline)

    def test_camera_progress_stops_after_two_fresh_yaw_non_improvements(self) -> None:
        task = WoodcutBankTask()
        baseline = CameraFramingDecision(
            classification="obscured_or_contradictory",
            desired_region=ScreenBounds(1252, 405, 1253, 865),
            target_point=ScreenPoint(1871, 297),
            action="reframe",
            hold_millis=90,
            route_direction_bias="north_east",
            correction_distance_px=108.0,
            source_tick=10,
            geometry_frame_id="yaw-progress-10",
            edge_clearance_px=-12.0,
            yaw_error_units=1135,
            screen_correction_y_px=108.0,
        )
        errors = (412, 168, 429, 191, 341, 155, 276, 310)

        for tick, yaw_error in enumerate(errors, start=11):
            task._camera_progress_baseline = baseline
            current = replace(
                baseline,
                source_tick=tick,
                geometry_frame_id=f"yaw-progress-{tick}",
                yaw_error_units=yaw_error,
            )
            task._consume_camera_framing_progress(current)
            baseline = current

        self.assertEqual(2, task._camera_non_improving_corrections)
        self.assertTrue(task._camera_framing_progress_stalled())

    def test_camera_progress_counts_return_to_prior_best_as_oscillation(self) -> None:
        task = WoodcutBankTask()
        visible = CameraFramingDecision(
            classification="obscured_or_contradictory",
            desired_region=ScreenBounds(1252, 285, 1253, 865),
            target_point=ScreenPoint(2380, 1031),
            action="reframe",
            hold_millis=238,
            route_direction_bias="south_west",
            correction_distance_px=0.0,
            source_tick=10,
            geometry_frame_id="oscillation-visible-10",
            edge_clearance_px=153.0,
            yaw_error_units=-7_650,
        )
        offscreen = replace(
            visible,
            classification="not_visible",
            target_point=ScreenPoint(2942, 1448),
            correction_distance_px=529.0,
            source_tick=11,
            geometry_frame_id="oscillation-offscreen-11",
            edge_clearance_px=-166.0,
            yaw_error_units=-6_469,
            screen_correction_x_px=-438.0,
            screen_correction_y_px=-299.0,
        )

        task._camera_progress_baseline = visible
        self.assertFalse(task._consume_camera_framing_progress(offscreen))
        task._camera_progress_baseline = offscreen
        returned = replace(
            visible,
            source_tick=12,
            geometry_frame_id="oscillation-visible-12",
        )
        self.assertFalse(task._consume_camera_framing_progress(returned))

        self.assertEqual(2, task._camera_non_improving_corrections)
        self.assertTrue(task._camera_framing_progress_stalled())

    def test_smaller_yaw_error_does_not_hide_worse_screen_framing(self) -> None:
        task = WoodcutBankTask()
        prior = CameraFramingDecision(
            classification="barely_visible",
            desired_region=ScreenBounds(1439, 285, 1253, 865),
            target_point=ScreenPoint(1547, 554),
            action="reframe",
            hold_millis=100,
            route_direction_bias="north_west",
            correction_distance_px=26.0,
            source_tick=10,
            geometry_frame_id="yaw-better-screen-10",
            edge_clearance_px=18.0,
            yaw_error_units=1088,
            screen_correction_y_px=26.0,
        )
        current = replace(
            prior,
            target_point=ScreenPoint(1964, 530),
            correction_distance_px=41.0,
            source_tick=11,
            geometry_frame_id="yaw-better-screen-11",
            edge_clearance_px=3.0,
            yaw_error_units=23,
            screen_correction_y_px=41.0,
        )

        self.assertFalse(task._camera_framing_improved(prior, current))

    def test_object_uses_coarse_yaw_then_never_activates_after_pitch_limit(self) -> None:
        viewport = ScreenBounds(1000, 2000, 600, 500)
        polygon = (
            ScreenPoint(1240, 2004),
            ScreenPoint(1399, 2004),
            ScreenPoint(1399, 2123),
            ScreenPoint(1240, 2123),
        )
        edge_geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1320, 2060),
            screen_bounds=ScreenBounds(1240, 2004, 160, 120),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=1.0,
        )
        target = tree(key="tree:pitch-limited", geometry=edge_geometry)
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = target.key

        first = task.decide(
            observation(
                objects=(target,),
                tick=20,
                viewport_bounds=viewport,
                geometry_frame_id="pitch-limited-20",
            )
        )
        self.assertEqual(ActionKind.WAIT, first.action.kind)
        coarse = task.decide(
            observation(
                objects=(target,),
                tick=21,
                viewport_bounds=viewport,
                geometry_frame_id="pitch-limited-20",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, coarse.action.kind)
        self.assertIn(coarse.action.key, {"left", "right"})
        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=22)
        )

        pitch = task.decide(
            observation(
                objects=(target,),
                tick=23,
                viewport_bounds=viewport,
                geometry_frame_id="pitch-limited-23",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, pitch.action.kind)
        self.assertEqual("down", pitch.action.key)
        task.apply_verification(
            verification_fail(
                "condition_unmet_at_deadline",
                VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
            )
        )

        blocked = task.decide(
            observation(
                objects=(target,),
                tick=24,
                viewport_bounds=viewport,
                geometry_frame_id="pitch-limited-24",
            )
        )
        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertNotIn(
            blocked.action.kind,
            {ActionKind.PRESS_KEY, ActionKind.INTERACT_OBJECT},
        )

    def test_non_improving_object_camera_reuses_exact_alternate_path(self) -> None:
        task = WoodcutBankTask()
        hidden = tree(
            key="resource:non-improving",
            geometry=TargetGeometry(),
        )
        visible = tree(
            key="resource:camera-alternate",
            location=WorldPoint(TREE_AREA.x + 1, TREE_AREA.y, TREE_AREA.plane),
        )
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = hidden.key
        task._camera_recovery_step_id = hidden.key
        task._route_projection_wait_since_tick = 9
        task._camera_non_improving_corrections = 2

        fallback = task._maybe_reframe_object(
            observation(
                objects=(hidden, visible),
                tick=10,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            ),
            hidden,
            action="Chop down",
        )

        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(ActionKind.WAIT, fallback.action.kind)
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIn("made no progress", fallback.reason)
        self.assertTrue(
            any(
                item.target.key == hidden.key
                and item.rejection_codes == ("camera_framing_non_improving",)
                for item in fallback.evidence.rejected
            )
        )
        self.assertEqual(
            10 + task.behavior.config.resource_camera_suppression_ticks,
            task._resource_camera_suppressions[hidden.key],
        )

    def test_recent_camera_failure_is_suppressed_until_expiry(self) -> None:
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(
                BehaviorConfig(
                    seed=2026071304,
                    resource_camera_suppression_ticks=12,
                )
            )
        )
        failed = tree(
            key="resource:recent-camera-failure",
            location=TREE_AREA,
            geometry=TargetGeometry(),
        )
        alternate = tree(
            key="resource:untried-camera-target",
            location=WorldPoint(TREE_AREA.x + 2, TREE_AREA.y, TREE_AREA.plane),
            geometry=TargetGeometry(),
        )
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = failed.key
        task._camera_recovery_step_id = failed.key
        task._route_projection_wait_since_tick = 9
        task._camera_non_improving_corrections = 2

        fallback = task._maybe_reframe_object(
            observation(
                objects=(failed, alternate),
                tick=10,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            ),
            failed,
            action="Chop down",
        )
        self.assertIsNotNone(fallback)
        task._next_resource_suppression = None

        selected_alternate = task.decide(
            observation(
                objects=(failed, alternate),
                tick=11,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            )
        )
        self.assertEqual(alternate.key, task.progress.target_key)
        self.assertTrue(
            any(
                item.target.key == failed.key
                and item.rejection_codes
                == ("camera_framing_recently_failed",)
                for item in selected_alternate.evidence.rejected
            )
        )

        task.progress.phase = TaskPhase.FIND_TREE
        task.progress.target_key = None
        expired = task.decide(
            observation(
                objects=(failed, alternate),
                tick=22,
                viewport_bounds=ScreenBounds(1000, 2000, 800, 600),
            )
        )
        self.assertEqual(failed.key, task.progress.target_key)
        self.assertEqual(failed.key, expired.evidence.selected.key)

    def test_non_improving_route_camera_blocks_without_another_keypress(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        step = ROUTE_TO_BANK[0]
        target = route_tile(step, geometry=TargetGeometry())
        task._camera_recovery_step_id = step.step_id
        task._route_projection_wait_since_tick = 9
        task._camera_non_improving_corrections = 2
        framing = CameraFramingDecision(
            classification="obscured_or_contradictory",
            desired_region=ScreenBounds(1000, 2000, 800, 600),
            target_point=ScreenPoint(1400, 2050),
            action="reframe",
            hold_millis=90,
            route_direction_bias="south",
            correction_distance_px=108.0,
            source_tick=10,
            geometry_frame_id="route-stalled-10",
            edge_clearance_px=-12.0,
            yaw_error_units=310,
            screen_correction_y_px=108.0,
        )

        blocked = task._recover_route_projection(
            observation(
                location=WorldPoint(3198, 3228, 0),
                inv=inventory(logs=28, full=True),
                objects=(target,),
                tick=10,
            ),
            step,
            target,
            framing=framing,
        )

        self.assertEqual(TaskPhase.BLOCKED.value, blocked.state)
        self.assertNotEqual(ActionKind.PRESS_KEY, blocked.action.kind)
        self.assertIn("made no progress", blocked.reason)
        self.assertEqual(
            ("camera_framing_non_improving",),
            blocked.evidence.rejected[0].rejection_codes,
        )

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
        self.assertEqual(selected.location, decision.evidence.selected.world_location)
        self.assertEqual(selected.distance, decision.evidence.selected.distance)

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
                objects=(bank_object(),),
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

        bottom_floor = next(
            step for step in ROUTE_TO_TREES if step.step_id == "bank_floor_bottom"
        )
        self.assertEqual("Bottom-floor", bottom_floor.action)

        for step in ROUTE_TO_TREES:
            requested = task.observation_request().tile_projections
            if step.is_walk:
                self.assertIn((step.target_key, step.location), requested)
                self.assertLessEqual(len(requested), 16)
                decision = task.decide(observation(location=step.location))
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
            else:
                self.assertEqual((), requested)
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

    def test_large_tree_shape_uses_seeded_variable_interior_points(self) -> None:
        polygon = (
            ScreenPoint(1300, 2140),
            ScreenPoint(1499, 2140),
            ScreenPoint(1499, 2359),
            ScreenPoint(1300, 2359),
        )
        shape = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1400, 2250),
            screen_bounds=ScreenBounds(1300, 2140, 200, 220),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=1.0,
        )

        def selected_sequence() -> list[ScreenPoint]:
            task = WoodcutBankTask(
                behavior=BehaviorPolicy(BehaviorConfig(seed=7341))
            )
            points = []
            for index in range(10):
                task.progress.phase = TaskPhase.CHOP
                task.progress.target_key = f"resource:{TREE_OBJECT_ID}"
                task.progress.pending = None
                decision = task.decide(
                    observation(
                        objects=(tree(geometry=shape),),
                        tick=100 + index,
                        geometry_frame_id=f"tree-shape-{index}",
                    )
                )
                self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
                self.assertIsNotNone(decision.evidence.targeting)
                assert decision.action.screen_point is not None
                self.assertTrue(point_in_polygon(decision.action.screen_point, polygon))
                self.assertEqual(
                    decision.action.screen_point,
                    decision.evidence.targeting.selected_point,
                )
                points.append(decision.action.screen_point)
            return points

        first = selected_sequence()
        second = selected_sequence()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(set(first)), 4)

    def test_object_camera_continues_from_barely_visible_to_well_framed(self) -> None:
        viewport = ScreenBounds(1000, 2000, 600, 500)
        edge_polygon = (
            ScreenPoint(1004, 2180),
            ScreenPoint(1079, 2180),
            ScreenPoint(1079, 2299),
            ScreenPoint(1004, 2299),
        )
        edge_geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1012, 2240),
            screen_bounds=ScreenBounds(1004, 2180, 76, 120),
            geometry_source="clickbox",
            screen_polygon=edge_polygon,
            visible_area_ratio=1.0,
        )
        task = WoodcutBankTask(
            behavior=BehaviorPolicy(BehaviorConfig(seed=20260712))
        )
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = f"resource:{TREE_OBJECT_ID}"

        stable_wait = task.decide(
            observation(
                objects=(tree(geometry=edge_geometry),),
                tick=20,
                viewport_bounds=viewport,
                geometry_frame_id="edge-frame-20",
            )
        )
        self.assertEqual(ActionKind.WAIT, stable_wait.action.kind)
        self.assertIn("stable proactive framing", stable_wait.reason)

        camera = task.decide(
            observation(
                objects=(tree(geometry=edge_geometry),),
                tick=21,
                viewport_bounds=viewport,
                geometry_frame_id="edge-frame-20",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, camera.action.kind)
        self.assertIn(camera.action.key, {"left", "right", "up", "down"})
        self.assertEqual("barely_visible", camera.evidence.camera.classification)
        self.assertGreaterEqual(camera.action.key_hold_millis, 80)
        self.assertLessEqual(camera.action.key_hold_millis, 250)
        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_POSE_CHANGED, tick=22)
        )

        framed_polygon = (
            ScreenPoint(1240, 2150),
            ScreenPoint(1399, 2150),
            ScreenPoint(1399, 2359),
            ScreenPoint(1240, 2359),
        )
        framed_geometry = replace(
            edge_geometry,
            screen_point=ScreenPoint(1320, 2250),
            screen_bounds=ScreenBounds(1240, 2150, 160, 210),
            screen_polygon=framed_polygon,
        )
        chop = task.decide(
            observation(
                objects=(tree(geometry=framed_geometry),),
                tick=23,
                viewport_bounds=viewport,
                geometry_frame_id="framed-after-camera",
            )
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, chop.action.kind)
        self.assertEqual("well_framed", chop.evidence.camera.classification)
        self.assertEqual("none", chop.evidence.camera.action)

    def test_camera_episode_refuses_unproven_reversal_and_allows_fresh_overshoot(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        target_location = WorldPoint(TREE_AREA.x + 2, TREE_AREA.y, TREE_AREA.plane)
        desired = desired_camera_yaw(TREE_AREA, target_location)
        assert desired is not None
        before_yaw = (desired - 1_000) % 16_384
        polygon = (
            ScreenPoint(1735, 2200),
            ScreenPoint(1794, 2200),
            ScreenPoint(1794, 2319),
            ScreenPoint(1735, 2319),
        )
        right_edge = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1785, 2260),
            screen_bounds=ScreenBounds(1735, 2200, 60, 120),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=1.0,
        )

        def run(after_delta: int) -> tuple[WoodcutBankTask, object]:
            task = WoodcutBankTask()
            target = tree(
                key=f"tree:reversal:{after_delta}",
                location=target_location,
                geometry=TargetGeometry(),
            )
            task.progress.phase = TaskPhase.CHOP
            task.progress.target_key = target.key
            first = task.decide(
                observation(
                    objects=(target,),
                    tick=10,
                    camera_yaw=before_yaw,
                    viewport_bounds=viewport,
                    geometry_frame_id="reversal-hidden-10",
                )
            )
            self.assertEqual(ActionKind.WAIT, first.action.kind)
            coarse = task.decide(
                observation(
                    objects=(target,),
                    tick=11,
                    camera_yaw=before_yaw,
                    viewport_bounds=viewport,
                    geometry_frame_id="reversal-hidden-11",
                )
            )
            self.assertEqual("right", coarse.action.key)
            after_yaw = (before_yaw + after_delta) % 16_384
            task.apply_verification(
                camera_verification_pass(
                    direction="right",
                    before_yaw=before_yaw,
                    after_yaw=after_yaw,
                    before_geometry_frame_id="reversal-hidden-11",
                    after_geometry_frame_id="reversal-edge-12",
                    tick=12,
                )
            )
            edge_target = replace(target, geometry=right_edge)
            fine = task.decide(
                observation(
                    objects=(edge_target,),
                    tick=12,
                    camera_yaw=after_yaw,
                    viewport_bounds=viewport,
                    geometry_frame_id="reversal-edge-12",
                )
            )
            return task, fine

        no_overshoot_task, refused = run(500)
        self.assertEqual(ActionKind.WAIT, refused.action.kind)
        self.assertIn("did not prove", refused.reason)
        self.assertFalse(no_overshoot_task._camera_episode.overshoot_proven)
        self.assertEqual(1, no_overshoot_task._camera_recovery_attempts)

        overshoot_task, allowed = run(1_500)
        self.assertEqual(ActionKind.CAMERA_HOLD, allowed.action.kind)
        self.assertEqual("left", allowed.action.key)
        self.assertTrue(overshoot_task._camera_episode.overshoot_proven)
        self.assertEqual(1, allowed.evidence.camera.response_sample_count)
        self.assertEqual(1_500, allowed.evidence.camera.last_observed_yaw_delta)

    def test_two_action_episode_activates_only_from_fresh_ready_geometry(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        target_location = WorldPoint(TREE_AREA.x + 2, TREE_AREA.y, TREE_AREA.plane)
        desired = desired_camera_yaw(TREE_AREA, target_location)
        assert desired is not None
        initial_yaw = (desired - 2_000) % 16_384
        target = tree(
            key="tree:coarse-fine-ready",
            location=target_location,
            geometry=TargetGeometry(),
        )
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = target.key

        task.decide(
            observation(
                objects=(target,),
                tick=10,
                camera_yaw=initial_yaw,
                viewport_bounds=viewport,
                geometry_frame_id="episode-hidden-10",
            )
        )
        coarse = task.decide(
            observation(
                objects=(target,),
                tick=11,
                camera_yaw=initial_yaw,
                viewport_bounds=viewport,
                geometry_frame_id="episode-hidden-11",
            )
        )
        self.assertEqual("right", coarse.action.key)
        coarse_yaw = (initial_yaw + 800) % 16_384
        task.apply_verification(
            camera_verification_pass(
                direction="right",
                before_yaw=initial_yaw,
                after_yaw=coarse_yaw,
                before_geometry_frame_id="episode-hidden-11",
                after_geometry_frame_id="episode-edge-12",
                tick=12,
            )
        )

        left_polygon = (
            ScreenPoint(1004, 2200),
            ScreenPoint(1063, 2200),
            ScreenPoint(1063, 2319),
            ScreenPoint(1004, 2319),
        )
        edge_target = replace(
            target,
            geometry=TargetGeometry(
                available=True,
                on_screen=True,
                visible=True,
                actionable=True,
                screen_point=ScreenPoint(1012, 2260),
                screen_bounds=ScreenBounds(1004, 2200, 60, 120),
                geometry_source="clickbox",
                screen_polygon=left_polygon,
                visible_area_ratio=1.0,
            ),
        )
        fine = task.decide(
            observation(
                objects=(edge_target,),
                tick=12,
                camera_yaw=coarse_yaw,
                viewport_bounds=viewport,
                geometry_frame_id="episode-edge-12",
            )
        )
        self.assertEqual(ActionKind.CAMERA_HOLD, fine.action.kind)
        self.assertEqual("right", fine.action.key)
        self.assertLessEqual(fine.action.key_hold_millis, 250)
        fine_yaw = (coarse_yaw + 400) % 16_384
        task.apply_verification(
            camera_verification_pass(
                direction="right",
                before_yaw=coarse_yaw,
                after_yaw=fine_yaw,
                before_geometry_frame_id="episode-edge-12",
                after_geometry_frame_id="episode-ready-13",
                tick=13,
            )
        )

        centered_polygon = (
            ScreenPoint(1340, 2180),
            ScreenPoint(1459, 2180),
            ScreenPoint(1459, 2319),
            ScreenPoint(1340, 2319),
        )
        ready_target = replace(
            target,
            geometry=TargetGeometry(
                available=True,
                on_screen=True,
                visible=True,
                actionable=True,
                screen_point=ScreenPoint(1400, 2250),
                screen_bounds=ScreenBounds(1340, 2180, 120, 140),
                geometry_source="clickbox",
                screen_polygon=centered_polygon,
                visible_area_ratio=1.0,
            ),
        )
        activation = task.decide(
            observation(
                objects=(ready_target,),
                tick=13,
                camera_yaw=fine_yaw,
                viewport_bounds=viewport,
                geometry_frame_id="episode-ready-13",
            )
        )

        self.assertEqual(ActionKind.INTERACT_OBJECT, activation.action.kind)
        self.assertEqual(
            CameraAcquisitionState.READY,
            activation.evidence.camera.acquisition_state,
        )
        self.assertEqual(target.key, activation.evidence.camera.locked_target_key)
        self.assertEqual(2, activation.evidence.camera.correction_attempt)
        self.assertEqual(2, activation.evidence.camera.response_sample_count)
        self.assertEqual("episode-ready-13", activation.evidence.camera.geometry_frame_id)

    def test_route_camera_lock_rejects_alternate_until_release_condition(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        first_step = ROUTE_TO_BANK[0]
        second_step = ROUTE_TO_BANK[1]
        viewport = ScreenBounds(1000, 2000, 800, 600)
        first_target = route_tile(first_step, geometry=TargetGeometry())
        second_target = route_tile(second_step, geometry=TargetGeometry())
        framing = task.behavior.classify_camera(
            TargetGeometry(),
            viewport,
            decision_id="locked-route-framing",
            yaw_error_units=2_000,
            source_tick=10,
            geometry_frame_id="locked-route-10",
            camera_pitch=1024,
        )
        started = task._recover_route_projection(
            observation(
                location=WorldPoint(3195, 3248, 0),
                inv=inventory(logs=28, full=True),
                objects=(first_target, second_target),
                tick=10,
                viewport_bounds=viewport,
                geometry_frame_id="locked-route-10",
            ),
            first_step,
            first_target,
            framing=framing,
            route_index=0,
        )
        self.assertEqual(ActionKind.WAIT, started.action.kind)
        episode_id = task._camera_episode.episode_id

        refused = task._recover_route_projection(
            observation(
                location=WorldPoint(3195, 3248, 0),
                inv=inventory(logs=28, full=True),
                objects=(first_target, second_target),
                tick=11,
                viewport_bounds=viewport,
                geometry_frame_id="locked-route-11",
            ),
            second_step,
            second_target,
            framing=framing,
            route_index=1,
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertNotEqual(ActionKind.PRESS_KEY, refused.action.kind)
        self.assertEqual(episode_id, task._camera_episode.episode_id)
        self.assertEqual(first_target.key, task._camera_episode.locked_target_key)
        self.assertEqual(CameraAcquisitionState.INVALIDATED, task._camera_episode.state)

    def test_route_camera_lock_retains_selector_approved_short_correction(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 2
        current = WorldPoint(3200, 3238, 0)
        corridor = ROUTE_TO_BANK[2]
        corner = ROUTE_TO_BANK[3]
        corridor_target = route_tile(corridor, geometry=NAV_GEOMETRY)
        corner_target = route_tile(
            corner,
            geometry=TargetGeometry(
                available=True,
                scene_supported=True,
                collision_supported=True,
                shortcut_clear=False,
            ),
        )

        stabilizing = task.decide(
            observation(
                location=current,
                inv=inventory(logs=28, full=True),
                objects=(corridor_target, corner_target),
                tick=10,
                camera_yaw=0,
                geometry_frame_id="short-correction-10",
            )
        )
        self.assertEqual(ActionKind.WAIT, stabilizing.action.kind)
        self.assertEqual(corner.target_key, task._camera_episode.locked_target_key)
        episode_id = task._camera_episode.episode_id

        correction = task.decide(
            observation(
                location=current,
                inv=inventory(logs=28, full=True),
                objects=(corridor_target, corner_target),
                tick=11,
                camera_yaw=0,
                geometry_frame_id="short-correction-10",
            )
        )

        self.assertEqual(ActionKind.CAMERA_HOLD, correction.action.kind)
        self.assertEqual(episode_id, task._camera_episode.episode_id)
        self.assertEqual(corner.target_key, correction.action.target_key)

    def test_route_camera_lock_releases_shortcut_unsupported_distant_target(self) -> None:
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        task.progress.route_index = 2
        corner = ROUTE_TO_BANK[3]
        corner_target = route_tile(
            corner,
            geometry=TargetGeometry(
                available=True,
                scene_supported=True,
                collision_supported=True,
                shortcut_clear=False,
            ),
        )
        framing = task.behavior.classify_camera(
            corner_target.geometry,
            ScreenBounds(1000, 2000, 800, 600),
            decision_id="distant-shortcut-framing",
            yaw_error_units=2_000,
            source_tick=10,
            geometry_frame_id="distant-shortcut-10",
            camera_pitch=1024,
        )
        task._recover_route_projection(
            observation(
                location=WorldPoint(3200, 3238, 0),
                inv=inventory(logs=28, full=True),
                objects=(corner_target,),
                tick=10,
                geometry_frame_id="distant-shortcut-10",
            ),
            corner,
            corner_target,
            framing=framing,
            route_index=3,
        )

        released = task.decide(
            observation(
                location=WorldPoint(3202, 3240, 0),
                inv=inventory(logs=28, full=True),
                objects=(corner_target,),
                tick=11,
                geometry_frame_id="distant-shortcut-11",
            )
        )

        self.assertNotEqual(ActionKind.PRESS_KEY, released.action.kind)
        self.assertIsNone(task._camera_episode)

    def test_camera_episode_geometry_change_is_terminal_before_keypress(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        target = tree(key="tree:geometry-lock", geometry=TargetGeometry())
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = target.key
        started = task.decide(
            observation(
                objects=(target,),
                tick=10,
                viewport_bounds=viewport,
                geometry_frame_id="geometry-lock-10",
            )
        )
        self.assertEqual(ActionKind.WAIT, started.action.kind)

        changed = replace(
            observation(
                objects=(target,),
                tick=11,
                viewport_bounds=viewport,
                geometry_frame_id="geometry-lock-11",
            ),
            canvas_bounds=ScreenBounds(1001, 2000, 800, 600),
        )
        blocked = task.decide(changed)

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertNotEqual(ActionKind.PRESS_KEY, blocked.action.kind)
        self.assertIn("canvas geometry changed", blocked.reason)
        self.assertEqual(
            CameraAcquisitionState.INVALIDATED,
            blocked.evidence.camera.acquisition_state,
        )

    def test_zoom_required_unavailable_is_typed_and_sends_no_key(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        polygon = (
            ScreenPoint(900, 2020),
            ScreenPoint(1899, 2020),
            ScreenPoint(1899, 2579),
            ScreenPoint(900, 2579),
        )
        geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1790, 2300),
            screen_bounds=ScreenBounds(900, 2020, 1000, 560),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=0.7,
        )
        target = tree(key="tree:zoom-required", geometry=geometry)
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = target.key

        blocked = task.decide(
            observation(
                objects=(target,),
                tick=10,
                viewport_bounds=viewport,
                geometry_frame_id="zoom-required-10",
                camera_zoom=700,
            )
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertNotEqual(ActionKind.PRESS_KEY, blocked.action.kind)
        self.assertIsNone(task.progress.pending)
        self.assertEqual(
            CameraAcquisitionState.ZOOM_REQUIRED_BUT_UNAVAILABLE,
            blocked.evidence.camera.acquisition_state,
        )
        self.assertTrue(blocked.evidence.camera.zoom_required_but_unavailable)

    def test_zoom_required_emits_one_semantic_wheel_then_blocks_repetition(self) -> None:
        viewport = ScreenBounds(1000, 2000, 800, 600)
        client = ScreenBounds(990, 1990, 820, 620)
        polygon = (
            ScreenPoint(900, 2020),
            ScreenPoint(1899, 2020),
            ScreenPoint(1899, 2579),
            ScreenPoint(900, 2579),
        )
        geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(1790, 2300),
            screen_bounds=ScreenBounds(900, 2020, 1000, 560),
            geometry_source="clickbox",
            screen_polygon=polygon,
            visible_area_ratio=0.7,
        )
        target = tree(key="tree:zoom-semantic", geometry=geometry)
        task = WoodcutBankTask()
        task.progress.phase = TaskPhase.CHOP
        task.progress.target_key = target.key

        zoom = task.decide(
            observation(
                objects=(target,),
                tick=10,
                viewport_bounds=viewport,
                client_window_bounds=client,
                geometry_frame_id="zoom-semantic-10",
                camera_zoom=700,
                text_input_active=False,
            )
        )

        self.assertEqual(ActionKind.CAMERA_ZOOM, zoom.action.kind)
        self.assertIsNone(zoom.action.key)
        self.assertIsNone(zoom.action.screen_point)
        self.assertIsNotNone(zoom.action.task_constraints.camera_zoom)
        constraint = zoom.action.task_constraints.camera_zoom
        assert constraint is not None
        self.assertEqual(-1, constraint.amount)
        self.assertEqual(target.key, constraint.target_key)
        self.assertEqual(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            task.progress.pending.kind,
        )
        self.assertEqual(-1, task.progress.pending.camera_zoom_amount)

        task.apply_verification(
            verification_pass(OutcomeKind.CAMERA_ZOOM_CHANGED, tick=11)
        )
        blocked = task.decide(
            observation(
                objects=(target,),
                tick=11,
                viewport_bounds=viewport,
                client_window_bounds=client,
                geometry_frame_id="zoom-semantic-11",
                camera_zoom=680,
                text_input_active=False,
            )
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, blocked.action.kind)
        self.assertIn("one bounded attempt", blocked.reason)

    def test_observation_query_plan_is_player_anchored_then_exact_target_bounded(self) -> None:
        task = WoodcutBankTask()
        player = WorldPoint(TREE_AREA.x + 3, TREE_AREA.y - 2, TREE_AREA.plane)
        waiting = task.decide(observation(location=player, tick=10))
        self.assertEqual(ActionKind.WAIT, waiting.action.kind)

        discovery = task.observation_request()
        self.assertEqual("resource_discovery", discovery.purpose)
        self.assertEqual(player, discovery.center_world_location)
        self.assertEqual(DEFINITION.resource.work_area.radius, discovery.radius_tiles)
        self.assertEqual(64, discovery.max_objects)
        self.assertEqual(32, discovery.max_projection_objects)
        self.assertEqual((TREE_OBJECT_ID,), discovery.priority_object_ids)
        self.assertEqual((), discovery.priority_object_keys)

        selected = tree(
            key="tree:query-lock",
            location=WorldPoint(player.x + 1, player.y, player.plane),
        )
        task.decide(observation(location=player, objects=(selected,), tick=11))
        exact = task.observation_request()
        self.assertEqual("resource_target_verification", exact.purpose)
        self.assertEqual(selected.location, exact.center_world_location)
        self.assertEqual(4, exact.radius_tiles)
        self.assertEqual(16, exact.max_objects)
        self.assertEqual(8, exact.max_projection_objects)
        self.assertEqual((selected.key,), exact.priority_object_keys)
        self.assertEqual((selected.object_id,), exact.priority_object_ids)

    def test_route_and_bank_query_plans_use_definition_owned_anchors(self) -> None:
        route_task = WoodcutBankTask()
        route_task.progress.phase = TaskPhase.NAVIGATE_TO_BANK
        route_request = route_task.observation_request()
        self.assertEqual("route_lookahead", route_request.purpose)
        self.assertEqual(ROUTE_TO_BANK[0].location, route_request.center_world_location)
        self.assertEqual(16, route_request.radius_tiles)
        self.assertEqual(24, route_request.max_objects)
        self.assertLessEqual(len(route_request.tile_projections), 16)

        transition_index = next(
            index for index, step in enumerate(ROUTE_TO_BANK) if not step.is_walk
        )
        route_task.progress.route_index = transition_index
        transition = route_task.observation_request()
        self.assertEqual("route_transition_acquisition", transition.purpose)
        self.assertEqual(ROUTE_TO_BANK[transition_index].location, transition.center_world_location)
        self.assertEqual(4, transition.radius_tiles)
        self.assertEqual(16, transition.max_objects)

        bank_task = WoodcutBankTask()
        bank_task.progress.phase = TaskPhase.OPEN_BANK
        bank_request = bank_task.observation_request()
        self.assertEqual("bank_acquisition", bank_request.purpose)
        self.assertEqual(BANK_ANCHOR, bank_request.center_world_location)
        self.assertEqual(4, bank_request.radius_tiles)
        self.assertEqual((BANK_OBJECT_ID,), bank_request.priority_object_ids)

    def test_locked_target_survives_capped_and_unknown_omissions_without_ping_pong(self) -> None:
        locked = tree(key="tree:capped-continuity")
        task = WoodcutBankTask()
        task.decide(observation(objects=(locked,), tick=10))
        capped = SceneCensusEvidence(
            metadata_present=True,
            complete=True,
            authoritative_absence_eligible=False,
            priority_absence_eligible=False,
            scene_coverage_complete=True,
            count=100,
            returned=64,
            response_cap_hit=True,
            source_cap_hit=False,
            reported_priority_object_keys=(locked.key,),
            returned_priority_object_keys=(),
            conflicting_duplicate_keys=("unrelated:key",),
        )

        first = task.decide(
            observation(objects=(), tick=11, scene_census=capped)
        )
        second = task.decide(
            observation(objects=(), tick=12, scene_census=capped)
        )

        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(locked.key, task.progress.target_key)
        self.assertEqual(locked.key, task._target_lock.key)
        self.assertIn("retained", first.reason)
        self.assertIn("2/2", second.reason)
        continuity = task.snapshot().target_continuity
        self.assertIsNotNone(continuity)
        self.assertEqual(locked.key, continuity.locked_target_key)
        self.assertEqual(10, continuity.locked_tick)
        self.assertEqual(10, continuity.last_seen_tick)
        self.assertEqual(2, continuity.incomplete_omission_frames)
        self.assertIn("frame 2/2", continuity.retention_reason)
        self.assertIsNone(continuity.last_unlock_reason)
        self.assertEqual((locked.key,), task.observation_request().priority_object_keys)

        reappeared = task.decide(
            observation(objects=(locked,), tick=13, scene_census=capped)
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, reappeared.action.kind)
        self.assertEqual(locked.key, reappeared.action.target_key)
        continuity = task.snapshot().target_continuity
        self.assertEqual(13, continuity.last_seen_tick)
        self.assertEqual(0, continuity.incomplete_omission_frames)
        self.assertEqual("fresh exact identity retained", continuity.retention_reason)

        exhausted_task = WoodcutBankTask()
        exhausted_task.decide(observation(objects=(locked,), tick=20))
        exhausted_task.decide(
            observation(objects=(), tick=21, scene_census=capped)
        )
        exhausted_task.decide(
            observation(objects=(), tick=22, scene_census=capped)
        )
        exhausted = exhausted_task.decide(
            observation(objects=(), tick=23, scene_census=capped)
        )
        self.assertEqual(TaskPhase.BLOCKED, exhausted_task.progress.phase)
        self.assertEqual(locked.key, exhausted_task._target_lock.key)
        self.assertIn("budget", exhausted.reason)
        self.assertEqual(
            ("target_omission_wait_exhausted",),
            exhausted.evidence.rejected[0].rejection_codes,
        )

    def test_exact_priority_absence_unlocks_but_complete_alone_does_not(self) -> None:
        locked = tree(key="tree:priority-absence")
        task = WoodcutBankTask()
        task.decide(observation(objects=(locked,), tick=10))

        complete_but_not_authoritative = SceneCensusEvidence(
            metadata_present=True,
            complete=True,
            authoritative_absence_eligible=False,
            priority_absence_eligible=False,
            scene_coverage_complete=True,
            response_cap_hit=True,
            source_cap_hit=False,
            reported_priority_object_keys=(locked.key,),
        )
        retained = task.decide(
            observation(
                objects=(),
                tick=11,
                scene_census=complete_but_not_authoritative,
            )
        )
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertIn("retained", retained.reason)

        unsolicited_absence = replace(
            complete_but_not_authoritative,
            priority_absence_eligible=True,
        )
        still_retained = task.decide(
            observation(objects=(), tick=12, scene_census=unsolicited_absence)
        )
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertIsNotNone(task._target_lock)
        self.assertIn("retained", still_retained.reason)

        exact_absence = replace(
            unsolicited_absence,
            requested_priority_object_keys=(locked.key,),
        )
        unlocked = task.decide(
            observation(objects=(), tick=13, scene_census=exact_absence)
        )
        self.assertEqual(TaskPhase.FIND_TREE, task.progress.phase)
        self.assertIsNone(task.progress.target_key)
        self.assertIsNone(task._target_lock)
        self.assertIn("authoritative exact-priority", unlocked.reason)
        self.assertEqual(
            ("authoritative_target_absence",),
            unlocked.evidence.rejected[0].rejection_codes,
        )
        continuity = task.snapshot().target_continuity
        self.assertIsNotNone(continuity)
        self.assertIsNone(continuity.locked_target_key)
        self.assertIsNone(continuity.locked_tick)
        self.assertIsNone(continuity.last_seen_tick)
        self.assertEqual(0, continuity.incomplete_omission_frames)
        self.assertIsNone(continuity.retention_reason)
        self.assertIn("authoritative exact-priority", continuity.last_unlock_reason)

    def test_explicit_raw_incompleteness_denies_object_activation_but_keeps_lock(self) -> None:
        locked = tree(key="tree:incomplete-activation")
        task = WoodcutBankTask()
        task.decide(observation(objects=(locked,), tick=10))
        incomplete = SceneCensusEvidence(
            metadata_present=True,
            complete=False,
            authoritative_absence_eligible=False,
            priority_absence_eligible=False,
            scene_coverage_complete=False,
            count=100,
            returned=64,
            response_cap_hit=True,
            source_cap_hit=False,
            reported_priority_object_keys=(locked.key,),
            returned_priority_object_keys=(locked.key,),
        )

        incomplete_row = replace(locked, actions=())
        denied = task.decide(
            observation(objects=(incomplete_row,), tick=11, scene_census=incomplete)
        )

        self.assertEqual(ActionKind.WAIT, denied.action.kind)
        self.assertIsNone(task.progress.pending)
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(locked.key, task._target_lock.key)
        self.assertEqual(
            ("action_unavailable", "incomplete_census_for_activation"),
            denied.evidence.rejected[0].rejection_codes,
        )

    def test_unknown_legacy_census_denies_object_activation_but_keeps_lock(self) -> None:
        locked = tree(key="tree:legacy-unknown-activation")
        task = WoodcutBankTask()
        task.decide(observation(objects=(locked,), tick=10))

        denied = task.decide(
            observation(
                objects=(locked,),
                tick=11,
                scene_census=SceneCensusEvidence(),
            )
        )

        self.assertEqual(ActionKind.WAIT, denied.action.kind)
        self.assertEqual(TaskPhase.CHOP, task.progress.phase)
        self.assertEqual(locked.key, task._target_lock.key)
        self.assertEqual(
            ("census_authority_unknown_for_activation",),
            denied.evidence.rejected[0].rejection_codes,
        )

    def test_contradictory_duplicate_quarantine_blocks_locked_target(self) -> None:
        locked = tree(key="tree:contradictory-lock")
        task = WoodcutBankTask()
        task.decide(observation(objects=(locked,), tick=10))
        contradictory = SceneCensusEvidence(
            metadata_present=True,
            complete=False,
            authoritative_absence_eligible=False,
            priority_absence_eligible=False,
            scene_coverage_complete=False,
            conflicting_duplicate_keys=(locked.key,),
        )

        blocked = task.decide(
            observation(objects=(), tick=11, scene_census=contradictory)
        )

        self.assertEqual(TaskPhase.BLOCKED, task.progress.phase)
        self.assertEqual(ActionKind.WAIT, blocked.action.kind)
        self.assertEqual(
            ("contradictory_duplicate_identity",),
            blocked.evidence.rejected[0].rejection_codes,
        )

    def test_resource_selection_is_permutation_stable_and_index_bounded(self) -> None:
        ready = tree(
            key="tree:a-ready",
            location=WorldPoint(TREE_AREA.x + 2, TREE_AREA.y, TREE_AREA.plane),
        )
        hidden = tree(
            key="tree:b-hidden",
            location=TREE_AREA,
            geometry=TargetGeometry(),
        )
        irrelevant = scene_object(
            "irrelevant:rock",
            99_999,
            "Rock",
            "Mine",
            TREE_AREA,
            geometry=TargetGeometry(),
        )
        outcomes = set()
        for ordering in permutations((hidden, irrelevant, ready)):
            task = WoodcutBankTask()
            decision = task.decide(observation(objects=ordering, tick=10))
            outcomes.add(
                (
                    task.progress.target_key,
                    tuple(target.key for target in decision.evidence.eligible),
                    tuple(item.target.key for item in decision.evidence.rejected),
                )
            )
        self.assertEqual(
            {(ready.key, (ready.key, hidden.key), (irrelevant.key,))},
            outcomes,
        )

        dense_irrelevant = tuple(
            scene_object(
                f"irrelevant:{index:04d}",
                99_999,
                "Rock",
                "Mine",
                TREE_AREA,
                geometry=TargetGeometry(),
            )
            for index in range(1_000)
        )
        task = WoodcutBankTask()
        decision = task.decide(
            observation(objects=(*dense_irrelevant, ready), tick=20)
        )
        metrics = task.last_resource_selection_metrics
        self.assertEqual(ready.key, decision.evidence.selected.key)
        self.assertEqual(1_001, metrics["scene_objects"])
        self.assertEqual(1, metrics["indexed_candidates"])
        self.assertEqual(33, metrics["identity_evaluations"])
        self.assertEqual(1, metrics["ranked_candidates"])
        self.assertEqual(32, len(decision.evidence.rejected))


if __name__ == "__main__":
    unittest.main()

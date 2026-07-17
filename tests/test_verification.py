from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from osrs_bot.model import (
    DialogueOption,
    InventoryItem,
    InventoryObservation,
    Observation,
    PlayerObservation,
    ScreenBounds,
    VerificationKind,
    VerificationSpec,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.verification import (
    CameraZoomResult,
    Outcome,
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
    Verifier,
)


ITEM_ID = 42


def item_inventory(
    quantity: int, *, item_id: int = ITEM_ID, known: bool = True
) -> InventoryObservation:
    items = (
        (InventoryItem(slot=0, item_id=item_id, quantity=quantity),)
        if quantity > 0
        else ()
    )
    return InventoryObservation(
        items=items,
        occupied_slots=1 if items else 0,
        free_slots=27 if items else 28,
        known=known,
    )


def observation(
    *,
    tick: int = 101,
    location: WorldPoint = WorldPoint(3201, 3200, 0),
    inventory: InventoryObservation | None = None,
    widgets: WidgetObservation | None = None,
    camera_yaw: int | None = None,
    camera_pitch: int | None = None,
    camera_zoom: int | None = None,
    geometry_frame_id: str | None = None,
    text_input_active: bool | None = False,
) -> Observation:
    timestamp = datetime.now(timezone.utc)
    session_id = "session-1"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=location,
        plane=location.plane,
        inventory=inventory or item_inventory(1),
        nearby_objects=(),
        menus=(),
        widgets=widgets or WidgetObservation(),
        canvas_bounds=ScreenBounds(0, 0, 765, 503),
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id=session_id,
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
    )


def specification(kind: VerificationKind, **changes: object) -> VerificationSpec:
    values = {
        "kind": kind,
        "before_tick": 100,
        "deadline_tick": 105,
        "source_session_id": "session-1",
    }
    values.update(changes)
    return VerificationSpec(**values)


class VerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = Verifier(max_observation_age_seconds=2.0)

    def test_pass_requires_a_typed_outcome(self) -> None:
        with self.assertRaises(ValueError):
            VerificationResult(VerificationStatus.PASS, "missing_outcome")
        present = VerificationResult(
            VerificationStatus.PASS,
            OutcomeKind.ARRIVED.value,
            Outcome(OutcomeKind.ARRIVED, 101),
        )

        self.assertTrue(present.passed)

    def test_fail_requires_a_typed_failure_and_only_fail_may_carry_it(self) -> None:
        with self.assertRaises(ValueError):
            VerificationResult(VerificationStatus.FAIL, "missing_failure_kind")
        with self.assertRaises(ValueError):
            VerificationResult(
                VerificationStatus.PENDING,
                "not_failed",
                failure_kind=VerificationFailureKind.RUNTIME_FAILURE,
            )

    def test_waits_for_an_observation_later_than_the_action(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=1,
            ),
            observation(tick=100, inventory=item_inventory(2)),
        )

        self.assertEqual(VerificationStatus.PENDING, result.status)
        self.assertEqual("awaiting_later_observation", result.reason)
        self.assertIsNone(result.outcome)

    def test_emits_item_quantity_increased(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=1,
            ),
            observation(inventory=item_inventory(2)),
        )

        self.assertTrue(result.passed)
        self.assertEqual("item_quantity_increased", result.reason)
        self.assertEqual(Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 101), result.outcome)

    def test_emits_item_quantity_equals_without_an_interface(self) -> None:
        spec = specification(
            VerificationKind.ITEM_QUANTITY_EQUALS,
            item_id=ITEM_ID,
            expected_quantity=0,
        )

        result = self.verifier.evaluate(spec, observation(inventory=item_inventory(0)))
        unmatched = self.verifier.evaluate(spec, observation(inventory=item_inventory(1)))

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.ITEM_QUANTITY_EQUALS, result.outcome.kind)
        self.assertTrue(unmatched.pending)

    def test_bank_item_quantity_equals_requires_readable_open_interface(self) -> None:
        spec = specification(
            VerificationKind.ITEM_QUANTITY_EQUALS,
            item_id=ITEM_ID,
            expected_quantity=0,
            interface_name="bank",
            expected_plane=0,
        )
        readable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=True)
        unreadable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=False)

        result = self.verifier.evaluate(
            spec, observation(inventory=item_inventory(0), widgets=readable)
        )
        pending = self.verifier.evaluate(
            spec, observation(inventory=item_inventory(0), widgets=unreadable)
        )

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.ITEM_QUANTITY_EQUALS, result.outcome.kind)
        self.assertTrue(pending.pending)

    def test_emits_moved_closer_and_arrived(self) -> None:
        spec = specification(
            VerificationKind.MOVED_CLOSER,
            before_location=WorldPoint(3200, 3200, 0),
            target_location=WorldPoint(3205, 3200, 0),
            target_radius=0,
        )

        closer = self.verifier.evaluate(
            spec, observation(location=WorldPoint(3202, 3200, 0))
        )
        arrived = self.verifier.evaluate(
            spec, observation(location=WorldPoint(3205, 3200, 0))
        )
        unchanged = self.verifier.evaluate(
            spec, observation(location=WorldPoint(3200, 3200, 0))
        )

        self.assertEqual(OutcomeKind.MOVED_CLOSER, closer.outcome.kind)
        self.assertEqual(OutcomeKind.ARRIVED, arrived.outcome.kind)
        self.assertTrue(closer.passed)
        self.assertTrue(arrived.passed)
        self.assertTrue(unchanged.pending)

    def test_emits_arrived_when_baseline_was_already_at_target(self) -> None:
        target = WorldPoint(3205, 3200, 0)
        spec = specification(
            VerificationKind.MOVED_CLOSER,
            before_location=target,
            target_location=target,
            target_radius=0,
        )

        result = self.verifier.evaluate(spec, observation(location=target))

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.ARRIVED, result.outcome.kind)

    def test_camera_pose_change_requires_direction_frame_and_stationary_player(self) -> None:
        location = WorldPoint(3195, 3248, 0)
        spec = specification(
            VerificationKind.CAMERA_POSE_CHANGED,
            before_location=location,
            before_camera_yaw=0,
            before_geometry_frame_id="camera-frame-0",
            camera_key="right",
        )

        changed = self.verifier.evaluate(
            spec,
            observation(
                location=location,
                camera_yaw=1_200,
                geometry_frame_id="camera-frame-1200",
            ),
        )
        self.assertTrue(changed.passed)
        self.assertEqual(OutcomeKind.CAMERA_POSE_CHANGED, changed.outcome.kind)
        pose = changed.outcome.camera_pose_result
        self.assertIsNotNone(pose)
        self.assertEqual("right", pose.camera_key)
        self.assertEqual(1_200, pose.yaw_delta)
        self.assertEqual("camera-frame-0", pose.before_geometry_frame_id)
        self.assertEqual("camera-frame-1200", pose.after_geometry_frame_id)

        left = replace(spec, before_camera_yaw=0, camera_key="left")
        wrapped_left = self.verifier.evaluate(
            left,
            observation(
                location=location,
                camera_yaw=16_147,
                geometry_frame_id="camera-frame-16147",
            ),
        )
        self.assertTrue(wrapped_left.passed)
        self.assertEqual(-237, wrapped_left.outcome.camera_pose_result.yaw_delta)

        pitch_up = replace(
            spec,
            before_camera_pitch=900,
            camera_key="up",
        )
        pitch_up_result = self.verifier.evaluate(
            pitch_up,
            observation(
                location=location,
                camera_yaw=0,
                camera_pitch=1_050,
                geometry_frame_id="camera-pitch-1050",
            ),
        )
        self.assertTrue(pitch_up_result.passed)
        self.assertEqual(150, pitch_up_result.outcome.camera_pose_result.pitch_delta)
        pitch_down = replace(
            pitch_up,
            before_camera_pitch=1_050,
            camera_key="down",
        )
        self.assertTrue(
            self.verifier.evaluate(
                pitch_down,
                observation(
                    location=location,
                    camera_yaw=0,
                    camera_pitch=930,
                    geometry_frame_id="camera-pitch-930",
                ),
            ).passed
        )

        for candidate in (
            observation(
                location=location,
                camera_yaw=1_200,
                geometry_frame_id="camera-frame-0",
            ),
            observation(
                location=WorldPoint(3196, 3248, 0),
                camera_yaw=1_200,
                geometry_frame_id="camera-frame-1200",
            ),
            observation(
                location=location,
                camera_yaw=16_000,
                geometry_frame_id="camera-frame-16000",
            ),
        ):
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    VerificationStatus.PENDING,
                    self.verifier.evaluate(spec, candidate).status,
                )

    def test_camera_zoom_change_requires_expected_signed_delta_and_retains_evidence(self) -> None:
        location = WorldPoint(3195, 3248, 0)
        base = specification(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            before_location=location,
            before_camera_yaw=640,
            before_camera_pitch=900,
            before_camera_zoom=300,
            camera_zoom_amount=1,
            before_process_id=1234,
            before_geometry_frame_id="zoom-frame-100",
            before_bank_known=False,
            before_bank_open=False,
            before_bank_pin_open=False,
            before_bank_readable=False,
            before_dialogue_active=False,
            before_dialogue_type="none",
            before_text_input_active=False,
        )

        increased = self.verifier.evaluate(
            base,
            observation(
                location=location,
                camera_yaw=640,
                camera_pitch=900,
                camera_zoom=324,
                geometry_frame_id="zoom-frame-101",
            ),
        )
        self.assertTrue(increased.passed)
        self.assertEqual(OutcomeKind.CAMERA_ZOOM_CHANGED, increased.outcome.kind)
        result = increased.outcome.camera_zoom_result
        self.assertIsInstance(result, CameraZoomResult)
        self.assertEqual(1, result.wheel_amount)
        self.assertEqual(24, result.zoom_delta)
        self.assertEqual(result.before_yaw, result.after_yaw)
        self.assertEqual(result.before_pitch, result.after_pitch)
        self.assertEqual(result.before_ui_state, result.after_ui_state)
        self.assertNotEqual(
            result.before_geometry_frame_id,
            result.after_geometry_frame_id,
        )

        decreased = self.verifier.evaluate(
            replace(base, camera_zoom_amount=-1),
            observation(
                location=location,
                camera_yaw=640,
                camera_pitch=900,
                camera_zoom=276,
                geometry_frame_id="zoom-frame-102",
            ),
        )
        self.assertTrue(decreased.passed)
        self.assertEqual(-24, decreased.outcome.camera_zoom_result.zoom_delta)

    def test_camera_zoom_unchanged_waits_then_fails_typed(self) -> None:
        spec = self._camera_zoom_specification()
        before_deadline = self.verifier.evaluate(
            spec,
            observation(
                tick=104,
                location=spec.before_location,
                camera_yaw=spec.before_camera_yaw,
                camera_pitch=spec.before_camera_pitch,
                camera_zoom=spec.before_camera_zoom,
                geometry_frame_id="zoom-frame-104",
            ),
        )
        at_deadline = self.verifier.evaluate(
            spec,
            observation(
                tick=105,
                location=spec.before_location,
                camera_yaw=spec.before_camera_yaw,
                camera_pitch=spec.before_camera_pitch,
                camera_zoom=spec.before_camera_zoom,
                geometry_frame_id="zoom-frame-105",
            ),
        )

        self.assertTrue(before_deadline.pending)
        self.assertEqual("condition_not_met", before_deadline.reason)
        self.assertTrue(at_deadline.failed)
        self.assertIs(
            VerificationFailureKind.CAMERA_ZOOM_UNCHANGED_AT_DEADLINE,
            at_deadline.failure_kind,
        )

        unavailable = self.verifier.evaluate(
            spec,
            observation(
                tick=105,
                location=spec.before_location,
                camera_yaw=spec.before_camera_yaw,
                camera_pitch=None,
                camera_zoom=340,
                geometry_frame_id="zoom-frame-105-missing-pose",
            ),
        )
        self.assertIs(
            VerificationFailureKind.CAMERA_ZOOM_EVIDENCE_UNAVAILABLE_AT_DEADLINE,
            unavailable.failure_kind,
        )

    def test_camera_zoom_contradictory_direction_fails_immediately(self) -> None:
        spec = self._camera_zoom_specification(camera_zoom_amount=1)
        result = self.verifier.evaluate(
            spec,
            observation(
                location=spec.before_location,
                camera_yaw=spec.before_camera_yaw,
                camera_pitch=spec.before_camera_pitch,
                camera_zoom=280,
                geometry_frame_id="zoom-frame-contradiction",
            ),
        )

        self.assertTrue(result.failed)
        self.assertEqual("camera_zoom_direction_contradicted", result.reason)
        self.assertIs(
            VerificationFailureKind.CAMERA_ZOOM_DIRECTION_CONTRADICTED,
            result.failure_kind,
        )

    def test_camera_zoom_rejects_pose_identity_and_ui_changes(self) -> None:
        spec = self._camera_zoom_specification()
        base = observation(
            location=spec.before_location,
            camera_yaw=spec.before_camera_yaw,
            camera_pitch=spec.before_camera_pitch,
            camera_zoom=340,
            geometry_frame_id="zoom-frame-changed",
        )
        cases = {
            "yaw": (
                replace(base, camera_yaw=641),
                VerificationFailureKind.CAMERA_POSE_CHANGED_DURING_ZOOM,
            ),
            "pitch": (
                replace(base, camera_pitch=901),
                VerificationFailureKind.CAMERA_POSE_CHANGED_DURING_ZOOM,
            ),
            "pid": (
                replace(base, client_process_id=4321),
                VerificationFailureKind.CAMERA_IDENTITY_CHANGED,
            ),
            "player": (
                replace(base, location=WorldPoint(3196, 3248, 0)),
                VerificationFailureKind.CAMERA_IDENTITY_CHANGED,
            ),
            "bank": (
                replace(base, widgets=WidgetObservation(bank_known=True, bank_open=True)),
                VerificationFailureKind.CAMERA_UI_STATE_CHANGED,
            ),
            "bank pin": (
                replace(base, widgets=WidgetObservation(bank_pin_open=True)),
                VerificationFailureKind.CAMERA_UI_STATE_CHANGED,
            ),
            "dialogue": (
                replace(
                    base,
                    widgets=WidgetObservation(
                        dialogue_active=True,
                        dialogue_type="continue",
                    ),
                ),
                VerificationFailureKind.CAMERA_UI_STATE_CHANGED,
            ),
            "text": (
                replace(base, text_input_active=True),
                VerificationFailureKind.CAMERA_UI_STATE_CHANGED,
            ),
        }
        for label, (candidate, failure_kind) in cases.items():
            with self.subTest(label=label):
                result = self.verifier.evaluate(spec, candidate)
                self.assertTrue(result.failed)
                self.assertIs(failure_kind, result.failure_kind)

    def test_camera_zoom_requires_later_fresh_coherent_changed_geometry(self) -> None:
        spec = self._camera_zoom_specification()
        changed = observation(
            location=spec.before_location,
            camera_yaw=spec.before_camera_yaw,
            camera_pitch=spec.before_camera_pitch,
            camera_zoom=340,
            geometry_frame_id="zoom-frame-101",
        )
        same_tick = replace(changed, tick=100)
        incoherent = replace(changed, source_coherent=False)
        unchanged_geometry = replace(
            changed,
            geometry_frame_id=spec.before_geometry_frame_id,
        )

        self.assertEqual(
            "awaiting_later_observation",
            self.verifier.evaluate(spec, same_tick).reason,
        )
        self.assertEqual(
            "observation_not_usable",
            self.verifier.evaluate(spec, incoherent).reason,
        )
        self.assertEqual(
            "condition_not_met",
            self.verifier.evaluate(spec, unchanged_geometry).reason,
        )

    def _camera_zoom_specification(
        self,
        *,
        camera_zoom_amount: int = 1,
    ) -> VerificationSpec:
        return specification(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            before_location=WorldPoint(3195, 3248, 0),
            before_camera_yaw=640,
            before_camera_pitch=900,
            before_camera_zoom=300,
            camera_zoom_amount=camera_zoom_amount,
            before_process_id=1234,
            before_geometry_frame_id="zoom-frame-100",
            before_bank_known=False,
            before_bank_open=False,
            before_bank_pin_open=False,
            before_bank_readable=False,
            before_dialogue_active=False,
            before_dialogue_type="none",
            before_text_input_active=False,
        )

    def test_emits_plane_changed(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.PLANE_CHANGED,
                before_location=WorldPoint(3200, 3200, 0),
                expected_plane=1,
            ),
            observation(location=WorldPoint(3200, 3200, 1)),
        )

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.PLANE_CHANGED, result.outcome.kind)

    def test_route_transition_emits_plane_or_dialogue_outcome(self) -> None:
        spec = specification(
            VerificationKind.ROUTE_TRANSITION,
            before_location=WorldPoint(3205, 3229, 0),
            expected_plane=1,
            dialogue_prompt_contains="direction",
            dialogue_option_contains="ascend",
        )
        dialogue = WidgetObservation(
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Choose a direction",
            dialogue_options=(DialogueOption(1, "1", "Ascend to the next floor"),),
            dialogue_number_keys=True,
        )

        appeared = self.verifier.evaluate(spec, observation(widgets=dialogue))
        changed = self.verifier.evaluate(
            spec, observation(location=WorldPoint(3205, 3229, 1))
        )

        self.assertEqual(OutcomeKind.DIALOGUE_OPTION_APPEARED, appeared.outcome.kind)
        self.assertEqual(OutcomeKind.PLANE_CHANGED, changed.outcome.kind)
        self.assertTrue(appeared.passed)
        self.assertTrue(changed.passed)

    def test_emits_interface_opened_only_when_bank_is_readable(self) -> None:
        spec = specification(
            VerificationKind.INTERFACE_OPENED,
            interface_name="bank",
            expected_plane=0,
        )
        readable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=True)
        unreadable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=False)

        result = self.verifier.evaluate(spec, observation(widgets=readable))
        pending = self.verifier.evaluate(spec, observation(widgets=unreadable))

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.INTERFACE_OPENED, result.outcome.kind)
        self.assertTrue(pending.pending)

    def test_emits_interface_closed_only_when_bank_state_is_known(self) -> None:
        spec = specification(
            VerificationKind.INTERFACE_CLOSED,
            interface_name="bank",
            expected_plane=0,
        )

        result = self.verifier.evaluate(
            spec, observation(widgets=WidgetObservation(bank_known=True, bank_open=False))
        )
        unknown = self.verifier.evaluate(
            spec, observation(widgets=WidgetObservation(bank_known=False, bank_open=False))
        )

        self.assertTrue(result.passed)
        self.assertEqual(OutcomeKind.INTERFACE_CLOSED, result.outcome.kind)
        self.assertTrue(unknown.pending)

    def test_bank_pin_fails_immediately(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_pin_open=True,
            bank_readable=False,
        )

        result = self.verifier.evaluate(
            specification(
                VerificationKind.INTERFACE_OPENED,
                interface_name="bank",
                expected_plane=0,
            ),
            observation(widgets=widgets),
        )

        self.assertTrue(result.failed)
        self.assertEqual("bank_pin_open", result.reason)
        self.assertIs(
            VerificationFailureKind.BANK_PIN_OPEN,
            result.failure_kind,
        )
        self.assertIsNone(result.outcome)

    def test_session_change_fails_instead_of_proving_an_action(self) -> None:
        spec = specification(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            item_id=ITEM_ID,
            before_quantity=1,
        )

        result = self.verifier.evaluate(
            spec,
            replace(observation(inventory=item_inventory(2)), session_id="new-session"),
        )

        self.assertTrue(result.failed)
        self.assertEqual("session_changed", result.reason)
        self.assertIs(
            VerificationFailureKind.SESSION_CHANGED,
            result.failure_kind,
        )

    def test_unmet_condition_is_pending_before_deadline(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=1,
            ),
            observation(tick=104, inventory=item_inventory(1)),
        )

        self.assertTrue(result.pending)
        self.assertEqual("condition_not_met", result.reason)

    def test_unusable_observations_are_pending_before_deadline(self) -> None:
        base = observation(tick=104, inventory=item_inventory(2))
        cases = {
            "non-pass": replace(base, status="WARN"),
            "snapshot stale": replace(base, fresh=False),
            "cache stale": replace(base, cache_wall_clock_fresh=False),
            "not loaded": replace(base, game_state="LOGIN_SCREEN"),
            "too old": replace(
                base, timestamp=datetime.now(timezone.utc) - timedelta(seconds=10)
            ),
            "future dated": replace(
                base, timestamp=datetime.now(timezone.utc) + timedelta(hours=1)
            ),
        }
        spec = specification(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            item_id=ITEM_ID,
            before_quantity=1,
        )
        for label, candidate in cases.items():
            with self.subTest(label=label):
                result = self.verifier.evaluate(spec, candidate)
                self.assertTrue(result.pending)
                self.assertEqual("observation_not_usable", result.reason)

    def test_condition_can_pass_on_the_deadline_tick(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=1,
            ),
            observation(tick=105, inventory=item_inventory(2)),
        )

        self.assertTrue(result.passed)
        self.assertEqual(105, result.outcome.observed_tick)

    def test_unmet_or_unusable_condition_fails_at_deadline(self) -> None:
        spec = specification(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            item_id=ITEM_ID,
            before_quantity=1,
        )
        unmet = self.verifier.evaluate(
            spec, observation(tick=105, inventory=item_inventory(1))
        )
        stale = self.verifier.evaluate(
            spec, replace(observation(tick=105, inventory=item_inventory(2)), fresh=False)
        )

        self.assertTrue(unmet.failed)
        self.assertTrue(stale.failed)
        self.assertEqual("item_quantity_unchanged_at_deadline", unmet.reason)
        self.assertIs(
            VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE,
            unmet.failure_kind,
        )
        self.assertEqual("observation_unusable_at_deadline", stale.reason)
        self.assertIs(
            VerificationFailureKind.OBSERVATION_UNUSABLE_AT_DEADLINE,
            stale.failure_kind,
        )

    def test_success_seen_only_after_deadline_fails_closed(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=1,
            ),
            observation(tick=106, inventory=item_inventory(2)),
        )

        self.assertTrue(result.failed)
        self.assertEqual("deadline_exceeded", result.reason)
        self.assertIs(
            VerificationFailureKind.DEADLINE_EXCEEDED,
            result.failure_kind,
        )

    def test_unknown_or_decreased_item_quantity_is_not_recoverable_no_yield(self) -> None:
        spec = specification(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            item_id=ITEM_ID,
            before_quantity=1,
        )
        unknown = self.verifier.evaluate(
            spec,
            observation(tick=105, inventory=item_inventory(0, known=False)),
        )
        decreased = self.verifier.evaluate(
            spec,
            observation(tick=105, inventory=item_inventory(0)),
        )

        self.assertIs(
            VerificationFailureKind.OBSERVATION_UNUSABLE_AT_DEADLINE,
            unknown.failure_kind,
        )
        self.assertIs(
            VerificationFailureKind.CONDITION_UNMET_AT_DEADLINE,
            decreased.failure_kind,
        )

    def test_unknown_inventory_cannot_prove_item_outcomes(self) -> None:
        unknown = item_inventory(0, known=False)
        increased = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=0,
            ),
            observation(inventory=unknown),
        )
        equals = self.verifier.evaluate(
            specification(
                VerificationKind.ITEM_QUANTITY_EQUALS,
                item_id=ITEM_ID,
                expected_quantity=0,
            ),
            observation(inventory=unknown),
        )

        self.assertTrue(increased.pending)
        self.assertTrue(equals.pending)

    def test_invalid_specs_fail_immediately(self) -> None:
        cases = {
            "deadline": VerificationSpec(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                before_tick=100,
                deadline_tick=100,
                item_id=ITEM_ID,
                before_quantity=0,
                source_session_id="session-1",
            ),
            "session": specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
                before_quantity=0,
                source_session_id=None,
            ),
            "item id": specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=0,
                before_quantity=0,
            ),
            "quantity baseline": specification(
                VerificationKind.ITEM_QUANTITY_INCREASED,
                item_id=ITEM_ID,
            ),
            "expected quantity": specification(
                VerificationKind.ITEM_QUANTITY_EQUALS,
                item_id=ITEM_ID,
            ),
            "item interface": specification(
                VerificationKind.ITEM_QUANTITY_EQUALS,
                item_id=ITEM_ID,
                expected_quantity=0,
                interface_name="other",
            ),
            "bank item plane": specification(
                VerificationKind.ITEM_QUANTITY_EQUALS,
                item_id=ITEM_ID,
                expected_quantity=0,
                interface_name="bank",
            ),
            "movement": specification(VerificationKind.MOVED_CLOSER),
            "plane": specification(VerificationKind.PLANE_CHANGED),
            "unchanged plane": specification(
                VerificationKind.PLANE_CHANGED,
                before_location=WorldPoint(1, 1, 0),
                expected_plane=0,
            ),
            "interface": specification(
                VerificationKind.INTERFACE_OPENED,
                expected_plane=0,
            ),
            "route": specification(VerificationKind.ROUTE_TRANSITION),
            "camera zoom": specification(VerificationKind.CAMERA_ZOOM_CHANGED),
            "camera zoom amount": self._camera_zoom_specification(
                camera_zoom_amount=4
            ),
            "unsupported": VerificationSpec(
                "other", before_tick=100, deadline_tick=105, source_session_id="session-1"
            ),
        }
        for label, spec in cases.items():
            with self.subTest(label=label):
                result = self.verifier.evaluate(spec, observation())
                self.assertTrue(result.failed)
                self.assertIsNone(result.outcome)
                self.assertIs(
                    VerificationFailureKind.INVALID_SPECIFICATION,
                    result.failure_kind,
                )


if __name__ == "__main__":
    unittest.main()

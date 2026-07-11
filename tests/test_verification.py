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
    Outcome,
    OutcomeKind,
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
    geometry_frame_id: str | None = None,
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
        self.assertEqual("deadline_exceeded", unmet.reason)
        self.assertEqual("deadline_exceeded", stale.reason)

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
            "unsupported": VerificationSpec(
                "other", before_tick=100, deadline_tick=105, source_session_id="session-1"
            ),
        }
        for label, spec in cases.items():
            with self.subTest(label=label):
                result = self.verifier.evaluate(spec, observation())
                self.assertTrue(result.failed)
                self.assertIsNone(result.outcome)


if __name__ == "__main__":
    unittest.main()

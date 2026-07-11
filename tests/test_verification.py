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
    Verification,
    VerificationKind,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.verification import VerificationStatus, Verifier


def logs(count: int, *, known: bool = True) -> InventoryObservation:
    items = (
        (InventoryItem(slot=0, item_id=1511, quantity=count),)
        if count > 0
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
) -> Observation:
    timestamp = datetime.now(timezone.utc)
    session_id = "session-1"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=location,
        plane=location.plane,
        inventory=inventory or logs(1),
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
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=process_id,
    )


def specification(kind: VerificationKind, **changes: object) -> Verification:
    values = {
        "kind": kind,
        "before_tick": 100,
        "deadline_tick": 105,
        "source_session_id": "session-1",
    }
    values.update(changes)
    return Verification(**values)


class VerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = Verifier(max_observation_age_seconds=2.0)

    def test_none_passes_without_an_observation_outcome(self) -> None:
        result = self.verifier.evaluate(
            Verification(VerificationKind.NONE, before_tick=100, deadline_tick=100),
            observation(tick=100),
        )

        self.assertEqual(result.status, VerificationStatus.PASS)
        self.assertTrue(result.passed)

    def test_waits_for_an_observation_later_than_the_action(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=1),
            observation(tick=100, inventory=logs(2)),
        )

        self.assertEqual(result.status, VerificationStatus.PENDING)
        self.assertEqual(result.reason, "awaiting_later_observation")

    def test_passes_log_gained(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=1),
            observation(inventory=logs(2)),
        )

        self.assertEqual(result.status, VerificationStatus.PASS)
        self.assertEqual(result.reason, "log_gained")

    def test_passes_moved_closer_and_arrival(self) -> None:
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

        self.assertEqual(closer.reason, "moved_closer")
        self.assertEqual(arrived.reason, "arrived")
        self.assertTrue(closer.passed)
        self.assertTrue(arrived.passed)
        self.assertTrue(unchanged.pending)

    def test_passes_arrival_even_when_the_baseline_was_already_at_target(self) -> None:
        target = WorldPoint(3205, 3200, 0)
        spec = specification(
            VerificationKind.MOVED_CLOSER,
            before_location=target,
            target_location=target,
            target_radius=0,
        )

        result = self.verifier.evaluate(spec, observation(location=target))

        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "arrived")

    def test_passes_plane_changed(self) -> None:
        result = self.verifier.evaluate(
            specification(
                VerificationKind.PLANE_CHANGED,
                before_location=WorldPoint(3200, 3200, 0),
                expected_plane=1,
            ),
            observation(location=WorldPoint(3200, 3200, 1)),
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "plane_changed")

    def test_route_transition_accepts_plane_change_or_exact_climb_dialogue(self) -> None:
        spec = specification(
            VerificationKind.ROUTE_TRANSITION_READY,
            before_location=WorldPoint(3205, 3229, 0),
            expected_plane=1,
        )
        dialogue = WidgetObservation(
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(DialogueOption(1, "1", "Climb up the stairs."),),
            dialogue_number_keys=True,
        )

        opened = self.verifier.evaluate(spec, observation(widgets=dialogue))
        changed = self.verifier.evaluate(
            spec, observation(location=WorldPoint(3205, 3229, 1))
        )

        self.assertEqual("dialogue_open", opened.reason)
        self.assertEqual("plane_changed", changed.reason)
        self.assertTrue(opened.passed)
        self.assertTrue(changed.passed)

    def test_passes_bank_open_only_when_readable(self) -> None:
        open_readable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=True)
        open_unreadable = WidgetObservation(bank_known=True, bank_open=True, bank_readable=False)
        spec = specification(VerificationKind.BANK_OPEN, expected_plane=0)

        passed = self.verifier.evaluate(spec, observation(widgets=open_readable))
        pending = self.verifier.evaluate(spec, observation(widgets=open_unreadable))

        self.assertTrue(passed.passed)
        self.assertEqual(passed.reason, "bank_open")
        self.assertTrue(pending.pending)

    def test_bank_pin_fails_immediately(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_pin_open=True,
            bank_readable=False,
        )

        result = self.verifier.evaluate(
            specification(VerificationKind.BANK_OPEN, expected_plane=0),
            observation(widgets=widgets),
        )

        self.assertTrue(result.failed)
        self.assertEqual(result.reason, "bank_pin_open")

    def test_session_change_fails_instead_of_proving_an_action(self) -> None:
        spec = specification(VerificationKind.LOG_GAINED, before_log_count=1)

        result = self.verifier.evaluate(
            spec,
            replace(observation(inventory=logs(2)), session_id="new-session"),
        )

        self.assertTrue(result.failed)
        self.assertEqual("session_changed", result.reason)

    def test_passes_logs_deposited_only_when_no_logs_remain(self) -> None:
        spec = specification(
            VerificationKind.LOGS_DEPOSITED,
            before_log_count=3,
            expected_plane=0,
        )
        result = self.verifier.evaluate(
            spec,
            observation(
                inventory=logs(0),
                widgets=WidgetObservation(bank_known=True, bank_open=True, bank_readable=True),
            ),
        )
        partial = self.verifier.evaluate(
            spec,
            observation(
                inventory=logs(1),
                widgets=WidgetObservation(bank_known=True, bank_open=True, bank_readable=True),
            ),
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "logs_deposited")
        self.assertTrue(partial.pending)

    def test_passes_bank_closed(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.BANK_CLOSED, expected_plane=0),
            observation(widgets=WidgetObservation(bank_known=True, bank_open=False)),
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "bank_closed")

    def test_unknown_bank_capture_cannot_prove_bank_closed(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.BANK_CLOSED, expected_plane=0),
            observation(widgets=WidgetObservation(bank_known=False, bank_open=False)),
        )

        self.assertTrue(result.pending)
        self.assertEqual("condition_not_met", result.reason)

    def test_unmet_condition_is_pending_before_deadline(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=1),
            observation(tick=104, inventory=logs(1)),
        )

        self.assertTrue(result.pending)
        self.assertEqual(result.reason, "condition_not_met")

    def test_unusable_observations_are_pending_before_deadline(self) -> None:
        base = observation(tick=104, inventory=logs(2))
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
        spec = specification(VerificationKind.LOG_GAINED, before_log_count=1)
        for label, candidate in cases.items():
            with self.subTest(label=label):
                result = self.verifier.evaluate(spec, candidate)
                self.assertTrue(result.pending)
                self.assertEqual(result.reason, "observation_not_usable")

    def test_condition_can_pass_on_the_deadline_tick(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=1),
            observation(tick=105, inventory=logs(2)),
        )

        self.assertTrue(result.passed)

    def test_unmet_or_unusable_condition_fails_at_deadline(self) -> None:
        spec = specification(VerificationKind.LOG_GAINED, before_log_count=1)
        unmet = self.verifier.evaluate(spec, observation(tick=105, inventory=logs(1)))
        stale = self.verifier.evaluate(
            spec, replace(observation(tick=105, inventory=logs(2)), fresh=False)
        )

        self.assertTrue(unmet.failed)
        self.assertTrue(stale.failed)
        self.assertEqual(unmet.reason, "deadline_exceeded")
        self.assertEqual(stale.reason, "deadline_exceeded")

    def test_success_seen_only_after_deadline_fails_closed(self) -> None:
        result = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=1),
            observation(tick=106, inventory=logs(2)),
        )

        self.assertTrue(result.failed)
        self.assertEqual(result.reason, "deadline_exceeded")

    def test_unknown_inventory_cannot_prove_inventory_outcomes(self) -> None:
        unknown = logs(0, known=False)
        gained = self.verifier.evaluate(
            specification(VerificationKind.LOG_GAINED, before_log_count=0),
            observation(inventory=unknown),
        )
        deposited = self.verifier.evaluate(
            specification(
                VerificationKind.LOGS_DEPOSITED,
                before_log_count=1,
                expected_plane=0,
            ),
            observation(inventory=unknown),
        )

        self.assertTrue(gained.pending)
        self.assertTrue(deposited.pending)

    def test_invalid_specs_fail_immediately(self) -> None:
        cases = {
            "deadline": Verification(
                VerificationKind.BANK_OPEN, before_tick=100, deadline_tick=100
            ),
            "log baseline": specification(VerificationKind.LOG_GAINED),
            "deposit baseline": specification(
                VerificationKind.LOGS_DEPOSITED, before_log_count=0
            ),
            "movement": specification(VerificationKind.MOVED_CLOSER),
            "plane": specification(VerificationKind.PLANE_CHANGED),
            "unsupported": Verification("other", before_tick=100, deadline_tick=105),
        }
        for label, spec in cases.items():
            with self.subTest(label=label):
                self.assertTrue(self.verifier.evaluate(spec, observation()).failed)


if __name__ == "__main__":
    unittest.main()

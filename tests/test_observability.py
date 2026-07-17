from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from osrs_bot.observability import (
    MAX_AGGREGATE_COUNT,
    MAX_DURATION_MILLIS,
    DurationAggregate,
    ObservabilityEvidence,
    TimingEvidence,
    TimingPhase,
    WaitState,
    safe_elapsed_millis,
)


class ObservabilityEvidenceTests(unittest.TestCase):
    def test_wait_state_vocabulary_is_exact(self) -> None:
        expected = (
            "WAITING_FOR_NEXT_SCENE_UPDATE",
            "WAITING_FOR_SOURCE_COHERENCE",
            "ENDPOINT_BACKPRESSURE",
            "INPUT_TRANSACTION_BUSY",
            "CURSOR_FEEDBACK_SETTLING",
            "ARDUINO_HEALTH_STALE",
            "ARDUINO_COMMAND_FAILED",
            "SENSOR_STALE",
            "PRESENTATION_FRAME_STALE",
        )

        self.assertEqual(expected, tuple(state.name for state in WaitState))
        self.assertEqual(expected, tuple(state.value for state in WaitState))

    def test_timing_phase_vocabulary_is_exact_and_ordered(self) -> None:
        self.assertEqual(
            (
                "observation_request_fetch",
                "endpoint_backpressure_wait",
                "source_coherence_freshness_wait",
                "task_decision",
                "safety_gate_evaluation",
                "input_lease_acquisition",
                "arduino_connect_negotiate_arm",
                "pointer_planning_feedback_settlement",
                "serial_write_acknowledgement",
                "post_action_fresh_observation_wait",
                "semantic_or_camera_verification",
                "final_cleanup",
            ),
            tuple(phase.value for phase in TimingPhase),
        )

    def test_aggregates_are_immutable_additive_and_consistent(self) -> None:
        empty = DurationAggregate()
        first = empty.add(4)
        second = first.add(9)

        self.assertEqual(DurationAggregate(), empty)
        self.assertEqual(DurationAggregate(1, 4, 4, 4), first)
        self.assertEqual(DurationAggregate(2, 13, 9, 9), second)
        self.assertFalse(hasattr(second, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            second.count = 3  # type: ignore[misc]
        with self.assertRaises(TypeError):
            empty.add(True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            empty.add(MAX_DURATION_MILLIS + 1)
        with self.assertRaises(ValueError):
            DurationAggregate(0, 1, 1, 1)
        with self.assertRaises(ValueError):
            DurationAggregate(2, 5, 3, 4)
        with self.assertRaises(ValueError):
            DurationAggregate(2, 7, 3, 3)

    def test_timing_record_lookup_and_merge_are_copy_on_write(self) -> None:
        first = TimingEvidence().record(
            TimingPhase.TASK_DECISION,
            7,
        ).record(TimingPhase.OBSERVATION_REQUEST_FETCH, 3)
        later = TimingEvidence().record(
            TimingPhase.TASK_DECISION,
            11,
        ).record(TimingPhase.FINAL_CLEANUP, 2)

        merged = first.merge(later)

        self.assertEqual(
            (
                TimingPhase.OBSERVATION_REQUEST_FETCH,
                TimingPhase.TASK_DECISION,
            ),
            tuple(phase for phase, _aggregate in first.aggregates),
        )
        self.assertEqual(DurationAggregate(1, 7, 7, 7), first.for_phase(TimingPhase.TASK_DECISION))
        self.assertEqual(DurationAggregate(2, 18, 11, 11), merged.for_phase(TimingPhase.TASK_DECISION))
        self.assertEqual(DurationAggregate(1, 2, 2, 2), merged.for_phase(TimingPhase.FINAL_CLEANUP))
        self.assertIsNone(merged.for_phase(TimingPhase.SAFETY_GATE_EVALUATION))
        self.assertFalse(hasattr(merged, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            merged.aggregates = ()  # type: ignore[misc]
        with self.assertRaises(ValueError):
            TimingEvidence(
                (
                    (TimingPhase.TASK_DECISION, DurationAggregate()),
                    (TimingPhase.TASK_DECISION, DurationAggregate()),
                )
            )
        with self.assertRaises(ValueError):
            DurationAggregate(
                MAX_AGGREGATE_COUNT + 1,
                0,
                0,
                0,
            )

    def test_safe_elapsed_helper_clamps_and_discards_invalid_inputs(self) -> None:
        sentinel = "typed-password SESSION_TOKEN=private"

        self.assertEqual(125, safe_elapsed_millis(10.0, 10.125))
        self.assertEqual(0, safe_elapsed_millis(10.0, 9.0))
        self.assertEqual(0, safe_elapsed_millis(float("nan"), 10.0))
        self.assertEqual(0, safe_elapsed_millis(10**10_000, 10.0))
        self.assertEqual(0, safe_elapsed_millis(sentinel, 10.0))
        self.assertEqual(
            MAX_DURATION_MILLIS,
            safe_elapsed_millis(0.0, MAX_DURATION_MILLIS),
        )
        self.assertNotIn(sentinel, repr(safe_elapsed_millis(sentinel, sentinel)))

    def test_serialization_contains_only_fixed_labels_and_numbers(self) -> None:
        sentinel = "hunter2 SESSION_TOKEN=private raw typed text"
        evidence = ObservabilityEvidence(
            timing=TimingEvidence()
            .record(TimingPhase.OBSERVATION_REQUEST_FETCH, 12)
            .record(TimingPhase.FINAL_CLEANUP, 3),
            wait_state=WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
            wait_elapsed_millis=18,
            observed_wait_states=(
                WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
                WaitState.CURSOR_FEEDBACK_SETTLING,
            ),
        )

        payload = evidence.to_dict()
        encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual("engine_observability.v1", payload["schema"])
        self.assertEqual("engine_phase_timing.v1", payload["timing"]["schema"])
        self.assertEqual(
            "WAITING_FOR_NEXT_SCENE_UPDATE",
            payload["waitState"],
        )
        self.assertEqual(
            {
                "phase": "observation_request_fetch",
                "count": 1,
                "totalMillis": 12,
                "maxMillis": 12,
                "lastMillis": 12,
            },
            payload["timing"]["phases"][0],
        )
        self.assertNotIn(sentinel, encoded)
        self.assertNotIn("password", encoded.lower())
        self.assertNotIn("session_token", encoded.lower())

    def test_defaults_support_legacy_absence(self) -> None:
        evidence = ObservabilityEvidence()

        self.assertEqual(TimingEvidence(), evidence.timing)
        self.assertIsNone(evidence.wait_state)
        self.assertEqual(0, evidence.wait_elapsed_millis)
        self.assertEqual((), evidence.observed_wait_states)
        self.assertEqual(
            {
                "schema": "engine_observability.v1",
                "timing": {
                    "schema": "engine_phase_timing.v1",
                    "phases": [],
                },
                "waitState": None,
                "waitElapsedMillis": 0,
                "observedWaitStates": [],
            },
            evidence.to_dict(),
        )

    def test_observability_rejects_mutable_or_ambiguous_values(self) -> None:
        with self.assertRaises(TypeError):
            ObservabilityEvidence(wait_state="SENSOR_STALE")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ObservabilityEvidence(wait_elapsed_millis=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ObservabilityEvidence(wait_elapsed_millis=1)
        with self.assertRaises(TypeError):
            ObservabilityEvidence(
                observed_wait_states=[WaitState.SENSOR_STALE]  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            TimingEvidence(aggregates=[])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

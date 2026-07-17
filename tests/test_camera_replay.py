from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from osrs_bot.camera_replay import (
    RetainedCameraTraceError,
    analyze_retained_camera_trace,
    target_locked_policy_envelope,
)


FIXTURE = Path(__file__).parent / "fixtures" / "retained_camera_79_trace.json"


class RetainedCameraReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trace = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_recomputes_exact_old_run_camera_metrics(self) -> None:
        analysis = analyze_retained_camera_trace(self.trace)

        self.assertEqual("retained_camera_replay_analysis.v1", analysis["schema"])
        self.assertEqual(79, analysis["cameraActions"])
        self.assertEqual(
            {"down": 6, "left": 26, "right": 47},
            analysis["keyCounts"],
        )
        self.assertEqual(
            [0, 0, 0, 0, 0, 2, 0, 2, 10, 0, 12, 2, 0, 27, 0, 0, 0, 2, 0, 1, 0],
            analysis["actionsBeforeInteractions"],
        )
        self.assertEqual(21, analysis["trailingActionsWithoutInteraction"])
        self.assertEqual(27, analysis["maximumBurst"])
        self.assertEqual(36, analysis["yawDirectionReversals"])
        self.assertEqual(5, analysis["provenOvershootReversals"])
        self.assertEqual(31, analysis["unjustifiedDirectionReversals"])
        self.assertEqual(7, analysis["targetSwitchesWithinBursts"])
        self.assertEqual(6, analysis["pitchNoEffectAttempts"])
        self.assertEqual(5, analysis["redundantPitchLimitAttempts"])

    def test_every_action_retains_ack_and_fresh_coherent_pose_proof(self) -> None:
        analysis = analyze_retained_camera_trace(self.trace)

        self.assertEqual(79, analysis["wireAcknowledgedActions"])
        self.assertEqual(79, analysis["freshCoherentSamples"])
        self.assertEqual(73, analysis["poseChangedVerifications"])
        self.assertEqual(73, analysis["geometryChangedVerifications"])
        self.assertTrue(analysis["allWireAcknowledged"])
        self.assertTrue(analysis["allSamplesFresh"])
        self.assertTrue(analysis["allSamplesWallClockFresh"])
        self.assertTrue(analysis["allSamplesCoherent"])
        self.assertTrue(
            analysis["allSuccessfulPoseVerificationsChangedGeometry"]
        )
        self.assertTrue(analysis["cleanupComplete"])

    def test_modular_wraparound_pose_result_remains_readable(self) -> None:
        wrap = next(
            action
            for burst in self.trace["bursts"]
            for action in burst["actions"]
            if action["sourceTick"] == 4704
        )

        self.assertEqual(15_535, wrap["beforeYaw"])
        self.assertEqual(417, wrap["afterYaw"])
        self.assertEqual(1_266, wrap["yawDelta"])
        self.assertTrue(wrap["geometryChanged"])
        analyze_retained_camera_trace(self.trace)

    def test_tampered_freshness_or_geometry_proof_is_rejected(self) -> None:
        stale = copy.deepcopy(self.trace)
        stale["universalCameraEvidence"]["wallClockFresh"] = False
        with self.assertRaisesRegex(
            RetainedCameraTraceError,
            "wallClockFresh must be true",
        ):
            analyze_retained_camera_trace(stale)

        unchanged = copy.deepcopy(self.trace)
        unchanged["bursts"][0]["actions"][1]["geometryChanged"] = False
        with self.assertRaisesRegex(
            RetainedCameraTraceError,
            "requires a changed geometry frame",
        ):
            analyze_retained_camera_trace(unchanged)

    def test_target_locked_coarse_fine_policy_is_an_envelope_not_outcome(self) -> None:
        analysis = analyze_retained_camera_trace(self.trace)
        envelope = target_locked_policy_envelope(analysis)

        self.assertEqual(9, envelope["episodeCount"])
        self.assertEqual(1, envelope["coarseActionBudget"])
        self.assertEqual(1, envelope["fineActionBudget"])
        self.assertEqual(2, envelope["maxActionsPerLockedEpisode"])
        self.assertLessEqual(envelope["modeledCameraActionUpperBound"], 18)
        self.assertGreaterEqual(envelope["minimumCameraActionReduction"], 61)
        self.assertEqual(0, envelope["lockedTargetSwitches"])
        self.assertEqual(0, envelope["redundantPitchLimitAttempts"])
        self.assertEqual(0, envelope["unjustifiedDirectionReversals"])
        self.assertTrue(envelope["reversalRequiresFreshOvershootProof"])
        self.assertFalse(envelope["counterfactualInteractionOutcomePredicted"])
        self.assertTrue(envelope["comparisonOnly"])


if __name__ == "__main__":
    unittest.main()

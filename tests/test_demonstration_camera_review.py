from __future__ import annotations

import unittest

from osrs_bot.demonstration_camera_review import (
    camera_control_pattern_review,
    enrich_camera_review_episodes,
)


def camera_event(
    sequence: int,
    time_millis: int,
    *,
    control: str,
    phase: str,
    hold_millis: int | None = None,
    input_kind: str = "key",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "inputKind": input_kind,
        "control": control,
        "phase": phase,
        "wallTimeMillis": time_millis,
        **extra,
    }
    if hold_millis is not None:
        payload["holdDurationMillis"] = hold_millis
    return {
        "kind": "camera_input",
        "recorderSequence": sequence,
        "payload": payload,
    }


class CameraControlPatternReviewTests(unittest.TestCase):
    def test_coarse_then_fine_and_yaw_pitch_chord_are_explicit(self) -> None:
        events = (
            camera_event(1, 1_000, control="A", phase="press"),
            camera_event(2, 1_300, control="W", phase="press"),
            camera_event(
                3,
                1_450,
                control="W",
                phase="release",
                hold_millis=150,
            ),
            camera_event(
                4,
                1_700,
                control="A",
                phase="release",
                hold_millis=700,
            ),
            camera_event(5, 1_800, control="D", phase="press"),
            camera_event(
                6,
                1_920,
                control="D",
                phase="release",
                hold_millis=120,
            ),
        )
        episode = {
            "intentClassification": "action_linked",
            "clickEventSequence": 7,
            "cameraPoseDelta": {"yaw": 512, "pitch": 96},
        }

        review = camera_control_pattern_review(events, episode)

        self.assertEqual("camera_control_pattern_review.v1", review["schema"])
        self.assertEqual("coarse_then_fine", review["patternClassification"])
        self.assertEqual(1, review["coarseHoldCount"])
        self.assertEqual(2, review["fineHoldCount"])
        self.assertEqual("A", review["coarseHolds"][0]["control"])
        self.assertEqual(1, review["yawPitchChordCount"])
        self.assertEqual(
            150,
            review["yawPitchChordIntervals"][0]["durationMillis"],
        )
        self.assertEqual(
            ["yaw", "pitch"],
            review["observedPoseAxes"],
        )
        self.assertEqual("action_linked", review["associationStatus"])
        self.assertFalse(review["exploratoryOrUnassociated"])
        self.assertTrue(review["reviewOnly"])
        self.assertFalse(review["automaticConfigurationAllowed"])

    def test_unassociated_episode_and_middle_drag_do_not_fabricate_chord(self) -> None:
        events = (
            camera_event(
                1,
                2_000,
                control="MIDDLE",
                phase="press",
                input_kind="middle_drag",
            ),
            camera_event(
                2,
                2_500,
                control="MIDDLE",
                phase="release",
                hold_millis=500,
                input_kind="middle_drag",
                totalDeltaX=40,
                totalDeltaY=-20,
            ),
        )
        episode = {
            "intentClassification": "exploratory_or_unassociated",
            "clickEventSequence": None,
            "cameraPoseDelta": {"yaw": 256, "pitch": -64},
        }

        review = camera_control_pattern_review(events, episode)

        self.assertEqual("coarse_only", review["patternClassification"])
        self.assertEqual("yaw_pitch", review["coarseHolds"][0]["axis"])
        self.assertEqual(0, review["yawPitchChordCount"])
        self.assertTrue(review["exploratoryOrUnassociated"])
        self.assertIn("does not infer wheel identity", review["inputIdentityPolicy"])

    def test_enrichment_is_additive_bounded_and_does_not_mutate_episode(self) -> None:
        events = (
            camera_event(1, 1_000, control="A", phase="press"),
            camera_event(
                2,
                1_350,
                control="A",
                phase="release",
                hold_millis=350,
            ),
        )
        episode = {
            "classification": "exploratory_or_unassociated",
            "cameraInputEventSequences": [1, 2],
            "clickEventSequence": None,
            "cameraPoseDelta": {"yaw": 128, "pitch": 0},
        }

        enriched = enrich_camera_review_episodes(events, (episode,))

        self.assertNotIn("cameraControlPattern", episode)
        self.assertEqual(1, len(enriched))
        pattern = enriched[0]["cameraControlPattern"]
        self.assertEqual(1, pattern["coarseHoldCount"])
        self.assertEqual("exploratory_or_unassociated", pattern["associationStatus"])


if __name__ == "__main__":
    unittest.main()

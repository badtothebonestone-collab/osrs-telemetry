from __future__ import annotations

import unittest
from dataclasses import dataclass

from osrs_bot.definition import LUMBRIDGE_WEST_TREES_V1
from osrs_bot.demonstration_review import (
    build_demonstration_review,
    compare_manual_route,
)


class DemonstrationRouteReviewTests(unittest.TestCase):
    def test_manual_bank_route_is_compared_without_conflating_observed_path(self) -> None:
        manual = (
            {
                "clickEventSequence": 10,
                "manualIntentTarget": {"x": 3198, "y": 3225, "plane": 0},
            },
            {
                "clickEventSequence": 20,
                "manualIntentTarget": {"x": 3203, "y": 3214, "plane": 0},
            },
            {
                "clickEventSequence": 30,
                "manualIntentTarget": {"x": 3208, "y": 3220, "plane": 2},
            },
        )
        observed = (
            {"x": 3203, "y": 3245, "plane": 0},
            {"x": 3208, "y": 3220, "plane": 2},
        )

        review = compare_manual_route(manual, observed, LUMBRIDGE_WEST_TREES_V1)

        self.assertEqual("compared", review["status"])
        self.assertEqual("to_bank", review["direction"])
        self.assertEqual(3, review["manualTargetCount"])
        self.assertEqual(2, review["observedPlayerPointCount"])
        self.assertFalse(review["automaticConfigurationAllowed"])
        planes = {item["plane"]: item for item in review["planeViews"]}
        self.assertEqual(
            {"x": 3198, "y": 3225, "plane": 0},
            planes[0]["manualTargets"][0],
        )
        self.assertEqual(
            {"x": 3203, "y": 3245, "plane": 0},
            planes[0]["observedPlayerPath"][0],
        )
        self.assertTrue(planes[0]["mandatoryDefinitionPoints"])
        self.assertEqual(
            "consecutive_manual_target_to_target_same_plane",
            review["targetDistanceSummary"]["basis"],
        )

    def test_manual_resource_route_direction_uses_progress_not_recording_name(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "manualIntentTarget": {"x": 3197, "y": 3232, "plane": 0},
                },
                {
                    "clickEventSequence": 2,
                    "manualIntentTarget": {"x": 3203, "y": 3242, "plane": 0},
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        self.assertEqual("to_resource", review["direction"])
        self.assertGreater(
            review["selectedRouteMetrics"]["forwardProgressTiles"], 0.0
        )

    def test_click_distance_histogram_prefers_player_at_click_measurement(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "chosenTargetWorld": {"worldX": 3198, "worldY": 3225, "plane": 0},
                    "requestedTileDistance": 20.6,
                },
                {
                    "clickEventSequence": 2,
                    "chosenTargetWorld": {"worldX": 3203, "worldY": 3214, "plane": 0},
                    "requestedTileDistance": 12.1,
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        summary = review["targetDistanceSummary"]
        self.assertEqual(
            "first_player_sample_then_consecutive_manual_targets", summary["basis"]
        )
        self.assertEqual([20.6, 12.083], summary["distancesTiles"])
        self.assertEqual([20.6, 12.1], summary["playerSampleToTargetDistancesTiles"])
        self.assertEqual(1, summary["histogram"]["12-19"])
        self.assertEqual(1, summary["histogram"]["20-30"])

    def test_click_distance_histogram_restarts_after_plane_transition(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "chosenTargetWorld": {"x": 3205, "y": 3220, "plane": 2},
                    "playerWorldAtClick": {"x": 3208, "y": 3220, "plane": 2},
                    "requestedTileDistance": 3.0,
                },
                {
                    "clickEventSequence": 2,
                    "chosenTargetWorld": {"x": 3206, "y": 3212, "plane": 2},
                    "playerWorldAtClick": {"x": 3208, "y": 3220, "plane": 2},
                    "requestedTileDistance": 8.25,
                },
                {
                    "clickEventSequence": 3,
                    "chosenTargetWorld": {"x": 3198, "y": 3236, "plane": 0},
                    "playerWorldAtClick": {"x": 3206, "y": 3208, "plane": 0},
                    "requestedTileDistance": 29.12,
                },
                {
                    "clickEventSequence": 4,
                    "chosenTargetWorld": {"x": 3197, "y": 3240, "plane": 0},
                    "playerWorldAtClick": {"x": 3206, "y": 3208, "plane": 0},
                    "requestedTileDistance": 33.24,
                },
                {
                    "clickEventSequence": 5,
                    "chosenTargetWorld": {"x": 3199, "y": 3245, "plane": 0},
                    "playerWorldAtClick": {"x": 3206, "y": 3208, "plane": 0},
                    "requestedTileDistance": 37.66,
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        summary = review["targetDistanceSummary"]
        self.assertEqual(
            "first_player_sample_per_plane_segment_then_consecutive_manual_targets",
            summary["basis"],
        )
        self.assertEqual(5, summary["sampleCount"])
        self.assertEqual([3.0, 8.062, 29.12, 4.123, 5.385], summary["distancesTiles"])
        self.assertEqual(
            [3.0, 8.25, 29.12, 33.24, 37.66],
            summary["playerSampleToTargetDistancesTiles"],
        )
        self.assertEqual(
            "plane_segment_start_basis_else_diagnostic_only",
            summary["playerSampleDistanceUse"],
        )
        self.assertEqual(
            {"1-4": 1, "5-11": 3, "12-19": 0, "20-30": 1, ">30": 0},
            summary["histogram"],
        )

    def test_later_stale_player_sample_does_not_replace_target_spacing(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "chosenTargetWorld": {"x": 20, "y": 0, "plane": 0},
                    "playerWorldAtClick": {"x": 0, "y": 0, "plane": 0},
                },
                {
                    "clickEventSequence": 2,
                    "chosenTargetWorld": {"x": 25, "y": 0, "plane": 0},
                    "playerWorldAtClick": {"x": 0, "y": 0, "plane": 0},
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        summary = review["targetDistanceSummary"]
        self.assertEqual([20.0, 5.0], summary["distancesTiles"])
        self.assertEqual([20.0, 25.0], summary["playerSampleToTargetDistancesTiles"])
        self.assertEqual(1, summary["histogram"]["5-11"])
        self.assertEqual(1, summary["histogram"]["20-30"])

    def test_not_claimed_segment_start_player_sample_is_not_recomputed(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "chosenTargetWorld": {"x": 30, "y": 0, "plane": 0},
                    "playerWorldAtClick": {"x": 0, "y": 0, "plane": 0},
                    "distanceFromLastObservedPlayer": 30.0,
                    "requestedTileDistance": None,
                    "requestedTileDistanceStatus": (
                        "not_claimed_from_prior_player_sample"
                    ),
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        summary = review["targetDistanceSummary"]
        self.assertEqual(0, summary["sampleCount"])
        self.assertEqual([], summary["distancesTiles"])
        self.assertEqual(
            [30.0], summary["playerSampleToTargetDistancesTiles"]
        )
        self.assertEqual(0, summary["histogram"][">30"])

    def test_near_tick_segment_start_estimate_is_explicitly_accepted(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "chosenTargetWorld": {"x": 30, "y": 0, "plane": 0},
                    "playerWorldAtClick": {"x": 0, "y": 0, "plane": 0},
                    "requestedTileDistance": 30.0,
                    "requestedTileDistanceStatus": (
                        "near_source_tick_player_sample_estimate"
                    ),
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        summary = review["targetDistanceSummary"]
        self.assertEqual([30.0], summary["distancesTiles"])
        self.assertEqual(1, summary["histogram"]["20-30"])

    def test_one_manual_target_is_not_overinterpreted(self) -> None:
        review = compare_manual_route(
            (
                {
                    "clickEventSequence": 1,
                    "manualIntentTarget": {"x": 3200, "y": 3238, "plane": 0},
                },
            ),
            (),
            LUMBRIDGE_WEST_TREES_V1,
        )

        self.assertEqual("not_compared", review["status"])
        self.assertIsNone(review["direction"])

    def test_review_layer_is_ephemeral_and_keeps_artifact_payload_unchanged(self) -> None:
        @dataclass(frozen=True)
        class Inspection:
            manual_route_targets: tuple[dict[str, object], ...]
            manual_route_review_targets: tuple[dict[str, object], ...]
            route_points: tuple[dict[str, object], ...]
            camera_review_episodes: tuple[dict[str, object], ...]
            timing_review_profiles: tuple[dict[str, object], ...]

            def to_dict(self) -> dict[str, object]:
                return {"valid": True, "status": "VERIFIED", "routePoints": []}

        inspection = Inspection(
            manual_route_targets=(
                {
                    "manualIntentTarget": {"x": 3198, "y": 3225, "plane": 0},
                    "clickEventSequence": 1,
                },
                {
                    "manualIntentTarget": {"x": 3203, "y": 3214, "plane": 0},
                    "clickEventSequence": 2,
                },
            ),
            manual_route_review_targets=(
                {
                    "chosenTargetWorld": {"x": 3198, "y": 3225, "plane": 0},
                    "clickEventSequence": 1,
                    "requestedTileDistanceStatus": "same_source_tick_player_sample",
                },
                {
                    "chosenTargetWorld": {"x": 3204, "y": 3214, "plane": 0},
                    "clickEventSequence": 2,
                    "requestedTileDistanceStatus": "not_claimed_from_prior_player_sample",
                },
            ),
            route_points=(),
            camera_review_episodes=(
                {
                    "intentClassification": "exploratory_or_unassociated",
                    "observedInputMethod": "middle_drag",
                },
            ),
            timing_review_profiles=(
                {
                    "clickEventSequence": 2,
                    "contextMenuOpenToClickMillis": 1_313,
                },
            ),
        )

        review = build_demonstration_review(inspection, LUMBRIDGE_WEST_TREES_V1)

        self.assertEqual({"valid": True, "status": "VERIFIED", "routePoints": []}, inspection.to_dict())
        self.assertIn("routeComparison", review)
        self.assertEqual(2, len(review["manualRouteTargets"]))
        self.assertEqual(2, len(review["manualRouteReviewTargets"]))
        self.assertEqual(
            "not_claimed_from_prior_player_sample",
            review["manualRouteReviewTargets"][1]["requestedTileDistanceStatus"],
        )
        self.assertEqual(
            1_313,
            review["timingReviewProfiles"][0]["contextMenuOpenToClickMillis"],
        )
        self.assertEqual(
            {"x": 3204, "y": 3214, "plane": 0},
            review["routeComparison"]["planeViews"][0]["manualTargets"][1],
        )
        self.assertEqual(
            "exploratory_or_unassociated",
            review["cameraReviewEpisodes"][0]["intentClassification"],
        )
        self.assertNotIn("routeComparison", inspection.to_dict())


if __name__ == "__main__":
    unittest.main()

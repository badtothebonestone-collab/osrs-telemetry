from __future__ import annotations

import unittest

from osrs_bot.definition import (
    FixedRoute,
    FixedRouteStep,
    LUMBRIDGE_WEST_TREES_V1,
    RoutePointClassification,
    RouteStepKind,
)
from osrs_bot.model import WorldPoint
from osrs_bot.movement import (
    RouteCandidateSupport,
    RouteSelectionLimits,
    route_progress,
    select_farthest_useful_route_target,
)


def _walk(
    step_id: str,
    x: int,
    y: int,
    *,
    classification: RoutePointClassification = RoutePointClassification.NORMAL_GUIDANCE,
    plane: int = 0,
    radius: int = 1,
) -> FixedRouteStep:
    return FixedRouteStep(
        step_id=step_id,
        kind=RouteStepKind.WALK,
        location=WorldPoint(x, y, plane),
        arrival_radius=radius,
        action="Walk here",
        classification=classification,
    )


def _route(*steps: FixedRouteStep, start: WorldPoint = WorldPoint(0, 0, 0)) -> FixedRoute:
    return FixedRoute(
        route_id="test_route",
        start_anchor=start,
        destination_anchor=steps[-1].location,
        steps=steps,
    )


def _support(
    count: int,
    *,
    unsupported: frozenset[int] = frozenset(),
) -> tuple[RouteCandidateSupport, ...]:
    return tuple(
        RouteCandidateSupport(
            route_index=index,
            plane_supported=True,
            scene_supported=index not in unsupported,
            collision_supported=True,
            projectable=True,
            ui_clear=True,
        )
        for index in range(count)
    )


def _reasons(selection: object, route_index: int) -> tuple[str, ...]:
    for rejection in selection.rejections:  # type: ignore[attr-defined]
        if rejection.route_index == route_index:
            return rejection.reasons
    return ()


class RouteLookaheadTests(unittest.TestCase):
    def test_builtin_route_supports_a_sixteen_tile_wall_corridor_click(self) -> None:
        route = LUMBRIDGE_WEST_TREES_V1.route_to_bank

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(3196, 3234, 0),
            _support(len(route.steps)),
        )

        self.assertEqual("south_corridor_entry", selection.selected_step.step_id)
        self.assertEqual(16.0, selection.requested_tile_distance)
        self.assertEqual((4, 5, 6, 7, 8), selection.skipped_guidance_indices)

    def test_open_terrain_selects_farthest_supported_point_up_to_thirty_tiles(self) -> None:
        route = _route(
            _walk("three", 3, 0),
            _walk("eight", 8, 0),
            _walk("sixteen", 16, 0),
            _walk("twenty_eight", 28, 0),
            _walk(
                "arrival",
                35,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(0, 0, 0),
            _support(len(route.steps)),
        )

        self.assertEqual(3, selection.selected_index)
        self.assertEqual("twenty_eight", selection.selected_step.step_id)
        self.assertEqual(28.0, selection.requested_tile_distance)
        self.assertGreater(selection.requested_tile_distance, 4.0)
        self.assertEqual((0, 1, 2), selection.skipped_guidance_indices)
        self.assertIn("outside_click_range", _reasons(selection, 4))

    def test_mandatory_turn_and_transition_are_never_skipped(self) -> None:
        turn_route = _route(
            _walk("guidance", 4, 0),
            _walk(
                "corner",
                8,
                0,
                classification=RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("after_corner", 12, 0),
            _walk(
                "arrival",
                16,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )
        turn_selection = select_farthest_useful_route_target(
            turn_route,
            WorldPoint(0, 0, 0),
            _support(len(turn_route.steps)),
        )

        self.assertEqual(1, turn_selection.selected_index)
        self.assertEqual(1, turn_selection.mandatory_next_index)
        self.assertIn("mandatory_point_not_skippable", _reasons(turn_selection, 2))

        transition = FixedRouteStep(
            step_id="stairs",
            kind=RouteStepKind.OBJECT,
            location=WorldPoint(6, 0, 0),
            arrival_radius=2,
            action="Climb-up",
            classification=RoutePointClassification.MANDATORY_TRANSITION,
            object_id=1,
            object_name="Staircase",
            alternate_actions=("Climb",),
            expected_plane=1,
        )
        transition_route = _route(
            _walk("approach", 4, 0),
            transition,
            _walk(
                "arrival",
                10,
                0,
                plane=1,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )
        transition_selection = select_farthest_useful_route_target(
            transition_route,
            WorldPoint(0, 0, 0),
            _support(len(transition_route.steps)),
        )

        self.assertEqual(0, transition_selection.selected_index)
        self.assertEqual(1, transition_selection.mandatory_next_index)
        self.assertIn("mandatory_transition_action", _reasons(transition_selection, 1))
        self.assertIn("mandatory_point_not_skippable", _reasons(transition_selection, 2))

    def test_invalid_shortcut_across_a_route_bend_is_rejected(self) -> None:
        route = _route(
            _walk("east", 10, 0),
            _walk("north", 10, 10),
            _walk(
                "arrival",
                20,
                10,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )
        limits = RouteSelectionLimits(
            corridor_limit_tiles=2.0,
            max_click_distance_tiles=30.0,
            max_skipped_turn_degrees=180.0,
        )

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(0, 0, 0),
            _support(len(route.steps)),
            limits=limits,
        )

        self.assertEqual(0, selection.selected_index)
        self.assertIn("corridor_violation", _reasons(selection, 1))
        self.assertIn("corridor_violation", _reasons(selection, 2))

    def test_progress_is_polyline_based_and_selection_stays_forward(self) -> None:
        route = _route(
            _walk("five", 5, 0),
            _walk("ten", 10, 0),
            _walk("fifteen", 15, 0),
            _walk(
                "arrival",
                20,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )

        progress = route_progress(route, WorldPoint(7, 2, 0))
        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(7, 2, 0),
            _support(len(route.steps)),
        )

        self.assertAlmostEqual(7.0, progress.distance_along_route)
        self.assertAlmostEqual(13.0, progress.remaining_distance)
        self.assertAlmostEqual(2.0, progress.lateral_deviation)
        self.assertEqual(3, selection.selected_index)
        self.assertGreater(selection.expected_progress, progress.distance_along_route)
        self.assertIn("not_forward", _reasons(selection, 0))

    def test_backtracking_and_lateral_zigzag_are_identified(self) -> None:
        route = _route(
            _walk("middle", 10, 0),
            _walk(
                "arrival",
                20,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )
        before = route_progress(route, WorldPoint(10, 1, 0))
        after = route_progress(route, WorldPoint(8, -1, 0), previous=before)

        self.assertAlmostEqual(-2.0, after.progress_delta)
        self.assertTrue(after.backtracking)
        self.assertTrue(after.zigzagging)

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(8, -1, 0),
            _support(len(route.steps)),
            previous_progress=before,
        )
        self.assertIn("backtracking", _reasons(selection, 0))
        self.assertGreater(selection.expected_progress, before.distance_along_route)

    def test_scene_support_can_force_a_short_precise_correction(self) -> None:
        route = _route(
            _walk("correction", 3, 0),
            _walk("far", 12, 0),
            _walk(
                "arrival",
                20,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(0, 0, 0),
            _support(len(route.steps), unsupported=frozenset({1, 2})),
        )

        self.assertEqual(0, selection.selected_index)
        self.assertEqual(3.0, selection.requested_tile_distance)
        self.assertEqual("short_correction_required", selection.fallback_reason)
        self.assertIn("scene_unsupported", _reasons(selection, 1))

    def test_short_forward_recovery_can_reenter_the_builtin_corridor(self) -> None:
        route = LUMBRIDGE_WEST_TREES_V1.route_to_bank
        support = (
            RouteCandidateSupport(0, True, True, False, True, True, shortcut_clear=False),
            RouteCandidateSupport(1, True, True, False, True, True, shortcut_clear=False),
            RouteCandidateSupport(2, True, True, False, True, True, shortcut_clear=False),
            RouteCandidateSupport(3, True, True, True, True, True, shortcut_clear=False),
        )

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(3199, 3235, 0),
            support,
        )

        self.assertGreater(selection.progress.lateral_deviation, 2.0)
        self.assertEqual(3, selection.selected_index)
        self.assertEqual("west_wall_corner", selection.selected_step.step_id)
        self.assertEqual(3.0, selection.requested_tile_distance)
        self.assertEqual("short_correction_required", selection.fallback_reason)
        self.assertNotIn("corridor_violation", _reasons(selection, 3))
        self.assertNotIn("shortcut_unsupported", _reasons(selection, 3))

    def test_short_nonforward_reentry_recovers_from_live_tree_lane_position(self) -> None:
        route = LUMBRIDGE_WEST_TREES_V1.route_to_bank
        support = tuple(
            RouteCandidateSupport(
                index,
                True,
                True,
                True,
                index == 0,
                True,
                camera_adjustable=index != 0,
                shortcut_clear=False,
            )
            for index in range(4)
        )

        selection = select_farthest_useful_route_target(
            route,
            WorldPoint(3194, 3248, 0),
            support,
        )

        self.assertGreater(selection.progress.lateral_deviation, 2.0)
        self.assertEqual(0, selection.selected_index)
        self.assertEqual("tree_lane_exit", selection.selected_step.step_id)
        self.assertEqual(4.0, selection.requested_tile_distance)
        self.assertEqual(
            "route_reentry_correction_required",
            selection.fallback_reason,
        )
        self.assertNotIn("not_forward", _reasons(selection, 0))
        self.assertNotIn("shortcut_unsupported", _reasons(selection, 0))

    def test_fresh_location_and_projection_facts_recompute_the_target(self) -> None:
        route = _route(
            _walk("five", 5, 0),
            _walk("twelve", 12, 0),
            _walk(
                "arrival",
                20,
                0,
                classification=RoutePointClassification.ARRIVAL_POINT,
            ),
        )
        first = select_farthest_useful_route_target(
            route,
            WorldPoint(0, 0, 0),
            _support(len(route.steps), unsupported=frozenset({2})),
        )
        refreshed_support = (
            _support(3)[0],
            RouteCandidateSupport(1, True, True, True, False, True, camera_adjustable=False),
            RouteCandidateSupport(2, True, True, True, False, True, camera_adjustable=True),
        )
        second = select_farthest_useful_route_target(
            route,
            WorldPoint(6, 0, 0),
            refreshed_support,
            previous_progress=first.progress,
        )

        self.assertEqual(1, first.selected_index)
        self.assertEqual(2, second.selected_index)
        self.assertAlmostEqual(6.0, second.progress.distance_along_route)
        self.assertEqual(14.0, second.requested_tile_distance)


if __name__ == "__main__":
    unittest.main()

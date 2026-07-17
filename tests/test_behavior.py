from __future__ import annotations

import random
import unittest

from osrs_bot.behavior import (
    BehaviorConfig,
    BehaviorPolicy,
    classify_camera_zoom,
    point_in_polygon,
)
from osrs_bot.camera import CameraKeyCapabilities
from osrs_bot.model import ScreenBounds, ScreenPoint, TargetGeometry


CANVAS = ScreenBounds(100, 200, 800, 600)


def geometry(
    *,
    left: int = 300,
    top: int = 320,
    width: int = 90,
    height: int = 110,
    point: ScreenPoint | None = None,
    ratio: float = 1.0,
) -> TargetGeometry:
    polygon = (
        ScreenPoint(left, top),
        ScreenPoint(left + width - 1, top),
        ScreenPoint(left + width - 1, top + height - 1),
        ScreenPoint(left, top + height - 1),
    )
    return TargetGeometry(
        available=True,
        on_screen=True,
        visible=True,
        actionable=True,
        screen_point=point or ScreenPoint(left + width // 2, top + height // 2),
        screen_bounds=ScreenBounds(left, top, width, height),
        geometry_source="clickbox",
        screen_polygon=polygon,
        visible_area_ratio=ratio,
    )


class BehaviorPolicyTest(unittest.TestCase):
    def test_camera_zoom_band_is_bounded_diagnostic_only(self) -> None:
        config = BehaviorConfig(
            camera_zoom_desired_min=320,
            camera_zoom_desired_max=448,
        )

        self.assertEqual("unavailable", classify_camera_zoom(None, config))
        self.assertEqual("too_far", classify_camera_zoom(319, config))
        self.assertEqual("moderate", classify_camera_zoom(320, config))
        self.assertEqual("moderate", classify_camera_zoom(448, config))
        self.assertEqual("too_close", classify_camera_zoom(449, config))
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            BehaviorConfig(
                camera_zoom_desired_min=449,
                camera_zoom_desired_max=448,
            )
        with self.assertRaisesRegex(ValueError, "zoom3d"):
            classify_camera_zoom(-1, config)

    def test_authoritative_source_without_polygon_fails_closed(self) -> None:
        target = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(340, 360),
            screen_bounds=ScreenBounds(300, 320, 90, 110),
            geometry_source="clickbox",
        )

        with self.assertRaisesRegex(ValueError, "missing its polygon"):
            BehaviorPolicy(BehaviorConfig(seed=40)).select_aim_point(
                target,
                CANVAS,
                target_key="tree:1276",
                decision_id="missing-polygon",
            )

    def test_large_shape_produces_multiple_inset_candidates(self) -> None:
        target = geometry()
        decision = BehaviorPolicy(BehaviorConfig(seed=41)).select_aim_point(
            target,
            CANVAS,
            target_key="tree:1276",
            decision_id="tree-1",
            cursor=ScreenPoint(120, 220),
        )

        self.assertEqual("clickbox", decision.geometry_source)
        self.assertGreaterEqual(len(decision.candidates), 8)
        self.assertIn(decision.selected, decision.candidates)
        for candidate in decision.candidates:
            self.assertTrue(point_in_polygon(candidate.point, target.screen_polygon))
            self.assertGreaterEqual(candidate.boundary_clearance_px, 4.0)

    def test_same_seed_reproduces_sequence_and_repeated_actions_vary(self) -> None:
        first = BehaviorPolicy(BehaviorConfig(seed=991))
        second = BehaviorPolicy(BehaviorConfig(seed=991))
        first_points = []
        second_points = []
        for index in range(12):
            values = {
                "target_key": "tree:1276",
                "decision_id": f"tree-{index}",
                "cursor": ScreenPoint(150 + index, 250),
            }
            first_points.append(
                first.select_aim_point(geometry(), CANVAS, **values).selected.point
            )
            second_points.append(
                second.select_aim_point(geometry(), CANVAS, **values).selected.point
            )

        self.assertEqual(first_points, second_points)
        self.assertGreaterEqual(len(set(first_points)), 4)

    def test_policy_does_not_consume_global_random_state(self) -> None:
        random.seed(12345)
        expected = random.random()
        random.seed(12345)
        BehaviorPolicy(BehaviorConfig(seed=7)).select_aim_point(
            geometry(),
            CANVAS,
            target_key="tree:1276",
            decision_id="tree-global-state",
        )
        self.assertEqual(expected, random.random())

    def test_geometry_change_regenerates_candidates(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=12))
        first = policy.select_aim_point(
            geometry(left=300),
            CANVAS,
            target_key="tree:1276",
            decision_id="frame-a",
        )
        second = policy.select_aim_point(
            geometry(left=470),
            CANVAS,
            target_key="tree:1276",
            decision_id="frame-b",
        )

        self.assertNotEqual(
            tuple(candidate.point for candidate in first.candidates),
            tuple(candidate.point for candidate in second.candidates),
        )
        self.assertGreaterEqual(second.selected.point.x, 470)

    def test_small_shape_retains_a_safe_candidate(self) -> None:
        target = geometry(left=350, top=350, width=5, height=5)
        decision = BehaviorPolicy(BehaviorConfig(seed=5)).select_aim_point(
            target,
            CANVAS,
            target_key="small",
            decision_id="small-1",
        )
        self.assertGreaterEqual(len(decision.candidates), 1)
        self.assertTrue(point_in_polygon(decision.selected.point, target.screen_polygon))

    def test_thin_projected_tile_uses_a_bounded_adaptive_inset(self) -> None:
        polygon = (
            ScreenPoint(180, 193),
            ScreenPoint(281, 286),
            ScreenPoint(349, 356),
            ScreenPoint(236, 256),
        )
        target = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(264, 274),
            screen_bounds=ScreenBounds(180, 193, 170, 164),
            geometry_source="canvas_tile",
            screen_polygon=polygon,
        )

        decision = BehaviorPolicy(
            BehaviorConfig(seed=6, aim_inset_px=14)
        ).select_aim_point(
            target,
            CANVAS,
            target_key="thin-tile",
            decision_id="thin-tile-1",
        )

        self.assertGreaterEqual(len(decision.candidates), 1)
        self.assertTrue(point_in_polygon(decision.selected.point, polygon))
        self.assertGreaterEqual(decision.selected.boundary_clearance_px, 2.0)
        self.assertIn("adaptive_inset_reduced", decision.rejected_reasons)

    def test_ui_and_competing_shapes_are_excluded(self) -> None:
        target = geometry()
        exclusion = ScreenBounds(300, 320, 45, 110)
        competitor = ScreenBounds(345, 320, 15, 110)
        decision = BehaviorPolicy(BehaviorConfig(seed=8)).select_aim_point(
            target,
            CANVAS,
            target_key="tree:1276",
            decision_id="tree-excluded",
            excluded_bounds=(exclusion,),
            competing_bounds=(competitor,),
        )
        self.assertTrue(
            all(not exclusion.contains(item.point) for item in decision.candidates)
        )
        self.assertTrue(
            all(not competitor.contains(item.point) for item in decision.candidates)
        )
        self.assertIn("overlapping_ui", decision.rejected_reasons)
        self.assertIn("competing_target_overlap", decision.rejected_reasons)

    def test_long_horizontal_aim_prefers_orthogonal_edge_clearance(self) -> None:
        canvas = ScreenBounds(0, 0, 1200, 800)
        polygon = (
            ScreenPoint(100, 10),
            ScreenPoint(500, 10),
            ScreenPoint(500, 110),
            ScreenPoint(340, 110),
            ScreenPoint(340, 650),
            ScreenPoint(260, 650),
            ScreenPoint(260, 110),
            ScreenPoint(100, 110),
        )
        target = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            screen_point=ScreenPoint(300, 300),
            screen_bounds=ScreenBounds(100, 10, 401, 641),
            geometry_source="clickbox",
            screen_polygon=polygon,
        )
        values = {
            "target_key": "edge-aware-tree",
            "decision_id": "edge-aware-tree-1",
            "cursor": ScreenPoint(1100, 40),
        }

        unweighted = BehaviorPolicy(
            BehaviorConfig(seed=81, aim_parallel_edge_cost_weight=0.0)
        ).select_aim_point(target, canvas, **values)
        weighted = BehaviorPolicy(BehaviorConfig(seed=81)).select_aim_point(
            target,
            canvas,
            **values,
        )

        self.assertLess(
            weighted.candidates[0].parallel_edge_transfer_cost,
            unweighted.candidates[0].parallel_edge_transfer_cost,
        )
        self.assertGreater(
            weighted.candidates[0].point.y,
            unweighted.candidates[0].point.y,
        )
        self.assertTrue(point_in_polygon(weighted.selected.point, polygon))

    def test_camera_barely_visible_differs_from_well_framed(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=44))
        barely = policy.classify_camera(
            geometry(point=ScreenPoint(CANVAS.x + 5, CANVAS.y + 40)),
            CANVAS,
            decision_id="camera-edge",
            route_dx=12,
        )
        well = policy.classify_camera(
            geometry(point=ScreenPoint(560, 500)),
            CANVAS,
            decision_id="camera-well",
            route_dx=12,
        )

        self.assertEqual("barely_visible", barely.classification)
        self.assertEqual("reframe", barely.action)
        self.assertGreater(barely.hold_millis, 0)
        self.assertEqual("well_framed", well.classification)
        self.assertEqual("none", well.action)
        self.assertEqual("east", well.route_direction_bias)
        self.assertNotEqual(CANVAS.center, well.desired_region.center)

    def test_camera_target_bias_leaves_space_ahead_in_each_route_direction(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=440))
        player = CANVAS.center

        east = policy.classify_camera(
            geometry(point=ScreenPoint(player.x + 120, player.y)),
            CANVAS,
            decision_id="camera-east",
            route_dx=12,
            player_point=player,
        )
        west = policy.classify_camera(
            geometry(point=ScreenPoint(player.x - 120, player.y)),
            CANVAS,
            decision_id="camera-west",
            route_dx=-12,
            player_point=player,
        )
        north = policy.classify_camera(
            geometry(point=ScreenPoint(player.x, player.y - 120)),
            CANVAS,
            decision_id="camera-north",
            route_dy=12,
            player_point=player,
        )
        south = policy.classify_camera(
            geometry(point=ScreenPoint(player.x, player.y + 120)),
            CANVAS,
            decision_id="camera-south",
            route_dy=-12,
            player_point=player,
        )

        self.assertLess(east.desired_region.center.x, CANVAS.center.x)
        self.assertGreater(west.desired_region.center.x, CANVAS.center.x)
        self.assertGreater(north.desired_region.center.y, CANVAS.center.y)
        self.assertLess(south.desired_region.center.y, CANVAS.center.y)

    def test_route_context_accepts_safe_leading_band_but_keeps_edge_marginal(self) -> None:
        viewport = ScreenBounds(0, 0, 512, 334)
        player = viewport.center
        policy = BehaviorPolicy(BehaviorConfig(seed=441))

        supported_long_click = policy.classify_camera(
            geometry(
                left=160,
                top=20,
                width=17,
                height=11,
                point=ScreenPoint(168, 25),
            ),
            viewport,
            decision_id="route-leading-safe",
            route_dy=20,
            player_point=player,
            framing_context="route",
        )
        against_edge = policy.classify_camera(
            geometry(
                left=160,
                top=8,
                width=17,
                height=11,
                point=ScreenPoint(168, 13),
            ),
            viewport,
            decision_id="route-leading-marginal",
            route_dy=20,
            player_point=player,
            framing_context="route",
        )
        strict_interaction = policy.classify_camera(
            geometry(
                left=160,
                top=20,
                width=17,
                height=11,
                point=ScreenPoint(168, 25),
            ),
            viewport,
            decision_id="object-leading-strict",
            route_dy=20,
            player_point=player,
            framing_context="interaction",
        )

        self.assertIn(supported_long_click.classification, {"usable", "well_framed"})
        self.assertEqual("none", supported_long_click.action)
        self.assertEqual(24, supported_long_click.required_edge_margin_px)
        self.assertEqual("barely_visible", against_edge.classification)
        self.assertEqual("reframe", against_edge.action)
        self.assertEqual("barely_visible", strict_interaction.classification)

    def test_route_lookahead_envelope_can_continue_camera_framing(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=442))
        target = geometry(point=ScreenPoint(500, 500))
        without_lookahead = policy.classify_camera(
            target,
            CANVAS,
            decision_id="route-envelope-none",
            framing_context="route",
        )
        with_lookahead = policy.classify_camera(
            target,
            CANVAS,
            decision_id="route-envelope-future",
            framing_context="route",
            lookahead_points=(ScreenPoint(880, 230),),
        )

        self.assertEqual("none", without_lookahead.action)
        self.assertEqual("reframe", with_lookahead.action)
        self.assertEqual(2, len(with_lookahead.lookahead_points))
        self.assertIsNotNone(with_lookahead.lookahead_bounds)

    def test_visible_edge_correction_hold_is_not_inflated_by_world_yaw(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=443))
        clipped = geometry(
            left=CANVAS.x + 200,
            top=CANVAS.y - 12,
            width=90,
            height=110,
            point=ScreenPoint(CANVAS.x + 245, CANVAS.y + 30),
        )
        small_yaw = policy.classify_camera(
            clipped,
            CANVAS,
            decision_id="object-clipped-visible-yaw",
            framing_context="interaction",
            yaw_error_units=40,
        )
        large_yaw = policy.classify_camera(
            clipped,
            CANVAS,
            decision_id="object-clipped-visible-yaw",
            framing_context="interaction",
            yaw_error_units=4096,
        )

        self.assertEqual("barely_visible", small_yaw.classification)
        self.assertLess(
            small_yaw.edge_clearance_px,
            small_yaw.required_edge_margin_px,
        )
        self.assertEqual(large_yaw.hold_millis, small_yaw.hold_millis)
        self.assertLess(large_yaw.hold_millis, 160)

    def test_clipped_shape_still_requires_a_useful_safe_portion(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=4431))
        narrow_visible_sliver = policy.classify_camera(
            geometry(
                left=CANVAS.x + 200,
                top=CANVAS.y - 100,
                width=90,
                height=154,
                point=ScreenPoint(CANVAS.x + 245, CANVAS.y + 50),
            ),
            CANVAS,
            decision_id="object-clipped-sliver",
        )

        self.assertEqual("barely_visible", narrow_visible_sliver.classification)
        self.assertEqual("reframe", narrow_visible_sliver.action)
        self.assertGreater(
            narrow_visible_sliver.screen_correction_y_px,
            policy.config.camera_deadband_px,
        )

    def test_large_clipped_interaction_shape_accepts_safe_partial_geometry(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=44311))
        large_clipped = policy.classify_camera(
            geometry(
                left=300,
                top=650,
                width=500,
                height=500,
                point=ScreenPoint(400, 700),
                ratio=0.41,
            ),
            CANVAS,
            decision_id="object-large-clipped-safe-partial",
            framing_context="interaction",
            yaw_error_units=7_700,
        )

        self.assertLess(
            0.41,
            policy.config.camera_min_visible_ratio,
        )
        self.assertEqual("usable", large_clipped.classification)
        self.assertEqual("none", large_clipped.action)
        self.assertEqual(0.0, large_clipped.correction_distance_px)
        self.assertGreaterEqual(
            large_clipped.edge_clearance_px,
            large_clipped.required_edge_margin_px,
        )

    def test_live_tall_tree_sequence_stops_before_pitch_limit(self) -> None:
        viewport = ScreenBounds(1252, 273, 1440, 1009)
        player = ScreenPoint(1972, 814)
        policy = BehaviorPolicy(BehaviorConfig(seed=4432))
        samples = (
            (3064, 355, 148, 0.50),
            (2862, 361, 155, 0.50),
            (2584, 373, 167, 0.50),
            (2261, 394, 188, 0.50),
            (2081, 409, 200, 0.50),
            (1971, 421, 209, 0.75),
        )
        decisions = []
        for pitch, target_y, shape_height, visible_ratio in samples:
            decisions.append(
                (
                    pitch,
                    policy.classify_camera(
                        geometry(
                            left=1901,
                            top=261,
                            width=221,
                            height=shape_height,
                            point=ScreenPoint(2011, target_y),
                            ratio=visible_ratio,
                        ),
                        viewport,
                        decision_id=f"live-tree-pitch-{pitch}",
                        route_dx=1,
                        route_dy=3,
                        player_point=player,
                        framing_context="interaction",
                    ),
                )
            )

        for _, decision in decisions[:-1]:
            self.assertEqual("reframe", decision.action)
        stopping_pitch, stopped = decisions[-1]
        self.assertEqual(1971, stopping_pitch)
        self.assertGreater(stopping_pitch, 1024)
        self.assertEqual("usable", stopped.classification)
        self.assertEqual("none", stopped.action)
        self.assertEqual(0.0, stopped.correction_distance_px)
        self.assertGreaterEqual(
            stopped.edge_clearance_px,
            stopped.required_edge_margin_px,
        )
        self.assertLess(stopped.target_bounds.y, viewport.y)

    def test_clipped_shape_preserves_signed_screen_correction(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=444))
        clipped_left = policy.classify_camera(
            geometry(
                left=CANVAS.x - 20,
                top=CANVAS.y + 180,
                width=90,
                height=110,
                point=ScreenPoint(CANVAS.x + 25, CANVAS.y + 235),
            ),
            CANVAS,
            decision_id="object-clipped-left",
        )
        clipped_right = policy.classify_camera(
            geometry(
                left=CANVAS.x + CANVAS.width - 50,
                top=CANVAS.y + 180,
                width=90,
                height=110,
                point=ScreenPoint(CANVAS.x + CANVAS.width - 5, CANVAS.y + 235),
            ),
            CANVAS,
            decision_id="object-clipped-right",
        )

        self.assertGreater(clipped_left.screen_correction_x_px, 0)
        self.assertLess(clipped_right.screen_correction_x_px, 0)
        self.assertGreater(clipped_left.correction_distance_px, 0)
        self.assertGreater(clipped_right.correction_distance_px, 0)

    def test_object_shape_margin_is_an_authoritative_tuning_knob(self) -> None:
        target = geometry(
            left=CANVAS.x + 15,
            top=CANVAS.y + 120,
            width=500,
            height=220,
            point=CANVAS.center,
        )
        lenient = BehaviorPolicy(
            BehaviorConfig(seed=445, camera_object_shape_margin_px=10)
        ).classify_camera(target, CANVAS, decision_id="object-margin-lenient")
        strict = BehaviorPolicy(
            BehaviorConfig(seed=445, camera_object_shape_margin_px=30)
        ).classify_camera(target, CANVAS, decision_id="object-margin-strict")

        self.assertEqual("well_framed", lenient.classification)
        self.assertEqual("barely_visible", strict.classification)
        self.assertEqual(10, lenient.required_edge_margin_px)
        self.assertEqual(30, strict.required_edge_margin_px)

    def test_object_edge_hysteresis_ignores_one_pixel_margin_noise(self) -> None:
        policy = BehaviorPolicy(
            BehaviorConfig(
                seed=446,
                camera_object_shape_margin_px=44,
                camera_edge_hysteresis_px=6,
            )
        )
        target = geometry(
            left=CANVAS.x + 43,
            top=CANVAS.y + 120,
            width=500,
            height=220,
            point=CANVAS.center,
        )

        framing = policy.classify_camera(
            target,
            CANVAS,
            decision_id="object-margin-one-pixel-noise",
        )

        self.assertEqual(43.0, framing.edge_clearance_px)
        self.assertEqual(1.0, framing.screen_correction_x_px)
        self.assertEqual("well_framed", framing.classification)
        self.assertEqual("none", framing.action)

    def test_camera_edge_deadband_stops_near_margin_without_accepting_clipping(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=45))
        within_deadband = policy.classify_camera(
            geometry(point=ScreenPoint(CANVAS.x + 50, CANVAS.y + 300)),
            CANVAS,
            decision_id="camera-edge-deadband",
        )
        clipped = policy.classify_camera(
            geometry(point=ScreenPoint(CANVAS.x + 25, CANVAS.y + 300)),
            CANVAS,
            decision_id="camera-edge-clipped",
        )

        self.assertEqual("well_framed", within_deadband.classification)
        self.assertEqual("none", within_deadband.action)
        self.assertEqual("barely_visible", clipped.classification)
        self.assertEqual("reframe", clipped.action)

    def test_offscreen_camera_target_uses_a_large_bounded_correction(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=46))
        offscreen = policy.classify_camera(
            TargetGeometry(),
            CANVAS,
            decision_id="camera-offscreen",
            route_dx=12,
        )

        self.assertEqual("not_visible", offscreen.classification)
        self.assertEqual("reframe", offscreen.action)
        self.assertGreaterEqual(offscreen.hold_millis, 232)
        self.assertLessEqual(offscreen.hold_millis, 250)

    def test_offscreen_hold_decelerates_as_world_bearing_approaches(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=461))
        near_bearing = policy.classify_camera(
            TargetGeometry(),
            CANVAS,
            decision_id="camera-offscreen-bearing",
            route_dx=12,
            yaw_error_units=512,
        )
        far_bearing = policy.classify_camera(
            TargetGeometry(),
            CANVAS,
            decision_id="camera-offscreen-bearing",
            route_dx=12,
            yaw_error_units=8192,
        )

        self.assertEqual("not_visible", near_bearing.classification)
        self.assertLess(near_bearing.hold_millis, far_bearing.hold_millis)
        self.assertGreaterEqual(near_bearing.hold_millis, 80)
        self.assertLessEqual(far_bearing.hold_millis, 250)

    def test_camera_hold_uses_injected_protocol_capability_not_policy_ceiling(self) -> None:
        policy = BehaviorPolicy(
            BehaviorConfig(
                seed=462,
                camera_hold_max_millis=1_000,
            ),
            camera_capabilities=CameraKeyCapabilities(
                max_hold_millis=90,
                source="test-arduino-capability",
            ),
        )

        framing = policy.classify_camera(
            TargetGeometry(),
            CANVAS,
            decision_id="capability-capped-camera",
            yaw_error_units=8_000,
        )

        self.assertEqual(90, framing.hold_millis)
        self.assertEqual(90, policy.camera_capabilities.max_hold_millis)

    def test_zoom_required_is_typed_only_when_framing_cannot_fit_safely(self) -> None:
        policy = BehaviorPolicy(BehaviorConfig(seed=463))
        oversized = geometry(
            left=CANVAS.x - 100,
            top=CANVAS.y + 20,
            width=CANVAS.width + 200,
            height=CANVAS.height - 40,
            point=ScreenPoint(CANVAS.x + CANVAS.width - 10, CANVAS.center.y),
            ratio=0.7,
        )

        required = policy.classify_camera(
            oversized,
            CANVAS,
            decision_id="zoom-required",
            camera_zoom=700,
            camera_pitch=1024,
        )
        safe = policy.classify_camera(
            geometry(point=CANVAS.center),
            CANVAS,
            decision_id="zoom-outside-but-safe",
            camera_zoom=700,
            camera_pitch=1024,
        )

        self.assertTrue(required.zoom_required_but_unavailable)
        self.assertEqual("zoom_required_but_unavailable", required.action)
        self.assertEqual(0, required.hold_millis)
        self.assertFalse(safe.zoom_required_but_unavailable)
        self.assertEqual("none", safe.action)

    def test_camera_hold_and_timing_are_seed_reproducible_and_contextual(self) -> None:
        first = BehaviorPolicy(BehaviorConfig(seed=2026))
        second = BehaviorPolicy(BehaviorConfig(seed=2026))
        camera_first = first.classify_camera(
            geometry(point=ScreenPoint(110, 230)),
            CANVAS,
            decision_id="camera-1",
            route_dy=20,
        )
        camera_second = second.classify_camera(
            geometry(point=ScreenPoint(110, 230)),
            CANVAS,
            decision_id="camera-1",
            route_dy=20,
        )
        timing_first = first.timing(
            "action-1",
            pointer_distance_px=780,
            target_extent_px=20,
            camera_moved=True,
            menu_opened=True,
            route_move=True,
        )
        timing_second = second.timing(
            "action-1",
            pointer_distance_px=780,
            target_extent_px=20,
            camera_moved=True,
            menu_opened=True,
            route_move=True,
        )

        self.assertEqual(camera_first, camera_second)
        self.assertEqual(timing_first, timing_second)
        self.assertGreaterEqual(camera_first.hold_millis, 80)
        self.assertLessEqual(camera_first.hold_millis, 250)
        self.assertGreater(timing_first.route_pause_seconds, 0.0)
        self.assertGreater(timing_first.settle_delay_seconds, 0.0)

    def test_invalid_configuration_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            BehaviorConfig(seed=-1)
        with self.assertRaises(ValueError):
            BehaviorConfig(route_lookahead_points=17)
        with self.assertRaises(ValueError):
            BehaviorConfig(camera_hold_min_millis=500, camera_hold_max_millis=100)
        with self.assertRaises(ValueError):
            BehaviorConfig(camera_yaw_deadband_units=257)
        with self.assertRaises(ValueError):
            BehaviorConfig(camera_max_corrections=17)
        with self.assertRaises(ValueError):
            BehaviorConfig(resource_camera_suppression_ticks=0)
        with self.assertRaises(ValueError):
            BehaviorConfig(aim_parallel_edge_clearance_floor_px=0)
        with self.assertRaises(ValueError):
            BehaviorConfig(aim_parallel_edge_cost_weight=51.0)
        with self.assertRaises(ValueError):
            BehaviorConfig(
                camera_edge_margin_px=40,
                camera_route_leading_allowance_px=41,
            )
        with self.assertRaises(ValueError):
            BehaviorConfig(
                camera_edge_margin_px=40,
                camera_edge_hysteresis_px=41,
            )
        with self.assertRaises(ValueError):
            BehaviorConfig(pre_move_delay_range=(0.5, 0.1))
        with self.assertRaises(ValueError):
            BehaviorConfig(
                route_corridor_radius_tiles=3.0,
                route_recovery_radius_tiles=2.0,
            )


if __name__ == "__main__":
    unittest.main()

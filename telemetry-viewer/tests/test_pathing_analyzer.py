import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import pathing_analyzer
from analyzers.live_state import ActivityContext, NavigationContext, NavigationIntentContext, PlayerContext, ProcessInventoryContext


FULL_BLOCK = 256


def collision_window(width=5, height=5, *, blocked=None, flags=None):
    blocked = set(blocked or [])
    flags = dict(flags or {})
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            row.append(flags.get((x, y), FULL_BLOCK if (x, y) in blocked else 0))
        rows.append(row)
    return {
        "collisionWindowAvailable": True,
        "collisionWindow": {
            "plane": 0,
            "playerSceneX": 1,
            "playerSceneY": 1,
            "minSceneX": 0,
            "minSceneY": 0,
            "width": width,
            "height": height,
            "flags": rows,
        },
    }


def player():
    return PlayerContext(world_x=100, world_y=100, plane=0, scene_x=1, scene_y=1)


def moved_player():
    return PlayerContext(world_x=101, world_y=100, plane=0, scene_x=2, scene_y=1)


def destination(**overrides):
    value = {
        "targetType": "sceneObject",
        "classId": "bank_booth",
        "targetName": "Bank booth",
        "id": 10355,
        "worldX": 102,
        "worldY": 100,
        "plane": 0,
        "sceneX": 3,
        "sceneY": 1,
        "distanceTiles": 2,
    }
    value.update(overrides)
    return value


def nav_intent(**overrides):
    value = NavigationIntentContext(
        navigation_needed=True,
        navigation_reason="service_target_available",
        target_kind="service",
        destination_target=destination(),
        source_tick=12,
    )
    for key, item in overrides.items():
        setattr(value, key, item)
    return value


class PathingAnalyzerTest(unittest.TestCase):
    def test_same_destination_path_is_retained_while_player_is_moving(self):
        state = pathing_analyzer.PathIntentState()
        first = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=moved_player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertTrue(second.path_intent_retained)
        self.assertEqual(second.movement_state, "moving")
        self.assertEqual(second.retention_reason, "player_moving_same_destination")
        self.assertEqual(second.predicted_path_tiles, first.predicted_path_tiles)
        self.assertEqual(second.path_stable_for_ticks, 2)
        self.assertEqual(second.path_intent_key, first.path_intent_key)

    def test_path_switches_when_policy_phase_changes(self):
        state = pathing_analyzer.PathIntentState()
        pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=moved_player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "target_selected", "activeIntent": "target_selected"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertFalse(second.path_intent_retained)
        self.assertEqual(second.switch_reason, "path_intent_key_changed")
        self.assertEqual(second.path_stable_for_ticks, 1)

    def test_path_switch_debounces_changed_destination_while_moving(self):
        state = pathing_analyzer.PathIntentState(switch_debounce_ticks=2)
        first = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100, objectKey="booth-a")),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=moved_player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=2, worldX=105, worldY=101, objectKey="booth-b")),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )
        third = pathing_analyzer.analyze_pathing_context(
            player_context=moved_player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=2, worldX=105, worldY=101, objectKey="booth-b")),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=3,
        )

        self.assertTrue(second.path_intent_retained)
        self.assertEqual(second.retention_reason, "candidate_switch_debounce")
        self.assertEqual(second.predicted_path_tiles, first.predicted_path_tiles)
        self.assertFalse(third.path_intent_retained)
        self.assertEqual(third.switch_reason, "destination_changed_after_debounce")
        self.assertEqual(third.destination_target_key, "objectKey:booth-b")

    def test_player_away_from_final_approach_is_not_arrived(self):
        state = pathing_analyzer.PathIntentState()
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )

        self.assertFalse(context.arrived_at_final_approach)
        self.assertFalse(context.arrived_near_destination)
        self.assertFalse(context.service_ready)
        self.assertFalse(context.path_completed)
        self.assertTrue(context.pathing_needed)
        self.assertGreater(context.distance_to_final_approach, 0)

    def test_path_completes_when_player_arrives_at_final_approach(self):
        state = pathing_analyzer.PathIntentState()
        first = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        final_tile = first.final_approach_tile
        arrived = PlayerContext(world_x=final_tile["worldX"], world_y=final_tile["worldY"], plane=0, scene_x=5, scene_y=1)
        second = pathing_analyzer.analyze_pathing_context(
            player_context=arrived,
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )
        third = pathing_analyzer.analyze_pathing_context(
            player_context=arrived,
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=3,
        )

        self.assertTrue(second.arrived_at_final_approach)
        self.assertEqual(second.distance_to_final_approach, 0)
        self.assertFalse(second.service_ready)
        self.assertEqual(second.arrival_reason, "arrival_tentative_player_moving")
        self.assertTrue(third.arrived_at_final_approach)
        self.assertTrue(third.service_ready)
        self.assertEqual(third.service_ready_reason, "arrived_at_service")
        self.assertEqual(third.path_completion_reason, "arrived_at_service")
        self.assertTrue(third.path_completed)
        self.assertFalse(third.pathing_needed)
        self.assertEqual(third.reason, "arrived_at_service")
        self.assertIsNotNone(state.active_path_intent_key)

    def test_moving_player_arrival_is_tentative_until_stopped(self):
        state = pathing_analyzer.PathIntentState()
        first = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        final_tile = first.final_approach_tile
        arrived = PlayerContext(world_x=final_tile["worldX"], world_y=final_tile["worldY"], plane=0, scene_x=5, scene_y=1)
        second = pathing_analyzer.analyze_pathing_context(
            player_context=arrived,
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertTrue(second.arrived_at_final_approach)
        self.assertFalse(second.service_ready)
        self.assertEqual(second.arrival_reason, "arrival_tentative_player_moving")
        self.assertTrue(second.pathing_needed)

    def test_player_near_destination_becomes_service_ready_after_stability(self):
        state = pathing_analyzer.PathIntentState()
        near_destination = PlayerContext(world_x=104, world_y=100, plane=0, scene_x=5, scene_y=1)
        destination_with_radius = destination(sceneX=6, sceneY=1, worldX=105, worldY=100, approachRadiusTiles=1)
        first = pathing_analyzer.analyze_pathing_context(
            player_context=near_destination,
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination_with_radius),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=near_destination,
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination_with_radius),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertTrue(first.arrived_near_destination)
        self.assertEqual(first.distance_to_destination, 1)
        self.assertFalse(first.service_ready)
        self.assertTrue(second.service_ready)
        self.assertIn(second.arrival_reason, {"arrived_near_destination", "arrived_at_final_approach"})

    def test_path_clears_when_current_path_is_blocked(self):
        state = pathing_analyzer.PathIntentState()
        pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5, blocked={(2, 1), (1, 0), (1, 2), (0, 1)})),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertFalse(second.path_intent_retained)
        self.assertEqual(second.local_reachability, "blocked")
        self.assertEqual(second.switch_reason, "path_blocked")
        self.assertIsNone(state.active_path_intent_key)

    def test_path_switches_when_destination_disappears(self):
        state = pathing_analyzer.PathIntentState()
        pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=6, sceneY=1, worldX=105, worldY=100)),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=1,
        )
        second = pathing_analyzer.analyze_pathing_context(
            player_context=moved_player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=5)),
            navigation_intent_context=NavigationIntentContext(
                navigation_needed=True,
                navigation_reason="destination_missing",
                target_kind="service",
                destination_target=None,
            ),
            activity_context=ActivityContext(current_activity="moving"),
            generic_task_state={"phase": "inventory_full", "activeIntent": "needs_service"},
            path_intent_state=state,
            source_tick=2,
        )

        self.assertFalse(second.path_intent_retained)
        self.assertEqual(second.switch_reason, "destination_missing")
        self.assertIsNone(state.active_path_intent_key)

    def test_osrs_like_predicted_is_default_when_collision_data_exists(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3, targetType="tile", classId="tile")),
        )

        payload = context.to_dict()
        self.assertEqual(payload["predictedMovementModel"], "osrs_like_predicted")
        self.assertEqual(payload["pathLengthTiles"], 2)
        self.assertEqual(payload["diagonalStepCount"], 2)
        self.assertEqual(payload["cardinalStepCount"], 0)

    def test_osrs_like_predicted_allows_guarded_diagonal_path(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3, targetType="tile", classId="tile")),
            movement_model="osrs_like_predicted",
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["predictedMovementModel"], "osrs_like_predicted")
        self.assertEqual(payload["pathLengthTiles"], 2)
        self.assertEqual(payload["nextWaypointTile"], {"worldX": 101, "worldY": 101, "plane": 0})
        self.assertTrue(payload["exactDestinationReached"])
        self.assertFalse(payload["finalApproachSubstituted"])
        self.assertEqual(payload["diagonalStepCount"], 2)
        self.assertEqual(payload["cardinalStepCount"], 0)

    def test_osrs_like_predicted_blocks_diagonal_when_adjacent_cardinals_are_blocked(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(
                collision_window_available=True,
                raw=collision_window(blocked={(2, 1), (1, 2), (0, 1), (1, 0), (0, 0), (0, 2), (2, 0)}),
            ),
            navigation_intent_context=nav_intent(destination_target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3)),
            movement_model="osrs_like_predicted",
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "blocked")
        self.assertEqual(payload["predictedPathTiles"], [])
        self.assertEqual(payload["reason"], "destination_inside_window_but_no_path")

    def test_reachable_path_in_simple_collision_grid(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(targetType="tile", classId="tile")),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["pathingNeeded"])
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["reason"], "path_reachable")
        self.assertEqual(payload["pathLengthTiles"], 2)
        self.assertEqual(payload["nextWaypointTile"], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 100, "plane": 0})
        self.assertTrue(payload["collisionWindowAvailable"])
        self.assertTrue(payload["destinationInsideCollisionWindow"])
        self.assertTrue(payload["destinationPlaneMatches"])
        self.assertLessEqual(len(payload["predictedPathTiles"]), 24)
        self.assertEqual(payload["predictedMovementModel"], "osrs_like_predicted")
        self.assertIn("Predicted local path", " ".join(payload["predictedMovementNotes"]))

    def test_object_destination_uses_final_approach_even_when_target_tile_is_walkable(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3)),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 102, "plane": 0})
        self.assertEqual(payload["finalApproachTile"], {"worldX": 101, "worldY": 102, "plane": 0})
        self.assertNotEqual(payload["destinationTile"], payload["finalApproachTile"])
        self.assertFalse(payload["exactDestinationReached"])
        self.assertTrue(payload["finalApproachSubstituted"])
        self.assertEqual(payload["finalApproachTileSource"], "local_collision_approach_candidate")
        self.assertTrue(payload["finalApproachTileUsed"])
        self.assertGreater(payload["finalApproachCandidateCount"], 0)
        self.assertEqual(payload["pathTargetTile"], payload["finalApproachTile"])
        self.assertNotIn("navigation.interaction_tile", payload["missingCapabilities"])

    def test_blocked_adjacent_approach_tile_is_rejected(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(blocked={(2, 1)})),
            navigation_intent_context=nav_intent(),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertNotEqual(payload["finalApproachTile"], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertGreaterEqual(payload["rejectedApproachTileReasons"].get("tile_blocked", 0), 1)
        self.assertEqual(payload["pathTargetTile"], payload["finalApproachTile"])

    def test_shortest_reachable_approach_tile_wins(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=6, height=5)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=4, sceneY=2, worldX=103, worldY=101)),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["finalApproachTile"], {"worldX": 102, "worldY": 101, "plane": 0})
        self.assertEqual(payload["pathLengthTiles"], 2)
        self.assertEqual(payload["pathTargetTile"], payload["finalApproachTile"])

    def test_bank_booth_prefers_inside_reachable_approach_tile_over_outside_wall(self):
        block = pathing_analyzer.navigation_reachability
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(
                collision_window_available=True,
                raw=collision_window(
                    width=6,
                    height=4,
                    flags={
                        (3, 1): block.BLOCK_MOVEMENT_OBJECT
                        | block.BLOCK_MOVEMENT_WEST
                        | block.BLOCK_MOVEMENT_NORTH
                        | block.BLOCK_MOVEMENT_SOUTH,
                    },
                ),
            ),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=3, sceneY=1, worldX=102, worldY=100)),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["finalApproachTile"], {"worldX": 103, "worldY": 100, "plane": 0})
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 100, "plane": 0})
        self.assertNotEqual(payload["finalApproachTile"], payload["destinationTile"])
        self.assertEqual(payload["approachQuality"], "direct_side_access")
        self.assertEqual(payload["selectedApproachReason"], "reachable_direct_side_access")
        self.assertGreaterEqual(payload["approachCandidatesRejectedByBlockedSide"], 1)

    def test_no_side_access_approach_is_marked_suspect_not_clean_reachable(self):
        block = pathing_analyzer.navigation_reachability
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(
                collision_window_available=True,
                raw=collision_window(
                    width=6,
                    height=4,
                    flags={
                        (3, 1): block.BLOCK_MOVEMENT_OBJECT
                        | block.BLOCK_MOVEMENT_WEST
                        | block.BLOCK_MOVEMENT_EAST
                        | block.BLOCK_MOVEMENT_NORTH
                        | block.BLOCK_MOVEMENT_SOUTH,
                    },
                ),
            ),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=3, sceneY=1, worldX=102, worldY=100)),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "unknown")
        self.assertEqual(payload["reason"], "approach_side_access_blocked")
        self.assertEqual(payload["approachQuality"], "suspect_outside_wall")
        self.assertTrue(payload["pathSegmentsValid"])
        self.assertGreater(payload["approachCandidatesRejectedByBlockedSide"], 0)

    def test_deposit_box_inferred_from_service_candidate_type_requires_strict_side_access(self):
        block = pathing_analyzer.navigation_reachability
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(
                collision_window_available=True,
                raw=collision_window(
                    width=6,
                    height=4,
                    flags={
                        (3, 1): block.BLOCK_MOVEMENT_OBJECT
                        | block.BLOCK_MOVEMENT_WEST
                        | block.BLOCK_MOVEMENT_EAST
                        | block.BLOCK_MOVEMENT_NORTH
                        | block.BLOCK_MOVEMENT_SOUTH,
                    },
                ),
            ),
            navigation_intent_context=nav_intent(
                destination_target=destination(
                    classId="bank_related",
                    serviceCandidateType="deposit_box",
                    targetName="Bank Deposit Box",
                    sceneX=3,
                    sceneY=1,
                    worldX=102,
                    worldY=100,
                )
            ),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "unknown")
        self.assertEqual(payload["reason"], "approach_side_access_blocked")
        self.assertEqual(payload["approachQuality"], "suspect_outside_wall")
        self.assertEqual(payload["selectedApproachReason"], "suspect_side_access_unknown")

    def test_path_segment_validation_rejects_blocked_cardinal_step(self):
        block = pathing_analyzer.navigation_reachability
        window = pathing_analyzer.navigation_reachability.parse_collision_window(
            collision_window(flags={(1, 1): block.BLOCK_MOVEMENT_EAST})["collisionWindow"]
        )

        validation = pathing_analyzer.validate_path_segments(window, [(1, 1), (2, 1)], "osrs_like_predicted")

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["invalidCount"], 1)
        self.assertEqual(validation["firstInvalidSegment"]["reason"], "blocked_cardinal_step")

    def test_path_segment_validation_rejects_diagonal_corner_clipping(self):
        window = pathing_analyzer.navigation_reachability.parse_collision_window(
            collision_window(blocked={(2, 1)})["collisionWindow"]
        )

        validation = pathing_analyzer.validate_path_segments(window, [(1, 1), (2, 2)], "osrs_like_predicted")

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["firstInvalidSegment"]["reason"], "blocked_diagonal_step")

    def test_invalid_emitted_path_is_marked_not_reachable_but_kept_for_diagnostics(self):
        block = pathing_analyzer.navigation_reachability
        fake_path = pathing_analyzer.LocalPathResult(
            "reachable",
            "local_path_found",
            [(1, 1), (2, 1)],
            2,
            False,
            True,
            final_approach_scene_tile=(1, 1),
            final_approach_tile_source="path_before_destination",
            path_target_scene_tile=(2, 1),
            path_target_tile_source="exact_destination_tile",
        )

        with patch.object(pathing_analyzer, "local_scene_path", return_value=fake_path):
            context = pathing_analyzer.analyze_pathing_context(
                player_context=player(),
                navigation_context=NavigationContext(
                    collision_window_available=True,
                    raw=collision_window(flags={(1, 1): block.BLOCK_MOVEMENT_EAST}),
                ),
                navigation_intent_context=nav_intent(destination_target=destination(targetType="tile", classId="tile")),
            )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "unknown")
        self.assertEqual(payload["reason"], "invalid_path_segment")
        self.assertFalse(payload["pathSegmentsValid"])
        self.assertEqual(payload["invalidPathSegmentCount"], 1)
        self.assertEqual(payload["firstInvalidPathSegment"]["reason"], "blocked_cardinal_step")
        self.assertEqual(payload["predictedPathTiles"], [{"worldX": 101, "worldY": 100, "plane": 0}])

    def test_npc_target_handling_preserves_service_selection_pathing(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(
                destination_target=destination(
                    targetType="npc",
                    classId="banker",
                    targetName="Banker",
                    sceneX=3,
                    sceneY=2,
                    worldX=102,
                    worldY=101,
                )
            ),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 101, "plane": 0})
        self.assertEqual(payload["finalApproachTileSource"], "local_collision_approach_candidate")
        self.assertEqual(payload["pathTargetTile"], payload["finalApproachTile"])

    def test_cardinal_only_fallback_still_uses_cardinal_steps(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(worldX=102, worldY=102, sceneX=3, sceneY=3, targetType="tile", classId="tile")),
            movement_model="cardinal_only",
        )

        payload = context.to_dict()
        self.assertEqual(payload["predictedMovementModel"], "cardinal_only")
        self.assertEqual(payload["pathLengthTiles"], 4)
        self.assertEqual(payload["nextWaypointTile"], {"worldX": 101, "worldY": 100, "plane": 0})

    def test_final_approach_tile_is_last_predicted_tile_when_destination_tile_is_blocked(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(blocked={(3, 1)})),
            navigation_intent_context=nav_intent(),
        )

        payload = context.to_dict()
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 100, "plane": 0})
        self.assertEqual(payload["predictedPathTiles"][-1], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertEqual(payload["finalApproachTile"], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertFalse(payload["exactDestinationReached"])
        self.assertTrue(payload["finalApproachSubstituted"])
        self.assertEqual(payload["finalApproachTileSource"], "local_collision_approach_candidate")
        self.assertNotIn("navigation.interaction_tile", payload["missingCapabilities"])

    def test_blocked_path_in_simple_collision_grid(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=PlayerContext(world_x=100, world_y=100, plane=0, scene_x=0, scene_y=1),
            navigation_context=NavigationContext(
                collision_window_available=True,
                raw=collision_window(width=3, height=3, blocked={(1, 0), (1, 1), (1, 2)}),
            ),
            navigation_intent_context=NavigationIntentContext(
                navigation_needed=True,
                navigation_reason="service_target_available",
                target_kind="service",
                destination_target=destination(worldX=102, worldY=100, sceneX=2, sceneY=1),
            ),
        )

        self.assertEqual(context.local_reachability, "blocked")
        self.assertEqual(context.status, "WARN")
        self.assertEqual(context.reason, "destination_inside_window_but_no_path")

    def test_destination_outside_collision_window_is_unknown(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=20, sceneY=20, worldX=119, worldY=119)),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "destination_outside_collision_window")
        self.assertFalse(context.destination_inside_collision_window)
        self.assertIn("navigation.global_pathfinding", context.missing_capabilities)

    def test_collision_window_missing_is_unknown(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=False, raw={}),
            navigation_intent_context=nav_intent(),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "collision_window_missing")
        self.assertFalse(context.collision_window_available)
        self.assertEqual(context.collision_window_missing_reason, "collision_window_missing")
        self.assertIn("navigation.local_collision_window", context.missing_capabilities)

    def test_stale_collision_window_is_unknown(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, collision_window_fresh=False, collision_window_age_ticks=8, raw=collision_window()),
            navigation_intent_context=nav_intent(),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "collision_window_stale")
        self.assertFalse(context.collision_window_fresh)
        self.assertEqual(context.collision_window_age_ticks, 8)
        self.assertIn("navigation.local_collision_window", context.missing_capabilities)

    def test_plane_mismatch_is_reported(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(plane=2)),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "destination_plane_mismatch")
        self.assertFalse(context.destination_plane_matches)

    def test_max_node_cap_prevents_runaway(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=8, height=8)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=7, sceneY=7, worldX=106, worldY=106)),
            max_nodes=1,
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertTrue(context.pathing_budget_exceeded)
        self.assertEqual(context.reason, "pathing_budget_exceeded")

    def test_process_inventory_does_not_need_pathing(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=NavigationIntentContext(
                navigation_needed=False,
                navigation_reason="local_navigation_only",
                target_kind="process_inventory",
            ),
            process_inventory_context=ProcessInventoryContext(process_required=True, process_type_needed="firemaking"),
        )

        self.assertFalse(context.pathing_needed)
        self.assertEqual(context.reason, "not_needed_for_process_inventory")
        self.assertEqual(context.local_reachability, "unknown")

    def test_service_target_missing_does_not_need_pathing(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=NavigationIntentContext(
                navigation_needed=False,
                navigation_reason="service_target_missing",
                target_kind="none",
                destination_target=None,
            ),
        )

        self.assertFalse(context.pathing_needed)
        self.assertEqual(context.reason, "service_target_missing")

    def test_predicted_path_tiles_are_capped(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window(width=20, height=3)),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=19, sceneY=1, worldX=118, worldY=100)),
            max_predicted_tiles=4,
        )

        self.assertLessEqual(len(context.predicted_path_tiles), 4)
        self.assertEqual(context.path_cap_tiles, 4)
        self.assertEqual(context.predicted_step_count, context.path_length_tiles)
        payload = context.to_dict()
        self.assertGreater(payload["predictedPathCount"], payload["predictedPathDisplayedCount"])
        self.assertEqual(payload["predictedPathAvailableCount"], payload["predictedPathCount"])
        self.assertEqual(payload["predictedPathDisplayedCount"], 4)
        self.assertTrue(payload["pathWasCapped"])
        self.assertTrue(payload["pathDisplayWasCapped"])

    def test_pathing_context_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            context = pathing_analyzer.analyze_pathing_context(
                player_context=player(),
                navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
                navigation_intent_context=nav_intent(),
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)
        payload = context.to_dict()
        self.assertIn("destinationTile", payload)
        self.assertIn("nextWaypointTile", payload)
        self.assertIn("predictedPathTiles", payload)


if __name__ == "__main__":
    unittest.main()

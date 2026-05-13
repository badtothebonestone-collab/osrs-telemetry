import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import pathing_analyzer
from analyzers.live_state import NavigationContext, NavigationIntentContext, PlayerContext, ProcessInventoryContext


FULL_BLOCK = 256


def collision_window(width=5, height=5, *, blocked=None):
    blocked = set(blocked or [])
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            row.append(FULL_BLOCK if (x, y) in blocked else 0)
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


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


class PathingAnalyzerTest(unittest.TestCase):
    def test_reachable_path_in_simple_collision_grid(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(),
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["pathingNeeded"])
        self.assertEqual(payload["localReachability"], "reachable")
        self.assertEqual(payload["pathLengthTiles"], 2)
        self.assertEqual(payload["nextWaypointTile"], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertEqual(payload["destinationTile"], {"worldX": 102, "worldY": 100, "plane": 0})
        self.assertLessEqual(len(payload["predictedPathTiles"]), 10)
        self.assertEqual(payload["predictedMovementModel"], "cardinal_only")
        self.assertIn("Predicted local path", " ".join(payload["predictedMovementNotes"]))

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
        self.assertEqual(context.reason, "no_local_path")

    def test_destination_outside_collision_window_is_unknown(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=True, raw=collision_window()),
            navigation_intent_context=nav_intent(destination_target=destination(sceneX=20, sceneY=20, worldX=119, worldY=119)),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "destination_outside_collision_window")
        self.assertIn("navigation.global_pathfinding", context.missing_capabilities)

    def test_collision_window_missing_is_unknown(self):
        context = pathing_analyzer.analyze_pathing_context(
            player_context=player(),
            navigation_context=NavigationContext(collision_window_available=False, raw={}),
            navigation_intent_context=nav_intent(),
        )

        self.assertEqual(context.local_reachability, "unknown")
        self.assertEqual(context.reason, "collision_window_missing")
        self.assertIn("navigation.local_collision_window", context.missing_capabilities)

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
        self.assertEqual(context.predicted_step_count, context.path_length_tiles)

    def test_no_action_like_command_fields_and_no_files_written(self):
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
        joined = " ".join(walk_keys(payload)).lower()
        for forbidden in ("action", "click", "input", "menu", "mouse", "keyboard", "walkcommand", "routecommand"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()

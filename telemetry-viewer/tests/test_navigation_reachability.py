import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import navigation_reachability as reachability


def window(flags, *, player_x=0, player_y=0):
    height = len(flags)
    width = len(flags[0]) if flags else 0
    return {
        "tick": 1,
        "plane": 0,
        "playerSceneX": player_x,
        "playerSceneY": player_y,
        "windowRadius": 4,
        "minSceneX": 0,
        "maxSceneX": width - 1,
        "minSceneY": 0,
        "maxSceneY": height - 1,
        "width": width,
        "height": height,
        "flags": flags,
        "collisionKnown": True,
        "collisionWindowHash": "test",
    }


class NavigationReachabilityTest(unittest.TestCase):
    def test_reachable_straight_path(self):
        flags = [[0 for _x in range(5)] for _y in range(5)]
        result = reachability.reachability_for_target(
            window(flags, player_x=0, player_y=2),
            player_scene_x=0,
            player_scene_y=2,
            player_plane=0,
            target_scene_x=4,
            target_scene_y=2,
            target_plane=0,
        )

        self.assertTrue(result["reachable"])
        self.assertEqual(result["directReachability"], "reachable")
        self.assertGreater(result["pathLengthTiles"], 0)
        self.assertTrue(result["conservativeMode"])

    def test_blocked_cardinal_path(self):
        flags = [[0 for _x in range(5)] for _y in range(5)]
        for y in range(5):
            flags[y][2] = reachability.BLOCK_MOVEMENT_FULL

        result = reachability.reachability_for_target(
            window(flags, player_x=0, player_y=2),
            player_scene_x=0,
            player_scene_y=2,
            player_plane=0,
            target_scene_x=4,
            target_scene_y=2,
            target_plane=0,
        )

        self.assertFalse(result["reachable"])
        self.assertEqual(result["directReachability"], "blocked")
        self.assertIn("no 4-direction local path", result["reason"])

    def test_target_outside_window_is_unknown(self):
        flags = [[0 for _x in range(3)] for _y in range(3)]
        result = reachability.reachability_for_target(
            window(flags, player_x=1, player_y=1),
            player_scene_x=1,
            player_scene_y=1,
            player_plane=0,
            target_scene_x=10,
            target_scene_y=10,
            target_plane=0,
        )

        self.assertIsNone(result["reachable"])
        self.assertEqual(result["directReachability"], "unknown")
        self.assertIn("targetInCollisionWindow", result["missingNavigationFields"])

    def test_same_tile_or_adjacent_returns_zero_length(self):
        flags = [[0 for _x in range(3)] for _y in range(3)]
        result = reachability.reachability_for_target(
            window(flags, player_x=1, player_y=1),
            player_scene_x=1,
            player_scene_y=1,
            player_plane=0,
            target_scene_x=1,
            target_scene_y=1,
            target_plane=0,
        )

        self.assertTrue(result["reachable"])
        self.assertEqual(result["pathLengthTiles"], 0)

    def test_missing_grid_is_unknown(self):
        result = reachability.reachability_for_target(
            None,
            player_scene_x=1,
            player_scene_y=1,
            player_plane=0,
            target_scene_x=2,
            target_scene_y=2,
            target_plane=0,
        )

        self.assertIsNone(result["reachable"])
        self.assertEqual(result["directReachability"], "unknown")
        self.assertIn("collisionWindow", result["missingNavigationFields"])


if __name__ == "__main__":
    unittest.main()

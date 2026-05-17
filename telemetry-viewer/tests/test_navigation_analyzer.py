import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import navigation_analyzer


def collision_window():
    return {
        "plane": 0,
        "playerSceneX": 10,
        "playerSceneY": 10,
        "minSceneX": 8,
        "minSceneY": 8,
        "width": 5,
        "height": 5,
        "windowRadius": 2,
        "flags": [[0, 0, 0, 0, 0] for _ in range(5)],
    }


class NavigationAnalyzerTest(unittest.TestCase):
    def test_summarizes_collision_and_reachability_without_pathfinding(self):
        context = navigation_analyzer.analyze_navigation(
            {
                "latestTick": 20,
                "collisionKnown": True,
                "collisionWindowAvailable": True,
                "collisionWindowTick": 19,
                "playerWorldX": 3200,
                "playerWorldY": 3200,
                "playerPlane": 0,
                "collisionWindow": collision_window(),
            },
            [
                {"navigation": {"directReachability": "reachable"}},
                {"navigation": {"directReachability": "blocked"}},
                {"navigation": {"directReachability": "unknown"}},
            ],
        )

        self.assertTrue(context.collision_known)
        self.assertTrue(context.collision_window_available)
        self.assertTrue(context.collision_window_fresh)
        self.assertEqual(context.collision_window_radius, 2)
        self.assertEqual(context.collision_window_center_world, {"worldX": 3200, "worldY": 3200, "plane": 0})
        self.assertEqual(context.collision_window_plane, 0)
        self.assertEqual(context.collision_window_age_ticks, 1)
        self.assertIsNone(context.collision_window_missing_reason)
        self.assertEqual(context.reachable_count, 1)
        self.assertEqual(context.blocked_count, 1)
        self.assertEqual(context.unknown_count, 1)

    def test_reports_stale_collision_window_reason(self):
        context = navigation_analyzer.analyze_navigation(
            {
                "latestTick": 20,
                "collisionKnown": True,
                "collisionWindowAvailable": True,
                "collisionWindowTick": 10,
                "collisionWindow": collision_window(),
            },
            [],
        )

        self.assertTrue(context.collision_window_available)
        self.assertFalse(context.collision_window_fresh)
        self.assertEqual(context.collision_window_missing_reason, "collision_window_stale")
        self.assertIn("navigation.local_collision_window", context.missing_capabilities)


if __name__ == "__main__":
    unittest.main()

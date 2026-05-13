import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import navigation_analyzer


class NavigationAnalyzerTest(unittest.TestCase):
    def test_summarizes_collision_and_reachability_without_pathfinding(self):
        context = navigation_analyzer.analyze_navigation(
            {"collisionKnown": True, "collisionWindowAvailable": True},
            [
                {"navigation": {"directReachability": "reachable"}},
                {"navigation": {"directReachability": "blocked"}},
                {"navigation": {"directReachability": "unknown"}},
            ],
        )

        self.assertTrue(context.collision_known)
        self.assertTrue(context.collision_window_available)
        self.assertEqual(context.reachable_count, 1)
        self.assertEqual(context.blocked_count, 1)
        self.assertEqual(context.unknown_count, 1)


if __name__ == "__main__":
    unittest.main()


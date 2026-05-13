import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import activity_analyzer


class ActivityAnalyzerTest(unittest.TestCase):
    def test_target_depleted_is_recent_signal_not_current_activity(self):
        context = activity_analyzer.analyze_activity(
            {
                "activityState": {"apparentState": "idle"},
                "woodcuttingState": {"woodcuttingState": "target_depleted"},
            },
            [],
        )

        self.assertEqual(context.current_activity, "idle")
        self.assertIn("target depleted recently", context.recent_task_signals)


if __name__ == "__main__":
    unittest.main()


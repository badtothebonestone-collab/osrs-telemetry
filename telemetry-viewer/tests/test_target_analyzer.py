import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import target_analyzer


class TargetAnalyzerTest(unittest.TestCase):
    def test_extracts_best_and_nearest_generically(self):
        candidates = [
            {"classId": "tree", "targetName": "Tree A", "qualityScore": 10, "distanceTiles": 1},
            {"classId": "tree", "targetName": "Tree B", "qualityScore": 99, "distanceTiles": 7},
            {"classId": "banker", "targetName": "Banker", "qualityScore": 500, "distanceTiles": 2},
        ]

        context = target_analyzer.analyze_targets(candidates, class_id="tree", max_candidates=2)

        self.assertEqual(context.raw_best_target["targetName"], "Tree B")
        self.assertEqual(context.nearest_target["targetName"], "Tree A")
        self.assertEqual(len(context.top_candidates), 2)

    def test_supports_future_target_classes_without_woodcutting_only_logic(self):
        candidates = [{"classId": "bank_booth", "targetName": "Booth", "qualityScore": 12, "distanceTiles": 4}]

        context = target_analyzer.analyze_targets(candidates, class_id="bank_booth")

        self.assertEqual(context.raw_best_target["targetName"], "Booth")
        self.assertEqual(context.candidate_count, 1)


if __name__ == "__main__":
    unittest.main()


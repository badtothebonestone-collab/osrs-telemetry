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

    def test_separates_profile_candidates_from_policy_service_candidates(self):
        candidates = [
            {"classId": "tree", "targetName": "Tree", "qualityScore": 30, "distanceTiles": 2},
            {"classId": "bank_booth", "targetName": "Bank booth", "qualityScore": 999, "distanceTiles": 1},
        ]

        context = target_analyzer.analyze_targets(candidates, class_id="tree", max_candidates=5)

        self.assertEqual(context.raw_best_target["targetName"], "Tree")
        self.assertEqual(context.profile_candidate_count, 1)
        self.assertEqual(context.broad_candidate_count, 2)
        self.assertEqual(context.service_candidate_input_count, 1)
        self.assertEqual(context.service_candidate_inputs[0]["targetName"], "Bank booth")
        self.assertEqual(context.service_candidate_visibility, "available")

    def test_loaded_service_scene_feeds_service_inputs_without_polluting_profile_candidates(self):
        candidates = [
            {"classId": "tree", "targetName": "Tree", "qualityScore": 30, "distanceTiles": 2},
        ]
        loaded_service_scene = [
            {"targetType": "sceneObject", "name": "Bank booth", "objectKey": "booth-loaded", "worldX": 3208, "worldY": 3221, "plane": 0},
        ]

        context = target_analyzer.analyze_targets(
            candidates,
            class_id="tree",
            max_candidates=5,
            loaded_service_scene=loaded_service_scene,
        )

        self.assertEqual(context.raw_best_target["targetName"], "Tree")
        self.assertEqual(context.profile_candidate_count, 1)
        self.assertEqual(context.broad_candidate_count, 1)
        self.assertEqual(context.loaded_service_scene_count, 1)
        self.assertEqual(context.service_candidate_input_count, 1)
        self.assertEqual(context.service_candidate_inputs[0]["objectKey"], "booth-loaded")
        self.assertEqual(context.service_candidate_inputs[0]["_serviceSourceLane"], "loadedServiceScene")

    def test_supports_future_target_classes_without_woodcutting_only_logic(self):
        candidates = [{"classId": "bank_booth", "targetName": "Booth", "qualityScore": 12, "distanceTiles": 4}]

        context = target_analyzer.analyze_targets(candidates, class_id="bank_booth")

        self.assertEqual(context.raw_best_target["targetName"], "Booth")
        self.assertEqual(context.candidate_count, 1)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import candidate_core


class CandidateCoreTest(unittest.TestCase):
    def test_unknown_woodcutting_level_prefers_basic_tree_over_oak(self):
        oak = {"name": "Oak tree", "id": 10820, "classId": "tree", "qualityScore": 100, "distanceTiles": 2}
        tree = {"name": "Tree", "id": 1278, "classId": "tree", "qualityScore": 80, "distanceTiles": 12}

        selected = candidate_core.preferred_woodcutting_resource_candidate([oak, tree])

        self.assertEqual(selected["name"], "Tree")
        self.assertEqual(candidate_core.woodcutting_required_level(oak), 15)
        self.assertEqual(candidate_core.woodcutting_required_level(tree), 1)

    def test_unknown_woodcutting_level_rejects_only_higher_level_resource(self):
        oak = {"name": "Oak tree", "id": 10820, "classId": "tree", "qualityScore": 100, "distanceTiles": 2}

        selected = candidate_core.preferred_woodcutting_resource_candidate([oak])

        self.assertIsNone(selected)

    def test_low_woodcutting_level_rejects_ineligible_resource(self):
        oak = {"name": "Oak tree", "id": 10820, "classId": "tree", "qualityScore": 100, "distanceTiles": 2}

        selected = candidate_core.preferred_woodcutting_resource_candidate([oak], woodcutting_level=10)

        self.assertIsNone(selected)

    def test_known_woodcutting_level_allows_higher_level_resource(self):
        oak = {"name": "Oak tree", "id": 10820, "classId": "tree", "qualityScore": 100, "distanceTiles": 2}
        tree = {"name": "Tree", "id": 1278, "classId": "tree", "qualityScore": 80, "distanceTiles": 12}

        selected = candidate_core.preferred_woodcutting_resource_candidate([oak, tree], woodcutting_level=15)

        self.assertEqual(selected["name"], "Oak tree")

    def test_skill_level_can_be_read_from_context_payload(self):
        level = candidate_core.woodcutting_level_from_context(
            {"skills": {"woodcutting": {"realLevel": 17}}},
            {"woodcuttingLevel": 1},
        )

        self.assertEqual(level, 17)


if __name__ == "__main__":
    unittest.main()

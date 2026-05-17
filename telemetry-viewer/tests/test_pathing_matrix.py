import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_pathing_matrix as matrix


class PathingMatrixTest(unittest.TestCase):
    def scenarios(self):
        return {result["scenario"]: result for result in matrix.run_matrix()}

    def test_straight_path_produces_expected_cardinal_path(self):
        result = self.scenarios()["straight_cardinal_path"]

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["localReachability"], "reachable")
        self.assertEqual(result["movementModel"], "osrs_like_predicted")
        self.assertEqual(result["predictedPathTiles"], [
            {"worldX": 101, "worldY": 100, "plane": 0},
            {"worldX": 102, "worldY": 100, "plane": 0},
        ])
        self.assertEqual(result["cardinalStepCount"], 2)
        self.assertEqual(result["diagonalStepCount"], 0)

    def test_diagonal_path_uses_diagonal_when_valid(self):
        result = self.scenarios()["diagonal_shortcut_available"]

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["localReachability"], "reachable")
        self.assertEqual(result["pathLengthTiles"], 2)
        self.assertEqual(result["nextWaypointTile"], {"worldX": 101, "worldY": 101, "plane": 0})
        self.assertEqual(result["diagonalStepCount"], 2)
        self.assertEqual(result["cardinalStepCount"], 0)

    def test_diagonal_is_blocked_when_adjacent_cardinals_block_corner(self):
        result = self.scenarios()["diagonal_blocked_by_corner"]

        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["localReachability"], "blocked")
        self.assertEqual(result["reason"], "destination_inside_window_but_no_path")
        self.assertEqual(result["predictedPathTiles"], [])

    def test_object_destination_routes_to_final_approach_tile_when_target_is_not_walkable(self):
        result = self.scenarios()["object_destination_uses_final_approach"]

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["localReachability"], "reachable")
        self.assertEqual(result["destinationTile"], {"worldX": 102, "worldY": 100, "plane": 0})
        self.assertEqual(result["finalApproachTile"], {"worldX": 101, "worldY": 100, "plane": 0})
        self.assertNotEqual(result["destinationTile"], result["finalApproachTile"])

    def test_outside_collision_window_returns_global_pathfinding_need(self):
        result = self.scenarios()["destination_outside_collision_window"]

        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["localReachability"], "unknown")
        self.assertEqual(result["reason"], "destination_outside_collision_window")
        self.assertIn("navigation.global_pathfinding", result["missingCapabilities"])

    def test_plane_mismatch_returns_unknown_with_plane_reason(self):
        result = self.scenarios()["destination_plane_mismatch"]

        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["localReachability"], "unknown")
        self.assertEqual(result["reason"], "destination_plane_mismatch")

    def test_blocked_path_returns_blocked(self):
        result = self.scenarios()["path_blocked"]

        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["localReachability"], "blocked")
        self.assertEqual(result["reason"], "destination_inside_window_but_no_path")

    def test_path_cap_sets_path_was_capped(self):
        result = self.scenarios()["path_cap_applied"]

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["pathWasCapped"])
        self.assertEqual(result["predictedPathDisplayedCount"], 4)
        self.assertGreater(result["predictedPathCount"], result["predictedPathDisplayedCount"])

    def test_json_cli_prints_matrix_to_stdout(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = matrix.main(["--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schema"], matrix.SCHEMA)
        self.assertEqual(len(payload["scenarios"]), 8)


if __name__ == "__main__":
    unittest.main()

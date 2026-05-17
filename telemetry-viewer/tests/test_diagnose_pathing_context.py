import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_pathing_context


class DiagnosePathingContextTest(unittest.TestCase):
    def test_prints_collision_window_details_from_pathing_context(self):
        payload = diagnose_pathing_context.build_from_daemon(
            {
                "brain": {
                    "noActionEmitted": True,
                    "pathingContext": {
                        "pathingNeeded": True,
                        "destination": {
                            "targetName": "Bank booth",
                            "targetType": "sceneObject",
                            "classId": "bank_booth",
                        },
                        "destinationTile": {"worldX": 3207, "worldY": 3215, "plane": 2},
                        "finalApproachTile": {"worldX": 3206, "worldY": 3215, "plane": 2},
                        "nextWaypointTile": {"worldX": 3206, "worldY": 3217, "plane": 2},
                        "predictedPathTiles": [{"worldX": 3206, "worldY": 3217, "plane": 2}],
                        "localReachability": "reachable",
                        "reason": "path_reachable",
                        "pathLengthTiles": 3,
                        "predictedMovementModel": "cardinal_only",
                        "collisionWindowAvailable": True,
                        "collisionWindowFresh": True,
                        "collisionWindowRadius": 24,
                        "collisionWindowCenterWorld": {"worldX": 3206, "worldY": 3217, "plane": 2},
                        "collisionWindowPlane": 2,
                        "collisionWindowAgeTicks": 0,
                        "destinationInsideCollisionWindow": True,
                        "destinationPlaneMatches": True,
                        "collisionWindowMissingReason": None,
                        "warnings": [],
                    }
                }
            }
        )
        text = diagnose_pathing_context.format_human(payload)

        self.assertEqual(payload["destinationTargetName"], "Bank booth")
        self.assertEqual(payload["predictedPathCount"], 1)
        self.assertIn("Destination target: Bank booth (bank_booth)", text)
        self.assertIn("Reachability reason: path_reachable", text)
        self.assertIn("Predicted path count: 1", text)
        self.assertIn("Collision window available: yes", text)
        self.assertIn("Collision window fresh: yes", text)
        self.assertIn("Collision window center: 3206,3217,2", text)
        self.assertIn("Destination inside collision window: yes", text)
        self.assertIn("Destination plane matches: yes", text)


if __name__ == "__main__":
    unittest.main()

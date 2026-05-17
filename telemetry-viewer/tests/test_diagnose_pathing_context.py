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
                        "predictedPathCount": 6,
                        "predictedPathDisplayedCount": 1,
                        "predictedPathAvailableCount": 6,
                        "pathDisplayWasCapped": True,
                        "overlayPredictedPathLimit": 8,
                        "pathWasCapped": True,
                        "pathCapTiles": 10,
                        "pathSegmentsValid": False,
                        "invalidPathSegmentCount": 1,
                        "firstInvalidPathSegment": {
                            "from": {"sceneX": 1, "sceneY": 1},
                            "to": {"sceneX": 2, "sceneY": 1},
                            "reason": "blocked_cardinal_step",
                        },
                        "selectedApproachReason": "reachable_direct_side_access",
                        "approachCandidatesTested": 4,
                        "approachCandidatesRejectedByBlockedSide": 1,
                        "approachCandidatesRejectedByNoLineOfSight": 1,
                        "exactDestinationReached": False,
                        "finalApproachSubstituted": True,
                        "diagonalStepCount": 1,
                        "cardinalStepCount": 5,
                        "skippedRunTiles": [],
                        "runBehavior": "unknown",
                        "localReachability": "reachable",
                        "reason": "path_reachable",
                        "pathLengthTiles": 3,
                        "predictedMovementModel": "osrs_like_predicted",
                        "collisionWindowAvailable": True,
                        "collisionWindowFresh": True,
                        "collisionWindowRadius": 24,
                        "collisionWindowCenterWorld": {"worldX": 3206, "worldY": 3217, "plane": 2},
                        "collisionWindowPlane": 2,
                        "collisionWindowAgeTicks": 0,
                        "destinationInsideCollisionWindow": True,
                        "destinationPlaneMatches": True,
                        "collisionWindowMissingReason": None,
                        "pathIntentKey": "woodcutting:inventory_full:needs_service|objectKey:bank-booth-1|3207:3215:2|sceneObject|bank_booth",
                        "destinationTargetKey": "objectKey:bank-booth-1",
                        "pathIntentRetained": True,
                        "pathStableForTicks": 4,
                        "movementState": "moving",
                        "retentionReason": "player_moving_same_destination",
                        "switchReason": None,
                        "warnings": [],
                    }
                }
            }
        )
        text = diagnose_pathing_context.format_human(payload)

        self.assertEqual(payload["destinationTargetName"], "Bank booth")
        self.assertEqual(payload["predictedPathCount"], 6)
        self.assertEqual(payload["predictedPathDisplayedCount"], 1)
        self.assertEqual(payload["predictedPathAvailableCount"], 6)
        self.assertEqual(payload["overlayPredictedPathLimit"], 8)
        self.assertTrue(payload["pathWasCapped"])
        self.assertTrue(payload["pathDisplayWasCapped"])
        self.assertFalse(payload["pathSegmentsValid"])
        self.assertEqual(payload["invalidPathSegmentCount"], 1)
        self.assertEqual(payload["diagonalSteps"], 1)
        self.assertEqual(payload["cardinalSteps"], 5)
        self.assertTrue(payload["pathRetained"])
        self.assertEqual(payload["stableForTicks"], 4)
        self.assertEqual(payload["movementState"], "moving")
        self.assertIn("Destination target: Bank booth (bank_booth)", text)
        self.assertIn("Reachability reason: path_reachable", text)
        self.assertIn("Path retained: yes", text)
        self.assertIn("Path stable for: 4", text)
        self.assertIn("Movement state: moving", text)
        self.assertIn("Retention reason: player_moving_same_destination", text)
        self.assertIn("Path cap used: 10", text)
        self.assertIn("Path was capped: yes", text)
        self.assertIn("Exact destination reached: no", text)
        self.assertIn("Final approach substituted: yes", text)
        self.assertIn("Diagonal steps: 1", text)
        self.assertIn("Cardinal steps: 5", text)
        self.assertIn("Comparison hint: diagonal=1 cardinal=5 finalApproach=yes exactDestination=no capped=yes", text)
        self.assertIn("Run behavior: unknown", text)
        self.assertIn("Predicted path count: 6", text)
        self.assertIn("Predicted path displayed: 1", text)
        self.assertIn("Predicted path available: 6", text)
        self.assertIn("Overlay predicted path limit: 8", text)
        self.assertIn("Path display capped: yes", text)
        self.assertIn("Path segments valid: no", text)
        self.assertIn("Invalid path segments: 1", text)
        self.assertIn("First invalid segment: blocked_cardinal_step", text)
        self.assertIn("Selected approach reason: reachable_direct_side_access", text)
        self.assertIn("Approach candidates tested: 4", text)
        self.assertIn("Rejected by blocked side: 1", text)
        self.assertIn("Rejected by no line-of-sight: 1", text)
        self.assertIn("Collision window available: yes", text)
        self.assertIn("Collision window fresh: yes", text)
        self.assertIn("Collision window center: 3206,3217,2", text)
        self.assertIn("Destination inside collision window: yes", text)
        self.assertIn("Destination plane matches: yes", text)


if __name__ == "__main__":
    unittest.main()

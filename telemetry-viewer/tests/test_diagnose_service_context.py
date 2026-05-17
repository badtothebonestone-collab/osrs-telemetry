import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_service_context


class DiagnoseServiceContextTest(unittest.TestCase):
    def test_builds_daemon_summary_from_status(self):
        payload = diagnose_service_context.build_from_daemon(
            {
                "activeProfile": "woodcutting",
                "brainTaskPolicy": "woodcutting_bank",
                "profileCandidateCount": 1,
                "broadCandidateCount": 2,
                "loadedServiceSceneCount": 1,
                "serviceCandidateInputCount": 1,
                "serviceCandidateVisibility": "available",
                "serviceCandidateSourceLanes": {
                    "profileCandidates": 1,
                    "broadCandidates": 2,
                    "loadedServiceScene": 1,
                    "serviceCandidateInputs": 1,
                    "retainedServiceCandidates": 1,
                },
                "pluginSnapshotTier": "hot",
                "pluginSnapshotMaxProjectionRefs": 32,
                "pluginSnapshotProjectionRefs": 31,
                "pluginSnapshotServiceHintsUsed": ["bank_booth", "banker"],
                "brain": {
                    "noActionEmitted": True,
                    "serviceContext": {
                        "serviceNeeded": True,
                        "serviceTypeNeeded": "bank_full",
                        "candidateCount": 1,
                        "candidateCountsByType": {"bank_booth": 1},
                        "candidateCountsByServiceGroup": {"full_bank": 1},
                        "visiblePrimaryServiceTargetCount": 1,
                        "visibleDepositServiceTargetCount": 0,
                        "sourceStageCounts": {
                            "bank_booth": {
                                "rawProjection": None,
                                "profileCandidates": 0,
                                "broadCandidates": 1,
                                "loadedServiceScene": 1,
                                "serviceCandidateInputs": 1,
                                "serviceCandidates": 1,
                                "retainedMemory": 1,
                                "lastSeenTick": 14,
                                "ageTicks": 1,
                                "missingReason": None,
                            },
                            "deposit_box": {
                                "rawProjection": None,
                                "profileCandidates": 0,
                                "broadCandidates": 1,
                                "loadedServiceScene": 0,
                                "serviceCandidateInputs": 1,
                                "serviceCandidates": 1,
                                "retainedMemory": 0,
                                "missingReason": None,
                            },
                        },
                        "memoryLifecycle": {
                            "serviceMemoryGraceTicks": 10,
                            "primaryBankGraceTicks": 50,
                            "depositGraceTicks": 10,
                            "memoryMaxDistanceTiles": None,
                            "memoryPlanePolicy": "same_plane",
                            "memorySize": 1,
                            "memoryEvictionReasons": [],
                            "retainedCandidates": [
                                {
                                    "targetName": "Bank booth",
                                    "serviceGroup": "full_bank",
                                    "worldX": 3207,
                                    "worldY": 3215,
                                    "plane": 2,
                                    "ageTicks": 1,
                                    "missingTicks": 1,
                                    "lastSeenSourceLane": "serviceCandidateInputs",
                                }
                            ],
                        },
                        "serviceCandidates": [
                            {
                                "targetType": "sceneObject",
                                "classId": "deposit_box",
                                "targetName": "Bank Deposit Box",
                                "worldX": 3210,
                                "worldY": 3217,
                                "plane": 2,
                                "serviceGroup": "deposit_only",
                                "policyEligible": False,
                                "ineligibleReason": "deposit_fallback_blocked_by_retained_primary",
                                "selectedServiceTargetSource": "fallback_deposit",
                            },
                            {
                                "targetType": "sceneObject",
                                "classId": "bank_booth",
                                "targetName": "Bank booth",
                                "worldX": 3207,
                                "worldY": 3215,
                                "plane": 2,
                                "serviceGroup": "full_bank",
                                "serviceCandidatePolicyGroupRank": 0,
                                "depositFallbackEligible": False,
                                "policyEligible": True,
                                "selectedServiceTargetSource": "retained_primary",
                            }
                        ],
                        "bestServiceCandidate": {
                            "targetType": "sceneObject",
                            "classId": "bank_booth",
                            "targetName": "Bank booth",
                            "worldX": 3207,
                            "worldY": 3215,
                            "plane": 2,
                            "distanceTiles": 3,
                            "serviceScore": 987.0,
                            "serviceTypePriority": 0,
                            "serviceReachabilityContribution": 0,
                            "serviceDistanceContribution": 3.0,
                            "servicePathingContribution": 3.0,
                            "serviceApproachQualityContribution": 0,
                            "serviceRetentionContribution": -20,
                            "serviceGroup": "full_bank",
                            "policyEligible": True,
                            "selectedServiceTargetSource": "retained_primary",
                            "serviceSelectedReason": "selected by type priority bank_booth",
                            "interactionRadiusTiles": 2,
                            "clickbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                        },
                        "serviceTargetRetained": True,
                        "retainedServiceTargetName": "Bank booth",
                        "retainedServiceMissingTicks": 1,
                        "retainedServiceCandidateCount": 1,
                        "retainedBestServiceCandidate": {
                            "targetType": "sceneObject",
                            "classId": "bank_booth",
                            "targetName": "Bank booth",
                            "worldX": 3207,
                            "worldY": 3215,
                            "plane": 2,
                            "retainedServiceAgeTicks": 1,
                        },
                        "retainedServiceAgeTicks": 1,
                        "preferredServiceTypesSeen": ["bank_booth"],
                        "preferredServiceTypesRecentlySeen": ["bank_booth"],
                        "missingPreferredReason": None,
                        "selectedServiceTargetSource": "retained_primary",
                        "primaryServiceVisible": True,
                        "primaryServiceRetained": True,
                        "depositFallbackAllowed": False,
                        "selectedServiceGroup": "full_bank",
                        "logicError": False,
                        "serviceSwitchReason": "retained_preferred_service_target_transient_missing",
                        "serviceCandidateDroppedReason": "preferred_service_missing_from_current_candidates",
                        "serviceReady": True,
                        "serviceReadyReason": "arrived_at_service",
                        "serviceReadyStableForTicks": 2,
                        "selectedServiceTargetName": "Bank booth",
                        "selectedServiceTargetTile": {"worldX": 3207, "worldY": 3215, "plane": 2},
                        "distanceToServiceTarget": 1,
                        "arrivedAtFinalApproach": True,
                        "arrivedNearDestination": True,
                        "distanceToFinalApproach": 0,
                    },
                },
            }
        )

        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["serviceCandidateInputCount"], 1)
        self.assertEqual(payload["bestServiceCandidate"]["targetName"], "Bank booth")
        self.assertEqual(payload["bestServiceCandidate"]["interactionRadiusTiles"], 2)
        self.assertEqual(payload["bestServiceCandidate"]["clickbox"]["w"], 3)
        self.assertEqual(payload["bestServiceCandidate"]["serviceTypePriority"], 0)
        self.assertEqual(payload["selectedReason"], "selected by type priority bank_booth")
        self.assertEqual(payload["candidatesByType"], {"bank_booth": 1})
        self.assertEqual(payload["serviceCandidateVisibility"], "available")
        self.assertTrue(payload["serviceTargetRetained"])
        self.assertEqual(payload["retainedServiceMissingTicks"], 1)
        self.assertEqual(payload["retainedServiceCandidateCount"], 1)
        self.assertEqual(payload["retainedBestServiceCandidate"]["targetName"], "Bank booth")
        self.assertEqual(payload["retainedServiceAgeTicks"], 1)
        self.assertEqual(payload["preferredServiceTypesRecentlySeen"], ["bank_booth"])
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")
        self.assertTrue(payload["primaryServiceVisible"])
        self.assertTrue(payload["primaryServiceRetained"])
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertEqual(payload["selectedServiceGroup"], "full_bank")
        self.assertEqual(payload["candidateCountsByServiceGroup"], {"full_bank": 1})
        self.assertEqual(payload["sourceStageCounts"]["bank_booth"]["retainedMemory"], 1)
        self.assertEqual(payload["memoryLifecycle"]["primaryBankGraceTicks"], 50)
        self.assertEqual(payload["memoryLifecycle"]["retainedCandidates"][0]["lastSeenSourceLane"], "serviceCandidateInputs")
        self.assertEqual(payload["visiblePrimaryServiceTargetCount"], 1)
        self.assertEqual(payload["visibleDepositServiceTargetCount"], 0)
        self.assertEqual(payload["serviceCandidates"][0]["serviceGroup"], "deposit_only")
        self.assertFalse(payload["serviceCandidates"][0]["policyEligible"])
        self.assertEqual(payload["serviceCandidates"][0]["ineligibleReason"], "deposit_fallback_blocked_by_retained_primary")
        self.assertFalse(payload["logicError"])
        self.assertEqual(payload["serviceCandidateSourceLanes"]["retainedServiceCandidates"], 1)
        self.assertEqual(payload["serviceCandidateSourceLanes"]["loadedServiceScene"], 1)
        self.assertEqual(payload["snapshotTier"], "hot")
        self.assertEqual(payload["projectionRefsRequested"], 32)
        self.assertEqual(payload["projectionRefsEffective"], 31)
        self.assertEqual(payload["serviceHintsUsed"], ["bank_booth", "banker"])
        self.assertEqual(payload["serviceSwitchReason"], "retained_preferred_service_target_transient_missing")
        self.assertTrue(payload["serviceReady"])
        self.assertEqual(payload["serviceReadyReason"], "arrived_at_service")
        self.assertEqual(payload["selectedServiceTargetName"], "Bank booth")
        self.assertEqual(payload["distanceToServiceTarget"], 1)

        text = diagnose_service_context.format_human(payload)
        self.assertIn("Score: 987.0", text)
        self.assertIn("Type priority: 0", text)
        self.assertIn("Approach quality contribution: 0", text)
        self.assertIn("Retention contribution: -20", text)
        self.assertIn("Service target retained: yes", text)
        self.assertIn("Retained missing ticks: 1", text)
        self.assertIn("Retained service candidates: 1", text)
        self.assertIn("Preferred service types recently seen: bank_booth", text)
        self.assertIn("Visible primary targets: yes", text)
        self.assertIn("Deposit fallback allowed: no", text)
        self.assertIn("Selected service group: full_bank", text)
        self.assertIn("Service ready: yes", text)
        self.assertIn("Service ready reason: arrived_at_service", text)
        self.assertIn("Selected service target: Bank booth", text)
        self.assertIn("Distance to service target: 1", text)
        self.assertIn("Visible deposit targets: 0", text)
        self.assertIn("Memory grace: primary=50 deposit=10", text)
        self.assertIn("Source stages:", text)
        self.assertIn("Loaded service scene: 1", text)
        self.assertIn("loaded=1", text)
        self.assertIn("bank_booth", text)
        self.assertIn("Retained memory:", text)
        self.assertIn("Service candidates:", text)
        self.assertIn("eligible=no", text)
        self.assertIn("deposit_fallback_blocked_by_retained_primary", text)
        self.assertIn("group=full_bank", text)
        self.assertIn("Snapshot tier: hot", text)
        self.assertIn("Projection refs: 31/32", text)
        self.assertIn("Service hints used: bank_booth, banker", text)
        self.assertIn("Switch reason: retained_preferred_service_target_transient_missing", text)
        self.assertIn("Selected reason: selected by type priority bank_booth", text)

    def test_json_prints_stdout_only_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = diagnose_service_context.main(["--json"])
            after = set(os.listdir(tmp))

        self.assertEqual(code, 0)
        self.assertEqual(before, after)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["schema"], diagnose_service_context.SCHEMA)
        self.assertFalse(payload["daemonReachable"])


if __name__ == "__main__":
    unittest.main()

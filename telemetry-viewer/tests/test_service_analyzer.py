import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from analyzers import service_analyzer
import task_policy


class ServiceAnalyzerTest(unittest.TestCase):
    def test_reports_bank_service_only_when_policy_requires_service(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {"targetType": "npc", "classId": "banker", "name": "Banker", "id": 2897},
                {"targetType": "sceneObject", "classId": "tree", "name": "Tree", "id": 1278},
            ],
            source_tick=12,
        )

        self.assertTrue(context.service_required)
        self.assertEqual(context.service_type_needed, "bank_full")
        self.assertEqual(context.best_service_candidate["classId"], "banker")
        self.assertEqual(context.source_tick, 12)
        self.assertTrue(context.to_dict()["serviceNeeded"])
        self.assertEqual(context.to_dict()["candidateCount"], 1)

    def test_identifies_bank_booth_by_name_and_class_and_preserves_context_fields(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "id": 10355,
                    "worldX": 3208,
                    "worldY": 3219,
                    "plane": 0,
                    "distanceTiles": 4,
                    "navigation": {"directReachability": "reachable"},
                    "interactionRadiusTiles": 2,
                    "clickbox": {"x": 10, "y": 20, "w": 30, "h": 40},
                    "clickableHull": [{"x": 1, "y": 2}],
                    "actions": ["Bank"],
                    "menuActions": ["Bank"],
                }
            ],
            source_tick=44,
        )

        payload = context.to_dict()
        self.assertEqual(context.status, "PASS")
        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["candidateCount"], 1)
        self.assertEqual(payload["reachableCount"], 1)
        self.assertEqual(payload["bestServiceCandidate"]["classId"], "bank_booth")
        self.assertEqual(payload["bestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["nearestServiceCandidate"]["classId"], "bank_booth")
        self.assertEqual(payload["unknownReachabilityCount"], 0)
        self.assertEqual(payload["candidateCountsByType"], {"bank_booth": 1})
        self.assertEqual(len(payload["candidatesByType"]["bank_booth"]), 1)
        self.assertEqual(payload["bestServiceCandidate"]["interactionRadiusTiles"], 2)
        self.assertEqual(payload["bestServiceCandidate"]["clickbox"]["w"], 30)
        self.assertEqual(payload["bestServiceCandidate"]["clickableHull"], [{"x": 1, "y": 2}])

    def test_identifies_bank_booth_by_name_when_class_is_generic(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[{"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank booth", "distanceTiles": 3}],
        )

        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "bank_booth")
        self.assertEqual(context.to_dict()["candidateCountsByType"], {"bank_booth": 1})

    def test_identifies_banker_npc_by_name_and_class(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "npc",
                    "classId": "banker",
                    "name": "Banker",
                    "id": 2897,
                    "worldX": 3207,
                    "worldY": 3222,
                    "plane": 0,
                    "distanceTiles": 2,
                }
            ],
        )

        self.assertEqual(context.best_service_candidate["targetType"], "npc")
        self.assertEqual(context.best_service_candidate["classId"], "banker")
        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "banker")
        self.assertIn("service.actions", context.missing_capabilities)

    def test_identifies_deposit_boxes_by_class_and_name(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "distanceTiles": 4},
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank deposit box", "distanceTiles": 2},
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Deposit chest", "distanceTiles": 6},
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["candidateCountsByType"], {"deposit_box": 2, "deposit_chest": 1})
        self.assertEqual(payload["nearestServiceCandidate"]["serviceCandidateType"], "deposit_box")

    def test_best_nearest_and_reachability_buckets_are_reported(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "qualityScore": 30, "distanceTiles": 1},
                {"targetType": "npc", "classId": "banker", "targetName": "Banker", "qualityScore": 100, "distanceTiles": 5, "navigation": {"directReachability": "reachable"}},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "qualityScore": 80, "distanceTiles": 2, "navigation": {"directReachability": "blocked"}},
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["bestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["nearestServiceCandidate"]["serviceCandidateType"], "bank_booth")
        self.assertEqual(payload["reachableCount"], 1)
        self.assertEqual(payload["unknownReachabilityCount"], 1)

    def test_bank_booth_outranks_generic_bank_table(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_related",
                    "targetName": "Bank table",
                    "qualityScore": 100,
                    "distanceTiles": 1,
                    "navigation": {"directReachability": "reachable"},
                },
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "qualityScore": 10,
                    "distanceTiles": 6,
                    "navigation": {"directReachability": "unknown"},
                },
            ],
        )

        best = context.to_dict()["bestServiceCandidate"]
        self.assertEqual(best["serviceCandidateType"], "bank_booth")
        self.assertLess(best["serviceTypePriority"], context.service_candidates[0]["serviceTypePriority"])
        self.assertIn("type priority", best["serviceSelectedReason"])
        self.assertIn("serviceScore", best)
        self.assertIn("serviceReachabilityContribution", best)
        self.assertIn("serviceDistanceContribution", best)
        self.assertIn("servicePathingContribution", best)

    def test_woodcut_bank_prefers_full_bank_target_over_closer_deposit_box(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "targetName": "Bank Deposit Box",
                    "objectKey": "deposit-1",
                    "distanceTiles": 1,
                    "pathLengthTiles": 1,
                    "approachQuality": "direct_side_access",
                    "navigation": {"directReachability": "reachable"},
                },
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "objectKey": "booth-1",
                    "distanceTiles": 9,
                    "pathLengthTiles": 9,
                    "approachQuality": "side_access_unknown",
                    "navigation": {"directReachability": "unknown"},
                },
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["serviceTypeNeeded"], "bank_full")
        self.assertEqual(payload["bestServiceCandidate"]["objectKey"], "booth-1")
        self.assertEqual(payload["bestServiceCandidate"]["serviceGroup"], "full_bank")
        self.assertTrue(payload["primaryServiceVisible"])
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertEqual(payload["selectedServiceGroup"], "full_bank")
        self.assertIn("full bank target required", payload["bestServiceCandidate"]["serviceSelectedReason"])

    def test_deposit_policy_prefers_deposit_box_over_full_bank_target(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_deposit",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "objectKey": "booth-1",
                    "distanceTiles": 1,
                    "pathLengthTiles": 1,
                },
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "targetName": "Bank Deposit Box",
                    "objectKey": "deposit-1",
                    "distanceTiles": 5,
                    "pathLengthTiles": 5,
                },
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["serviceTypeNeeded"], "bank_deposit")
        self.assertEqual(payload["bestServiceCandidate"]["objectKey"], "deposit-1")
        self.assertEqual(payload["selectedServiceGroup"], "deposit_only")
        self.assertTrue(payload["depositFallbackAllowed"])

    def test_deposit_box_outranks_generic_bank_related_fallback(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_related", "targetName": "Bank table", "qualityScore": 100, "distanceTiles": 1},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "qualityScore": 1, "distanceTiles": 8},
            ],
        )

        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "deposit_box")

    def test_bank_booth_is_retained_when_hot_candidates_temporarily_drop_it(self):
        state = service_analyzer.ServiceTargetState()
        first = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "booth-1", "worldX": 3208, "worldY": 3221, "plane": 2, "distanceTiles": 5},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2, "distanceTiles": 2},
            ],
            source_tick=10,
            service_target_state=state,
            current_plane=2,
        )
        second = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2, "distanceTiles": 2},
            ],
            source_tick=11,
            service_target_state=state,
            current_plane=2,
        )
        payload = second.to_dict()

        self.assertEqual(first.best_service_candidate["serviceCandidateType"], "bank_booth")
        self.assertEqual(second.best_service_candidate["objectKey"], "booth-1")
        self.assertTrue(payload["serviceTargetRetained"])
        self.assertEqual(payload["retainedServiceTargetName"], "Bank booth")
        self.assertEqual(payload["retainedServiceMissingTicks"], 1)
        self.assertEqual(payload["serviceSwitchReason"], "retained_primary_blocks_deposit_fallback")
        self.assertEqual(payload["serviceCandidateDroppedReason"], "preferred_service_missing_from_current_candidates")
        self.assertEqual(payload["retainedServiceCandidateCount"], 1)
        self.assertEqual(payload["retainedBestServiceCandidate"]["objectKey"], "booth-1")
        self.assertEqual(payload["retainedServiceAgeTicks"], 1)
        self.assertEqual(payload["preferredServiceTypesSeen"], [])
        self.assertEqual(payload["preferredServiceTypesRecentlySeen"], ["bank_booth"])
        self.assertEqual(payload["missingPreferredReason"], "preferred_service_missing_from_current_candidates")
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")
        self.assertTrue(payload["primaryServiceRetained"])
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertEqual(payload["selectedServiceGroup"], "full_bank")

    def test_bank_full_retained_primary_blocks_active_visible_deposit_box(self):
        state = service_analyzer.ServiceTargetState(
            active_service_target_key="objectKey:deposit-1",
            active_policy_name="woodcutting_bank",
            active_service_type="bank_full",
            retained_candidate={
                "targetType": "sceneObject",
                "classId": "deposit_box",
                "targetName": "Bank Deposit Box",
                "objectKey": "deposit-1",
                "worldX": 3210,
                "worldY": 3217,
                "plane": 2,
                "serviceCandidateType": "deposit_box",
                "serviceGroup": "deposit_only",
            },
            retained_candidate_type="deposit_box",
            last_seen_tick=14,
        )
        state.recent_service_candidates["objectKey:booth-1"] = {
            "targetType": "sceneObject",
            "classId": "bank_booth",
            "targetName": "Bank booth",
            "objectKey": "booth-1",
            "worldX": 3208,
            "worldY": 3221,
            "plane": 2,
            "serviceCandidateType": "bank_booth",
            "serviceGroup": "full_bank",
            "lastSeenTick": 10,
        }

        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "targetName": "Bank Deposit Box",
                    "objectKey": "deposit-1",
                    "worldX": 3210,
                    "worldY": 3217,
                    "plane": 2,
                    "distanceTiles": 1,
                    "pathLengthTiles": 1,
                },
            ],
            source_tick=15,
            service_target_state=state,
            current_plane=2,
        )
        payload = context.to_dict()
        deposit = next(candidate for candidate in payload["serviceCandidates"] if candidate.get("objectKey") == "deposit-1")

        self.assertEqual(payload["bestServiceCandidate"]["objectKey"], "booth-1")
        self.assertEqual(payload["selectedServiceGroup"], "full_bank")
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")
        self.assertTrue(payload["primaryServiceRetained"])
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertFalse(payload["logicError"])
        self.assertFalse(deposit["policyEligible"])
        self.assertEqual(deposit["ineligibleReason"], "deposit_fallback_blocked_by_retained_primary")

    def test_bank_booth_seen_in_broad_candidates_enters_memory_without_being_selected_first(self):
        state = service_analyzer.ServiceTargetState()

        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "targetName": "Bank Deposit Box",
                    "objectKey": "deposit-1",
                    "worldX": 3210,
                    "worldY": 3217,
                    "plane": 2,
                    "distanceTiles": 2,
                },
            ],
            memory_candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "objectKey": "booth-broad",
                    "worldX": 3208,
                    "worldY": 3221,
                    "plane": 2,
                    "distanceTiles": 7,
                    "_serviceSourceLane": "broadCandidates",
                }
            ],
            source_tick=100,
            service_target_state=state,
            current_plane=2,
        )
        payload = context.to_dict()

        self.assertEqual(payload["bestServiceCandidate"]["objectKey"], "booth-broad")
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")
        self.assertTrue(payload["primaryServiceRetained"])
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertEqual(payload["retainedServiceCandidateCount"], 1)
        self.assertEqual(payload["retainedBestServiceCandidate"]["lastSeenSourceLane"], "broadCandidates")
        self.assertEqual(payload["sourceStageCounts"]["bank_booth"]["broadCandidates"], 1)
        self.assertEqual(payload["memoryLifecycle"]["memorySize"], 2)

    def test_loaded_service_scene_bank_booth_enters_memory_without_projection_candidate(self):
        state = service_analyzer.ServiceTargetState()

        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "targetName": "Bank Deposit Box",
                    "objectKey": "deposit-current",
                    "worldX": 3210,
                    "worldY": 3217,
                    "plane": 2,
                    "_serviceSourceLane": "serviceCandidateInputs",
                }
            ],
            memory_candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "objectKey": "booth-loaded",
                    "worldX": 3208,
                    "worldY": 3221,
                    "plane": 2,
                    "_serviceSourceLane": "loadedServiceScene",
                }
            ],
            loaded_service_scene=[
                {
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "objectKey": "booth-loaded",
                    "worldX": 3208,
                    "worldY": 3221,
                    "plane": 2,
                }
            ],
            source_tick=100,
            service_target_state=state,
            current_plane=2,
        )
        payload = context.to_dict()

        self.assertEqual(payload["bestServiceCandidate"]["objectKey"], "booth-loaded")
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")
        self.assertFalse(payload["depositFallbackAllowed"])
        self.assertEqual(payload["sourceStageCounts"]["bank_booth"]["loadedServiceScene"], 1)
        self.assertEqual(payload["retainedBestServiceCandidate"]["lastSeenSourceLane"], "loadedServiceScene")

    def test_primary_memory_survives_longer_than_deposit_memory(self):
        state = service_analyzer.ServiceTargetState()
        service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "booth-1", "worldX": 3208, "worldY": 3221, "plane": 2},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2},
            ],
            source_tick=10,
            service_target_state=state,
            current_plane=2,
        )

        retained_at_59 = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-2", "worldX": 3211, "worldY": 3217, "plane": 2},
            ],
            source_tick=59,
            service_target_state=state,
            current_plane=2,
        )
        expired_at_61 = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-2", "worldX": 3211, "worldY": 3217, "plane": 2},
            ],
            source_tick=61,
            service_target_state=state,
            current_plane=2,
        )

        self.assertEqual(retained_at_59.best_service_candidate["objectKey"], "booth-1")
        self.assertEqual(retained_at_59.to_dict()["retainedServiceAgeTicks"], 49)
        self.assertEqual(expired_at_61.best_service_candidate["objectKey"], "deposit-2")
        self.assertEqual(expired_at_61.to_dict()["memoryLifecycle"]["memoryEvictionReasons"][0]["reason"], "stale_beyond_grace")

    def test_default_service_memory_survives_multiple_hot_omissions(self):
        state = service_analyzer.ServiceTargetState()
        service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "booth-1", "worldX": 3208, "worldY": 3221, "plane": 2, "distanceTiles": 5},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2, "distanceTiles": 2},
            ],
            source_tick=10,
            service_target_state=state,
            current_plane=2,
        )

        latest = None
        for tick in range(11, 17):
            latest = service_analyzer.analyze_service_context(
                "woodcutting_bank",
                candidates=[
                    {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2, "distanceTiles": 2},
                ],
                source_tick=tick,
                service_target_state=state,
                current_plane=2,
            )

        payload = latest.to_dict()
        self.assertEqual(latest.best_service_candidate["objectKey"], "booth-1")
        self.assertTrue(payload["serviceTargetRetained"])
        self.assertEqual(payload["retainedServiceMissingTicks"], 6)
        self.assertEqual(payload["retainedServiceAgeTicks"], 6)
        self.assertEqual(payload["selectedServiceTargetSource"], "retained_primary")

    def test_deposit_box_selected_after_preferred_service_retention_expires(self):
        state = service_analyzer.ServiceTargetState()
        state.primary_bank_grace_ticks = 1
        service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "booth-1", "worldX": 3208, "worldY": 3221, "plane": 2},
            ],
            source_tick=10,
            service_target_state=state,
            current_plane=2,
        )

        service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2},
            ],
            source_tick=11,
            service_target_state=state,
            current_plane=2,
        )
        second_missing = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "worldX": 3210, "worldY": 3217, "plane": 2},
            ],
            source_tick=12,
            service_target_state=state,
            current_plane=2,
        )

        self.assertFalse(second_missing.service_target_retained)
        self.assertEqual(second_missing.best_service_candidate["serviceCandidateType"], "deposit_box")
        self.assertEqual(second_missing.service_switch_reason, "retained_service_missing_grace_expired")

    def test_direct_approach_quality_beats_unknown_side_access_within_same_type(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "objectKey": "unknown", "distanceTiles": 1, "approachQuality": "side_access_unknown"},
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Deposit box", "objectKey": "direct", "distanceTiles": 5, "approachQuality": "direct_side_access"},
            ],
        )

        best = context.to_dict()["bestServiceCandidate"]
        self.assertEqual(best["objectKey"], "direct")
        self.assertEqual(best["serviceApproachQualityContribution"], 0)
        self.assertIn("approach quality", best["serviceSelectedReason"])

    def test_side_access_unknown_deposit_box_is_tentative(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "deposit_box", "targetName": "Bank Deposit Box", "objectKey": "deposit-1", "approachQuality": "side_access_unknown"},
            ],
        )
        payload = context.to_dict()

        self.assertEqual(payload["bestServiceCandidate"]["serviceCandidateType"], "deposit_box")
        self.assertTrue(payload["bestServiceCandidate"]["serviceSelectionTentative"])
        self.assertEqual(payload["bestServiceCandidate"]["serviceApproachQualityContribution"], 2)
        self.assertIn("tentative", payload["bestServiceCandidate"]["serviceSelectedReason"])

    def test_distance_breaks_ties_within_same_service_priority(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "far", "distanceTiles": 8},
                {"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "objectKey": "near", "distanceTiles": 2},
            ],
        )

        self.assertEqual(context.best_service_candidate["objectKey"], "near")
        self.assertEqual(context.best_service_candidate["serviceDistanceContribution"], 2.0)

    def test_does_not_request_service_for_process_or_continue_policies(self):
        for policy_name in ("woodcutting_firemake", "woodcutting_drop", "combat_default", "observe_only"):
            with self.subTest(policy_name=policy_name):
                context = service_analyzer.analyze_service_context(task_policy.resolve_task_policy(policy_name), candidates=[])
                self.assertFalse(context.service_required)
                self.assertIsNone(context.service_type_needed)
                self.assertEqual(context.missing_capabilities, [])
                self.assertEqual(context.warnings, [])

    def test_no_service_candidate_is_clean_warning(self):
        context = service_analyzer.analyze_service_context(
            "woodcutting_bank",
            candidates=[{"targetType": "sceneObject", "classId": "tree", "targetName": "Tree"}],
        )
        payload = context.to_dict()

        self.assertEqual(context.status, "WARN")
        self.assertTrue(payload["serviceNeeded"])
        self.assertEqual(payload["candidateCount"], 0)
        self.assertEqual(payload["candidatesByType"], {})
        self.assertIn("bank_service candidate", " ".join(context.warnings))

    def test_service_context_preserves_read_only_interaction_metadata(self):
        context = service_analyzer.analyze_service_context(
            task_policy.resolve_task_policy("woodcutting_bank"),
            candidates=[
                {
                    "targetType": "sceneObject",
                    "classId": "deposit_box",
                    "name": "Deposit box",
                    "interactionRadiusTiles": 1,
                    "approachRadiusTiles": 2,
                    "clickboxPolygon": [{"x": 1, "y": 1}],
                }
            ],
        )

        candidate = context.to_dict()["bestServiceCandidate"]
        self.assertEqual(candidate["interactionRadiusTiles"], 1)
        self.assertEqual(candidate["approachRadiusTiles"], 2)
        self.assertEqual(candidate["clickboxPolygon"], [{"x": 1, "y": 1}])

    def test_service_ranking_writes_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            context = service_analyzer.analyze_service_context(
                "woodcutting_bank",
                candidates=[{"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth"}],
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)
        self.assertEqual(context.best_service_candidate["serviceCandidateType"], "bank_booth")


if __name__ == "__main__":
    unittest.main()

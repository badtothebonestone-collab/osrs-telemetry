import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import intent_stabilizer
from analyzers import intent_overlay_analyzer as overlay


def candidate(key: str, *, hull: bool = False, offset: int = 0) -> dict:
    value = {
        "objectKey": key,
        "targetName": "Tree",
        "targetType": "sceneObject",
        "classId": "tree",
        "id": 1278,
        "hash": hash(key) & 0xFFFF,
        "worldX": 3200 + offset,
        "worldY": 3200,
        "plane": 0,
        "sceneX": 10 + offset,
        "sceneY": 10,
        "qualityScore": 100,
        "distanceTiles": 2,
        "targetLiveState": "live_assumed",
        "navigation": {"directReachability": "reachable"},
        "aimPoint": {"canvasX": 100, "canvasY": 110},
    }
    if hull:
        value["clickableHull"] = [[1, 1], [2, 1], [2, 2]]
    return value


class IntentOverlayAnalyzerTest(unittest.TestCase):
    def test_selected_marker_and_backups_exclude_selected(self):
        selected = candidate("selected")
        backup = candidate("backup", offset=2)
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [selected, backup],
            {"activeTask": "woodcutting", "activeIntent": "target_available", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": selected},
        )
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 1}, "candidates": [selected, backup]},
            {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        markers = result["markers"]
        self.assertEqual(markers[0]["markerType"], "selected_target")
        self.assertEqual(markers[0]["role"], "selected")
        self.assertTrue(markers[0]["label"].startswith("Target:"))
        backups = [marker for marker in markers if marker["markerType"] == "backup_candidate"]
        self.assertEqual([marker["objectKey"] for marker in backups], ["backup"])
        self.assertTrue(all(marker["role"] == "backup" for marker in backups))

    def test_duplicate_backup_geometry_is_merged_into_selected(self):
        selected = candidate("selected", hull=False)
        richer_duplicate = candidate("selected", hull=True)
        backup = candidate("backup", hull=True, offset=2)
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [richer_duplicate, backup],
            {"activeTask": "woodcutting", "activeIntent": "target_available", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": selected},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 1}, "candidates": [richer_duplicate, backup]},
            {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        selected_marker = result["markers"][0]
        backups = [marker for marker in result["markers"] if marker["markerType"] == "backup_candidate"]
        self.assertIn("clickableHull", selected_marker)
        self.assertTrue(selected_marker["clickableHullAvailable"])
        self.assertEqual([marker["objectKey"] for marker in backups], ["backup"])

    def test_generic_future_marker_types_keep_read_only_fields(self):
        marker = overlay.intent_marker_from_candidate(
            {
                "objectKey": "banker-1",
                "name": "Banker",
                "targetType": "npc",
                "classId": "banker",
                "id": 1,
                "worldX": 3200,
                "worldY": 3201,
                "plane": 0,
                "sceneX": 12,
                "sceneY": 13,
                "aimPoint": {"canvasX": 10, "canvasY": 20},
                "clickbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                "interactionRadiusTiles": 1,
            },
            "selected_target",
            "Target: Banker",
            "test",
        )

        self.assertEqual(marker["targetType"], "npc")
        self.assertEqual(marker["markerType"], "selected_target")
        self.assertEqual(marker["clickbox"], {"x": 1, "y": 2, "w": 3, "h": 4})
        self.assertEqual(marker["interactionRadiusTiles"], 1)

    def test_overlay_uses_generic_active_intent_when_present(self):
        selected = candidate("selected")
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [selected],
            {"activeTask": "woodcutting", "activeIntent": "target_selected", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": selected},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 1}, "candidates": [selected]},
            {
                "task": "woodcutting",
                "phase": "target_available",
                "genericTaskState": {"phase": "target_selected", "activeIntent": "target_selected"},
                "confidence": 0.8,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        self.assertEqual(result["activeIntent"], "target_selected")
        self.assertEqual(result["markers"][0]["markerType"], "selected_target")

    def test_inventory_full_generic_intent_does_not_draw_selected_tree(self):
        selected = candidate("selected")
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [selected],
            {"activeTask": "woodcutting", "activeIntent": "target_selected", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": selected},
        )
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [],
            {"activeTask": "woodcutting", "activeIntent": "needs_service", "profile": "woodcutting", "latestTick": 2, "rawBestTarget": {}, "intentPriority": 70},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 2}, "candidates": [selected]},
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "confidence": 0.95,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        self.assertEqual(result["activeIntent"], "needs_service")
        self.assertFalse([marker for marker in result["markers"] if marker["markerType"] == "selected_target"])

    def test_wait_for_world_view_does_not_retain_bank_marker(self):
        bank_target = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_related",
            "id": 10355,
            "worldX": 3208,
            "worldY": 3219,
            "plane": 0,
            "sceneX": 20,
            "sceneY": 21,
            "qualityScore": 95,
            "distanceTiles": 1,
            "navigation": {"directReachability": "reachable"},
        }
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [bank_target],
            {"activeTask": "woodcutting", "activeIntent": "select_target", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": bank_target},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 2}, "candidates": [bank_target]},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "waiting_for_world_view", "activeIntent": "wait_for_world_view"},
                "postBankReacquisitionContext": {
                    "postBankReacquisitionNeeded": True,
                    "bankUiStillOpen": True,
                    "reason": "bank_ui_still_open",
                },
                "pathingContext": {
                    "pathCompletionReason": "arrived_at_service",
                    "predictedPathTiles": [{"worldX": 3208, "worldY": 3219, "plane": 0}],
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        self.assertEqual(result["activeIntent"], "wait_for_world_view")
        self.assertFalse([marker for marker in result["markers"] if marker["markerType"] == "selected_target"])
        self.assertFalse([marker for marker in result["markers"] if marker["markerType"] == "predicted_path_tile"])

    def test_bank_policy_overlay_selects_service_target_when_available(self):
        tree = candidate("selected")
        service_target = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_booth",
            "id": 10355,
            "worldX": 3208,
            "worldY": 3219,
            "plane": 0,
            "sceneX": 20,
            "sceneY": 21,
            "qualityScore": 95,
            "distanceTiles": 4,
            "navigation": {"directReachability": "reachable"},
            "aimPoint": {"canvasX": 220, "canvasY": 230},
        }

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 3}, "candidates": [tree, service_target]},
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "genericTaskState": {
                    "phase": "inventory_full",
                    "activeIntent": "needs_service",
                    "activeIntentTarget": service_target,
                    "serviceTypeNeeded": "bank",
                },
                "serviceContext": {"serviceNeeded": True, "bestServiceCandidate": service_target},
                "navigationIntentContext": {
                    "navigationNeeded": True,
                    "navigationReason": "service_target_available",
                    "targetKind": "service",
                    "directReachability": "reachable",
                },
                "confidence": 0.95,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            None,
        )

        selected = [marker for marker in result["markers"] if marker["markerType"] == "selected_target"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["classId"], "bank_booth")
        self.assertEqual(selected[0]["label"], "Service: Bank booth")
        self.assertTrue(selected[0]["navigationNeeded"])
        self.assertEqual(selected[0]["navigationStatus"], "reachable")
        self.assertFalse([marker for marker in result["markers"] if marker.get("classId") == "tree" and marker.get("markerType") == "selected_target"])

    def test_bank_policy_overlay_uses_alternate_service_backups(self):
        tree = candidate("selected")
        booth = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_booth",
            "serviceCandidateType": "bank_booth",
            "id": 10355,
            "worldX": 3208,
            "worldY": 3219,
            "plane": 0,
            "sceneX": 20,
            "sceneY": 21,
            "qualityScore": 100,
            "distanceTiles": 4,
            "navigation": {"directReachability": "reachable"},
        }
        banker = {
            "objectKey": "banker-1",
            "targetName": "Banker",
            "targetType": "npc",
            "classId": "banker",
            "serviceCandidateType": "banker",
            "id": 2897,
            "worldX": 3210,
            "worldY": 3220,
            "plane": 0,
            "sceneX": 22,
            "sceneY": 22,
            "qualityScore": 90,
            "distanceTiles": 5,
        }

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 3}, "candidates": [tree, booth, banker]},
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "genericTaskState": {
                    "phase": "inventory_full",
                    "activeIntent": "needs_service",
                    "activeIntentTarget": booth,
                    "serviceTypeNeeded": "bank",
                },
                "serviceContext": {"serviceNeeded": True, "bestServiceCandidate": booth, "serviceCandidates": [booth, banker]},
                "confidence": 0.95,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            None,
        )

        selected = [marker for marker in result["markers"] if marker["markerType"] == "selected_target"]
        backups = [marker for marker in result["markers"] if marker["markerType"] == "backup_candidate"]
        self.assertEqual(selected[0]["classId"], "bank_booth")
        self.assertEqual([marker["classId"] for marker in backups], ["banker"])
        self.assertFalse([marker for marker in backups if marker.get("classId") == "tree"])

    def test_bank_policy_without_service_candidate_emits_compact_warning(self):
        tree = candidate("selected")

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 3}, "candidates": [tree]},
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service", "serviceTypeNeeded": "bank"},
                "serviceContext": {"serviceNeeded": True, "candidateCount": 0},
                "confidence": 0.95,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            None,
        )

        self.assertFalse([marker for marker in result["markers"] if marker["markerType"] == "selected_target"])
        self.assertTrue(any(marker["markerType"] == "warning" and marker["label"] == "Inventory full: bank target not observed" for marker in result["markers"]))

    def test_process_inventory_intent_does_not_draw_selected_tree(self):
        selected = candidate("selected")
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [selected],
            {"activeTask": "woodcutting", "activeIntent": "target_selected", "profile": "woodcutting", "latestTick": 1, "rawBestTarget": selected},
        )
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [],
            {"activeTask": "woodcutting", "activeIntent": "process_inventory", "profile": "woodcutting", "latestTick": 2, "rawBestTarget": {}, "intentPriority": 70},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 2}, "candidates": [selected]},
            {
                "task": "woodcutting",
                "phase": "inventory_full",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "process_inventory", "processTypeNeeded": "firemaking"},
                "confidence": 0.95,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2),
            "2026-01-01T00:00:00Z",
            stable,
        )

        self.assertEqual(result["activeIntent"], "process_inventory")
        self.assertFalse([marker for marker in result["markers"] if marker["markerType"] == "selected_target"])
        self.assertTrue(any(marker["markerType"] in {"warning", "diagnostic"} and "firemaking" in marker["label"] for marker in result["markers"]))

    def test_daily_overlay_emits_destination_waypoint_and_full_default_predicted_path(self):
        service_target = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_booth",
            "id": 10355,
            "worldX": 3208,
            "worldY": 3219,
            "plane": 0,
            "sceneX": 20,
            "sceneY": 21,
        }
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {
                    "phase": "inventory_full",
                    "activeIntent": "needs_service",
                    "activeIntentTarget": service_target,
                },
                "serviceContext": {"serviceNeeded": True, "bestServiceCandidate": service_target, "serviceCandidates": [service_target]},
                "pathingContext": {
                    "pathingNeeded": True,
                    "destinationTile": {"worldX": 3208, "worldY": 3219, "plane": 0},
                    "finalApproachTile": {"worldX": 3207, "worldY": 3219, "plane": 0},
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3201, "worldY": 3200, "plane": 0},
                        {"worldX": 3202, "worldY": 3200, "plane": 0},
                        {"worldX": 3203, "worldY": 3200, "plane": 0},
                        {"worldX": 3204, "worldY": 3200, "plane": 0},
                        {"worldX": 3205, "worldY": 3200, "plane": 0},
                        {"worldX": 3206, "worldY": 3200, "plane": 0},
                        {"worldX": 3207, "worldY": 3200, "plane": 0},
                        {"worldX": 3208, "worldY": 3200, "plane": 0},
                        {"worldX": 3209, "worldY": 3200, "plane": 0},
                    ],
                    "localReachability": "reachable",
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        marker_types = [marker["markerType"] for marker in result["markers"]]
        selected = [marker for marker in result["markers"] if marker["markerType"] == "selected_target"]
        path_markers = [marker for marker in result["markers"] if marker["markerType"] == "predicted_path_tile"]
        destination = [marker for marker in result["markers"] if marker["markerType"] == "destination_tile"]
        waypoint = [marker for marker in result["markers"] if marker["markerType"] == "waypoint"]
        final_approach = [marker for marker in result["markers"] if marker["markerType"] == "final_approach_tile"]
        self.assertEqual(selected[0]["label"], "Service: Bank booth")
        self.assertIn("destination_tile", marker_types)
        self.assertIn("waypoint", marker_types)
        self.assertIn("final_approach_tile", marker_types)
        self.assertEqual(len(path_markers), 8)
        self.assertEqual(path_markers[0]["label"], "Path 2")
        self.assertEqual(path_markers[-1]["label"], "Path 9")
        self.assertEqual(destination[0]["label"], "Destination")
        self.assertEqual(waypoint[0]["label"], "Next waypoint")
        self.assertEqual(final_approach[0]["label"], "Final approach")
        self.assertEqual(destination[0]["markerId"], "destination_tile:3208:3219:0")
        self.assertEqual(waypoint[0]["markerId"], "next_waypoint_tile:3201:3200:0")
        self.assertEqual(final_approach[0]["markerId"], "final_approach_tile:3207:3219:0")
        self.assertEqual(path_markers[0]["markerId"], "predicted_path_tile:2:3202:3200:0")
        self.assertEqual(result["pathingOverlaySummary"]["overlayPredictedPathLimit"], 24)
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathDisplayedCount"], 9)
        self.assertFalse(result["pathingOverlaySummary"]["pathDisplayWasCapped"])

    def test_daily_predicted_path_limit_can_be_overridden(self):
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "predictedPathTiles": [
                        {"worldX": 3201, "worldY": 3200, "plane": 0},
                        {"worldX": 3202, "worldY": 3200, "plane": 0},
                        {"worldX": 3203, "worldY": 3200, "plane": 0},
                    ],
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=2),
            "2026-01-01T00:00:00Z",
            None,
        )

        path_markers = [marker for marker in result["markers"] if marker.get("markerType") == "predicted_path_tile"]
        self.assertEqual(len(path_markers), 2)

    def test_daily_overlay_summary_reports_retained_path_intent(self):
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "destinationTile": {"worldX": 3205, "worldY": 3200, "plane": 0},
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "predictedPathTiles": [{"worldX": 3201, "worldY": 3200, "plane": 0}],
                    "pathIntentRetained": True,
                    "pathStableForTicks": 5,
                    "movementState": "moving",
                    "retentionReason": "player_moving_same_destination",
                    "switchReason": None,
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        summary = result["pathingOverlaySummary"]
        self.assertTrue(summary["pathIntentRetained"])
        self.assertEqual(summary["pathStableForTicks"], 5)
        self.assertEqual(summary["pathMovementState"], "moving")
        self.assertEqual(summary["pathRetentionReason"], "player_moving_same_destination")

    def test_debug_overlay_mode_can_emit_predicted_path_tiles(self):
        state = overlay.build_overlay_state_for_mode(
            Path("."),
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="debug", overlay_predicted_path_limit=None),
            {
                "overlayDebug": {
                    "summary": {},
                    "targets": [],
                    "markers": [],
                }
            },
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "destinationTile": {"worldX": 3205, "worldY": 3200, "plane": 0},
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "finalApproachTile": {"worldX": 3204, "worldY": 3200, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3201, "worldY": 3200, "plane": 0},
                        {"worldX": 3202, "worldY": 3200, "plane": 0},
                        {"worldX": 3203, "worldY": 3200, "plane": 0},
                        {"worldX": 3204, "worldY": 3200, "plane": 0},
                        {"worldX": 3205, "worldY": 3200, "plane": 0},
                    ],
                },
            },
            "2026-01-01T00:00:00Z",
            None,
        )

        path_markers = [marker for marker in state["markers"] if marker.get("markerType") == "predicted_path_tile"]
        final_approach_markers = [marker for marker in state["markers"] if marker.get("markerType") == "final_approach_tile"]
        self.assertEqual(len(final_approach_markers), 1)
        self.assertEqual(final_approach_markers[0]["label"], "Final approach")
        self.assertEqual(final_approach_markers[0]["markerId"], "final_approach_tile:3204:3200:0")
        self.assertEqual(len(path_markers), 2)
        self.assertEqual([marker["label"] for marker in path_markers], ["Path 2", "Path 3"])
        self.assertEqual([marker["pathIndex"] for marker in path_markers], [2, 3])
        self.assertTrue(all(marker.get("source") == "pathing_context" for marker in path_markers))

    def test_debug_overlay_mode_defaults_to_higher_predicted_path_cap(self):
        state = overlay.build_overlay_state_for_mode(
            Path("."),
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="debug", overlay_predicted_path_limit=None),
            {"overlayDebug": {"summary": {}, "targets": [], "markers": []}},
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "predictedPathTiles": [
                        {"worldX": 3200 + index, "worldY": 3200, "plane": 0}
                        for index in range(30)
                    ],
                },
            },
            "2026-01-01T00:00:00Z",
            None,
        )

        path_markers = [marker for marker in state["markers"] if marker.get("markerType") == "predicted_path_tile"]
        self.assertEqual(len(path_markers), 24)
        self.assertEqual(state["summary"]["predictedPathTilesAvailableCount"], 30)
        self.assertEqual(state["summary"]["predictedPathMarkersEmittedCount"], 24)
        self.assertEqual(state["summary"]["predictedPathLimit"], 24)
        self.assertEqual(state["summary"]["overlayPredictedPathLimit"], 24)
        self.assertTrue(state["summary"]["pathDisplayWasCapped"])

    def test_daily_overlay_reports_default_full_path_without_hiding_intermediate_tiles(self):
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "destinationTile": {"worldX": 3212, "worldY": 3200, "plane": 0},
                    "finalApproachTile": {"worldX": 3211, "worldY": 3200, "plane": 0},
                    "nextWaypointTile": {"worldX": 3201, "worldY": 3200, "plane": 0},
                    "predictedPathTiles": [
                        {"worldX": 3200 + index, "worldY": 3200, "plane": 0}
                        for index in range(1, 13)
                    ],
                    "predictedPathCount": 12,
                    "predictedPathDisplayedCount": 12,
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        path_markers = [marker for marker in result["markers"] if marker.get("markerType") == "predicted_path_tile"]
        self.assertEqual(len(path_markers), 9)
        self.assertEqual([marker["pathIndex"] for marker in path_markers], [2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathTilesAvailableCount"], 12)
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathDisplayedCount"], 12)
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathMarkersEmittedCount"], 9)
        self.assertEqual(result["pathingOverlaySummary"]["overlayPredictedPathLimit"], 24)
        self.assertFalse(result["pathingOverlaySummary"]["pathDisplayWasCapped"])

    def test_daily_overlay_defaults_to_internal_predicted_path_cap(self):
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "predictedPathTiles": [
                        {"worldX": 3200 + index, "worldY": 3200, "plane": 0}
                        for index in range(30)
                    ],
                    "predictedPathCount": 30,
                    "predictedPathAvailableCount": 30,
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        path_markers = [marker for marker in result["markers"] if marker.get("markerType") == "predicted_path_tile"]
        self.assertEqual(len(path_markers), 24)
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathDisplayedCount"], 24)
        self.assertEqual(result["pathingOverlaySummary"]["predictedPathMarkersEmittedCount"], 24)
        self.assertEqual(result["pathingOverlaySummary"]["overlayPredictedPathLimit"], 24)
        self.assertTrue(result["pathingOverlaySummary"]["pathDisplayWasCapped"])

    def test_selected_service_target_geometry_is_separate_from_path_marker_cap(self):
        service_target = {
            "objectKey": "bank-booth-1",
            "targetName": "Bank booth",
            "targetType": "sceneObject",
            "classId": "bank_booth",
            "id": 10355,
            "worldX": 3208,
            "worldY": 3219,
            "plane": 0,
            "sceneX": 20,
            "sceneY": 21,
            "clickableHull": [[1, 1], [4, 1], [4, 4]],
            "clickboxPolygon": [[1, 1], [4, 1], [4, 4]],
        }
        result = overlay.build_overlay_state_for_mode(
            Path("."),
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=3),
            {"overlayDebug": {"summary": {}, "targets": [], "markers": []}},
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {
                    "phase": "inventory_full",
                    "activeIntent": "needs_service",
                    "activeIntentTarget": service_target,
                },
                "serviceContext": {"serviceNeeded": True, "bestServiceCandidate": service_target, "serviceCandidates": [service_target]},
                "pathingContext": {
                    "pathingNeeded": True,
                    "predictedPathTiles": [
                        {"worldX": 3200 + index, "worldY": 3200, "plane": 0}
                        for index in range(12)
                    ],
                    "predictedPathCount": 12,
                    "predictedPathAvailableCount": 12,
                },
            },
            "2026-01-01T00:00:00Z",
            None,
        )

        selected = [marker for marker in result["markers"] if marker.get("markerType") == "selected_target"]
        path_markers = [marker for marker in result["markers"] if marker.get("markerType") == "predicted_path_tile"]
        self.assertEqual(len(selected), 1)
        self.assertIn("clickableHull", selected[0])
        self.assertIn("clickboxPolygon", selected[0])
        self.assertEqual(len(path_markers), 3)
        self.assertTrue(result["summary"]["selectedTargetGeometryPresent"])
        self.assertEqual(result["summary"]["selectedTargetGeometrySource"], "clickableHull")
        self.assertFalse(result["summary"]["selectedTargetDroppedByPathCap"])
        self.assertEqual(result["summary"]["pathMarkersAvailable"], 12)
        self.assertEqual(result["summary"]["pathMarkersEmitted"], 3)
        self.assertEqual(result["summary"]["pathMarkerLimit"], 3)
        self.assertTrue(result["summary"]["pathMarkersCapped"])
        self.assertEqual(result["summary"]["geometryLaneCounts"]["selectedTarget"], 1)
        self.assertEqual(result["summary"]["geometryLaneCounts"]["predictedPath"], 3)

    def test_daily_overlay_does_not_draw_clean_path_for_suspect_approach(self):
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 4}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {"phase": "inventory_full", "activeIntent": "needs_service"},
                "pathingContext": {
                    "pathingNeeded": True,
                    "localReachability": "unknown",
                    "reason": "approach_side_access_blocked",
                    "approachQuality": "suspect_outside_wall",
                    "destinationTile": {"worldX": 3210, "worldY": 3217, "plane": 2},
                    "nextWaypointTile": {"worldX": 3211, "worldY": 3216, "plane": 2},
                    "predictedPathTiles": [
                        {"worldX": 3211, "worldY": 3216, "plane": 2},
                        {"worldX": 3210, "worldY": 3216, "plane": 2},
                    ],
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        marker_types = [marker.get("markerType") for marker in result["markers"]]
        self.assertIn("destination_tile", marker_types)
        self.assertNotIn("waypoint", marker_types)
        self.assertNotIn("predicted_path_tile", marker_types)
        self.assertIn("diagnostic", marker_types)

    def test_service_ready_daily_overlay_keeps_service_target_and_suppresses_completed_path(self):
        service_target = {
            "targetName": "Bank booth",
            "classId": "bank_booth",
            "targetType": "sceneObject",
            "worldX": 3208,
            "worldY": 3221,
            "plane": 2,
            "objectKey": "booth-1",
            "clickboxPolygon": [{"x": 1, "y": 1}],
        }
        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 8}, "candidates": []},
            {
                "task": "woodcutting",
                "genericTaskState": {
                    "phase": "service_available",
                    "activeIntent": "service_available",
                    "activeIntentTarget": service_target,
                },
                "serviceContext": {
                    "serviceNeeded": True,
                    "serviceReady": True,
                    "bestServiceCandidate": service_target,
                    "serviceCandidates": [service_target],
                },
                "pathingContext": {
                    "pathingNeeded": False,
                    "pathCompleted": True,
                    "pathCompletionReason": "arrived_at_service",
                    "destinationTile": {"worldX": 3208, "worldY": 3221, "plane": 2},
                    "finalApproachTile": {"worldX": 3207, "worldY": 3221, "plane": 2},
                    "predictedPathTiles": [
                        {"worldX": 3206, "worldY": 3221, "plane": 2},
                        {"worldX": 3207, "worldY": 3221, "plane": 2},
                    ],
                },
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            None,
        )

        marker_types = [marker.get("markerType") for marker in result["markers"]]
        selected = next(marker for marker in result["markers"] if marker.get("markerType") == "selected_target")
        self.assertEqual(selected["label"], "Service: Bank booth")
        self.assertIn("final_approach_tile", marker_types)
        self.assertNotIn("predicted_path_tile", marker_types)
        self.assertTrue(result["pathingOverlaySummary"]["pathCompleted"])

    def test_return_to_resource_overlay_suppresses_completed_service_path(self):
        tree = candidate("oak-1")
        state = intent_stabilizer.IntentState()
        stable = intent_stabilizer.choose_stable_intent(
            state,
            [tree],
            {"activeTask": "woodcutting", "activeIntent": "select_target", "profile": "woodcutting", "latestTick": 9, "rawBestTarget": tree},
        )

        result = overlay.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 9}, "candidates": [tree]},
            {
                "task": "woodcutting",
                "genericTaskState": {
                    "phase": "target_selected",
                    "activeIntent": "select_target",
                    "activeIntentTarget": tree,
                },
                "returnToResourceContext": {
                    "returnNeeded": True,
                    "returnReady": True,
                    "resourceTargetAvailable": True,
                    "bestResourceTarget": tree,
                },
                "pathingContext": {
                    "pathingNeeded": False,
                    "pathCompleted": True,
                    "pathCompletionReason": "arrived_at_service",
                    "destinationTarget": {"targetName": "Bank booth", "classId": "bank_booth"},
                    "destinationTile": {"worldX": 3208, "worldY": 3221, "plane": 2},
                    "finalApproachTile": {"worldX": 3207, "worldY": 3221, "plane": 2},
                    "predictedPathTiles": [
                        {"worldX": 3206, "worldY": 3221, "plane": 2},
                        {"worldX": 3207, "worldY": 3221, "plane": 2},
                    ],
                },
                "confidence": 0.9,
            },
            SimpleNamespace(brain_task="woodcutting", overlay_backup_candidates=2, overlay_mode="intent", overlay_predicted_path_limit=None),
            "2026-01-01T00:00:00Z",
            stable,
        )

        marker_types = [marker.get("markerType") for marker in result["markers"]]
        selected = next(marker for marker in result["markers"] if marker.get("markerType") == "selected_target")
        self.assertEqual(selected["classId"], "tree")
        self.assertNotIn("final_approach_tile", marker_types)
        self.assertNotIn("predicted_path_tile", marker_types)
        self.assertFalse(result["pathingOverlaySummary"]["pathCompleted"])


if __name__ == "__main__":
    unittest.main()

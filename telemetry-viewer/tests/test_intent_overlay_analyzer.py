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

    def test_generic_future_marker_types_and_no_action_fields(self):
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
            },
            "selected_target",
            "Target: Banker",
            "test",
        )

        self.assertEqual(marker["targetType"], "npc")
        self.assertEqual(marker["markerType"], "selected_target")
        forbidden = {"action", "click", "mouse", "keyboard", "menu", "invoke", "execute"}
        self.assertFalse(forbidden.intersection(marker.keys()))

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


if __name__ == "__main__":
    unittest.main()

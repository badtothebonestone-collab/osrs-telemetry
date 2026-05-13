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


if __name__ == "__main__":
    unittest.main()

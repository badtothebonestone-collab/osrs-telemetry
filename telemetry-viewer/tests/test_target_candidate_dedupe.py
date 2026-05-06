import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SELECT_SCRIPT = VIEWER_DIR / "select_target_candidates.py"
BUILD_WORLD_SCRIPT = VIEWER_DIR / "build_world_target_geometry.py"
SUMMARY_SCRIPT = VIEWER_DIR / "summarize_candidate_quality.py"


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, separators=(",", ":")))
            file.write("\n")


def raw_scene_object(object_id: int, tick_id: int, world_x: int, world_y: int, aim_x: int, aim_y: int) -> dict:
    return {
        "kind": "GAME_OBJECT",
        "id": object_id,
        "objectName": "Tree",
        "objectNameSource": "test",
        "worldX": world_x,
        "worldY": world_y,
        "plane": 0,
        "sceneX": world_x - 3200,
        "sceneY": world_y - 3200,
        "localX": (world_x - 3200) * 128,
        "localY": (world_y - 3200) * 128,
        "canvasLocation": {"x": aim_x, "y": aim_y},
        "canvasTilePolygon": [
            [aim_x - 5, aim_y - 5],
            [aim_x + 5, aim_y - 5],
            [aim_x + 5, aim_y + 5],
            [aim_x - 5, aim_y + 5],
        ],
        "clickboxBounds": {"x": aim_x - 4, "y": aim_y - 4, "w": 8, "h": 8},
        "convexHullBounds": {"x": aim_x - 6, "y": aim_y - 6, "w": 12, "h": 12},
        "onScreen": True,
        "geometryAvailable": True,
    }


def raw_tick(tick_id: int, objects: list[dict]) -> dict:
    return {
        "schemaVersion": "test.tick",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": f"2026-01-01T00:00:{tick_id:02d}Z",
        "gameState": "LOGGED_IN",
        "canvasWidth": 300,
        "canvasHeight": 300,
        "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0},
        "npcs": [],
        "players": [],
        "sceneObjects": objects,
        "groundItems": [],
    }


def world_target(tick_id: int, object_id: int, world_x: int, world_y: int, aim_x: int, aim_y: int, suffix: str) -> dict:
    return {
        "schemaVersion": "interaction_geometry.world_target.v1",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": f"2026-01-01T00:00:{tick_id:02d}Z",
        "frame": {"path": f"frames/frame-tick-{tick_id:08d}.jpg", "exists": False, "width": 300, "height": 300},
        "canvas": {"width": 300, "height": 300},
        "target": {
            "targetId": f"{tick_id}:sceneObject:{object_id}:{world_x}:{world_y}:{suffix}",
            "targetType": "sceneObject",
            "name": "Tree",
            "id": object_id,
            "rawId": object_id,
            "targetRole": "interactable",
            "targetCategory": "tree",
            "targetTags": ["tree", "clickable_candidate"],
            "world": {"x": world_x, "y": world_y, "plane": 0},
        },
        "geometry": {
            "coordinateSpace": "canvasPixels",
            "clickboxBounds": {"x": aim_x - 4, "y": aim_y - 4, "w": 8, "h": 8},
            "canvasLocation": {"x": aim_x, "y": aim_y},
            "onScreen": True,
            "geometryAvailable": True,
        },
    }


def npc_target(tick_id: int, npc_id: int, world_x: int, world_y: int, aim_x: int, aim_y: int) -> dict:
    record = world_target(tick_id, npc_id, world_x, world_y, aim_x, aim_y, "npc")
    record["target"].update(
        {
            "targetId": f"{tick_id}:npc:{npc_id}",
            "targetType": "npc",
            "name": "Goblin",
            "targetRole": "entity",
            "targetCategory": "npc",
            "targetTags": ["npc", "entity"],
        }
    )
    record["geometry"]["canvasPoint"] = {"x": aim_x, "y": aim_y}
    return record


def ui_blocker(tick_id: int, name: str, x: int, y: int, w: int, h: int) -> dict:
    return {
        "schemaVersion": "interaction_geometry.ui_target.v1",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": f"2026-01-01T00:00:{tick_id:02d}Z",
        "frame": {"path": f"frames/frame-tick-{tick_id:08d}.jpg", "exists": False, "width": 300, "height": 300},
        "target": {
            "targetId": f"{tick_id}:base:{name}",
            "targetType": "baseUiRegion",
            "targetName": name,
            "regionProfile": "base",
            "regionName": name,
        },
        "geometry": {
            "coordinateSpace": "framePixels",
            "pixelBox": {"x": x, "y": y, "w": w, "h": h},
            "center": {"x": x + w / 2, "y": y + h / 2},
        },
    }


def make_session(root: Path, ticks: list[dict]) -> Path:
    session = root / "session"
    write_jsonl(session / "ticks" / "ticks-000001.jsonl", ticks)
    (session / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (session / "manifest.json").write_text(json.dumps({"sessionId": "fake", "tickCount": len(ticks)}), encoding="utf-8")
    return session


class TargetCandidateDedupeTest(unittest.TestCase):
    def test_candidate_dedupe_removes_same_object_same_aim(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            targets = [
                world_target(1, 1276, 3201, 3201, 100, 100, "a"),
                world_target(1, 1276, 3201, 3201, 100, 100, "duplicate"),
                world_target(1, 1276, 3204, 3204, 150, 150, "different-tree"),
            ]
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", targets)

            subprocess.run(
                [
                    sys.executable,
                    str(SELECT_SCRIPT),
                    "--session",
                    str(session),
                    "--target-type",
                    "sceneObject",
                    "--limit",
                    "20",
                    "--summary",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            candidates = [
                json.loads(line)
                for line in (session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertTrue(index["dedupeEnabled"])
            self.assertEqual(index["matchingTargetCountBeforeDedupe"], 3)
            self.assertEqual(index["duplicatesRemoved"], 1)
            self.assertEqual(index["candidateCountBeforeLimit"], 2)
            self.assertEqual(index["candidateCount"], 2)
            self.assertEqual({candidate["targetWorld"]["x"] for candidate in candidates}, {3201, 3204})

    def test_candidate_dedupe_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            targets = [
                world_target(1, 1276, 3201, 3201, 100, 100, "a"),
                world_target(1, 1276, 3201, 3201, 100, 100, "duplicate"),
            ]
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", targets)

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--target-type", "sceneObject", "--limit", "20", "--no-dedupe"],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            self.assertFalse(index["dedupeEnabled"])
            self.assertEqual(index["duplicatesRemoved"], 0)
            self.assertEqual(index["candidateCount"], 2)

    def test_candidate_dedupe_prefers_object_key_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            duplicate_a = world_target(1, 1276, 3201, 3201, 100, 100, "a")
            duplicate_b = world_target(1, 1276, 3201, 3201, 100, 100, "b")
            same_id_elsewhere = world_target(1, 1276, 3202, 3202, 120, 120, "c")
            duplicate_a["target"]["objectKey"] = "0:3201:3201:1:1:GAME_OBJECT:1276:hash:0"
            duplicate_b["target"]["objectKey"] = duplicate_a["target"]["objectKey"]
            same_id_elsewhere["target"]["objectKey"] = "0:3202:3202:2:2:GAME_OBJECT:1276:hash2:0"
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [duplicate_a, duplicate_b, same_id_elsewhere])

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--target-type", "sceneObject", "--limit", "20"],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            candidates = [
                json.loads(line)
                for line in (session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(index["duplicatesRemoved"], 1)
            self.assertEqual(index["candidateCount"], 2)
            self.assertEqual({candidate["target"]["objectKey"] for candidate in candidates}, {duplicate_a["target"]["objectKey"], same_id_elsewhere["target"]["objectKey"]})

    def test_candidate_no_limit_writes_all_after_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            targets = [
                world_target(1, 1276, 3201, 3201, 100, 100, "a"),
                world_target(1, 1277, 3202, 3202, 120, 120, "b"),
                world_target(1, 1278, 3203, 3203, 140, 140, "c"),
            ]
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", targets)

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--target-type", "sceneObject", "--limit", "0"],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            self.assertTrue(index["noLimit"])
            self.assertEqual(index["discardedByLimit"], 0)
            self.assertEqual(index["candidateCount"], 3)

    def test_build_world_tick_selection_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                [
                    raw_tick(1, [raw_scene_object(1001, 1, 3201, 3201, 80, 80)]),
                    raw_tick(2, [raw_scene_object(1002, 2, 3202, 3202, 90, 90)]),
                    raw_tick(3, [raw_scene_object(1003, 3, 3203, 3203, 100, 100)]),
                ],
            )

            subprocess.run([sys.executable, str(BUILD_WORLD_SCRIPT), "--session", str(session), "--all-ticks", "--target-type", "sceneObject"], text=True, capture_output=True, check=True)
            index = json.loads((session / "interaction_geometry" / "world_geometry_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["selectedBy"], "all-ticks")
            self.assertEqual(index["selectionSource"], "raw tick records")
            self.assertEqual(index["selectedTickCount"], 3)
            self.assertEqual(index["selectedTickRange"], [1, 3])

            subprocess.run([sys.executable, str(BUILD_WORLD_SCRIPT), "--session", str(session), "--latest", "2", "--target-type", "sceneObject"], text=True, capture_output=True, check=True)
            index = json.loads((session / "interaction_geometry" / "world_geometry_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["selectedBy"], "latest")
            self.assertEqual(index["selectedTickCount"], 2)
            self.assertEqual(index["selectedTickRange"], [2, 3])

            subprocess.run([sys.executable, str(BUILD_WORLD_SCRIPT), "--session", str(session), "--range", "2", "2", "--target-type", "sceneObject"], text=True, capture_output=True, check=True)
            index = json.loads((session / "interaction_geometry" / "world_geometry_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["selectedBy"], "range")
            self.assertEqual(index["selectedTickCount"], 1)
            self.assertEqual(index["selectedTickRange"], [2, 2])

    def test_build_world_supports_static_index_visible_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = raw_scene_object(1276, 1, 3201, 3201, 100, 100)
            obj["objectKey"] = "0:3201:3201:1:1:GAME_OBJECT:1276:hash:0"
            obj["source"] = "initialFullPlaneScan"
            tick = raw_tick(1, [])
            tick["sceneIndexSummary"] = {
                "sceneCaptureMode": "STATIC_SCENE_INDEX_DIAGNOSTIC",
                "indexEnabled": True,
                "indexObjectCount": 1,
                "presentObjectCount": 1,
                "fullResyncThisTick": True,
            }
            tick["sceneProjectionSummary"] = {
                "projectionRefreshMode": "VISIBLE_AND_NEARBY",
                "visibleObjectCount": 1,
                "projectionObjectsUpdated": 1,
            }
            tick["visibleSceneObjectRefs"] = [obj]
            session = make_session(Path(tmp), [tick])

            subprocess.run(
                [sys.executable, str(BUILD_WORLD_SCRIPT), "--session", str(session), "--target-type", "all"],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "world_geometry_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["sourceSchema"], "staticIndexDelta")
            self.assertTrue(index["objectKeySupport"])
            self.assertEqual(index["staticIndexRecordCount"], 1)
            self.assertTrue((session / "interaction_geometry" / "scene_static_index.jsonl").exists())

    def test_woodcutting_profile_selects_tree_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            tree = world_target(1, 1276, 3201, 3201, 100, 100, "tree")
            door = world_target(1, 1530, 3202, 3202, 140, 140, "door")
            door["target"]["name"] = "Door"
            door["target"]["targetCategory"] = "door"
            door["target"]["targetTags"] = ["door", "navigation_geometry"]
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [tree, door])

            subprocess.run(
                [
                    sys.executable,
                    str(SELECT_SCRIPT),
                    "--session",
                    str(session),
                    "--profile",
                    "woodcutting",
                    "--target-type",
                    "all",
                    "--limit",
                    "20",
                    "--summary",
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            candidates = [
                json.loads(line)
                for line in (session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(index["profileId"], "woodcutting")
            self.assertEqual(index["matchingTargetsBeforeFilters"], 2)
            self.assertEqual(index["candidateCount"], 1)
            self.assertEqual(candidates[0]["classId"], "tree")
            self.assertEqual(candidates[0]["profileId"], "woodcutting")
            self.assertTrue(candidates[0]["selectedByProfile"])
            self.assertIn(candidates[0]["qualityTier"], {"excellent", "good", "questionable"})

    def test_npc_profile_selects_entity_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [npc_target(1, 1, 3201, 3201, 100, 100)])

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--profile", "npc_qa", "--target-type", "all", "--limit", "20"],
                text=True,
                capture_output=True,
                check=True,
            )
            candidate = json.loads((session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(candidate["classId"], "npc")
            self.assertEqual(candidate["targetType"], "npc")

    def test_ui_blocked_signal_and_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            tree = world_target(1, 1276, 3201, 3201, 100, 100, "tree")
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [tree])
            write_jsonl(session / "interaction_geometry" / "ui_targets.jsonl", [ui_blocker(1, "minimap", 90, 90, 30, 30)])

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--profile", "broad_qa", "--target-type", "sceneObject", "--limit", "20"],
                text=True,
                capture_output=True,
                check=True,
            )
            candidate = json.loads((session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            self.assertTrue(candidate["uiBlocked"])
            self.assertIn("minimap", candidate["blockingUiRegions"])
            self.assertEqual(index["uiBlockedCount"], 1)

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--profile", "broad_qa", "--target-type", "sceneObject", "--exclude-ui-blocked", "--limit", "20"],
                text=True,
                capture_output=True,
                check=True,
            )
            index = json.loads((session / "interaction_geometry" / "target_candidates_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["candidateCount"], 0)
            self.assertEqual(index["excludedUiBlockedCount"], 1)
            self.assertIn("uiBlocked", index["topRejectReasons"])

    def test_candidate_packet_metadata_and_quality_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [])])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [world_target(1, 1276, 3201, 3201, 100, 100, "tree")])

            subprocess.run(
                [sys.executable, str(SELECT_SCRIPT), "--session", str(session), "--profile", "woodcutting", "--target-type", "all", "--limit", "20"],
                text=True,
                capture_output=True,
                check=True,
            )
            candidate = json.loads((session / "interaction_geometry" / "target_candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(candidate["recordSchema"], "target_candidate.v1")
            self.assertEqual(candidate["source"]["type"], "world_targets")
            self.assertEqual(candidate["targetType"], "sceneObject")
            self.assertIsNotNone(candidate["qualityScore"])
            self.assertIn("knownTargetClass", candidate["positiveSignals"])

            result = subprocess.run(
                [sys.executable, str(SUMMARY_SCRIPT), "--session", str(session), "--profile", "woodcutting"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Candidate Quality Summary", result.stdout)
            self.assertIn("candidates by quality tier", result.stdout)


if __name__ == "__main__":
    unittest.main()

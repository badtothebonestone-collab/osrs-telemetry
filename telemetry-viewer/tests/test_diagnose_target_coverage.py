import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_target_coverage.py"


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            if isinstance(record, str):
                file.write(record)
            else:
                file.write(json.dumps(record, separators=(",", ":")))
            file.write("\n")


def raw_scene_object(
    object_id: int,
    *,
    tick: int = 1,
    kind: str = "GAME_OBJECT",
    world_x: int = 3200,
    world_y: int = 3200,
    scene_x: int = 10,
    scene_y: int = 10,
    canvas_x: int = 50,
    canvas_y: int = 50,
    object_hash: str | None = None,
    on_screen: bool = True,
) -> dict:
    record = {
        "kind": kind,
        "id": object_id,
        "worldX": world_x,
        "worldY": world_y,
        "plane": 0,
        "sceneX": scene_x,
        "sceneY": scene_y,
        "localX": scene_x * 128,
        "localY": scene_y * 128,
        "canvasLocation": {"x": canvas_x, "y": canvas_y},
        "canvasTilePolygon": [
            [canvas_x - 5, canvas_y - 5],
            [canvas_x + 5, canvas_y - 5],
            [canvas_x + 5, canvas_y + 5],
            [canvas_x - 5, canvas_y + 5],
        ],
        "clickboxBounds": {"x": canvas_x - 4, "y": canvas_y - 4, "w": 8, "h": 8},
        "convexHullBounds": {"x": canvas_x - 6, "y": canvas_y - 6, "w": 12, "h": 12},
        "onScreen": on_screen,
        "geometryAvailable": True,
    }
    if object_hash:
        record["hash"] = object_hash
    return record


def raw_tick(tick_id: int, scene_objects: list[dict] | None = None) -> dict:
    return {
        "schemaVersion": "test.tick",
        "tickId": tick_id,
        "timestampUtc": "2026-01-01T00:00:00Z",
        "gameState": "LOGGED_IN",
        "canvasWidth": 300,
        "canvasHeight": 300,
        "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0},
        "npcs": [],
        "players": [],
        "sceneObjects": scene_objects or [],
        "groundItems": [],
        "framePath": f"frames/frame-tick-{tick_id:08d}.jpg",
    }


def scene_capture_summary(**overrides) -> dict:
    summary = {
        "sceneCaptureMode": "LOCAL_DEFAULT",
        "fullCurrentPlaneScan": False,
        "configuredRadius": 12,
        "configuredMaxSceneObjects": 250,
        "scanRadius": 12,
        "maxSceneObjects": 250,
        "maxGroundItems": 250,
        "scannedPlane": 0,
        "scannedTiles": 625,
        "tilesWithObjects": 300,
        "scanMinSceneX": 0,
        "scanMaxSceneX": 24,
        "scanMinSceneY": 0,
        "scanMaxSceneY": 24,
        "scanWidth": 25,
        "scanHeight": 25,
        "sceneObjectsSeen": 600,
        "sceneObjectsCaptured": 250,
        "sceneObjectsSkippedByCap": 350,
        "sceneObjectCapHit": True,
        "captureRatio": 250 / 600,
        "gameObjectsSeen": 200,
        "groundObjectsSeen": 400,
        "gameObjectsCaptured": 80,
        "groundObjectsCaptured": 170,
        "gameObjectsSkippedByCap": 120,
        "groundObjectsSkippedByCap": 230,
    }
    summary.update(overrides)
    return summary


def world_target_from_raw(raw_object: dict, tick_id: int, *, frame_tick: int | None = None, target_name: str = "Tree") -> dict:
    frame_tick = tick_id if frame_tick is None else frame_tick
    target = {
        "targetType": "sceneObject",
        "targetId": f"{tick_id}:sceneObject:{raw_object.get('kind')}:{raw_object.get('id')}:{raw_object.get('sceneX')}:{raw_object.get('sceneY')}",
        "id": raw_object.get("id"),
        "rawId": raw_object.get("id"),
        "kind": raw_object.get("kind"),
        "name": target_name,
        "targetName": target_name,
        "targetRole": "interactable",
        "targetCategory": "tree",
        "targetTags": ["tree", "clickable_candidate"],
        "world": {"x": raw_object.get("worldX"), "y": raw_object.get("worldY"), "plane": raw_object.get("plane")},
        "scene": {"x": raw_object.get("sceneX"), "y": raw_object.get("sceneY")},
        "local": {"x": raw_object.get("localX"), "y": raw_object.get("localY")},
    }
    if raw_object.get("hash"):
        target["hash"] = raw_object["hash"]
    return {
        "schemaVersion": "interaction_geometry.world_target.v1",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": "2026-01-01T00:00:00Z",
        "frame": {"path": f"frames/frame-tick-{frame_tick:08d}.jpg", "exists": True, "width": 300, "height": 300},
        "canvas": {"width": 300, "height": 300},
        "target": target,
        "geometry": {
            "coordinateSpace": "canvasPixels",
            "canvasLocation": raw_object.get("canvasLocation"),
            "clickboxBounds": raw_object.get("clickboxBounds"),
            "convexHullBounds": raw_object.get("convexHullBounds"),
            "onScreen": raw_object.get("onScreen", True),
            "geometryAvailable": True,
        },
        "warnings": [],
    }


def candidate_from_world(world_target: dict) -> dict:
    target = dict(world_target["target"])
    point = world_target["geometry"].get("canvasLocation") or {"x": 0, "y": 0}
    return {
        "schemaVersion": "interaction_geometry.target_candidate.v1",
        "sessionId": "fake",
        "tickId": world_target["tickId"],
        "timestampUtc": world_target["timestampUtc"],
        "rank": 1,
        "score": 100,
        "target": target,
        "geometry": {
            "coordinateSpace": "canvasPixels",
            "preferredAimGeometryType": "clickboxBounds",
            "preferredAimGeometry": world_target["geometry"].get("clickboxBounds"),
            "aimPoint": point,
            "aimBounds": world_target["geometry"].get("clickboxBounds"),
            "geometryQuality": 1.0,
        },
        "scoring": {"scoreParts": [], "reasons": ["test"], "penalties": []},
        "frame": world_target["frame"],
        "safety": {"readOnly": True, "actionGenerated": False},
    }


def scenario_record_from_candidate(candidate: dict) -> dict:
    return {
        "schemaVersion": "scenario_dataset.record.v1",
        "scenarioType": "test_scenario",
        "sessionId": "fake",
        "tickId": candidate["tickId"],
        "timestampUtc": candidate["timestampUtc"],
        "frame": candidate["frame"],
        "selectedCandidates": [
            {
                "rankWithinScenario": 1,
                "originalRank": candidate.get("rank"),
                "score": candidate.get("score"),
                "target": candidate.get("target"),
                "aimPoint": candidate.get("geometry", {}).get("aimPoint"),
                "preferredAimGeometryType": candidate.get("geometry", {}).get("preferredAimGeometryType"),
            }
        ],
        "context": {"targets": []},
        "safety": {"readOnly": True, "actionGenerated": False, "inputGenerated": False},
    }


def make_session(root: Path, ticks: list[dict]) -> Path:
    session = root / "session"
    write_jsonl(session / "ticks" / "ticks-000001.jsonl", ticks)
    (session / "frames").mkdir(parents=True, exist_ok=True)
    (session / "manifest.json").write_text(json.dumps({"sessionId": "fake", "tickCount": len(ticks)}), encoding="utf-8")
    return session


def run_diagnostic(session: Path, *args: str) -> dict:
    command = [sys.executable, str(SCRIPT), "--session", str(session), "--project-root", str(session), "--json", *args]
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class DiagnoseTargetCoverageTest(unittest.TestCase):
    def test_synthetic_loss_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_objects = [
                raw_scene_object(1001, scene_x=1, scene_y=1),
                raw_scene_object(1002, scene_x=2, scene_y=2),
                raw_scene_object(1003, scene_x=3, scene_y=3),
            ]
            session = make_session(root, [raw_tick(1, raw_objects)])
            world_targets = [world_target_from_raw(raw_objects[0], 1), world_target_from_raw(raw_objects[1], 1)]
            candidate = candidate_from_world(world_targets[0])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", world_targets)
            write_jsonl(session / "interaction_geometry" / "target_candidates.jsonl", [candidate])
            write_jsonl(session / "scenario_datasets" / "test_scenario.jsonl", [scenario_record_from_candidate(candidate)])

            report = run_diagnostic(session, "--tick", "1", "--scenario", "test_scenario")
            ledger = report["lossLedger"]["byTick"][0]

            self.assertEqual(report["reportSchema"], "target_coverage_diagnostic.v1")
            self.assertEqual(ledger["raw"]["sceneObjects"], 3)
            self.assertEqual(ledger["worldTargets"]["sceneObject"], 2)
            self.assertEqual(ledger["targetCandidates"]["total"], 1)
            self.assertEqual(ledger["scenario"]["selected"], 1)
            self.assertIn("largestComparableLoss", report["lossLedger"])

    def test_missing_optional_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [raw_scene_object(1276)])])

            report = run_diagnostic(session, "--tick", "1", "--scenario", "missing_scenario")

            self.assertTrue(report["session"]["rawTickFilesFound"])
            self.assertFalse(report["session"]["worldTargetsPresent"])
            self.assertFalse(report["session"]["targetCandidatesPresent"])
            self.assertFalse(report["session"]["scenarioFilePresent"])
            self.assertEqual(report["worldTargetCoverage"]["total"], 0)

    def test_session_path_with_spaces_outputs_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root with spaces"
            session = make_session(root, [raw_tick(1, [raw_scene_object(1276)])])

            report = run_diagnostic(session, "--tick", "1")

            self.assertEqual(report["reportSchema"], "target_coverage_diagnostic.v1")
            self.assertEqual(Path(report["session"]["sessionPath"]), session.resolve())

    def test_malformed_jsonl_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session"
            write_jsonl(
                session / "ticks" / "ticks-000001.jsonl",
                [
                    raw_tick(1, [raw_scene_object(1)]),
                    "{not valid json",
                    raw_tick(2, [raw_scene_object(2)]),
                ],
            )

            report = run_diagnostic(session, "--latest", "2")

            malformed = report["malformedCounts"]
            self.assertTrue(any(count >= 1 for count in malformed.values()))
            self.assertEqual(report["rawCoverage"]["totals"]["sceneObjects"], 2)

    def test_scene_capture_summary_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick = raw_tick(1, [raw_scene_object(1)])
            tick["sceneCaptureSummary"] = scene_capture_summary()
            session = make_session(Path(tmp), [tick])

            report = run_diagnostic(session, "--tick", "1")
            summary = report["rawCoverage"]["sceneCaptureSummary"]

            self.assertTrue(summary["present"])
            self.assertEqual(summary["sceneObjectCapHitTickCount"], 1)
            self.assertEqual(summary["byTick"]["1"]["sceneObjectsSeen"], 600)
            self.assertEqual(summary["byTick"]["1"]["sceneCaptureMode"], "LOCAL_DEFAULT")
            self.assertEqual(summary["byTick"]["1"]["scanWidth"], 25)
            self.assertEqual(summary["totals"]["sceneObjectsSkippedByCap"], 350)
            self.assertEqual(summary["modeCounts"]["LOCAL_DEFAULT"], 1)
            self.assertAlmostEqual(summary["averagesPerTick"]["sceneObjectsSeen"], 600)
            self.assertIn("sceneCaptureSummary", " ".join(report["conclusion"]["strongestEvidence"]))
            self.assertIn("WIDE_DIAGNOSTIC", report["conclusion"]["recommendedNextDiagnosticCommand"])

    def test_full_plane_no_cap_conclusion_separates_source_from_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick = raw_tick(1, [raw_scene_object(1)])
            tick["sceneCaptureSummary"] = scene_capture_summary(
                sceneCaptureMode="FULL_CURRENT_PLANE_DIAGNOSTIC",
                fullCurrentPlaneScan=True,
                configuredRadius=0,
                configuredMaxSceneObjects=25000,
                scanRadius=0,
                maxSceneObjects=25000,
                scannedTiles=10816,
                scanWidth=104,
                scanHeight=104,
                sceneObjectsSeen=7831,
                sceneObjectsCaptured=7831,
                sceneObjectsSkippedByCap=0,
                sceneObjectCapHit=False,
                captureRatio=1.0,
                gameObjectsSkippedByCap=0,
                groundObjectsSkippedByCap=0,
            )
            session = make_session(Path(tmp), [tick])

            report = run_diagnostic(session, "--tick", "1")
            conclusion = report["conclusion"]

            self.assertFalse(conclusion["selectedTicksHitSceneObjectCap"])
            self.assertEqual(conclusion["selectedSceneObjectsSkippedByCap"], 0)
            self.assertTrue(conclusion["currentCaptureModeLikelyCompleteForSelectedScan"])
            self.assertFalse(conclusion["javaAppearsToCapOrSkipSceneObjects"])
            self.assertIn("Only one tick selected", conclusion["warnings"][0])

    def test_scene_index_and_projection_summaries_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = raw_scene_object(1276, world_x=3201, world_y=3201, scene_x=1, scene_y=1)
            obj["objectKey"] = "0:3201:3201:1:1:GAME_OBJECT:1276:hash:0"
            tick = raw_tick(1, [])
            tick["sceneCaptureSummary"] = scene_capture_summary(
                sceneCaptureMode="STATIC_SCENE_INDEX_DIAGNOSTIC",
                sceneObjectsSeen=1,
                sceneObjectsCaptured=1,
                sceneObjectsSkippedByCap=0,
                sceneObjectCapHit=False,
                captureRatio=1.0,
            )
            tick["sceneIndexSummary"] = {
                "sceneCaptureMode": "STATIC_SCENE_INDEX_DIAGNOSTIC",
                "indexEnabled": True,
                "indexObjectCount": 1,
                "presentObjectCount": 1,
                "newlyIndexedCount": 1,
                "fullResyncThisTick": True,
                "resyncReason": "startup",
                "sceneIndexBuildDurationMillis": 3,
                "sceneIndexUpdateDurationMillis": 4,
            }
            tick["sceneProjectionSummary"] = {
                "projectionStateHash": "abc",
                "projectionStateChanged": True,
                "projectionRefreshMode": "VISIBLE_AND_NEARBY",
                "projectionCandidatesConsidered": 1,
                "projectionObjectsUpdated": 1,
                "projectionObjectsReused": 0,
                "projectionDurationMillis": 2,
                "visibleObjectCount": 1,
            }
            tick["visibleSceneObjectRefs"] = [obj]
            session = make_session(Path(tmp), [tick])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [world_target_from_raw(obj, 1)])

            report = run_diagnostic(session, "--tick", "1")

            self.assertTrue(report["rawCoverage"]["sceneIndexSummary"]["present"])
            self.assertTrue(report["rawCoverage"]["sceneProjectionSummary"]["present"])
            self.assertEqual(report["rawCoverage"]["totals"]["visibleSceneObjectRefs"], 1)
            self.assertEqual(report["lossLedger"]["byTick"][0]["raw"]["visibleSceneObjectRefs"], 1)

    def test_candidate_limit_metadata_informs_conclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_object = raw_scene_object(1276)
            session = make_session(Path(tmp), [raw_tick(1, [raw_object])])
            world_targets = [world_target_from_raw(raw_object, 1)]
            candidate = candidate_from_world(world_targets[0])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", world_targets)
            write_jsonl(session / "interaction_geometry" / "target_candidates.jsonl", [candidate])
            (session / "interaction_geometry" / "target_candidates_index.json").write_text(
                json.dumps({"limit": 1, "dedupeEnabled": True, "duplicatesRemoved": 2, "discardedByLimit": 5}),
                encoding="utf-8",
            )

            report = run_diagnostic(session, "--tick", "1")

            self.assertTrue(report["conclusion"]["candidateLimitActive"])
            self.assertEqual(report["conclusion"]["candidateDiscardedByLimit"], 5)
            self.assertEqual(report["conclusion"]["candidateDuplicatesRemoved"], 2)

    def test_all_ticks_selection_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                [
                    raw_tick(1, [raw_scene_object(1)]),
                    raw_tick(2, [raw_scene_object(2)]),
                    raw_tick(3, [raw_scene_object(3)]),
                ],
            )

            report = run_diagnostic(session, "--all-ticks")

            self.assertEqual(report["selectedTicks"], [1, 2, 3])
            self.assertEqual(report["session"]["tickSelectionMode"], "explicit --all-ticks")

    def test_identity_matching_reports_missing_raw_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_a = raw_scene_object(1276, object_hash="hash-a", kind="GAME_OBJECT", world_x=3200, world_y=3200)
            obj_b = raw_scene_object(1277, object_hash="hash-b", kind="GROUND_OBJECT", world_x=3201, world_y=3201)
            session = make_session(Path(tmp), [raw_tick(1, [obj_a, obj_b])])
            write_jsonl(session / "interaction_geometry" / "world_targets.jsonl", [world_target_from_raw(obj_a, 1)])

            report = run_diagnostic(session, "--tick", "1")
            identity = report["identityMatching"]["byTick"]["1"]

            self.assertGreaterEqual(identity["rawIdentitiesMissingFromWorldTargets"], 1)
            self.assertIn("GROUND_OBJECT", report["identityMatching"]["rawIdentitiesMissingTraits"]["kind"])

    def test_object_id_trace_reports_absent_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1, [raw_scene_object(1276)])])

            report = run_diagnostic(session, "--tick", "1", "--object-id", "1276")
            trace = report["trace"]

            self.assertTrue(trace["enabled"])
            self.assertEqual(trace["countsByTick"]["1"]["raw"], 1)
            self.assertEqual(trace["countsByTick"]["1"]["worldTargets"], 0)
            self.assertEqual(trace["firstAbsentStageByTick"]["1"], "worldTargets")

    def test_near_trace_uses_default_radius(self):
        with tempfile.TemporaryDirectory() as tmp:
            near = raw_scene_object(1, world_x=3201, world_y=3202)
            far = raw_scene_object(2, world_x=3210, world_y=3210)
            session = make_session(Path(tmp), [raw_tick(1, [near, far])])
            write_jsonl(
                session / "interaction_geometry" / "world_targets.jsonl",
                [world_target_from_raw(near, 1), world_target_from_raw(far, 1)],
            )

            report = run_diagnostic(session, "--tick", "1", "--near", "3200", "3200")
            trace = report["trace"]

            self.assertEqual(trace["countsByTick"]["1"]["raw"], 1)
            self.assertEqual(trace["countsByTick"]["1"]["worldTargets"], 1)
            world_sample = trace["samples"]["worldTargets"]["1"][0]
            self.assertLessEqual(world_sample["match"]["near"]["distance"], 3)

    def test_viewport_sector_counts_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            top_left = raw_scene_object(1, canvas_x=10, canvas_y=10)
            bottom_right = raw_scene_object(2, canvas_x=290, canvas_y=290)
            session = make_session(Path(tmp), [raw_tick(1, [top_left, bottom_right])])
            write_jsonl(
                session / "interaction_geometry" / "world_targets.jsonl",
                [world_target_from_raw(top_left, 1), world_target_from_raw(bottom_right, 1)],
            )

            report = run_diagnostic(session, "--tick", "1")
            sectors = report["viewportSectors"]["rawSceneObjects"]["1"]["counts"]

            self.assertGreater(sectors["top-left"], 0)
            self.assertGreater(sectors["bottom-right"], 0)

    def test_frame_alignment_reports_nearest_retained_frame_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(201, [raw_scene_object(1)])])
            (session / "frames" / "frame-tick-00000200.jpg").write_bytes(b"fake image")
            write_jsonl(
                session / "interaction_geometry" / "world_targets.jsonl",
                [world_target_from_raw(raw_scene_object(1), 201, frame_tick=200)],
            )

            report = run_diagnostic(session, "--tick", "201")
            alignment = report["frameAlignment"]["byTick"]["201"]

            self.assertEqual(alignment["nearestRetainedFrameTick"]["tick"], 200)
            self.assertEqual(alignment["nearestRetainedFrameTick"]["delta"], -1)


if __name__ == "__main__":
    unittest.main()

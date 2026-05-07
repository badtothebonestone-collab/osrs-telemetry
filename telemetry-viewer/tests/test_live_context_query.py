import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "live_context_query.py"
sys.path.insert(0, str(VIEWER_DIR))

import live_context_query as query


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, separators=(",", ":")) + "\n")


def fresh_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def old_time() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def candidate(
    tick: int,
    class_id: str,
    distance: int,
    quality: int,
    *,
    name: str = "Tree",
    aim: bool = True,
    ui_blocked: bool = False,
    on_screen: bool = True,
    geometry: bool = True,
    object_key: str | None = None,
) -> dict:
    value = {
        "schema": "live_candidate_packet.v1",
        "tickId": tick,
        "tick": tick,
        "profileId": "woodcutting",
        "targetType": "sceneObject",
        "classId": class_id,
        "targetClassIds": [class_id],
        "name": name,
        "id": 1276,
        "hash": 12345,
        "objectKey": object_key or f"{class_id}-{distance}-{quality}",
        "targetRole": "interactable",
        "targetCategory": "tree",
        "targetTags": ["tree"],
        "worldX": 3200 + distance,
        "worldY": 3200,
        "plane": 0,
        "sceneX": distance,
        "sceneY": 0,
        "targetDistanceChebyshev": distance,
        "distanceTiles": distance,
        "onScreen": on_screen,
        "geometryAvailable": geometry,
        "uiBlocked": ui_blocked,
        "blockingUiRegions": ["inventory"] if ui_blocked else [],
        "preferredGeometryType": "clickboxBounds",
        "qualityScore": quality,
        "qualityTier": "excellent" if quality >= 90 else "good",
        "positiveSignals": ["onScreen", "geometryAvailable"],
        "negativeSignals": ["uiBlocked"] if ui_blocked else [],
        "targetLiveState": "live",
        "targetLiveStateConfidence": 0.9,
        "targetLiveEvidence": ["test candidate live"],
    }
    if aim:
        value["aimPoint"] = {"x": 100 + distance, "y": 120}
        value["aimPointContext"] = {"canvasX": 100 + distance, "canvasY": 120, "source": "clickboxBounds"}
    return value


def make_live_session(root: Path, *, candidates: list[dict] | None = None, stale: bool = False, source_cap_hit: bool = False, navigation: bool = True) -> Path:
    session = root / "session"
    live_dir = session / "interaction_geometry" / "live"
    generated = old_time() if stale else fresh_time()
    candidates = candidates if candidates is not None else [candidate(10, "tree", 2, 95), candidate(10, "tree", 5, 99)]
    write_json(
        live_dir / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated,
            "latestTick": 10,
            "latestFramePath": str(session / "frames" / "frame-tick-00000010.jpg"),
            "player": {
                "worldX": 3200,
                "worldY": 3200,
                "plane": 0,
                "sceneX": 10,
                "sceneY": 10,
                "localX": 1280,
                "localY": 1280,
                "animation": -1,
                "interacting": None,
                "isMoving": False,
                "runEnergy": 75,
            },
            "cameraViewport": {"canvasWidth": 300, "canvasHeight": 300},
            "inventory": {"itemCount": 4, "freeSlots": 24},
        },
    )
    write_json(
        live_dir / "live_status.json",
        {
            "schema": "live_status.v1",
            "generatedAtUtc": generated,
            "latestTick": 10,
            "latestTickProcessed": 10,
            "profile": "woodcutting",
            "candidateCount": len(candidates),
            "sourceSceneKnowledgeComplete": True,
            "sourceCapHit": source_cap_hit,
            "budgetExceeded": False,
            "writeFailureCount": 0,
            "warningCount": 0,
        },
    )
    write_json(
        live_dir / "live_context_index.json",
        {
            "schema": "live_context_index.v1",
            "latestTick": 10,
            "activeProfile": "woodcutting",
            "candidateCountsByClassId": {"tree": len(candidates)},
            "bestCandidateByClassId": {"tree": {"name": "Tree"}},
            "nearestCandidateByClassId": {"tree": {"name": "Tree"}},
        },
    )
    write_json(
        live_dir / "live_activity_state.json",
        {
            "schema": "live_activity_state.v1",
            "generatedAtUtc": generated,
            "latestTick": 10,
            "player": {
                "worldX": 3200,
                "worldY": 3200,
                "plane": 0,
                "animation": -1,
                "poseAnimation": 808,
                "interacting": {},
            },
            "inventory": {
                "known": True,
                "freeSlots": 24,
                "filledSlots": 4,
                "itemCount": 4,
                "inventoryFull": False,
                "changedThisTick": False,
                "changedRecently": False,
                "recentItemDeltas": [],
            },
            "equipment": {"known": True, "items": []},
            "targetLiveness": {
                "bestCandidateLiveState": "live",
                "recentlyUnavailableCount": 0,
                "recentlyDepletedCount": 0,
                "suppressedCandidateCount": 0,
                "recentlyUnavailableTargets": [],
            },
            "activity": {
                "apparentState": "idle",
                "apparentTask": "unknown",
                "confidence": 0.5,
                "evidence": ["test idle"],
                "warnings": [],
            },
            "woodcuttingState": {
                "woodcuttingState": "likely_idle",
                "confidence": 0.55,
                "evidence": ["test tree available"],
                "warnings": [],
            },
        },
    )
    if navigation:
        write_json(
            live_dir / "live_navigation_summary.json",
            {
                "schema": "live_navigation_summary.v1",
                "collisionKnown": False,
                "plane": 0,
                "playerSceneX": 10,
                "playerSceneY": 10,
                "mapBounds": None,
                "blockedMovementTileCount": None,
                "obstaclesKnown": False,
                "notes": ["collision maps are not captured"],
            },
        )
    write_jsonl(live_dir / "live_candidates.jsonl", candidates)
    return session


class LiveContextQueryTest(unittest.TestCase):
    def test_summary_payload_contains_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.summary_payload(context, args)
            self.assertEqual(payload["schema"], "live_context_summary.v1")
            self.assertEqual(payload["candidateCount"], 2)
            self.assertEqual(payload["candidateCountsByClassId"]["tree"], 2)

    def test_nearest_candidate_by_distance(self):
        candidates = [candidate(10, "tree", 7, 99), candidate(10, "tree", 2, 80)]
        nearest = query.nearest_candidate(candidates, "tree")
        self.assertEqual(nearest["targetDistanceChebyshev"], 2)

    def test_best_candidate_by_quality_score(self):
        candidates = [candidate(10, "tree", 2, 80), candidate(10, "tree", 7, 99)]
        best = query.best_candidate(candidates, "tree")
        self.assertEqual(best["qualityScore"], 99)

    def test_json_output_purity(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--nearest", "tree", "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "live_context_answer.v1")
            self.assertEqual(payload["query"]["type"], "nearest")
            self.assertEqual(payload["answer"]["classId"], "tree")

    def test_task_default_output_is_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--task", "woodcutting"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("best tree:", result.stdout)
            self.assertIn("inventory:", result.stdout)
            self.assertNotIn("top candidates:", result.stdout)

    def test_verbose_task_output_prints_top_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--task", "woodcutting", "--verbose", "--top", "1"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("top candidates:", result.stdout)
            self.assertEqual(result.stdout.count(". Tree"), 1)

    def test_compact_json_omits_bulky_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--task", "woodcutting", "--json", "--compact-json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "live_task_context.v1")
            self.assertIn("bestTree", payload)
            self.assertNotIn("sourceFiles", payload)
            self.assertNotIn("candidateSummary", payload)

    def test_benchmark_prints_query_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--summary", "--benchmark"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("query timing:", result.stdout)

    def test_stale_live_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), stale=True)
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "max_distance": None, "freshness_ticks": 5, "freshness_ms": 1})()
            payload = query.direct_query_payload(context, "nearest", "tree", args)
            self.assertEqual(payload["status"], "WARN")
            self.assertTrue(any("stale" in warning for warning in payload["warnings"]))

    def test_no_aim_point_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), candidates=[candidate(10, "tree", 2, 90, aim=False)])
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "max_distance": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.direct_query_payload(context, "nearest", "tree", args)
            self.assertIn("aimPoint", payload["missingFields"])
            self.assertTrue(any("no aim point" in warning for warning in payload["warnings"]))

    def test_ui_blocked_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), candidates=[candidate(10, "tree", 2, 90, ui_blocked=True)])
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "max_distance": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.direct_query_payload(context, "nearest", "tree", args)
            self.assertEqual(payload["status"], "WARN")
            self.assertTrue(any("UI-blocked" in warning for warning in payload["warnings"]))

    def test_source_cap_hit_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), source_cap_hit=True)
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "max_distance": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.direct_query_payload(context, "nearest", "tree", args)
            self.assertTrue(any("cap was hit" in warning for warning in payload["warnings"]))

    def test_missing_files_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            context = query.load_live_context(session)
            self.assertIn("live_baseline_state", context["missingFields"])
            self.assertIn("live_status", context["missingFields"])
            self.assertIn("live_candidates", context["missingFields"])

    def test_woodcutting_task_context_with_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.woodcutting_task_payload(context, args)
            self.assertIn(payload["status"], {"PASS", "WARN"})
            self.assertTrue(payload["canAnswerCoreQuestions"])
            self.assertEqual(payload["candidateSummary"]["visibleTreeCandidateCount"], 2)
            self.assertEqual(payload["targetLivenessState"]["bestCandidateLiveState"], "live")
            self.assertEqual(payload["woodcuttingState"]["woodcuttingState"], "likely_idle")

    def test_woodcutting_task_context_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), candidates=[])
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.woodcutting_task_payload(context, args)
            self.assertEqual(payload["status"], "FAIL")
            self.assertFalse(payload["canAnswerCoreQuestions"])

    def test_navigation_readiness_unknown_without_collision_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp), navigation=False)
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": None, "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.woodcutting_task_payload(context, args)
            self.assertEqual(payload["navigationReadiness"]["status"], "unknown")
            self.assertTrue(any("collision/navigation data unavailable" in warning for warning in payload["warnings"]))

    def test_activity_inventory_liveness_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            context = query.load_live_context(session)

            activity = query.activity_payload(context)
            inventory = query.inventory_payload(context)
            liveness = query.liveness_payload(context)

            self.assertEqual(activity["status"], "PASS")
            self.assertEqual(activity["activityState"]["apparentState"], "idle")
            self.assertEqual(inventory["inventoryFull"], False)
            self.assertEqual(liveness["targetLivenessState"]["bestCandidateLiveState"], "live")

    def test_self_test_pass_warn_fail_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_live_session(Path(tmp))
            context = query.load_live_context(session)
            args = type("Args", (), {"profile": "woodcutting", "freshness_ticks": 5, "freshness_ms": 60000})()
            payload = query.self_test_payload(context, args)
            self.assertEqual(payload["status"], "PASS")

            empty_session = make_live_session(Path(tmp) / "empty", candidates=[])
            empty_context = query.load_live_context(empty_session)
            empty_payload = query.self_test_payload(empty_context, args)
            self.assertEqual(empty_payload["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()

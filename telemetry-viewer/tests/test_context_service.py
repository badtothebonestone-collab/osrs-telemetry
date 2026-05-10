import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "context_service.py"
sys.path.insert(0, str(VIEWER_DIR))

import context_service as service


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


def stale_time() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")


def candidate(
    distance: int,
    quality: int,
    *,
    class_id: str = "tree",
    ui_blocked: bool = False,
    live_state: str = "live",
    reachability: str = "reachable",
    in_collision_window: bool | None = True,
) -> dict:
    return {
        "schema": "live_candidate_packet.v1",
        "tick": 10,
        "tickId": 10,
        "profileId": "woodcutting",
        "classId": class_id,
        "targetClassIds": [class_id],
        "targetType": "sceneObject",
        "name": "Tree",
        "id": 1276,
        "hash": 12345 + distance,
        "objectKey": f"{class_id}-{distance}",
        "targetRole": "interactable",
        "targetCategory": "tree",
        "targetTags": ["tree"],
        "worldX": 3200 + distance,
        "worldY": 3200,
        "plane": 0,
        "sceneX": distance,
        "sceneY": 0,
        "distanceTiles": distance,
        "targetDistanceChebyshev": distance,
        "onScreen": True,
        "geometryAvailable": True,
        "uiBlocked": ui_blocked,
        "blockingUiRegions": ["inventory"] if ui_blocked else [],
        "qualityScore": quality,
        "qualityTier": "excellent",
        "aimPoint": {"x": 100 + distance, "y": 120, "source": "clickboxCenter"},
        "aimPointContext": {"canvasX": 100 + distance, "canvasY": 120, "source": "clickboxCenter"},
        "preferredGeometryType": "clickboxBounds",
        "positiveSignals": ["onScreen", "geometryAvailable"],
        "negativeSignals": ["uiBlocked"] if ui_blocked else [],
        "targetLiveState": live_state,
        "targetLiveStateConfidence": 0.55 if live_state == "live_assumed" else 0.95 if live_state == "live" else 0.2,
        "targetLiveEvidence": ["no direct depletion delta seen"] if live_state == "live_assumed" else ["test live"],
        "navigation": {
            "collisionKnown": True,
            "collisionWindowAvailable": True,
            "targetInCollisionWindow": in_collision_window,
            "samePlane": True,
            "playerTileKnown": True,
            "targetTileKnown": True,
            "directReachability": reachability,
            "pathLengthTiles": max(0, distance - 1) if reachability == "reachable" else None,
            "checkedTiles": distance + 1,
            "reachabilityConfidence": 0.85 if reachability == "reachable" else 0.75 if reachability == "blocked" else 0.2,
            "reachabilityEvidence": ["synthetic local path"] if reachability == "reachable" else ["synthetic blocked path"] if reachability == "blocked" else [],
            "missingNavigationFields": [] if reachability != "unknown" else ["localReachability"],
            "conservativeMode": True,
        },
    }


def make_session(root: Path, *, candidates: list[dict] | None = None, stale: bool = False, include_activity: bool = True) -> Path:
    session = root / "session"
    live_dir = session / "interaction_geometry" / "live"
    generated = stale_time() if stale else fresh_time()
    candidates = candidates if candidates is not None else [candidate(5, 95), candidate(2, 80)]
    write_json(
        live_dir / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated,
            "latestTick": 10,
            "latestFrameTick": 10,
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
                "isMoving": False,
                "runEnergy": 80,
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
            "inputSourceRequested": "auto",
            "inputSourceActive": "compact-packets",
            "defaultLiveInputPreference": "compact-packets",
            "compactPacketsAvailable": True,
            "compactPacketsRecent": True,
            "compactPacketLastSequence": 123,
            "compactPacketLatestSegment": str(session / "live_packets" / "live-000001.ndjson"),
            "sourceSceneKnowledgeComplete": True,
            "sourceCapHit": False,
            "budgetExceeded": False,
            "writeFailureCount": 0,
            "livenessMode": "delta",
            "livenessDegraded": False,
            "livenessBudgetExceeded": False,
            "candidatesSuppressedByLiveness": 0,
            "candidatesSuppressedAsDepleted": 0,
            "candidatesRevivedAfterRespawn": 0,
        },
    )
    write_json(
        live_dir / "live_context_index.json",
        {
            "schema": "live_context_index.v1",
            "latestTick": 10,
            "activeProfile": "woodcutting",
            "candidateCountsByClassId": {"tree": len(candidates)},
        },
    )
    write_json(
        live_dir / "live_navigation_summary.json",
        {
            "schema": "live_navigation_summary.v1",
            "collisionKnown": False,
            "plane": 0,
            "playerSceneX": 10,
            "playerSceneY": 10,
            "obstaclesKnown": False,
        },
    )
    if include_activity:
        write_json(
            live_dir / "live_activity_state.json",
            {
                "schema": "live_activity_state.v1",
                "generatedAtUtc": generated,
                "latestTick": 10,
                "activity": {"apparentState": "idle", "confidence": 0.5, "evidence": ["test"]},
                "woodcuttingState": {"woodcuttingState": "likely_idle", "confidence": 0.55},
                "inventory": {
                    "known": True,
                    "freeSlots": 24,
                    "filledSlots": 4,
                    "itemCount": 4,
                    "inventoryFull": False,
                    "changedRecently": False,
                    "recentItemDeltas": [],
                },
                "targetLiveness": {
                    "activeCandidateLiveState": candidates[0].get("targetLiveState") if candidates else None,
                    "bestCandidateLiveState": candidates[0].get("targetLiveState") if candidates else None,
                    "recentlyUnavailableCount": 0,
                    "recentlyDepletedCount": 0,
                    "suppressedCandidateCount": 0,
                    "candidatesSuppressedByLiveness": 0,
                    "candidatesSuppressedAsDepleted": 0,
                    "candidatesRevivedAfterRespawn": 0,
                    "livenessMode": "delta",
                    "livenessDegraded": False,
                    "livenessBudgetExceeded": False,
                    "recentlyUnavailableTargets": [{"objectKey": f"old-{index}"} for index in range(5)],
                },
            },
        )
    write_jsonl(live_dir / "live_candidates.jsonl", candidates)
    return session


def args_for(session: Path, *, token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session=str(session),
        sessions_dir=None,
        latest_session=False,
        reload_interval=0,
        max_candidates=3,
        max_response_bytes=1_000_000,
        auth_token=token,
        no_auth_token=False,
        debug=False,
        compact_include_source_files=False,
        compact_include_liveness_examples=0,
    )


class ServerFixture:
    def __init__(self, session: Path, token: str | None = None):
        self.state = service.ContextState(args_for(session, token=token))
        self.server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.ContextRequestHandler)
        self.server.context_state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.port = self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path: str, token: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Context-Token": token} if token else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def get_raw(self, path: str, token: str | None = None) -> tuple[int, str, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Context-Token": token} if token else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        content_type = resp.getheader("Content-Type") or ""
        text = resp.read().decode("utf-8")
        conn.close()
        return resp.status, content_type, text

    def post(self, path: str, payload, token: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Context-Token"] = token
        conn.request("POST", path, body=json.dumps(payload), headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data


class ContextServiceTest(unittest.TestCase):
    def test_context_request_parsing_and_compact_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            cache = service.LiveContextCache(session, reload_interval=0)
            context = cache.load(force=True)
            response = service.build_context_response(
                context,
                {
                    "schema": "context_request.v1",
                    "requestId": "abc",
                    "task": "woodcutting",
                    "needs": ["baseline", "best:tree", "inventory", "activity", "liveness"],
                    "maxCandidates": 1,
                    "responseMode": "compact",
                },
            )
            self.assertEqual(response["schema"], "context_response.v1")
            self.assertEqual(response["requestId"], "abc")
            self.assertIn(response["status"], {"PASS", "WARN"})
            self.assertIn("bestCandidates", response)
            self.assertEqual(response["bestCandidates"]["tree"]["targetName"], "Tree")
            self.assertNotIn("candidateSummary", response)
            self.assertNotIn("sourceFiles", response)
            self.assertIn("sourceFilesSummary", response)

    def test_context_diagnostics_include_input_source_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            cache = service.LiveContextCache(session, reload_interval=0)
            context = cache.load(force=True)
            response = service.build_context_response(
                context,
                {
                    "schema": "context_request.v1",
                    "needs": ["diagnostics"],
                    "responseMode": "compact",
                },
            )
            input_source = response["diagnostics"]["inputSource"]
            self.assertEqual(input_source["inputSourceActive"], "compact-packets")
            self.assertTrue(input_source["compactPacketsAvailable"])
            self.assertEqual(input_source["latestCompactPacketSequence"], 123)

    def test_context_warns_when_live_processor_uses_raw_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            status_path = session / "interaction_geometry" / "live" / "live_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["inputSourceActive"] = "raw-ticks"
            status["inputSourceRequested"] = "auto"
            status["inputFallbackReason"] = "compact live packets unavailable; falling back to raw tick JSONL"
            write_json(status_path, status)
            cache = service.LiveContextCache(session, reload_interval=0)
            response = service.build_context_response(
                cache.load(force=True),
                {"schema": "context_request.v1", "needs": ["diagnostics"], "responseMode": "compact"},
            )
            self.assertTrue(any("raw tick fallback" in warning for warning in response["warnings"]))
            self.assertEqual(response["diagnostics"]["inputSource"]["inputSourceActive"], "raw-ticks")

    def test_live_assumed_liveness_is_assumed_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), candidates=[candidate(2, 95, live_state="live_assumed")])
            response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "needs": ["best:tree", "liveness"], "responseMode": "compact"},
            )
            self.assertEqual(response["bestCandidates"]["tree"]["livenessInterpretation"], "assumed")
            self.assertEqual(response["liveness"]["livenessInterpretation"], "assumed")
            self.assertFalse(any("liveness is unknown" in warning for warning in response["warnings"]))

    def test_degraded_liveness_still_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), candidates=[candidate(2, 95, live_state="unknown")])
            response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "needs": ["best:tree"], "responseMode": "compact"},
            )
            self.assertEqual(response["bestCandidates"]["tree"]["livenessInterpretation"], "unknown")
            self.assertTrue(any("liveness is unknown" in warning for warning in response["warnings"]))

    def test_compact_liveness_omits_full_recently_unavailable_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["liveness"], "responseMode": "compact"},
            )
            self.assertIn("liveness", response)
            self.assertNotIn("recentlyUnavailableTargets", response["liveness"])

    def test_normal_liveness_includes_capped_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["liveness"], "responseMode": "normal"},
                compact_liveness_examples=2,
            )
            self.assertEqual(len(response["liveness"]["recentlyUnavailableTargets"]), 2)

    def test_full_liveness_includes_full_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["liveness"], "responseMode": "full"},
            )
            self.assertIn("targetLivenessState", response["liveness"])
            self.assertEqual(len(response["liveness"]["targetLivenessState"]["recentlyUnavailableTargets"]), 5)

    def test_context_response_warns_not_fails_when_liveness_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            status_path = session / "interaction_geometry" / "live" / "live_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["livenessDegraded"] = True
            status["livenessBudgetExceeded"] = True
            status_path.write_text(json.dumps(status), encoding="utf-8")
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "needs": ["best:tree"], "responseMode": "compact"})
            self.assertEqual(response["status"], "WARN")
            self.assertTrue(any("liveness degraded" in warning for warning in response["warnings"]))

    def test_compact_source_files_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["baseline"], "responseMode": "compact"},
                compact_include_source_files=True,
            )
            self.assertIn("sourceFiles", response)
            self.assertNotIn("sourceFilesSummary", response)

    def test_nearest_tree_request_uses_distance(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), candidates=[candidate(8, 99), candidate(2, 60)])
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "needs": ["nearest:tree"], "responseMode": "compact"})
            self.assertEqual(response["nearestCandidates"]["tree"]["distanceTiles"], 2)

    def test_task_woodcutting_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "task": "woodcutting", "needs": ["task_summary"]})
            self.assertIn("taskSummary", response)
            self.assertEqual(response["taskSummary"]["task"], "woodcutting")

    def test_context_response_includes_navigation_readiness_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            live_dir = session / "interaction_geometry" / "live"
            write_json(
                live_dir / "live_navigation_summary.json",
                {
                    "schema": "live_navigation_summary.v1",
                    "latestTick": 10,
                    "collisionKnown": True,
                    "plane": 0,
                    "playerSceneX": 10,
                    "playerSceneY": 10,
                    "playerTileKnown": True,
                    "mapWidth": 104,
                    "mapHeight": 104,
                    "blockedMovementTileCount": 12,
                    "blockedFullTileCount": 3,
                    "collisionHash": "abc123",
                    "obstaclesKnown": True,
                    "reachabilityComputed": False,
                    "fullCollisionGridAvailable": False,
                },
            )
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["navigation_readiness", "best:tree"], "responseMode": "compact"},
            )

            self.assertEqual(response["navigationReadiness"]["status"], "summary")
            self.assertTrue(response["navigationReadiness"]["collisionKnown"])
            self.assertEqual(response["navigationReadiness"]["blockedMovementTileCount"], 12)
            self.assertIn("fullPathfinding", response["missingCapabilities"])

    def test_context_response_includes_local_candidate_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            live_dir = session / "interaction_geometry" / "live"
            write_json(
                live_dir / "live_navigation_summary.json",
                {
                    "schema": "live_navigation_summary.v1",
                    "latestTick": 10,
                    "collisionKnown": True,
                    "collisionWindowAvailable": True,
                    "collisionWindowRadius": 24,
                    "collisionWindowBounds": {"minSceneX": 0, "maxSceneX": 48, "minSceneY": 0, "maxSceneY": 48, "width": 49, "height": 49},
                    "collisionWindowHash": "win123",
                    "collisionWindowTick": 10,
                    "plane": 0,
                    "playerSceneX": 10,
                    "playerSceneY": 10,
                    "playerTileKnown": True,
                    "mapWidth": 104,
                    "mapHeight": 104,
                    "blockedMovementTileCount": 12,
                    "blockedFullTileCount": 3,
                    "collisionHash": "abc123",
                    "obstaclesKnown": True,
                    "reachabilityComputed": True,
                    "fullCollisionGridAvailable": False,
                },
            )
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["navigation_readiness", "best:tree"], "responseMode": "compact"},
            )

            self.assertEqual(response["navigationReadiness"]["status"], "local")
            self.assertTrue(response["navigationReadiness"]["collisionWindowAvailable"])
            self.assertEqual(response["bestCandidates"]["tree"]["navigation"]["directReachability"], "reachable")
            self.assertIn("fullPathfinding", response["missingCapabilities"])

    def test_context_response_includes_reachability_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(
                Path(tmp),
                candidates=[
                    candidate(2, 99, reachability="reachable", in_collision_window=True),
                    candidate(4, 95, reachability="blocked", in_collision_window=True),
                    candidate(8, 90, reachability="unknown", in_collision_window=False),
                ],
            )
            live_dir = session / "interaction_geometry" / "live"
            write_json(
                live_dir / "live_navigation_summary.json",
                {
                    "schema": "live_navigation_summary.v1",
                    "latestTick": 10,
                    "collisionKnown": True,
                    "collisionWindowAvailable": True,
                    "collisionWindowRadius": 24,
                    "collisionWindowBounds": {"minSceneX": 0, "maxSceneX": 48, "minSceneY": 0, "maxSceneY": 48, "width": 49, "height": 49},
                    "plane": 0,
                    "playerSceneX": 10,
                    "playerSceneY": 10,
                    "playerTileKnown": True,
                    "reachabilityComputed": True,
                    "fullCollisionGridAvailable": False,
                },
            )
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["reachability:tree"], "maxCandidates": 2, "responseMode": "compact"},
            )
            summary = response["reachabilitySummary"]["tree"]
            self.assertEqual(summary["candidateCount"], 3)
            self.assertEqual(summary["reachableCount"], 1)
            self.assertEqual(summary["blockedCount"], 1)
            self.assertEqual(summary["unknownCount"], 1)
            self.assertLessEqual(len(response["reachabilityCandidates"]["tree"]), 2)
            self.assertNotIn("pathSteps", json.dumps(response))

    def test_context_reachability_missing_collision_window_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {"schema": "context_request.v1", "needs": ["reachability:tree"], "responseMode": "compact"},
            )
            self.assertEqual(response["status"], "WARN")
            self.assertTrue(any("collision window unavailable" in warning for warning in response["warnings"]))

    def test_missing_live_files_warn_or_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "missing"
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "needs": ["baseline", "best:tree"]})
            self.assertEqual(response["status"], "FAIL")
            self.assertIn("baseline", response["missingCapabilities"])

    def test_stale_live_files_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), stale=True)
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "needs": ["best:tree"], "responseMode": "compact"})
            self.assertEqual(response["status"], "WARN")
            self.assertTrue(any("stale" in warning for warning in response["warnings"]))

    def test_cache_reload_only_on_signature_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            cache = service.LiveContextCache(session, reload_interval=0)
            cache.load(force=True)
            first_reload_count = cache.reload_count
            cache.load()
            self.assertEqual(cache.reload_count, first_reload_count)
            self.assertGreaterEqual(cache.cache_hit_count, 1)

    def test_transient_json_error_keeps_previous_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            cache = service.LiveContextCache(session, reload_interval=0)
            first = cache.load(force=True)
            self.assertTrue(first["status"])
            status_path = session / "interaction_geometry" / "live" / "live_status.json"
            status_path.write_text("{not-json", encoding="utf-8")
            second = cache.load()
            self.assertTrue(second["status"])
            self.assertGreaterEqual(second["cacheStats"]["readErrorCount"], 1)
            self.assertTrue(any("kept previous cached data" in warning for warning in second["warnings"]))

    def test_health_schema_status_and_context_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, health = server.get("/health")
                self.assertEqual(code, 200)
                self.assertEqual(health["schema"], "context_health.v1")
                code, schema = server.get("/schema")
                self.assertEqual(code, 200)
                self.assertIn("context_request.v1", schema["supportedRequestSchemas"])
                code, status = server.get("/status")
                self.assertEqual(code, 200)
                self.assertEqual(status["schema"], "context_status.v1")
                code, response = server.post("/context", {"schema": "context_request.v1", "needs": ["best:tree"], "maxCandidates": 1})
                self.assertEqual(code, 200)
                self.assertEqual(response["schema"], "context_response.v1")
                self.assertEqual(response["bestCandidates"]["tree"]["targetName"], "Tree")

    def test_summary_endpoint_returns_text_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            live_dir = session / "interaction_geometry" / "live"
            write_json(
                live_dir / "live_navigation_summary.json",
                {
                    "schema": "live_navigation_summary.v1",
                    "latestTick": 10,
                    "collisionKnown": True,
                    "collisionWindowAvailable": True,
                    "collisionWindowRadius": 24,
                    "collisionWindowBounds": {"minSceneX": 0, "maxSceneX": 48, "minSceneY": 0, "maxSceneY": 48, "width": 49, "height": 49},
                    "plane": 0,
                    "playerSceneX": 10,
                    "playerSceneY": 10,
                    "playerTileKnown": True,
                    "reachabilityComputed": True,
                    "fullCollisionGridAvailable": False,
                },
            )
            with ServerFixture(session) as server:
                code, content_type, text = server.get_raw("/summary?task=woodcutting&top=2")
                self.assertEqual(code, 200)
                self.assertIn("text/plain", content_type)
                self.assertIn("WOODCUTTING CONTEXT", text)
                self.assertIn("Best tree:", text)
                self.assertIn("Reachable: yes", text)
                self.assertNotIn("sourceFiles", text)

    def test_summary_endpoint_can_return_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, payload = server.get("/summary?task=woodcutting&format=json&top=2")
                self.assertEqual(code, 200)
                self.assertEqual(payload["schema"], "context_response.v1")
                self.assertIn("bestCandidates", payload)

    def test_batch_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, response = server.post(
                    "/context/batch",
                    [
                        {"schema": "context_request.v1", "needs": ["best:tree"]},
                        {"schema": "context_request.v1", "needs": ["nearest:tree"]},
                    ],
                )
                self.assertEqual(code, 200)
                self.assertEqual(len(response), 2)
                self.assertEqual(response[0]["schema"], "context_response.v1")

    def test_auth_token_accepted_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session, token="secret") as server:
                code, _payload = server.get("/health")
                self.assertEqual(code, 401)
                code, payload = server.get("/health", token="secret")
                self.assertEqual(code, 200)
                self.assertEqual(payload["schema"], "context_health.v1")

    def test_no_action_fields_appear_in_context_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "task": "woodcutting", "needs": ["task_summary", "best:tree"], "responseMode": "normal"})
            text = json.dumps(response)
            self.assertNotIn("send input", text.lower())
            self.assertNotIn("execute", text.lower())
            self.assertNotIn('"action"', text)
            self.assertNotIn('"actions"', text)

    def test_oneshot_request_outputs_json_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            request = json.dumps({"schema": "context_request.v1", "task": "woodcutting", "needs": ["baseline", "best:tree"], "maxCandidates": 1})
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--session", str(session), "--oneshot-request", request],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "context_response.v1")
            self.assertIn("bestCandidates", payload)
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()

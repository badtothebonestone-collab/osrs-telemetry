import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
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
            "recordingMode": "LIVE_COMPACT_ONLY",
            "rawTickRecordingEnabled": False,
            "rawEventRecordingEnabled": False,
            "frameRecordingEnabled": False,
            "compactPacketRecordingEnabled": True,
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
        live_dir / "live_watch_values.json",
        {
            "schema": "live_watch_values.v1",
            "generatedAtUtc": generated,
            "latestTick": 10,
            "activeWatchCount": 4,
            "rejectedWatchCount": 0,
            "watchBudgetExceeded": False,
            "valuesByAlias": {
                "inventory_summary": {
                    "alias": "inventory_summary",
                    "type": "builtin",
                    "value": {"known": True, "freeSlots": 24, "filledSlots": 4, "inventoryFull": False},
                    "changed": False,
                    "latestTick": 10,
                    "source": "live_activity_state.inventoryState",
                },
                "activity_animation": {
                    "alias": "activity_animation",
                    "type": "builtin",
                    "value": {"animation": -1, "apparentState": "idle"},
                    "changed": False,
                    "latestTick": 10,
                    "source": "live_activity_packet",
                },
            },
            "changedAliases": [],
            "unavailableWatches": [],
            "warnings": [],
            "source": "live_target_processor",
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
                "activityState": {"apparentState": "idle", "confidence": 0.5, "evidence": ["test"]},
                "woodcuttingState": {"woodcuttingState": "likely_idle", "confidence": 0.55},
                "inventory": {
                    "known": True,
                    "freeSlots": 24,
                    "filledSlots": 4,
                    "itemCount": 4,
                    "inventoryFull": False,
                    "changedRecently": False,
                    "inventoryDeltaTrackingKnown": True,
                    "recentItemDeltas": [],
                },
                "inventoryState": {
                    "known": True,
                    "freeSlots": 24,
                    "filledSlots": 4,
                    "itemCount": 4,
                    "inventoryFull": False,
                    "changedRecently": False,
                    "inventoryDeltaTrackingKnown": True,
                    "recentItemDeltas": [],
                },
                "recentInventoryDeltas": [],
                "recentActivityEvents": [],
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
    write_jsonl(
        live_dir / "live_event_timeline.jsonl",
        [
            {
                "schema": "live_context_event.v1",
                "generatedAtUtc": generated,
                "tick": 10,
                "eventType": "best_candidate_changed",
                "severity": "info",
                "summary": "Best candidate changed: Tree at 3202,3200",
                "details": {},
                "source": "live_target_processor",
                "profile": "woodcutting",
            }
        ],
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
            self.assertIn("woodcuttingState", response)
            self.assertIn("recentInventoryDeltas", response)
            self.assertIn("recentActivityEvents", response)

    def test_context_events_need_returns_recent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            generated = fresh_time()
            write_jsonl(
                session / "interaction_geometry" / "live" / "live_event_timeline.jsonl",
                [
                    {
                        "schema": "live_context_event.v1",
                        "generatedAtUtc": generated,
                        "tick": 8,
                        "eventType": "nearest_candidate_changed",
                        "severity": "info",
                        "summary": "Nearest candidate changed: Tree at 3201,3200",
                        "details": {},
                        "source": "live_target_processor",
                        "profile": "woodcutting",
                    },
                    {
                        "schema": "live_context_event.v1",
                        "generatedAtUtc": generated,
                        "tick": 9,
                        "eventType": "best_candidate_changed",
                        "severity": "info",
                        "summary": "Best candidate changed: Tree at 3202,3200",
                        "details": {"reason": "test"},
                        "source": "live_target_processor",
                        "profile": "woodcutting",
                    },
                    {
                        "schema": "live_context_event.v1",
                        "generatedAtUtc": generated,
                        "tick": 10,
                        "eventType": "inventory_changed",
                        "severity": "info",
                        "summary": "Inventory changed: +1 item 1511",
                        "details": {"recentDelta": {"itemId": 1511, "delta": 1}},
                        "source": "live_target_processor",
                        "profile": "woodcutting",
                    },
                ],
            )
            response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "needs": ["events"], "responseMode": "compact", "maxCandidates": 1, "maxEvents": 2},
            )
            self.assertEqual([event["eventType"] for event in response["events"]], ["best_candidate_changed", "inventory_changed"])
            self.assertEqual(response["recentEvents"], response["events"])
            self.assertEqual(response["eventCount"], 3)
            self.assertNotIn("details", response["events"][0])

            full_response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "needs": ["events"], "responseMode": "full", "maxEvents": 1},
            )
            self.assertEqual(full_response["events"][0]["eventType"], "inventory_changed")
            self.assertIn("details", full_response["events"][0])

    def test_context_response_includes_recent_inventory_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            activity_path = session / "interaction_geometry" / "live" / "live_activity_state.json"
            activity = json.loads(activity_path.read_text(encoding="utf-8"))
            delta = {"toTick": 10, "changes": [{"itemId": 1511, "delta": 1}], "freeSlotsBefore": 25, "freeSlotsAfter": 24}
            activity["inventoryState"]["changedRecently"] = True
            activity["inventoryState"]["recentItemDeltas"] = [delta]
            activity["recentInventoryDeltas"] = [delta]
            write_json(activity_path, activity)
            response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "needs": ["inventory", "activity"], "responseMode": "compact", "maxCandidates": 1},
            )
            self.assertTrue(response["inventory"]["changedRecently"])
            self.assertEqual(response["recentInventoryDeltas"][0]["changes"][0]["itemId"], 1511)

    def test_compact_context_inventory_preserves_item_list_for_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            activity_path = session / "interaction_geometry" / "live" / "live_activity_state.json"
            activity = json.loads(activity_path.read_text(encoding="utf-8"))
            items = [{"slot": 0, "itemId": 1511, "quantity": 1}, {"slot": 1, "itemId": 1521, "quantity": 1}]
            activity["inventoryState"]["items"] = items
            activity["inventoryState"]["filledSlots"] = 2
            activity["inventoryState"]["freeSlots"] = 26
            activity["inventoryState"]["resourceCounts"] = {
                "woodcutting_logs": {
                    "count": 2,
                    "matchedItemIds": [1511, 1521],
                    "byItemId": {"1511": 1, "1521": 1},
                    "matchedSlots": [0, 1],
                }
            }
            write_json(activity_path, activity)
            response = service.build_context_response(
                service.LiveContextCache(session, reload_interval=0).load(force=True),
                {"schema": "context_request.v1", "task": "woodcutting", "needs": ["inventory"], "responseMode": "compact"},
            )
            self.assertEqual([item["itemId"] for item in response["inventory"]["items"]], [1511, 1521])
            self.assertEqual(response["inventory"]["resourceCounts"]["woodcutting_logs"]["matchedSlots"], [0, 1])

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
            self.assertEqual(input_source["recordingMode"], "LIVE_COMPACT_ONLY")
            self.assertFalse(input_source["rawTickRecordingEnabled"])

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

    def test_named_current_debug_context_query_outputs_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--session",
                    str(session),
                    "--query",
                    "current-debug-context",
                    "--max-candidates",
                    "2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(result.stdout)
            self.assertIn(result.returncode, {0, 1})
            self.assertEqual(payload["schema"], "context_response.v1")
            self.assertIn("knowledgeCurrentDebugContext", payload)
            self.assertIn("currentBlocker", payload["knowledgeCurrentDebugContext"]["data"])

    def live_query_args(self, *, session: Path | None = None, query: str = "current-debug-context") -> SimpleNamespace:
        return SimpleNamespace(
            query=query,
            session=str(session) if session else None,
            latest_session=False,
            sessions_dir=None,
            reload_interval=0,
            max_candidates=2,
            max_response_bytes=1_000_000,
            compact_include_source_files=False,
            compact_include_liveness_examples=0,
            daemon_url="http://daemon.test:8890",
            snapshot_url="http://snapshot.test:8893/snapshot",
            live_timeout=1.25,
            world_max_objects=77,
            profile="woodcutting",
            daemon_url_explicit=True,
            auth_token=None,
            no_auth_token=True,
            debug=False,
        )

    def live_fabric(self, session: Path) -> service.knowledge_fabric.KnowledgeFabric:
        return service.knowledge_fabric.KnowledgeFabric(
            world_model_payloads={},
            daemon_status={
                "schema": "context_status.v1",
                "status": "ok",
                "sessionPath": str(session),
                "latestTick": 42,
                "inputSourceActive": "plugin-snapshot",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "candidateCount": 0,
            },
            source="live_8890_8893",
        )

    def live_loading_fabric_without_tick(self, session: Path) -> service.knowledge_fabric.KnowledgeFabric:
        return service.knowledge_fabric.KnowledgeFabric(
            world_model_payloads={},
            daemon_status={
                "schema": "context_status.v1",
                "status": "ok",
                "sessionPath": str(session),
                "latestTick": None,
                "inputSourceActive": "plugin-snapshot",
                "noFileDaily": True,
                "compactPacketFilesRequired": False,
                "compactPacketFilesWriting": False,
                "candidateCount": 0,
                "clientTickHot": {
                    "schema": "client_tick_hot.v1",
                    "gameState": "LOGIN_SCREEN",
                    "sourceEvent": "GameStateChanged",
                    "sampleSource": "GameStateChanged",
                    "latency": {"ageMillis": 125},
                },
            },
            source="live_8890_8893",
        )

    def test_current_debug_context_with_explicit_daemon_url_uses_live_daemon_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "live-session"
            args = self.live_query_args()
            with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=self.live_fabric(live_session)) as live_mock:
                payload = service.build_named_query_response(args, "current-debug-context", ["knowledge_current_debug_context"])

            self.assertEqual(payload["schema"], "context_response.v1")
            self.assertEqual(payload["sourceUsed"], "live_daemon")
            self.assertEqual(payload["contextSource"], "live_daemon")
            self.assertEqual(payload["daemonUrl"], "http://daemon.test:8890")
            self.assertEqual(payload["snapshotUrl"], "http://snapshot.test:8893/snapshot")
            self.assertFalse(payload["fileSessionFallbackUsed"])
            self.assertEqual(payload["freshnessSource"], "daemon_status+plugin_snapshot")
            self.assertEqual(payload["latestTick"], 42)
            self.assertIn("knowledgeCurrentDebugContext", payload)
            self.assertEqual(payload["knowledgeCurrentDebugContext"]["source"], "live_8890_8893")
            self.assertEqual(payload["knowledgeCurrentDebugContext"]["freshness"]["sessionPath"], str(live_session))
            self.assertNotIn("No --session or --latest-session supplied.", payload["warnings"])
            self.assertEqual(payload["sourceFilesSummary"]["fileCount"], 0)
            self.assertTrue(payload["sourceFilesSummary"]["allRequiredPresent"])
            live_mock.assert_called_once()
            _, kwargs = live_mock.call_args
            self.assertEqual(kwargs["daemon_url"], "http://daemon.test:8890")
            self.assertEqual(kwargs["snapshot_url"], "http://snapshot.test:8893/snapshot")
            self.assertEqual(kwargs["timeout"], 1.25)
            self.assertTrue(kwargs["include_projection"])
            self.assertTrue(kwargs["include_collision"])
            self.assertEqual(kwargs["max_objects"], 77)

    def test_daemon_url_queries_use_live_daemon_truth_when_reachable_without_latest_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "loading-session"
            for query_name, needs, expected_key in (
                ("current-debug-context", ["knowledge_current_debug_context"], "knowledgeCurrentDebugContext"),
                ("current-blocker", ["knowledge_current_blocker"], "knowledgeCurrentBlocker"),
            ):
                args = self.live_query_args(query=query_name)
                with mock.patch.object(
                    service.knowledge_fabric,
                    "fabric_from_live",
                    return_value=self.live_loading_fabric_without_tick(live_session),
                ):
                    payload = service.build_named_query_response(args, query_name, needs)

                self.assertEqual(payload["contextSource"], "live_daemon")
                self.assertEqual(payload["sourceUsed"], "live_daemon")
                self.assertFalse(payload["fileSessionFallbackUsed"])
                self.assertNotIn("fileSessionFallbackReason", payload)
                self.assertNotIn("daemonQueryError", payload)
                self.assertIsNone(payload["latestTick"])
                self.assertIn("daemon /status did not include latestTick", payload["warnings"])
                self.assertIn("daemon.latestTick", payload["missingCapabilities"])
                self.assertIn(expected_key, payload)

    def test_current_blocker_with_explicit_daemon_url_uses_live_daemon_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "live-session"
            args = self.live_query_args(query="current-blocker")
            with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=self.live_fabric(live_session)):
                payload = service.build_named_query_response(args, "current-blocker", ["knowledge_current_blocker"])

            self.assertEqual(payload["contextSource"], "live_daemon")
            self.assertEqual(payload["sourceUsed"], "live_daemon")
            self.assertFalse(payload["fileSessionFallbackUsed"])
            self.assertEqual(payload["freshnessSource"], "daemon_status+plugin_snapshot")
            self.assertIn("knowledgeCurrentBlocker", payload)
            blocker = payload["knowledgeCurrentBlocker"]
            self.assertEqual(blocker["freshness"]["sessionPath"], str(live_session))
            self.assertEqual(blocker["freshness"]["sourceTick"], 42)
            blocker_text = json.dumps(blocker)
            self.assertNotIn("daemon_session_missing", blocker_text)
            self.assertNotIn("No telemetry session selected", blocker_text)

    def test_navigation_decision_trace_with_explicit_daemon_url_returns_trace_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "live-session"
            args = self.live_query_args(query="navigation-decision-trace")
            with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=self.live_fabric(live_session)):
                payload = service.build_named_query_response(
                    args,
                    "navigation-decision-trace",
                    ["knowledge_navigation_decision_trace"],
                )

            self.assertEqual(payload["contextSource"], "live_daemon")
            self.assertFalse(payload["fileSessionFallbackUsed"])
            self.assertIn("knowledgeNavigationDecisionTrace", payload)
            trace = payload["knowledgeNavigationDecisionTrace"]
            self.assertEqual(trace["schema"], "navigation_decision_trace_summary.v1")
            self.assertIn("tracePresent", trace["data"])
            self.assertEqual(payload["status"], trace["status"])

    def test_live_daemon_named_query_does_not_require_latest_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "live-session"
            args = self.live_query_args(session=None)
            with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=self.live_fabric(live_session)):
                payload = service.build_named_query_response(args, "current-debug-context", ["knowledge_current_debug_context"])

            self.assertNotEqual(payload["status"], "FAIL")
            self.assertEqual(payload["contextSource"], "live_daemon")
            self.assertFalse(payload["fileSessionFallbackUsed"])
            self.assertFalse((Path(tmp) / "live_packets").exists())
            self.assertEqual(list(Path(tmp).rglob("*.ndjson")), [])
            self.assertEqual(list(Path(tmp).rglob("*.jsonl")), [])

    def test_daemon_url_query_fallback_is_labeled_when_daemon_status_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            args = self.live_query_args(session=session)
            failed_fabric = service.knowledge_fabric.KnowledgeFabric(
                world_model_payloads={},
                daemon_status={"schema": "http_fetch_error.v1", "status": "FAIL", "error": "connection refused"},
                source="live_8890_8893",
            )
            with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=failed_fabric):
                payload = service.build_named_query_response(args, "current-debug-context", ["knowledge_current_debug_context"])

            self.assertEqual(payload["contextSource"], "file_session_fallback")
            self.assertTrue(payload["fileSessionFallbackUsed"])
            self.assertEqual(payload["fileSessionFallbackReason"], "live daemon query failed; using file-session context")
            self.assertIn("connection refused", payload["daemonQueryError"])
            self.assertIn("knowledgeCurrentDebugContext", payload)

    def test_named_data_quality_and_handoff_queries_output_structured_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            for query_name, expected_key in (
                ("data-quality-report", "knowledgeDataQualityReport"),
                ("data-source-inventory", "knowledgeDataSourceInventory"),
                ("query-coverage-matrix", "knowledgeQueryCoverageMatrix"),
                ("navigation-decision-trace", "knowledgeNavigationDecisionTrace"),
                ("coverage-report", "knowledgeCoverageReport"),
                ("task-script-runtime-evidence", "knowledgeTaskScriptRuntimeEvidence"),
                ("task-failure-classification", "knowledgeTaskFailureClassification"),
                ("task-script-step-readiness", "knowledgeTaskStepReadiness"),
                ("task-script-run-readiness", "knowledgeTaskRunReadiness"),
                ("run-readiness", "knowledgeTaskRunReadiness"),
                ("external-knowledge-status", "externalKnowledgeStatus"),
                ("handoff-summary", "knowledgeHandoffSummary"),
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--session",
                        str(session),
                        "--query",
                        query_name,
                        "--max-candidates",
                        "2",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                payload = json.loads(result.stdout)
                self.assertIn(result.returncode, {0, 1})
                self.assertEqual(payload["schema"], "context_response.v1")
                self.assertIn(expected_key, payload)

    def test_live_daemon_task_script_named_queries_use_live_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            live_session = Path(tmp) / "live-session"
            for query_name, needs, expected_key, expected_schema in (
                (
                    "task-script-runtime-evidence",
                    ["knowledge_task_script_runtime_evidence"],
                    "knowledgeTaskScriptRuntimeEvidence",
                    "task_runtime_evidence.v1",
                ),
                (
                    "task-failure-classification",
                    ["knowledge_task_failure_classification"],
                    "knowledgeTaskFailureClassification",
                    "task_failure_classification.v1",
                ),
                (
                    "task-script-step-readiness",
                    ["knowledge_task_step_readiness"],
                    "knowledgeTaskStepReadiness",
                    "task_step_readiness.v1",
                ),
                (
                    "task-script-run-readiness",
                    ["knowledge_task_run_readiness"],
                    "knowledgeTaskRunReadiness",
                    "task_run_readiness.v1",
                ),
            ):
                args = self.live_query_args(query=query_name)
                with mock.patch.object(service.knowledge_fabric, "fabric_from_live", return_value=self.live_fabric(live_session)):
                    payload = service.build_named_query_response(args, query_name, needs)

                self.assertEqual(payload["contextSource"], "live_daemon")
                self.assertFalse(payload["fileSessionFallbackUsed"])
                self.assertIn(expected_key, payload)
                self.assertEqual(payload[expected_key]["schema"], expected_schema)

    def test_handoff_summary_cli_outputs_chatgpt_paste_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--session",
                    str(session),
                    "--handoff-summary",
                    "--handoff-question",
                    "Which local query should I run next?",
                    "--handoff-tests-run",
                    "test_context_service.py PASS",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("PASTE_TO_CHATGPT:", text)
        self.assertIn("Context:", text)
        self.assertIn("What I tried:", text)
        self.assertIn("Evidence:", text)
        self.assertIn("Files changed:", text)
        self.assertIn("Tests run:", text)
        self.assertIn("Current blocker:", text)
        self.assertIn("Specific question:", text)
        self.assertIn("Options I\u2019m considering:", text)
        self.assertIn("My recommended next step:", text)
        self.assertIn("Which local query should I run next?", text)
        self.assertIn("test_context_service.py PASS", text)
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("token=", text.lower())

    def test_handoff_summary_json_cli_remains_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--session",
                    str(session),
                    "--handoff-summary-json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(result.stdout)

        self.assertIn(result.returncode, {0, 1})
        self.assertEqual(payload["schema"], "knowledge_fabric_handoff_summary.v1")

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

    def test_capability_and_watch_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, capabilities = server.get("/capabilities")
                self.assertEqual(code, 200)
                self.assertEqual(capabilities["schema"], "capability_registry.v1")
                by_id = {item["id"]: item for item in capabilities["capabilities"]}
                self.assertEqual(by_id["compact_packets.input"]["runtimeStatus"], "retired")
                self.assertIn("runtimeSummary", capabilities)

                code, watches = server.get("/watches")
                self.assertEqual(code, 200)
                self.assertEqual(watches["schema"], "watch_library.v1")
                self.assertTrue(any(item["alias"] == "inventory_summary" for item in watches["watches"]))
                self.assertIn("inventory_summary", watches["currentValues"]["valuesByAlias"])

    def test_watch_request_accepts_bounded_builtin_and_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, response = server.post(
                    "/watch-request",
                    {
                        "schema": "context_watch_request.v1",
                        "requestId": "watch-test",
                        "task": "woodcutting",
                        "watches": [
                            {
                                "alias": "example_state",
                                "type": "builtin",
                                "id": "inventory.summary",
                                "sampleMode": "on_change",
                                "ttlTicks": 500,
                            }
                        ],
                    },
                )
                self.assertEqual(code, 200)
                self.assertEqual(response["schema"], "context_watch_response.v1")
                self.assertTrue(response["requestWritten"])
                self.assertEqual(response["accepted"][0]["alias"], "example_state")
                self.assertTrue(response["noActionEmitted"])
                request_path = session / "live_requests" / "watch_requests.json"
                self.assertTrue(request_path.exists())
                written = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertEqual(written["activeWatches"][0]["alias"], "example_state")

    def test_watch_request_rejects_unbounded_watches(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            with ServerFixture(session) as server:
                code, response = server.post(
                    "/watch-request",
                    {
                        "schema": "context_watch_request.v1",
                        "watches": [
                            {"alias": "*", "type": "varbit", "id": 123, "sampleMode": "on_change"},
                        ],
                    },
                )
                self.assertEqual(code, 200)
                self.assertEqual(response["accepted"], [])
                reasons = " ".join(item["reason"] for item in response["rejected"])
                self.assertIn("wildcard", reasons)

    def test_context_response_includes_watch_values_and_suggestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(
                context,
                {
                    "schema": "context_request.v1",
                    "needs": ["watches", "watch:inventory_summary", "watch:example_state", "capability:watch_values.java_runtime"],
                    "responseMode": "compact",
                },
            )
            self.assertIn("inventory_summary", response["watchValues"]["valuesByAlias"])
            self.assertIn("watch:example_state", response["missingCapabilities"])
            self.assertIn("capability:watch_values.java_runtime", response["missingCapabilities"])
            self.assertTrue(any(item["alias"] == "example_state" for item in response["suggestedWatchRequests"]))
            self.assertEqual(response["capabilityStatus"]["watch_values.java_runtime"]["runtimeStatus"], "unsupported")

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

    def test_context_response_includes_requested_task_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp))
            context = service.LiveContextCache(session, reload_interval=0).load(force=True)
            response = service.build_context_response(context, {"schema": "context_request.v1", "task": "woodcutting", "needs": ["task_summary", "best:tree"], "responseMode": "normal"})
            self.assertEqual(response["schema"], "context_response.v1")
            self.assertIn("taskSummary", response)

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

    def test_pipeline_health_reports_manifest_config_and_legacy_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions"
            legacy = sessions / "s1" / "live_packets" / "live-000001.ndjson"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}\n", encoding="utf-8")
            args = SimpleNamespace(
                daemon_url="http://127.0.0.1:1",
                snapshot_url="http://127.0.0.1:2/snapshot",
                live_timeout=0.001,
                sessions_dir=str(sessions),
            )

            payload = service.pipeline_health_payload(args)

            self.assertEqual(payload["schema"], "pipeline_health.v1")
            self.assertIn("plugin_snapshot_endpoint_8893", payload["activeComponents"])
            self.assertIn("legacy_live_packet_archive", payload["disabledRemovedComponents"])
            self.assertIn("enabled", payload["configUi"]["activeExposedKeys"])
            self.assertIn("debugRecordRawTicks", payload["configUi"]["retiredKeys"])
            self.assertTrue(payload["legacyLivePackets"]["legacyLivePacketFilesPresent"])
            self.assertTrue(payload["livePacketsRuntimeRemoved"])
            self.assertFalse(payload["livePacketWriterActive"])


if __name__ == "__main__":
    unittest.main()

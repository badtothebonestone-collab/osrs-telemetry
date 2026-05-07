import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = VIEWER_DIR / "live_target_processor.py"
sys.path.insert(0, str(VIEWER_DIR))

import live_target_processor as live
import target_geometry_inspector as inspector


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, separators=(",", ":")))
            file.write("\n")


def raw_scene_object(
    object_id: int,
    world_x: int,
    world_y: int,
    aim_x: int,
    aim_y: int,
    *,
    name: str = "Tree",
    actions=None,
    object_key: str | None = None,
) -> dict:
    return {
        "kind": "GAME_OBJECT",
        "id": object_id,
        "objectName": name,
        "objectNameSource": "test",
        "actions": ["Chop down"] if actions is None else actions,
        "objectKey": object_key,
        "worldX": world_x,
        "worldY": world_y,
        "plane": 0,
        "sceneX": world_x - 3200,
        "sceneY": world_y - 3200,
        "localX": (world_x - 3200) * 128,
        "localY": (world_y - 3200) * 128,
        "canvasLocation": {"x": aim_x, "y": aim_y},
        "clickboxBounds": {"x": aim_x - 4, "y": aim_y - 4, "w": 8, "h": 8},
        "convexHullBounds": {"x": aim_x - 6, "y": aim_y - 6, "w": 12, "h": 12},
        "canvasTilePolygon": [
            [aim_x - 5, aim_y - 5],
            [aim_x + 5, aim_y - 5],
            [aim_x + 5, aim_y + 5],
            [aim_x - 5, aim_y + 5],
        ],
        "onScreen": True,
        "geometryAvailable": True,
    }


def raw_tick(tick_id: int, object_id: int = 1276, *, objects=None, scene_summary: bool = False) -> dict:
    scene_objects = objects if objects is not None else [raw_scene_object(object_id, 3200 + tick_id, 3201, 100 + tick_id, 100)]
    tick = {
        "schemaVersion": "test.tick",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": f"2026-01-01T00:00:{tick_id:02d}Z",
        "gameState": "LOGGED_IN",
        "canvasWidth": 300,
        "canvasHeight": 300,
        "framePath": f"frames/frame-tick-{tick_id:08d}.jpg",
        "frameWidth": 300,
        "frameHeight": 300,
        "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0},
        "npcs": [],
        "players": [],
        "sceneObjects": scene_objects,
        "groundItems": [],
    }
    if scene_summary:
        tick["sceneCaptureSummary"] = {
            "sceneCaptureMode": "STATIC_SCENE_INDEX_DIAGNOSTIC",
            "sceneObjectsSeen": len(scene_objects),
            "sceneObjectsCaptured": len(scene_objects),
            "sceneObjectsSkippedByCap": 0,
            "sceneObjectCapHit": False,
        }
    return tick


def legacy_raw_tick(tick_id: int, object_id: int = 1276) -> dict:
    return {
        "schemaVersion": "test.tick",
        "sessionId": "fake",
        "tickId": tick_id,
        "timestampUtc": f"2026-01-01T00:00:{tick_id:02d}Z",
        "gameState": "LOGGED_IN",
        "canvasWidth": 300,
        "canvasHeight": 300,
        "framePath": f"frames/frame-tick-{tick_id:08d}.jpg",
        "frameWidth": 300,
        "frameHeight": 300,
        "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0},
        "npcs": [],
        "players": [],
        "sceneObjects": [raw_scene_object(object_id, 3200 + tick_id, 3201, 100 + tick_id, 100)],
        "groundItems": [],
    }


def make_session(root: Path, ticks: list[dict], segmented: bool = False) -> Path:
    session = root / "session"
    if segmented:
        for index, tick in enumerate(ticks, start=1):
            write_jsonl(session / "ticks" / f"ticks-{index:06d}.jsonl", [tick])
    else:
        write_jsonl(session / "ticks" / "ticks-000001.jsonl", ticks)
    (session / "manifest.json").write_text(json.dumps({"sessionId": "fake"}), encoding="utf-8")
    return session


def live_args(**overrides):
    values = {
        "profile": "woodcutting",
        "target_type": "all",
        "limit": 20,
        "window_ticks": 100,
        "poll_interval": 0.5,
        "once": True,
        "follow": False,
        "latency_mode": "realtime",
        "max_new_ticks_per_update": 1,
        "candidate_output_window": "latest",
        "drop_backlog_to_meet_budget": True,
        "drain_backlog_on_overrun": True,
        "include_ui_targets": False,
        "latest": None,
        "latest_with_frames": None,
        "exclude_ui_blocked": False,
        "summary": False,
        "clear_live_output": False,
        "max_runtime_seconds": None,
        "emit_world_targets": "candidates",
        "world_target_output_limit": 2000,
        "depleted_suppress_ticks": 20,
        "liveness_mode": "delta",
        "liveness_budget_ms": 20.0,
        "max_recently_unavailable": 1000,
        "liveness_visible_ref_scan_limit": 500,
        "target_update_ms": 100.0,
        "warn_update_ms": 250.0,
        "benchmark": False,
        "verbose": False,
        "quiet": False,
        "log_every": 1,
        "force_window_rebuild": False,
        "startup_backfill_ticks": 10,
        "no_startup_backfill": False,
        "process_existing": False,
        "no_ui_targets": True,
        "write_retry_attempts": 10,
        "write_retry_delay_ms": 0,
        "strict_writes": False,
        "target_library": str(VIEWER_DIR / "target_library.json"),
        "target_profiles": str(VIEWER_DIR / "target_profiles.json"),
    }
    values.update(overrides)
    if "liveness_mode" not in overrides:
        values["liveness_mode"] = "full" if values.get("latency_mode") == "complete" else "delta"
    return SimpleNamespace(**values)


class LiveTargetProcessorTest(unittest.TestCase):
    def test_tailer_handles_partial_line_and_later_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            tick_path = session / "ticks" / "ticks-000001.jsonl"
            first = json.dumps(raw_tick(1), separators=(",", ":"))
            second = json.dumps(raw_tick(2), separators=(",", ":"))
            tick_path.write_text(first + "\n" + second[:20], encoding="utf-8")

            tailer = live.TickJsonlTailer(session)
            records = tailer.read_new_records()
            self.assertEqual([record[2]["tickId"] for record in records], [1])
            self.assertTrue(tailer.partial_line_files())

            with tick_path.open("a", encoding="utf-8") as file:
                file.write(second[20:] + "\n")

            records = tailer.read_new_records()
            self.assertEqual([record[2]["tickId"] for record in records], [2])
            self.assertEqual(tailer.malformed_total, 0)

    def test_tailer_counts_malformed_lines_but_keeps_valid_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            tick_path = session / "ticks" / "ticks-000001.jsonl"
            tick_path.write_text(json.dumps(raw_tick(1)) + "\n{bad json}\n" + json.dumps(raw_tick(2)) + "\n", encoding="utf-8")

            tailer = live.TickJsonlTailer(session)
            records = tailer.read_new_records()
            self.assertEqual([record[2]["tickId"] for record in records], [1, 2])
            self.assertEqual(tailer.malformed_total, 1)

    def test_realtime_tailer_coalesces_before_json_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(index) for index in range(1, 31)])

            tailer = live.TickJsonlTailer(session)
            records = tailer.read_new_records(realtime=True, max_records=1)

            self.assertEqual([record[2]["tickId"] for record in records], [30])
            self.assertEqual(tailer.last_raw_records_seen, 30)
            self.assertEqual(tailer.last_raw_records_fully_parsed, 1)
            self.assertEqual(tailer.last_raw_records_skipped_before_parse, 29)
            self.assertEqual(tailer.last_coalesced_before_parse, 29)

            records = tailer.read_new_records(realtime=True, max_records=1)
            self.assertEqual(records, [])
            self.assertEqual(tailer.last_raw_records_seen, 0)

    def test_realtime_tailer_preserves_incomplete_trailing_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            tick_path = session / "ticks" / "ticks-000001.jsonl"
            first = json.dumps(raw_tick(1), separators=(",", ":"))
            second = json.dumps(raw_tick(2), separators=(",", ":"))
            tick_path.write_text(first + "\n" + second[:25], encoding="utf-8")

            tailer = live.TickJsonlTailer(session)
            records = tailer.read_new_records(realtime=True, max_records=1)
            self.assertEqual([record[2]["tickId"] for record in records], [1])
            self.assertTrue(tailer.partial_line_files())

            with tick_path.open("a", encoding="utf-8") as file:
                file.write(second[25:] + "\n")

            records = tailer.read_new_records(realtime=True, max_records=1)
            self.assertEqual([record[2]["tickId"] for record in records], [2])
            self.assertEqual(tailer.last_raw_records_skipped_before_parse, 0)

    def test_processor_handles_segmented_files_and_rolling_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2)], segmented=True)
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    window_ticks=1,
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )

            added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(added, 2)
            self.assertEqual(status["tickRangeInWindow"], [2, 2])
            self.assertEqual(status["selectedTickCount"], 1)
            self.assertGreater(status["worldTargetsBuilt"], 0)
            self.assertTrue((session / "interaction_geometry" / "live" / "live_status.json").exists())

    def test_realtime_processor_processes_newest_tick_only_when_backlog_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks = [raw_tick(index, scene_summary=True) for index in range(1, 31)]
            session = make_session(Path(tmp), ticks)
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", max_new_ticks_per_update=1))

            added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(added, 1)
            self.assertEqual(status["rawRecordsSeenThisPoll"], 30)
            self.assertEqual(status["rawRecordsFullyParsedThisPoll"], 1)
            self.assertEqual(status["rawRecordsSkippedBeforeParse"], 29)
            self.assertEqual(status["rawRecordsFullyProcessed"], 1)
            self.assertEqual(status["processedTickIds"], [30])
            self.assertEqual(status["coalescedBeforeParse"], 29)
            self.assertTrue(status["fileOffsetsAdvancedPastSkippedRecords"])
            self.assertTrue(status["sourceSceneKnowledgeComplete"])

            added, result = processor.poll_once()
            self.assertEqual(added, 0)
            self.assertEqual(result["status"]["rawRecordsSeenThisPoll"], 0)

    def test_complete_processor_processes_all_new_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(index) for index in range(1, 6)])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )

            added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(added, 5)
            self.assertEqual(status["rawRecordsFullyParsedThisPoll"], 5)
            self.assertEqual(status["rawRecordsSkippedBeforeParse"], 0)
            self.assertEqual(status["rawRecordsFullyProcessed"], 5)
            self.assertEqual(status["processedTickIds"], [1, 2, 3, 4, 5])

    def test_complete_mode_status_is_labeled_audit_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(index) for index in range(1, 4)])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertTrue(status["auditMode"])
            self.assertFalse(status["realtimeMode"])
            self.assertIn("COMPLETE AUDIT MODE", status["modeLabel"])
            self.assertFalse(status["budgetExceeded"])
            self.assertIsNotNone(status["auditDurationMillis"])

    def test_realtime_mode_status_is_labeled_realtime_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args())

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertTrue(status["realtimeMode"])
            self.assertFalse(status["auditMode"])
            self.assertIn("REALTIME MODE", status["modeLabel"])
            self.assertIsNotNone(status["realtimeDurationMillis"])

    def test_liveness_mode_flags_and_defaults(self):
        with mock.patch.object(sys, "argv", ["live_target_processor.py", "--session", "s", "--once"]):
            args = live.parse_args()
            self.assertEqual(args.liveness_mode, "delta")
            self.assertEqual(args.liveness_budget_ms, 20.0)

        with mock.patch.object(sys, "argv", ["live_target_processor.py", "--session", "s", "--once", "--latency-mode", "complete"]):
            args = live.parse_args()
            self.assertEqual(args.liveness_mode, "full")

        with mock.patch.object(sys, "argv", ["live_target_processor.py", "--session", "s", "--once", "--liveness-mode", "off", "--liveness-budget-ms", "5"]):
            args = live.parse_args()
            self.assertEqual(args.liveness_mode, "off")
            self.assertEqual(args.liveness_budget_ms, 5.0)

    def test_live_performance_summary_is_written_with_percentiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))

            _added, result = processor.poll_once()
            summary_path = session / "interaction_geometry" / "live" / "live_performance_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            self.assertEqual(summary["schema"], "live_performance_summary.v1")
            self.assertEqual(summary["sampleCount"], 1)
            self.assertEqual(summary["latestTick"], result["status"]["lastProcessedTick"])
            self.assertIsNotNone(summary["p50TotalMs"])
            self.assertIsNotNone(summary["p95TotalMs"])

    def test_performance_summary_percentiles_are_computed_from_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            processor = live.LiveTargetProcessor(session, live_args())
            for value in (10, 20, 30, 40, 50):
                processor.performance_history.append(
                    {
                        "tick": value,
                        "totalMs": value,
                        "candidateMs": 1,
                        "writeMs": 2,
                        "worldBuilt": 3,
                        "candidates": 4,
                        "budgetExceeded": value > 30,
                        "writeRetryCount": 0,
                        "writeFailureCount": 0,
                        "rawSeen": 1,
                        "processed": 1,
                        "coalesced": 0,
                    }
                )

            summary = processor.performance_summary_payload({"mode": "follow", "lastProcessedTick": 50}, "2026-01-01T00:00:00Z")

            self.assertEqual(summary["sampleCount"], 5)
            self.assertEqual(summary["p50TotalMs"], 30)
            self.assertEqual(summary["maxTotalMs"], 50)
            self.assertGreater(summary["p95TotalMs"], 40)
            self.assertEqual(summary["budgetExceededCount"], 2)

    def test_realtime_activity_inventory_liveness_use_latest_state_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(index) for index in range(1, 8)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", max_new_ticks_per_update=1))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertFalse(status["activityUsedRollingScan"])
            self.assertFalse(status["inventoryUsedRollingScan"])
            self.assertIn("livenessUpdateMillis", status["timingBreakdownMillis"])
            self.assertLessEqual(status["rawRecordsFullyProcessed"], 1)
            self.assertEqual(status["livenessMode"], "delta")
            self.assertEqual(status["livenessFullScanCount"], 0)

    def test_backlog_drain_records_after_overrun(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(target_update_ms=0.001, warn_update_ms=0.001))

            _added, first = processor.poll_once()
            self.assertTrue(first["status"]["budgetExceeded"])

            tick_path = session / "ticks" / "ticks-000001.jsonl"
            with tick_path.open("a", encoding="utf-8") as file:
                for tick_id in range(2, 7):
                    file.write(json.dumps(raw_tick(tick_id), separators=(",", ":")) + "\n")

            _added, second = processor.poll_once()
            status = second["status"]

            self.assertGreaterEqual(status["backlogDrainCount"], 4)
            self.assertEqual(status["lastBacklogDrainReason"], "previous update exceeded realtime budget")
            self.assertEqual(status["processedTickIds"], [6])

    def test_processor_writes_profile_candidate_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", limit=20))
            _added, result = processor.poll_once()

            candidates = result["candidates"]
            self.assertEqual(result["status"]["candidateCount"], 1)
            self.assertEqual(candidates[0]["liveSchema"], "live_candidate_packet.v1")
            self.assertEqual(candidates[0]["profileId"], "woodcutting")
            self.assertEqual(candidates[0]["classId"], "tree")
            self.assertIn(candidates[0]["qualityTier"], {"excellent", "good", "questionable"})

    def test_once_cli_exits_and_writes_live_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            subprocess.run(
                [
                    sys.executable,
                    str(LIVE_SCRIPT),
                    "--session",
                    str(session),
                    "--profile",
                    "woodcutting",
                    "--once",
                    "--summary",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            status = json.loads((session / "interaction_geometry" / "live" / "live_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["schema"], "live_status.v1")
            self.assertEqual(status["profile"], "woodcutting")
            self.assertEqual(status["candidateCount"], 1)

    def test_emit_world_targets_candidates_keeps_output_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            objects = [
                raw_scene_object(1276, 3201, 3201, 110, 100, name="Tree", object_key="tree-1"),
                raw_scene_object(1111, 3202, 3201, 130, 100, name="Wall", actions=[], object_key="wall-1"),
            ]
            session = make_session(Path(tmp), [raw_tick(1, objects=objects)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", limit=1, emit_world_targets="candidates"))
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["emitWorldTargetsMode"], "candidates")
            self.assertEqual(status["worldTargetsBuilt"], 1)
            self.assertEqual(status["worldTargetsWritten"], 1)
            self.assertEqual(status["worldTargetSourceRecordsConsidered"], 2)
            self.assertEqual(status["worldTargetsPrefilteredOut"], 1)

    def test_emit_world_targets_full_preserves_debug_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", emit_world_targets="full", world_target_output_limit=0))
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["emitWorldTargetsMode"], "full")
            self.assertTrue(status["fullWorldTargetOutputEnabled"])
            self.assertEqual(status["worldTargetsWritten"], status["worldTargetsBuilt"])

    def test_baseline_and_context_index_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))
            _added, _result = processor.poll_once()
            baseline = json.loads((session / "interaction_geometry" / "live" / "live_baseline_state.json").read_text(encoding="utf-8"))
            context = json.loads((session / "interaction_geometry" / "live" / "live_context_index.json").read_text(encoding="utf-8"))

            self.assertEqual(baseline["schema"], "live_baseline_state.v1")
            self.assertEqual(context["schema"], "live_context_index.v1")
            self.assertIn("tree", context["bestCandidateByClassId"])

    def test_no_startup_backfill_starts_from_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(no_startup_backfill=True))

            added = processor.initialize_from_existing()

            self.assertEqual(added, 0)
            self.assertEqual(len(processor.tick_window), 0)

    def test_startup_backfill_limits_initial_catchup(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2), raw_tick(3)])
            processor = live.LiveTargetProcessor(session, live_args(startup_backfill_ticks=2))

            added = processor.initialize_from_existing()

            self.assertEqual(added, 2)
            self.assertEqual(list(processor.tick_window.keys()), [2, 3])

    def test_live_context_query_nearest(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))
            processor.poll_once()
            result = subprocess.run(
                [
                    sys.executable,
                    str(VIEWER_DIR / "live_context_query.py"),
                    "--session",
                    str(session),
                    "--nearest",
                    "tree",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["nearest"], "tree")
            self.assertEqual(payload["candidate"]["classId"], "tree")

    def test_realtime_mode_processes_newest_tick_when_backlog_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2), raw_tick(3)])
            processor = live.LiveTargetProcessor(session, live_args(latency_mode="realtime", max_new_ticks_per_update=1, candidate_output_window="latest"))
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["processedTickIds"], [3])
            self.assertEqual(status["selectedTickRange"], [3, 3])
            self.assertEqual(status["coalescedBacklogTicks"], 2)
            self.assertEqual(status["candidateCount"], 1)

    def test_complete_mode_processes_every_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2), raw_tick(3)])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["processedTickIds"], [1, 2, 3])
            self.assertEqual(status["selectedTickRange"], [1, 3])
            self.assertEqual(status["coalescedBacklogTicks"], 0)
            self.assertEqual(status["candidateCount"], 3)

    def test_max_new_ticks_per_update_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2), raw_tick(3), raw_tick(4)])
            processor = live.LiveTargetProcessor(
                session,
                live_args(latency_mode="realtime", max_new_ticks_per_update=2, candidate_output_window="latest"),
            )
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["processedTickIds"], [3, 4])
            self.assertEqual(status["coalescedBacklogTicks"], 2)
            self.assertEqual(status["selectedTickRange"], [4, 4])

    def test_candidate_output_window_rolling_preserves_cached_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2)])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )
            processor.poll_once()
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["candidateCount"], 2)
            self.assertGreaterEqual(status["candidateTickCacheHits"], 2)

    def test_classification_cache_hits_on_repeated_static_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="stable-tree")
            second = raw_scene_object(1276, 3201, 3201, 111, 101, object_key="stable-tree")
            session = make_session(Path(tmp), [raw_tick(1, objects=[first]), raw_tick(2, objects=[second])])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="rolling",
                    drop_backlog_to_meet_budget=False,
                ),
            )
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["classificationCacheMisses"], 1)
            self.assertEqual(status["classificationCacheHits"], 1)
            self.assertEqual(status["classificationCacheSize"], 1)

    def test_source_completeness_survives_profile_limited_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            objects = [
                raw_scene_object(1276, 3201, 3201, 110, 100, name="Tree", object_key="tree-1"),
                raw_scene_object(1111, 3202, 3201, 130, 100, name="Wall", actions=[], object_key="wall-1"),
            ]
            session = make_session(Path(tmp), [raw_tick(1, objects=objects, scene_summary=True)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", limit=1))
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertTrue(status["sourceSceneKnowledgeComplete"])
            self.assertFalse(status["sourceCapHit"])
            self.assertEqual(status["sceneObjectsSeen"], 2)
            self.assertEqual(status["worldTargetsBuilt"], 1)
            self.assertEqual(status["worldTargetsPrefilteredOut"], 1)

    def test_liveness_off_marks_candidates_unknown_without_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            tick = raw_tick(1, objects=[old_tree])
            tick["sceneObjectDeltas"] = {"despawnedObjects": [old_tree], "newObjects": [], "updatedObjects": []}
            session = make_session(Path(tmp), [tick])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", liveness_mode="off", limit=20))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["livenessMode"], "off")
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["targetLiveState"], "unknown")
            self.assertEqual(status["candidatesSuppressedByLiveness"], 0)

    def test_basic_liveness_uses_direct_cache_without_full_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            session = make_session(Path(tmp), [raw_tick(1, objects=[tree])])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", liveness_mode="basic", limit=20))
            processor.mark_unavailable(["objectKey:tree-old"], 1, "test unavailable", "recently_despawned", tree, {"classId": "tree"}, ["test"])

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["livenessMode"], "basic")
            self.assertEqual(status["livenessFullScanCount"], 0)
            self.assertEqual(status["candidateCount"], 0)
            self.assertEqual(status["candidatesSuppressedByLiveness"], 1)

    def test_delta_liveness_uses_latest_tick_deltas_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            other_tree = raw_scene_object(1276, 3205, 3201, 150, 100, object_key="tree-live")
            tick1 = raw_tick(1, objects=[old_tree, other_tree])
            tick2 = raw_tick(2, objects=[old_tree, other_tree])
            tick2["sceneObjectDeltas"] = {"despawnedObjects": [old_tree], "newObjects": [], "updatedObjects": []}
            session = make_session(Path(tmp), [tick1, tick2])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    profile="woodcutting",
                    liveness_mode="delta",
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="latest",
                    drop_backlog_to_meet_budget=False,
                    limit=20,
                ),
            )

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["livenessMode"], "delta")
            self.assertEqual(status["livenessFullScanCount"], 0)
            self.assertEqual(status["candidatesSuppressedByLiveness"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "tree-live")
            self.assertEqual(result["candidates"][0]["targetLiveState"], "live_assumed")

    def test_liveness_budget_degrades_instead_of_consuming_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            objects = [raw_scene_object(1276, 3200 + index, 3201, 100 + index, 100, object_key=f"tree-{index}") for index in range(1, 8)]
            session = make_session(Path(tmp), [raw_tick(1, objects=objects)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", liveness_budget_ms=0.0001, limit=20))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertTrue(status["livenessBudgetExceeded"])
            self.assertTrue(status["livenessDegraded"])
            self.assertGreater(status["livenessCandidatesSkippedByBudget"], 0)
            self.assertGreater(status["candidateCount"], 0)

    def test_recently_unavailable_cache_prunes_expired_and_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(50)])
            processor = live.LiveTargetProcessor(session, live_args(max_recently_unavailable=2))
            for index in range(5):
                processor.recently_unavailable_targets[f"key-{index}"] = {
                    "unavailableSinceTick": index,
                    "suppressUntilTick": index,
                    "targetLiveState": "recently_despawned",
                }

            processor.prune_unavailable(50, force=True)

            self.assertLessEqual(len(processor.recently_unavailable_targets), 2)
            self.assertGreaterEqual(processor.last_recently_unavailable_pruned, 3)

    def test_latest_frame_path_is_used_without_frame_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            frame_path = session / "frames" / "frame-tick-00000001.jpg"
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_bytes(b"not-a-real-jpeg")
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))
            _added, result = processor.poll_once()
            status = result["status"]

            self.assertFalse(status["frameIndexExists"])
            self.assertTrue(status["selectedTickHasFrame"])
            self.assertEqual(Path(status["latestFramePath"]), frame_path)
            self.assertEqual(status["latestFrameTick"], 1)
            self.assertIn("timingMode", status)
            self.assertIn("classificationCacheSize", status)

    def test_target_liveness_suppresses_recently_despawned_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            new_tree = raw_scene_object(1276, 3204, 3201, 140, 100, object_key="tree-new")
            tick = raw_tick(1, objects=[old_tree, new_tree])
            tick["sceneObjectDeltas"] = {"despawnedObjects": [old_tree], "newObjects": [], "updatedObjects": []}
            session = make_session(Path(tmp), [tick])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", limit=20))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["candidatesSuppressedByLiveness"], 1)
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "tree-new")
            self.assertEqual(result["candidates"][0]["targetLiveState"], "live_assumed")

    def test_same_location_stump_suppresses_old_tree_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            stump = raw_scene_object(1342, 3201, 3201, 112, 100, name="Stump", actions=[], object_key="stump-new")
            tick = raw_tick(1, objects=[old_tree])
            tick["sceneObjectDeltas"] = {"despawnedObjects": [old_tree], "newObjects": [stump], "updatedObjects": []}
            session = make_session(Path(tmp), [tick])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", limit=20))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["candidateCount"], 0)
            self.assertGreaterEqual(status["candidatesSuppressedAsDepleted"], 1)
            self.assertGreaterEqual(status["recentlyDepletedCount"], 1)

    def test_suppression_clears_when_tree_respawns(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-old")
            stump = raw_scene_object(1342, 3201, 3201, 112, 100, name="Stump", actions=[], object_key="stump-new")
            respawned_tree = raw_scene_object(1276, 3201, 3201, 111, 100, object_key="tree-respawned")
            tick1 = raw_tick(1, objects=[old_tree])
            tick2 = raw_tick(2, objects=[old_tree])
            tick2["sceneObjectDeltas"] = {"despawnedObjects": [old_tree], "newObjects": [stump], "updatedObjects": []}
            tick3 = raw_tick(3, objects=[respawned_tree])
            tick3["sceneObjectDeltas"] = {"despawnedObjects": [stump], "newObjects": [respawned_tree], "updatedObjects": []}
            session = make_session(Path(tmp), [tick1, tick2, tick3])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    profile="woodcutting",
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="latest",
                    drop_backlog_to_meet_budget=False,
                    limit=20,
                ),
            )

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "tree-respawned")
            self.assertEqual(result["candidates"][0]["targetLiveState"], "live")
            self.assertGreaterEqual(status["candidatesRevivedAfterRespawn"], 1)

    def test_live_activity_state_reports_inventory_delta_and_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick1 = raw_tick(1)
            tick1["inventory"] = [{"slot": index, "itemId": 1511, "quantity": 1} for index in range(27)]
            tick2 = raw_tick(2)
            tick2["inventory"] = [{"slot": index, "itemId": 1511, "quantity": 1} for index in range(28)]
            session = make_session(Path(tmp), [tick1, tick2])
            processor = live.LiveTargetProcessor(
                session,
                live_args(
                    profile="woodcutting",
                    latency_mode="complete",
                    max_new_ticks_per_update=0,
                    candidate_output_window="latest",
                    drop_backlog_to_meet_budget=False,
                ),
            )

            _added, result = processor.poll_once()
            activity = result["activity"]

            self.assertEqual(activity["schema"], "live_activity_state.v1")
            self.assertTrue(activity["inventory"]["changedRecently"])
            self.assertTrue(activity["inventory"]["inventoryFull"])
            self.assertEqual(activity["woodcuttingState"]["woodcuttingState"], "inventory_full")

    def test_atomic_write_text_retries_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_index.json"
            stats = live.WriteStats()
            original_replace = Path.replace
            calls = {"count": 0}

            def flaky_replace(self, target):
                if self.name.startswith(".live_index.json") and calls["count"] < 2:
                    calls["count"] += 1
                    raise PermissionError("locked")
                return original_replace(self, target)

            with mock.patch.object(Path, "replace", flaky_replace):
                size = live.atomic_write_text(
                    path,
                    "ok",
                    options=live.WriteOptions(retry_attempts=5, retry_delay_seconds=0.0, strict=True),
                    stats=stats,
                )

            self.assertEqual(size, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), "ok")
            self.assertEqual(stats.retry_count, 2)
            self.assertEqual(stats.failure_count, 0)

    def test_atomic_write_text_non_strict_warns_after_repeated_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_index.json"
            stats = live.WriteStats()

            with mock.patch.object(Path, "replace", side_effect=PermissionError("locked")):
                size = live.atomic_write_text(
                    path,
                    "ok",
                    options=live.WriteOptions(retry_attempts=2, retry_delay_seconds=0.0, strict=False),
                    stats=stats,
                )

            self.assertEqual(size, 0)
            self.assertEqual(stats.retry_count, 1)
            self.assertEqual(stats.failure_count, 1)
            self.assertIn("PermissionError", stats.last_error)

    def test_atomic_write_text_strict_raises_after_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_index.json"
            stats = live.WriteStats()

            with mock.patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    live.atomic_write_text(
                        path,
                        "ok",
                        options=live.WriteOptions(retry_attempts=2, retry_delay_seconds=0.0, strict=True),
                        stats=stats,
                    )

            self.assertEqual(stats.retry_count, 1)
            self.assertEqual(stats.failure_count, 1)

    def test_processor_records_write_retry_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(write_retry_attempts=4))
            original_replace = Path.replace
            calls = {"count": 0}

            def flaky_replace(self, target):
                if self.name.startswith(".live_index.json") and calls["count"] < 1:
                    calls["count"] += 1
                    raise PermissionError("locked")
                return original_replace(self, target)

            with mock.patch.object(Path, "replace", flaky_replace):
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertGreaterEqual(status["writeRetryCount"], 1)
            self.assertEqual(status["writeFailureCount"], 0)

    def test_inspector_live_read_keeps_previous_data_on_transient_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            candidate = {
                "tickId": 1,
                "target": {"targetType": "sceneObject", "name": "Tree", "targetRole": "interactable", "targetCategory": "tree"},
                "geometry": {"onScreen": True, "geometryAvailable": True},
                "frame": {"path": "frames/frame-tick-00000001.jpg", "exists": True},
            }
            write_jsonl(live_dir / "live_candidates.jsonl", [candidate])
            write_jsonl(live_dir / "live_world_targets.jsonl", [])
            write_jsonl(live_dir / "live_ui_targets.jsonl", [])
            (live_dir / "live_index.json").write_text(json.dumps({"schema": "live_index.v1"}), encoding="utf-8")
            (live_dir / "live_status.json").write_text(json.dumps({"schema": "live_status.v1", "lastProcessedTick": 1}), encoding="utf-8")
            (live_dir / "live_context_index.json").write_text(json.dumps({"schema": "live_context_index.v1"}), encoding="utf-8")

            dataset = inspector.GeometryDataset(session, live=True)
            dataset.load()
            self.assertEqual(len(dataset.candidate_records), 1)

            (live_dir / "live_candidates.jsonl").write_text("{not-json", encoding="utf-8")
            dataset.load()

            self.assertEqual(len(dataset.candidate_records), 1)
            self.assertTrue(any("kept previous live inspector data" in warning for warning in dataset.warnings))


if __name__ == "__main__":
    unittest.main()

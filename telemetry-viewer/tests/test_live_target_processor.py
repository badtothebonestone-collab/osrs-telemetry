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
        "include_ui_targets": False,
        "latest": None,
        "latest_with_frames": None,
        "exclude_ui_blocked": False,
        "summary": False,
        "clear_live_output": False,
        "max_runtime_seconds": None,
        "emit_world_targets": "candidates",
        "world_target_output_limit": 2000,
        "target_update_ms": 100.0,
        "warn_update_ms": 250.0,
        "benchmark": False,
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

    def test_processor_handles_segmented_files_and_rolling_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1), raw_tick(2)], segmented=True)
            processor = live.LiveTargetProcessor(session, live_args(window_ticks=1))

            added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(added, 2)
            self.assertEqual(status["tickRangeInWindow"], [2, 2])
            self.assertEqual(status["selectedTickCount"], 1)
            self.assertGreater(status["worldTargetsBuilt"], 0)
            self.assertTrue((session / "interaction_geometry" / "live" / "live_status.json").exists())

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

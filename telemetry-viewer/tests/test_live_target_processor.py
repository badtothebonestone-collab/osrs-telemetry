import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
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


def compact_packet(packet_type: str, tick: int, sequence: int, payload: dict) -> dict:
    return {
        "schema": "osrs_telemetry_live_packet.v1",
        "packetType": packet_type,
        "sessionId": "fake",
        "tick": tick,
        "sequence": sequence,
        "timestampUtc": f"2026-01-01T00:00:{tick:02d}Z",
        "payload": payload,
    }


def compact_scene_object(source: dict) -> dict:
    return {
        "objectKey": source.get("objectKey"),
        "targetType": "sceneObject",
        "id": source.get("id"),
        "hash": source.get("hash"),
        "name": source.get("objectName"),
        "nameSource": source.get("objectNameSource"),
        "actions": source.get("actions"),
        "kind": source.get("kind"),
        "layer": source.get("kind"),
        "worldX": source.get("worldX"),
        "worldY": source.get("worldY"),
        "plane": source.get("plane"),
        "sceneX": source.get("sceneX"),
        "sceneY": source.get("sceneY"),
        "localX": source.get("localX"),
        "localY": source.get("localY"),
        "present": source.get("present", True),
        "source": "test",
        "firstSeenTick": 1,
        "lastSeenTick": 1,
        "lastUpdatedTick": 1,
        "onScreen": source.get("onScreen"),
        "geometryAvailable": source.get("geometryAvailable"),
        "aimPoint": {
            "canvasX": source.get("canvasLocation", {}).get("x"),
            "canvasY": source.get("canvasLocation", {}).get("y"),
            "source": "canvasLocation",
        },
        "geometrySummary": {
            "hasClickbox": source.get("clickboxBounds") is not None,
            "hasConvexHull": source.get("convexHullBounds") is not None,
            "hasCanvasTilePolygon": source.get("canvasTilePolygon") is not None,
            "clickboxBounds": source.get("clickboxBounds"),
            "convexHullBounds": source.get("convexHullBounds"),
        },
    }


def compact_collision_window(tick_id: int, *, player_scene_x: int = 10, player_scene_y: int = 10) -> dict:
    min_scene_x = 0
    min_scene_y = 0
    width = 21
    height = 21
    return {
        "tick": tick_id,
        "plane": 0,
        "playerSceneX": player_scene_x,
        "playerSceneY": player_scene_y,
        "windowRadius": 10,
        "minSceneX": min_scene_x,
        "maxSceneX": min_scene_x + width - 1,
        "minSceneY": min_scene_y,
        "maxSceneY": min_scene_y + height - 1,
        "width": width,
        "height": height,
        "encoding": "json-rows-int-flags",
        "flags": [[0 for _x in range(width)] for _y in range(height)],
        "collisionWindowTileCount": width * height,
        "collisionWindowHash": f"window-{tick_id}",
        "windowHash": f"window-{tick_id}",
        "mapWidth": 104,
        "mapHeight": 104,
        "collisionKnown": True,
        "generatedFromPlane": 0,
        "warnings": [],
    }


def write_compact_packets(session: Path, tick_objects: dict[int, list[dict]]) -> None:
    live_dir = session / "live_packets"
    live_dir.mkdir(parents=True, exist_ok=True)
    segment = live_dir / "live-000001.ndjson"
    sequence = 0
    counts = {}
    first_tick = min(tick_objects) if tick_objects else None
    last_tick = max(tick_objects) if tick_objects else None
    with segment.open("w", encoding="utf-8") as file:
        for tick_id, objects in sorted(tick_objects.items()):
            visible = [compact_scene_object(source) for source in objects]
            packets = [
                compact_packet(
                    "live_baseline_packet.v1",
                    tick_id,
                    sequence + 1,
                    {
                        "tick": tick_id,
                        "gameState": "LOGGED_IN",
                        "player": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10, "localX": 1280, "localY": 1280, "animation": -1, "poseAnimation": -1},
                        "cameraViewport": {"canvasWidth": 300, "canvasHeight": 300},
                        "latestFramePath": f"frames/frame-tick-{tick_id:08d}.jpg",
                        "sceneCaptureMode": "STATIC_SCENE_INDEX_DIAGNOSTIC",
                        "source": {
                            "sourceSceneKnowledgeComplete": True,
                            "sourceCapHit": False,
                            "sceneObjectsSeen": len(objects),
                            "sceneObjectsCaptured": len(objects),
                            "sceneObjectsSkippedByCap": 0,
                            "sceneObjectCapHit": False,
                        },
                    },
                ),
                compact_packet(
                    "live_scene_delta_packet.v1",
                    tick_id,
                    sequence + 2,
                    {
                        "sceneIndexSummary": {"indexEnabled": True, "indexObjectCount": len(objects), "presentObjectCount": len(objects), "indexCapHit": False},
                        "sceneCaptureSummary": {
                            "sceneCaptureMode": "STATIC_SCENE_INDEX_DIAGNOSTIC",
                            "sceneObjectsSeen": len(objects),
                            "sceneObjectsCaptured": len(objects),
                            "sceneObjectsSkippedByCap": 0,
                            "sceneObjectCapHit": False,
                        },
                        "sceneObjectDeltas": {"newObjects": [], "updatedObjects": [], "despawnedObjects": []},
                    },
                ),
                compact_packet(
                    "live_projection_packet.v1",
                    tick_id,
                    sequence + 3,
                    {
                        "sceneProjectionSummary": {"projectionStateHash": f"h-{tick_id}", "visibleObjectCount": len(objects), "onScreenObjectCount": len(objects), "geometryAvailableCount": len(objects)},
                        "projectionStateHash": f"h-{tick_id}",
                        "refreshMode": "VISIBLE_AND_NEARBY",
                        "visibleObjectRefs": visible,
                    },
                ),
                compact_packet(
                    "live_inventory_packet.v1",
                    tick_id,
                    sequence + 4,
                    {"inventory": {"known": True, "freeSlots": 28, "filledSlots": 0, "itemCount": 0, "signature": "", "items": []}, "equipment": {"known": True, "items": []}},
                ),
                compact_packet("live_activity_packet.v1", tick_id, sequence + 5, {"animation": -1, "poseAnimation": -1, "movementKnown": False}),
                compact_packet(
                    "live_navigation_packet.v1",
                    tick_id,
                    sequence + 6,
                    {
                        "tick": tick_id,
                        "plane": 0,
                        "player": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10, "localX": 1280, "localY": 1280},
                        "collision": {
                            "collisionKnown": True,
                            "planeKnown": True,
                            "plane": 0,
                            "mapWidth": 104,
                            "mapHeight": 104,
                            "blockedMovementTileCount": 12,
                            "blockedFullTileCount": 3,
                            "collisionHash": f"collision-{tick_id}",
                        },
                        "bounds": {"sceneMinX": 0, "sceneMaxX": 103, "sceneMinY": 0, "sceneMaxY": 103},
                        "source": {"worldViewId": 0, "topLevelWorldView": True},
                    },
                ),
                compact_packet(
                    "live_collision_window_packet.v1",
                    tick_id,
                    sequence + 7,
                    compact_collision_window(tick_id),
                ),
                compact_packet("live_writer_health_packet.v1", tick_id, sequence + 8, {"rawWriterQueueDepth": 0, "droppedRawRecords": 0, "compactLiveEnabled": True}),
            ]
            sequence += 8
            for packet in packets:
                counts[packet["packetType"]] = counts.get(packet["packetType"], 0) + 1
                file.write(json.dumps(packet, separators=(",", ":")) + "\n")
    (live_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")
    (live_dir / "live_packet_index.json").write_text(
        json.dumps(
            {
                "schema": "live_packet_index.v1",
                "activeSegment": "live-000001.ndjson",
                "latestSegment": "live-000001.ndjson",
                "segments": [
                    {
                        "path": "live-000001.ndjson",
                        "firstSequence": 1,
                        "lastSequence": sequence,
                        "firstTick": first_tick,
                        "lastTick": last_tick,
                        "bytes": segment.stat().st_size,
                        "packetCountsByType": counts,
                    }
                ],
                "latestTick": last_tick,
                "latestSequence": sequence,
            }
        ),
        encoding="utf-8",
    )


def live_args(**overrides):
    values = {
        "input_source": "raw-ticks",
        "compare_input_sources": False,
        "require_compact_packets": False,
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
        "event_limit": 200,
        "overlay_debug_target_limit": 50,
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

    def test_input_source_raw_ticks_preserves_current_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(input_source="raw-ticks"))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["inputSourceRequested"], "raw-ticks")
            self.assertEqual(status["inputSourceActive"], "raw-ticks")
            self.assertTrue(status["rawTicksAvailable"])
            self.assertEqual(status["candidateCount"], 1)

    def test_input_source_compact_packets_reads_synthetic_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="compact-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})
            processor = live.LiveTargetProcessor(session, live_args(input_source="compact-packets"))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["inputSourceActive"], "compact-packets")
            self.assertEqual(status["compactPacketsSeen"], 8)
            self.assertEqual(status["compactPacketsProcessed"], 8)
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "compact-tree")
            self.assertEqual(result["candidates"][0]["classId"], "tree")
            self.assertTrue(result["navigation"]["collisionKnown"])
            self.assertTrue(result["navigation"]["collisionWindowAvailable"])
            self.assertTrue(result["candidates"][0]["navigation"]["collisionKnown"])
            self.assertTrue(result["candidates"][0]["navigation"]["collisionWindowAvailable"])
            self.assertEqual(result["candidates"][0]["navigation"]["directReachability"], "reachable")
            self.assertTrue(status["sourceSceneKnowledgeComplete"])

    def test_input_source_auto_prefers_compact_packets_when_index_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="compact-tree")
            session = make_session(Path(tmp), [raw_tick(1, object_id=1111)])
            write_compact_packets(session, {2: [tree]})
            processor = live.LiveTargetProcessor(session, live_args(input_source="auto"))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["inputSourceActive"], "compact-packets")
            self.assertEqual(status["processedTickIds"], [2])
            self.assertEqual(result["candidates"][0]["objectKey"], "compact-tree")

    def test_input_source_auto_falls_back_to_raw_ticks_when_compact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(input_source="auto"))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["inputSourceActive"], "raw-ticks")
            self.assertIn("falling back", status["inputFallbackReason"])
            self.assertEqual(status["candidateCount"], 1)

    def test_require_compact_packets_fails_when_packets_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            result = subprocess.run(
                [
                    sys.executable,
                    str(LIVE_SCRIPT),
                    "--session",
                    str(session),
                    "--require-compact-packets",
                    "--once",
                    "--quiet",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Compact live packets are required", result.stdout)

    def test_require_compact_packets_fails_when_packets_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-1")]})
            stale_time = time.time() - 3600
            os.utime(session / "live_packets" / "live-000001.ndjson", (stale_time, stale_time))
            result = subprocess.run(
                [
                    sys.executable,
                    str(LIVE_SCRIPT),
                    "--session",
                    str(session),
                    "--input-source",
                    "compact-packets",
                    "--require-compact-packets",
                    "--once",
                    "--quiet",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Compact live packets are required", result.stdout)

    def test_require_compact_packets_passes_when_packets_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-1")]})
            result = subprocess.run(
                [
                    sys.executable,
                    str(LIVE_SCRIPT),
                    "--session",
                    str(session),
                    "--input-source",
                    "compact-packets",
                    "--require-compact-packets",
                    "--profile",
                    "woodcutting",
                    "--once",
                    "--quiet",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compact_packet_realtime_coalesces_by_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            write_compact_packets(
                session,
                {
                    1: [raw_scene_object(1276, 3201, 3201, 110, 100, object_key="tree-1")],
                    2: [raw_scene_object(1276, 3202, 3201, 120, 100, object_key="tree-2")],
                    3: [raw_scene_object(1276, 3203, 3201, 130, 100, object_key="tree-3")],
                },
            )
            processor = live.LiveTargetProcessor(session, live_args(input_source="compact-packets", max_new_ticks_per_update=1))

            _added, result = processor.poll_once()
            status = result["status"]

            self.assertEqual(status["inputSourceActive"], "compact-packets")
            self.assertEqual(status["rawRecordsSeenThisPoll"], 3)
            self.assertEqual(status["rawRecordsFullyProcessed"], 1)
            self.assertEqual(status["coalescedBacklogTicks"], 2)
            self.assertEqual(status["compactPacketsCoalesced"], 16)
            self.assertEqual(status["processedTickIds"], [3])

    def test_compact_packet_tailer_preserves_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [])
            live_dir = session / "live_packets"
            live_dir.mkdir(parents=True, exist_ok=True)
            segment = live_dir / "live-000001.ndjson"
            first = compact_packet("live_baseline_packet.v1", 1, 1, {"tick": 1, "player": {}})
            second = compact_packet("live_baseline_packet.v1", 2, 2, {"tick": 2, "player": {}})
            second_text = json.dumps(second, separators=(",", ":"))
            segment.write_text(json.dumps(first, separators=(",", ":")) + "\n" + second_text[:20], encoding="utf-8")
            (live_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")

            tailer = live.CompactPacketTailer(session)
            records = tailer.read_new_records(realtime=True, max_records=1)

            self.assertEqual([record[2]["tickId"] for record in records], [1])
            self.assertTrue(tailer.partial_line_files())

            with segment.open("a", encoding="utf-8") as file:
                file.write(second_text[20:] + "\n")

            records = tailer.read_new_records(realtime=True, max_records=1)
            self.assertEqual([record[2]["tickId"] for record in records], [2])

    def test_compare_input_sources_reports_warning_or_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="same-tree")
            session = make_session(Path(tmp), [raw_tick(1, objects=[tree])])
            write_compact_packets(session, {1: [tree]})
            output = StringIO()

            with redirect_stdout(output):
                code = live.compare_input_sources(session, live_args(input_source="auto", latest=1))

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertIn(payload["status"], {"PASS", "WARN"})
            self.assertTrue(payload["rawTicks"]["available"])
            self.assertTrue(payload["compactPackets"]["available"])

    def test_inventory_summary_recomputes_compact_free_slots(self):
        tick = {
            "inventory": {
                "known": True,
                "freeSlots": 1,
                "filledSlots": 16,
                "itemCount": 723,
                "items": [{"slot": 0, "itemId": 1511, "quantity": 700}, {"slot": 1, "itemId": 995, "quantity": 23}],
            }
        }
        summary = live.inventory_summary(tick)
        self.assertEqual(summary["inventorySlotCount"], 28)
        self.assertEqual(summary["filledSlots"], 16)
        self.assertEqual(summary["freeSlots"], 12)
        self.assertEqual(summary["itemCount"], 723)
        self.assertEqual(summary["totalItemQuantity"], 723)

    def test_inventory_full_calculation(self):
        tick = {"inventory": {"known": True, "slotCount": 28, "filledSlots": 28, "freeSlots": 0, "itemCount": 1200, "items": []}}
        state = live.inventory_state_for_ticks([tick], tick)
        self.assertEqual(state["inventorySlotCount"], 28)
        self.assertEqual(state["freeSlots"], 0)
        self.assertTrue(state["inventoryFull"])

    def test_compact_packet_inventory_mapping(self):
        packets = [
            compact_packet("live_baseline_packet.v1", 4, 1, {"player": {"worldX": 3200, "worldY": 3200, "plane": 0}}),
            compact_packet(
                "live_inventory_packet.v1",
                4,
                2,
                {
                    "inventory": {
                        "known": True,
                        "freeSlots": 1,
                        "filledSlots": 16,
                        "itemCount": 723,
                        "items": [{"slot": 0, "itemId": 1511, "quantity": 700}, {"slot": 1, "itemId": 995, "quantity": 23}],
                    },
                    "equipment": {"known": True, "items": []},
                },
            ),
        ]
        tick = live.compact_packets_to_tick(packets)
        state = live.inventory_state_for_ticks([tick], tick)
        self.assertEqual(state["inventorySlotCount"], 28)
        self.assertEqual(state["freeSlots"], 12)
        self.assertEqual(state["filledSlots"], 16)
        self.assertEqual(state["totalItemQuantity"], 723)

    def test_compact_inventory_delta_packet_marks_recent_change(self):
        packets = [
            compact_packet("live_baseline_packet.v1", 5, 1, {"player": {"worldX": 3200, "worldY": 3200, "plane": 0}}),
            compact_packet(
                "live_inventory_packet.v1",
                5,
                2,
                {
                    "inventoryDeltaTrackingAvailable": True,
                    "inventory": {
                        "known": True,
                        "slotCount": 28,
                        "freeSlots": 26,
                        "filledSlots": 2,
                        "itemCount": 2,
                        "totalItemQuantity": 2,
                        "signature": "after",
                        "items": [{"slot": 0, "itemId": 1511, "quantity": 2}],
                    },
                    "equipment": {"known": True, "items": []},
                },
            ),
            compact_packet(
                "live_inventory_delta_packet.v1",
                5,
                3,
                {
                    "tick": 5,
                    "inventorySignatureBefore": "before",
                    "inventorySignatureAfter": "after",
                    "quantityChanges": [{"itemId": 1511, "beforeQuantity": 1, "afterQuantity": 2, "delta": 1, "changeType": "itemAdded"}],
                    "changedSlots": [{"slot": 0, "beforeItemId": 1511, "beforeQuantity": 1, "afterItemId": 1511, "afterQuantity": 2}],
                    "freeSlotsBefore": 27,
                    "freeSlotsAfter": 26,
                    "filledSlotsBefore": 1,
                    "filledSlotsAfter": 2,
                    "inventoryFull": False,
                    "generatedFromItemContainerChanged": False,
                },
            ),
        ]
        tick = live.compact_packets_to_tick(packets)
        state = live.inventory_state_for_ticks([tick], tick)
        self.assertTrue(state["changedThisTick"])
        self.assertTrue(state["changedRecently"])
        self.assertTrue(state["inventoryDeltaTrackingKnown"])
        self.assertEqual(state["recentItemDeltas"][0]["changes"][0]["itemId"], 1511)

    def test_activity_state_reports_recent_activity_events(self):
        tick = {
            "tickId": 8,
            "localPlayer": {"animation": 879, "poseAnimation": 808},
            "_activityPacket": {
                "animation": 879,
                "previousAnimation": -1,
                "poseAnimation": 808,
                "previousPoseAnimation": 808,
                "changedFields": ["animation"],
                "activityChanged": True,
                "eventSource": "gameTickActivitySnapshot",
            },
            "inventory": {"known": True, "slotCount": 28, "freeSlots": 20, "filledSlots": 8, "items": []},
        }
        activity = live.activity_state_for(tick, [tick], [], {}, "2026-01-01T00:00:00Z", 0)
        self.assertEqual(activity["activityState"]["apparentState"], "animating")
        self.assertEqual(activity["recentActivityEvents"][0]["changedFields"], ["animation"])

    def test_live_event_timeline_does_not_duplicate_unchanged_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))
            candidate = {
                "classId": "tree",
                "name": "Tree",
                "id": 1276,
                "objectKey": "tree-a",
                "worldX": 3201,
                "worldY": 3201,
                "plane": 0,
                "distanceTiles": 1,
                "targetLiveState": "live_assumed",
                "navigation": {"directReachability": "reachable"},
            }
            inventory = {"signature": "a", "freeSlots": 20, "inventoryFull": False}
            activity = {"activityState": {"apparentState": "idle"}, "player": {"animation": -1}}
            status = {"latestTick": 1, "warningCount": 0, "budgetExceeded": False, "writeFailureCount": 0, "sourceCapHit": False}

            processor.emit_timeline_events(latest_tick_record={"tickId": 1}, candidates=[candidate], inventory_state=inventory, activity=activity, status=status, processed_at="2026-01-01T00:00:00Z")
            count = len(processor.event_timeline)
            processor.emit_timeline_events(latest_tick_record={"tickId": 2}, candidates=[candidate], inventory_state=inventory, activity=activity, status=status, processed_at="2026-01-01T00:00:01Z")
            self.assertEqual(len(processor.event_timeline), count)

    def test_live_event_timeline_records_candidate_liveness_and_inventory_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting"))
            base_candidate = {
                "classId": "tree",
                "name": "Tree",
                "id": 1276,
                "objectKey": "tree-a",
                "worldX": 3201,
                "worldY": 3201,
                "plane": 0,
                "distanceTiles": 1,
                "targetLiveState": "live_assumed",
                "navigation": {"directReachability": "reachable"},
            }
            activity = {"activityState": {"apparentState": "idle"}, "player": {"animation": -1}}
            status = {"latestTick": 1, "warningCount": 0, "budgetExceeded": False, "writeFailureCount": 0, "sourceCapHit": False}
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 1},
                candidates=[base_candidate],
                inventory_state={"signature": "a", "freeSlots": 20, "inventoryFull": False},
                activity=activity,
                status=status,
                processed_at="2026-01-01T00:00:00Z",
            )
            depleted = dict(base_candidate, targetLiveState="depleted_or_stump")
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 2},
                candidates=[depleted],
                inventory_state={
                    "signature": "b",
                    "freeSlots": 19,
                    "inventoryFull": False,
                    "recentItemDeltas": [{"toTick": 2, "changes": [{"itemId": 1511, "delta": 1}]}],
                },
                activity=activity,
                status=status,
                processed_at="2026-01-01T00:00:01Z",
            )
            event_types = [event["eventType"] for event in processor.event_timeline]
            self.assertIn("target_depleted", event_types)
            self.assertIn("inventory_changed", event_types)

    def test_live_event_timeline_is_bounded_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", event_limit=2))
            candidate = {
                "classId": "tree",
                "name": "Tree",
                "id": 1276,
                "objectKey": "tree-a",
                "worldX": 3201,
                "worldY": 3201,
                "plane": 0,
                "distanceTiles": 1,
                "targetLiveState": "live_assumed",
                "navigation": {"directReachability": "reachable"},
            }
            activity = {"activityState": {"apparentState": "idle"}, "player": {"animation": -1}}
            status = {"latestTick": 1, "warningCount": 0, "budgetExceeded": False, "writeFailureCount": 0, "sourceCapHit": False}
            for index in range(5):
                processor.emit_timeline_events(
                    latest_tick_record={"tickId": index + 1},
                    candidates=[dict(candidate, objectKey=f"tree-{index}")],
                    inventory_state={"signature": str(index), "freeSlots": 20 - index, "inventoryFull": False},
                    activity=activity,
                    status=status,
                    processed_at=f"2026-01-01T00:00:0{index}Z",
                )
            self.assertLessEqual(len(processor.event_timeline), 2)

            _added, result = processor.poll_once()
            events_path = session / "interaction_geometry" / "live" / "live_event_timeline.jsonl"
            self.assertTrue(events_path.exists())
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertLessEqual(len(events), 2)
            self.assertIn("events", result)

    def test_overlay_debug_state_caps_targets_and_uses_compact_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            candidates = []
            for index in range(5):
                candidates.append(
                    {
                        "classId": "tree",
                        "name": "Tree",
                        "id": 1276,
                        "objectKey": f"tree-{index}",
                        "worldX": 3200 + index,
                        "worldY": 3201,
                        "plane": 0,
                        "sceneX": index,
                        "sceneY": 1,
                        "onScreen": True,
                        "geometryAvailable": True,
                        "qualityTier": "excellent",
                        "qualityScore": 1.0,
                        "targetLiveState": "live_assumed",
                        "aimPointContext": {"canvasX": 100 + index, "canvasY": 120, "source": "test"},
                        "geometrySummary": {"bounds": {"x": 95 + index, "y": 115, "width": 10, "height": 10}},
                        "navigation": {"directReachability": "reachable", "reachabilityConfidence": 0.9},
                    }
                )

            state = live.overlay_debug_state_for(
                session,
                live_args(profile="woodcutting", overlay_debug_target_limit=2),
                {"tickId": 1, "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10}},
                candidates,
                {"collisionWindowAvailable": True, "collisionWindowRadius": 24, "playerSceneX": 10, "playerSceneY": 10},
                {"budgetExceeded": False, "writeFailureCount": 0, "warnings": []},
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(state["schema"], "telemetry_overlay_debug_state.v1")
            self.assertEqual(state["summary"]["candidateCount"], 5)
            self.assertEqual(state["summary"]["targetsWritten"], 2)
            self.assertEqual(state["summary"]["targetsSuppressedByCap"], 3)
            self.assertEqual(len(state["targets"]), 2)
            self.assertEqual(state["targets"][0]["aimPoint"]["canvasX"], 100)
            self.assertEqual(state["targets"][0]["bounds"]["width"], 10)
            self.assertTrue(state["safety"]["readOnly"])

    def test_overlay_debug_state_is_written_by_processor(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", overlay_debug_target_limit=1))

            _added, result = processor.poll_once()
            overlay_path = session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            self.assertTrue(overlay_path.exists())
            state = json.loads(overlay_path.read_text(encoding="utf-8"))
            self.assertEqual(state["schema"], "telemetry_overlay_debug_state.v1")
            self.assertLessEqual(len(state["targets"]), 1)
            self.assertIn("overlayDebug", result)
            text = json.dumps(state)
            for forbidden in ("clickCommand", "mouse", "keyboard", "menu", "execute", "automation", "actionCommand"):
                self.assertNotIn(forbidden, text)

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
            self.assertEqual(args.input_source, "auto")

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

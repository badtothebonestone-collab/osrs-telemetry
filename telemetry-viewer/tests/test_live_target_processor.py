import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = VIEWER_DIR / "live_target_processor.py"
sys.path.insert(0, str(VIEWER_DIR))

import live_target_processor as live
import diagnose_plugin_snapshot as diagnose_snapshot
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
        "clickboxPolygon": source.get("clickboxPolygon"),
        "convexHullPolygon": source.get("convexHullPolygon"),
        "canvasTilePolygon": source.get("canvasTilePolygon"),
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


def write_compact_packets(session: Path, tick_objects: dict[int, list[dict]], *, include_watch_values: bool = False) -> None:
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
                compact_packet(
                    "live_writer_health_packet.v1",
                    tick_id,
                    sequence + 8,
                    {
                        "recordingMode": "LIVE_COMPACT_ONLY",
                        "rawTickRecordingEnabled": False,
                        "rawEventRecordingEnabled": False,
                        "frameRecordingEnabled": False,
                        "compactPacketRecordingEnabled": True,
                        "rawTicksWritten": 0,
                        "rawTicksSuppressedByMode": tick_id,
                        "rawEventsWritten": 0,
                        "rawEventsSuppressedByMode": 0,
                        "framesWritten": 0,
                        "framesSuppressedByMode": tick_id,
                        "rawWriterQueueDepth": 0,
                        "droppedRawRecords": 0,
                        "compactLiveEnabled": True,
                    },
                ),
            ]
            if include_watch_values:
                packets.append(
                    compact_packet(
                        "live_watch_values_packet.v1",
                        tick_id,
                        sequence + 9,
                        {
                            "activeWatchCount": 1,
                            "rejectedWatchCount": 0,
                            "watchBudgetExceeded": False,
                            "values": [
                                {
                                    "alias": "test_varbit",
                                    "type": "varbit",
                                    "id": 123,
                                    "value": 7,
                                    "changed": True,
                                    "latestTick": tick_id,
                                    "source": "synthetic",
                                }
                            ],
                        },
                    )
                )
            sequence += len(packets)
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


def compact_packet_lines(session: Path, tick_objects: dict[int, list[dict]]) -> list[str]:
    write_compact_packets(session, tick_objects)
    segment = session / "live_packets" / "live-000001.ndjson"
    return [line + "\n" for line in segment.read_text(encoding="utf-8").splitlines()]


PACKET_TYPE_TO_SNAPSHOT_NEED = {
    "live_baseline_packet.v1": "baseline",
    "live_scene_delta_packet.v1": "scene_delta",
    "live_projection_packet.v1": "projection",
    "live_inventory_packet.v1": "inventory",
    "live_inventory_delta_packet.v1": "inventory_delta",
    "live_activity_packet.v1": "activity",
    "live_navigation_packet.v1": "navigation",
    "live_collision_window_packet.v1": "collision_window",
    "live_writer_health_packet.v1": "writer_health",
    "live_watch_values_packet.v1": "watch_values",
}


def snapshot_response_from_lines(lines: list[str], *, omit_needs=None, status: str = "PASS", warnings=None) -> dict:
    omit_needs = set(omit_needs or [])
    payloads = {}
    latest_tick = None
    latest_sequence = None
    latest_tick_by_type = {}
    latest_sequence_by_type = {}
    for line in lines:
        packet = json.loads(line)
        packet_type = packet.get("packetType")
        need = PACKET_TYPE_TO_SNAPSHOT_NEED.get(packet_type)
        if not need or need in omit_needs:
            continue
        payloads[need] = packet.get("payload") or {}
        tick = packet.get("tick")
        sequence = packet.get("sequence")
        if isinstance(tick, int):
            latest_tick = tick if latest_tick is None else max(latest_tick, tick)
            latest_tick_by_type[packet_type] = tick
        if isinstance(sequence, int):
            latest_sequence = sequence if latest_sequence is None else max(latest_sequence, sequence)
            latest_sequence_by_type[packet_type] = sequence
    missing = sorted(set(live.PLUGIN_SNAPSHOT_REQUIRED_NEEDS) - set(payloads))
    response_warnings = list(warnings or [])
    if missing and status == "PASS":
        status = "WARN"
    return {
        "schema": "plugin_snapshot_response.v1",
        "requestId": "test",
        "generatedAtUtc": "2026-01-01T00:00:01Z",
        "latestTick": latest_tick,
        "status": status,
        "freshness": {"latestTick": latest_tick, "fresh": True, "ageTicksByNeed": {key: 0 for key in payloads}},
        "payloads": payloads,
        "missingCapabilities": missing,
        "warnings": response_warnings,
        "serviceTimingMillis": 1.0,
        "cacheHealth": {
            "liveCacheLatestSequence": latest_sequence,
            "liveCacheLatestTickByType": latest_tick_by_type,
            "liveCacheLatestSequenceByType": latest_sequence_by_type,
        },
    }


class NdjsonTestServer:
    def __init__(self, chunks: list[bytes], *, pause_after_first: bool = False):
        self.chunks = chunks
        self.pause_after_first = pause_after_first
        self.continue_event = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.ready.wait(timeout=1.0)
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.continue_event.set()
        try:
            self.socket.close()
        except OSError:
            pass
        self.thread.join(timeout=1.0)

    def _run(self):
        self.ready.set()
        try:
            conn, _addr = self.socket.accept()
        except OSError:
            self.done.set()
            return
        with conn:
            for index, chunk in enumerate(self.chunks):
                try:
                    conn.sendall(chunk)
                except OSError:
                    break
                if index == 0 and self.pause_after_first:
                    self.continue_event.wait(timeout=2.0)
        self.done.set()


def live_args(**overrides):
    values = {
        "input_source": "raw-ticks",
        "compact_stream_host": "127.0.0.1",
        "compact_stream_port": 1,
        "compact_stream_timeout": 0.1,
        "plugin_snapshot_host": "127.0.0.1",
        "plugin_snapshot_port": 8893,
        "plugin_snapshot_token": "",
        "plugin_snapshot_timeout": 0.1,
        "plugin_snapshot_tier": "hot",
        "plugin_snapshot_max_projection_refs": None,
        "plugin_snapshot_max_age_ticks": 5,
        "plugin_snapshot_include_geometry": False,
        "plugin_snapshot_response_mode": "compact",
        "plugin_snapshot_projection_field_mode": "compact",
        "plugin_snapshot_fallback": "none",
        "plugin_snapshot_auto_escalate": False,
        "plugin_snapshot_min_candidates": 1,
        "auto_prefer_plugin_snapshot": False,
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
        "event_timeline_limit": 200,
        "disable_event_timeline": False,
        "overlay_debug_target_limit": 50,
        "overlay_debug_hull_limit": 10,
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
    if "event_timeline_limit" not in overrides and "event_limit" in overrides:
        values["event_timeline_limit"] = values["event_limit"]
    return SimpleNamespace(**values)


@unittest.skip("legacy live_target_processor file-source suite retired with live packet archive removal")
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

    def test_live_inspector_default_prefers_session_with_live_overlay_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            new_empty = root / "new-empty"
            old_live = root / "old-live"
            new_empty.mkdir(parents=True)
            (new_empty / "manifest.json").write_text("{}", encoding="utf-8")
            overlay = old_live / "interaction_geometry" / "live" / "overlay_debug_state.json"
            overlay.parent.mkdir(parents=True)
            overlay.write_text("{}", encoding="utf-8")

            resolved = inspector.resolve_session(
                SimpleNamespace(
                    session=None,
                    sessions_dir=str(root),
                    live=True,
                    from_daemon=False,
                    daemon_url="http://127.0.0.1:1",
                    daemon_timeout=0.01,
                )
            )

            self.assertEqual(resolved, old_live)

    def test_live_processor_can_resolve_daemon_session_instead_of_empty_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon-active"
            new_empty = root / "new-empty"
            write_jsonl(daemon_session / "ticks" / "ticks-000001.jsonl", [raw_tick(1)])
            new_empty.mkdir(parents=True)
            (new_empty / "manifest.json").write_text('{"sessionId":"new-empty"}', encoding="utf-8")
            os.utime(new_empty / "manifest.json", (4102444800, 4102444800))

            args = live_args(
                session=None,
                latest_session=True,
                from_daemon=True,
                daemon_url="http://127.0.0.1:8890",
                daemon_timeout=0.01,
                sessions_dir=str(root),
            )
            with mock.patch("live_session_core.fetch_json", return_value={"sessionPath": str(daemon_session)}):
                resolved = live.resolve_session(args)

            self.assertEqual(resolved, daemon_session.resolve())

    def test_live_inspector_uses_overlay_debug_state_when_live_candidates_are_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            overlay_path = session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            overlay_marker = {
                "name": "Tree",
                "classId": "tree",
                "targetType": "sceneObject",
                "targetKey": "tree-a",
                "objectKey": "tree-a",
                "selected": True,
                "role": "selected",
                "id": 1276,
                "worldX": 3200,
                "worldY": 3201,
                "plane": 0,
                "tick": 10,
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"x": 100, "y": 120},
                "bounds": {"x": 90, "y": 100, "w": 20, "h": 40},
            }
            write_jsonl(session / "ticks" / "ticks-000001.jsonl", [raw_tick(10)])
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            overlay_path.write_text(
                json.dumps({"schema": "telemetry_overlay_debug_state.v1", "latestTick": 10, "targets": [overlay_marker]}),
                encoding="utf-8",
            )

            dataset = inspector.GeometryDataset(session, live=True)
            dataset.load()
            summary = dataset.summary()

            self.assertEqual(summary["sourceType"], "overlay_debug_state")
            self.assertEqual(summary["sourcePath"], str(overlay_path))
            self.assertEqual(summary["overlayMarkerCount"], 1)
            self.assertTrue(summary["selectedTargetPresent"])
            self.assertEqual(summary["targetCandidateCount"], 1)
            self.assertNotIn("Run python telemetry-viewer\\live_target_processor.py first.", summary["messages"])

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
            self.assertEqual(status["recordingMode"], "LIVE_COMPACT_ONLY")
            self.assertFalse(status["rawTickRecordingEnabled"])
            self.assertFalse(status["rawEventRecordingEnabled"])
            self.assertFalse(status["frameRecordingEnabled"])
            self.assertEqual(status["rawTicksWritten"], 0)
            self.assertEqual(result["candidates"][0]["objectKey"], "compact-tree")
            self.assertEqual(result["candidates"][0]["classId"], "tree")
            self.assertTrue(result["navigation"]["collisionKnown"])
            self.assertTrue(result["navigation"]["collisionWindowAvailable"])
            self.assertTrue(result["candidates"][0]["navigation"]["collisionKnown"])
            self.assertTrue(result["candidates"][0]["navigation"]["collisionWindowAvailable"])
            self.assertEqual(result["candidates"][0]["navigation"]["directReachability"], "reachable")
            self.assertTrue(status["sourceSceneKnowledgeComplete"])

    def test_compact_stream_tailer_preserves_partial_line(self):
        baseline = compact_packet(
            "live_baseline_packet.v1",
            1,
            1,
            {"tick": 1, "gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3200, "plane": 0}},
        )
        projection = compact_packet(
            "live_projection_packet.v1",
            1,
            2,
            {"tick": 1, "visibleObjectRefs": []},
        )
        baseline_line = json.dumps(baseline, separators=(",", ":")) + "\n"
        projection_line = json.dumps(projection, separators=(",", ":")) + "\n"
        split_at = len(projection_line) // 2
        with NdjsonTestServer(
            [(baseline_line + projection_line[:split_at]).encode("utf-8"), projection_line[split_at:].encode("utf-8")],
            pause_after_first=True,
        ) as server:
            tailer = live.CompactStreamTailer("127.0.0.1", server.port, 0.05)

            first = tailer.read_new_records(realtime=True, max_records=1)
            self.assertEqual(first, [])
            self.assertTrue(tailer.partial_line_files())
            self.assertEqual(tailer.last_stream_ticks_waiting_for_projection, 1)

            server.continue_event.set()
            second = tailer.read_new_records(realtime=True, max_records=1)
            tailer.close()

            self.assertEqual(len(second), 1)
            self.assertEqual(second[0][2]["tickId"], 1)
            self.assertEqual(second[0][2]["_inputSource"], "compact-stream")

    def test_input_source_compact_stream_reads_synthetic_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="stream-tree")
            session = make_session(Path(tmp), [])
            lines = compact_packet_lines(session, {1: [tree]})
            with NdjsonTestServer([line.encode("utf-8") for line in lines]) as server:
                processor = live.LiveTargetProcessor(
                    session,
                    live_args(
                        input_source="compact-stream",
                        compact_stream_port=server.port,
                        compact_stream_timeout=0.05,
                    ),
                )

                result = {}
                for _attempt in range(5):
                    _added, result = processor.poll_once()
                    if result.get("status", {}).get("candidateCount"):
                        break
                    time.sleep(0.02)
                status = result["status"]
                processor.tailer.close()

            self.assertEqual(status["inputSourceActive"], "compact-stream")
            self.assertGreaterEqual(status["compactStreamReconnects"], 1)
            self.assertGreaterEqual(status["compactPacketsProcessed"], 2)
            self.assertGreaterEqual(status["compactStreamPacketsSeen"], 2)
            self.assertEqual(status["compactStreamPacketsByType"]["live_projection_packet.v1"], 1)
            self.assertEqual(status["compactStreamLatestTickByType"]["live_projection_packet.v1"], 1)
            self.assertEqual(status["compactStreamMissingRequiredTypesForLatestTick"], [])
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "stream-tree")
            self.assertEqual(result["candidates"][0]["classId"], "tree")

    def test_plugin_snapshot_to_tick_conversion_with_synthetic_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))

            tick = live.plugin_snapshot_to_tick(response)

            self.assertIsNotNone(tick)
            self.assertEqual(tick["tickId"], 1)
            self.assertEqual(tick["_inputSource"], "plugin-snapshot")
            self.assertIn("visibleSceneObjectRefs", tick)
            self.assertEqual(tick["visibleSceneObjectRefs"][0]["objectKey"], "snapshot-tree")

    def test_plugin_snapshot_projection_refs_shape_converts_to_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-refs-tree")
            compact_tree = compact_scene_object(tree)
            compact_tree["aimPoint"] = {"x": 110, "y": 100}
            compact_tree.pop("actions", None)
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            response["payloads"]["projection"] = {
                "sceneProjectionSummary": {"visibleObjectCount": 1},
                "refs": [compact_tree],
            }

            tick = live.plugin_snapshot_to_tick(response)

            self.assertIsNotNone(tick)
            self.assertEqual(tick["visibleSceneObjectRefs"][0]["objectKey"], "snapshot-refs-tree")
            self.assertEqual(tick["visibleSceneObjectRefs"][0]["canvasLocation"], {"x": 110, "y": 100})
            self.assertEqual(tick["_pluginSnapshotProjectionDiagnostics"]["refListPath"], "refs")

    def test_plugin_snapshot_packet_envelope_shape_converts_to_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-envelope-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            projection_payload = response["payloads"]["projection"]
            response["payloads"]["projection"] = compact_packet(
                "live_projection_packet.v1",
                1,
                3,
                projection_payload,
            )

            tick = live.plugin_snapshot_to_tick(response)

            self.assertIsNotNone(tick)
            self.assertEqual(tick["visibleSceneObjectRefs"][0]["objectKey"], "snapshot-envelope-tree")
            self.assertEqual(tick["_pluginSnapshotProjectionDiagnostics"]["refListPath"], "visibleObjectRefs")

    def test_input_source_plugin_snapshot_builds_candidates_from_refs_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="snapshot-oak")
            compact_tree = compact_scene_object(tree)
            compact_tree["aimPoint"] = {"canvasX": 110, "canvasY": 100}
            compact_tree.pop("actions", None)
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            response["payloads"]["projection"] = {
                "sceneProjectionSummary": {"visibleObjectCount": 1},
                "projectedRefs": [compact_tree],
            }

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "snapshot-oak")
            self.assertIn(result["candidates"][0]["classId"], {"tree", "oak_tree"})
            self.assertEqual(result["candidates"][0]["name"], "Oak tree")
            self.assertEqual(status["pluginSnapshotProjectionRefListPath"], "projectedRefs")
            self.assertEqual(status["pluginSnapshotRefsConverted"], 1)

    def test_woodcutting_profile_rejects_ambiguous_pear_tree_without_chop_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3204, 3201, 150, 100, name="Tree", actions=["Chop down"], object_key="valid-tree")
            pear = raw_scene_object(999001, 3201, 3201, 110, 100, name="Pear tree", actions=["Pick fruit"], object_key="pear-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [pear, tree]}))
            response["payloads"]["projection"] = {
                "sceneProjectionSummary": {"visibleObjectCount": 2},
                "projectedRefs": [compact_scene_object(pear), compact_scene_object(tree)],
            }

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot", profile="woodcutting"))
                _added, result = processor.poll_once()

            object_keys = {candidate.get("objectKey") for candidate in result["candidates"]}
            self.assertIn("valid-tree", object_keys)
            self.assertNotIn("pear-tree", object_keys)
            reject_reasons = result["status"].get("pluginSnapshotPrefilterRejectReasons") or result["status"].get("pluginSnapshotCandidateRejectReasons") or {}
            self.assertGreaterEqual(reject_reasons.get("ambiguousTreeNoChopAction", 0), 1)

    def test_plugin_snapshot_missing_name_actions_still_builds_world_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="snapshot-unknown-oak")
            compact_tree = compact_scene_object(tree)
            compact_tree.pop("name", None)
            compact_tree.pop("objectName", None)
            compact_tree.pop("actions", None)
            compact_tree["aimPoint"] = {"x": 110, "y": 100}
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            response["payloads"]["projection"] = {
                "sceneProjectionSummary": {"visibleObjectCount": 1},
                "visibleObjectRefs": [compact_tree],
            }

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertEqual(status["worldTargetsBuilt"], 1)
            self.assertEqual(status["pluginSnapshotRefsAcceptedForWorldTargets"], 1)
            self.assertEqual(status["pluginSnapshotVisibleRefsExpectedPathCount"], 1)
            self.assertEqual(status["candidateCount"], 0)
            self.assertTrue(status["pluginSnapshotCandidateRejectReasons"])

    def test_plugin_snapshot_synthetic_tick_shape_matches_compact_packet_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="same-shape-tree")
            session = make_session(Path(tmp), [])
            lines = compact_packet_lines(session, {1: [tree]})
            response = snapshot_response_from_lines(lines)
            packets = [json.loads(line) for line in lines]
            compact_tick = live.compact_packets_to_tick(packets)
            snapshot_tick = live.plugin_snapshot_to_tick(response)

            compact_diag = live.synthetic_tick_ref_diagnostics(compact_tick)
            snapshot_diag = live.synthetic_tick_ref_diagnostics(snapshot_tick)
            self.assertEqual(compact_diag["pathCounts"]["visibleSceneObjectRefs"], 1)
            self.assertEqual(snapshot_diag["pathCounts"]["visibleSceneObjectRefs"], 1)
            self.assertEqual(snapshot_diag["refsAcceptedForWorldTargets"], compact_diag["refsAcceptedForWorldTargets"])
            self.assertEqual(snapshot_tick["visibleSceneObjectRefs"][0]["objectKey"], compact_tick["visibleSceneObjectRefs"][0]["objectKey"])

    def test_input_source_plugin_snapshot_reads_synthetic_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}), warnings=["projection refs capped"])
            response["clientTickHot"] = {
                "schema": "client_tick_hot.v1",
                "clientTick": 33,
                "gameTickAtSample": 1,
                "postMenuSort": {"topOption": "Chop down", "topTarget": "Tree"},
                "lastMenuOptionClicked": {"option": "Chop down", "target": "Tree"},
                "latency": {"postMenuSortAgeMillis": 12, "lastClickAgeMillis": 24, "samplesBuffered": 3},
            }

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertEqual(status["inputSourceActive"], "plugin-snapshot")
            self.assertTrue(status["pluginSnapshotAvailable"])
            self.assertEqual(status["pluginSnapshotStatus"], "PASS")
            self.assertEqual(status["pluginSnapshotLatestTick"], 1)
            self.assertIn("projection", status["pluginSnapshotPayloadTypes"])
            self.assertEqual(status["pluginSnapshotProjectionRefs"], 1)
            self.assertTrue(status["pluginSnapshotProjectionCapped"])
            self.assertEqual(status["clientTickHotSchema"], "client_tick_hot.v1")
            self.assertEqual(status["clientTickTopOption"], "Chop down")
            self.assertEqual(status["clientTickLastClickedOption"], "Chop down")
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "snapshot-tree")

    def test_plugin_snapshot_missing_projection_warns_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}), omit_needs={"projection"})

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertEqual(status["inputSourceActive"], "plugin-snapshot")
            self.assertTrue(status["pluginSnapshotAvailable"])
            self.assertIn("projection", status["pluginSnapshotMissingCapabilities"])
            self.assertEqual(status["candidateCount"], 0)
            self.assertTrue(any("missing required payload" in warning for warning in status["warnings"]))

    def test_plugin_snapshot_auto_escalates_from_hot_to_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="expanded-oak")
            session = make_session(Path(tmp), [])
            hot_response = snapshot_response_from_lines(compact_packet_lines(session, {1: []}), warnings=["projection refs capped"])
            hot_response["snapshotTier"] = "hot"
            expanded_response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}), warnings=["projection refs capped"])
            expanded_response["snapshotTier"] = "expanded"

            with mock.patch.object(
                live.PluginSnapshotTailer,
                "_request_snapshot",
                side_effect=[
                    (hot_response, len(json.dumps(hot_response))),
                    (expanded_response, len(json.dumps(expanded_response))),
                ],
            ):
                processor = live.LiveTargetProcessor(
                    session,
                    live_args(
                        input_source="plugin-snapshot",
                        plugin_snapshot_auto_escalate=True,
                        plugin_snapshot_min_candidates=1,
                    ),
                )
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertTrue(status["pluginSnapshotEscalated"])
            self.assertEqual(status["pluginSnapshotTier"], "expanded")
            self.assertEqual(status["pluginSnapshotInitialRefs"], 0)
            self.assertEqual(status["pluginSnapshotFinalRefs"], 1)
            self.assertEqual(status["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "expanded-oak")

    def test_plugin_snapshot_request_body_construction(self):
        args = live_args(
            plugin_snapshot_max_projection_refs=123,
            plugin_snapshot_max_age_ticks=7,
            plugin_snapshot_include_geometry=True,
            plugin_snapshot_response_mode="normal",
            plugin_snapshot_projection_field_mode="compact",
        )
        body = live.plugin_snapshot_request_body(args)

        self.assertEqual(body["schema"], "plugin_snapshot_request.v1")
        self.assertEqual(body["maxProjectionRefs"], 123)
        self.assertEqual(body["maxAgeTicks"], 7)
        self.assertTrue(body["includeGeometry"])
        self.assertEqual(body["responseMode"], "normal")
        self.assertEqual(body["projectionFieldMode"], "compact")
        self.assertEqual(body["snapshotTier"], "hot")
        self.assertEqual(body["profileHint"], "woodcutting")
        self.assertEqual(body["classHint"], "tree")
        self.assertEqual(body["targetTypeHint"], "sceneObject")
        self.assertTrue(body["requireOnScreen"])
        self.assertTrue(body["requireGeometryAvailable"])
        self.assertEqual(body["desiredClasses"], ["tree"])
        self.assertIn("interaction_hot", body["needs"])
        self.assertIn("projection", body["needs"])
        self.assertIn("writer_health", body["needs"])

    def test_plugin_snapshot_request_adds_service_hints_for_service_policy(self):
        body = live.plugin_snapshot_request_body(live_args(input_source="plugin-snapshot", task_policy="woodcutting_bank"))

        self.assertEqual(body["maxProjectionRefs"], 150)
        self.assertEqual(body["classHint"], "tree")
        self.assertNotIn("targetTypeHint", body)
        self.assertIn("tree", body["desiredClasses"])
        self.assertIn("bank_related", body["desiredClasses"])
        self.assertIn("banker", body["desiredClasses"])
        self.assertIn("deposit_box", body["desiredClasses"])
        self.assertIn("route_transition", body["desiredClasses"])

    def test_plugin_snapshot_request_adds_service_hints_for_service_preset(self):
        body = live.plugin_snapshot_request_body(live_args(input_source="plugin-snapshot", preset="woodcut_bank"))

        self.assertEqual(body["maxProjectionRefs"], 150)
        self.assertEqual(body["classHint"], "tree")
        self.assertNotIn("targetTypeHint", body)
        self.assertIn("tree", body["desiredClasses"])
        self.assertIn("bank_related", body["desiredClasses"])
        self.assertIn("route_transition", body["desiredClasses"])

    def test_plugin_snapshot_tier_defaults_and_manual_override(self):
        hot = live.plugin_snapshot_request_body(live_args(plugin_snapshot_tier="hot", plugin_snapshot_max_projection_refs=None))
        expanded = live.plugin_snapshot_request_body(live_args(plugin_snapshot_tier="expanded", plugin_snapshot_max_projection_refs=None))
        audit = live.plugin_snapshot_request_body(live_args(plugin_snapshot_tier="audit", plugin_snapshot_max_projection_refs=None))
        manual = live.plugin_snapshot_request_body(live_args(plugin_snapshot_tier="expanded", plugin_snapshot_max_projection_refs=77))

        self.assertEqual(hot["maxProjectionRefs"], 100)
        self.assertEqual(expanded["maxProjectionRefs"], 500)
        self.assertEqual(audit["maxProjectionRefs"], 2000)
        self.assertEqual(manual["maxProjectionRefs"], 77)
        service_hot = live.plugin_snapshot_request_body(live_args(plugin_snapshot_tier="hot", plugin_snapshot_max_projection_refs=None, preset="woodcut_bank"))
        self.assertEqual(service_hot["maxProjectionRefs"], 150)

    def test_plugin_snapshot_tailer_preserves_service_preset_in_request_body(self):
        tailer = live.PluginSnapshotTailer(profile="woodcutting", preset="woodcut_bank", snapshot_tier="hot", max_projection_refs=None)

        body = tailer.request_body()

        self.assertEqual(tailer.max_projection_refs, 150)
        self.assertEqual(body["maxProjectionRefs"], 150)
        self.assertIn("route_transition", body["desiredClasses"])

    def test_plugin_snapshot_timeout_is_reported(self):
        tailer = live.PluginSnapshotTailer("127.0.0.1", 8893, timeout=0.01)
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow endpoint")):
            records = tailer.read_new_records()

        self.assertEqual(records, [])
        self.assertEqual(tailer.snapshot_timeouts, 1)
        self.assertEqual(tailer.snapshot_endpoint_errors, 1)
        self.assertFalse(tailer.snapshot_available)

    def test_plugin_snapshot_structured_response_too_large_is_endpoint_available(self):
        body = json.dumps(
            {
                "schema": "plugin_snapshot_response.v1",
                "status": "FAIL",
                "errorCode": "response_too_large",
                "warnings": ["responseTooLarge"],
                "responseSizing": {
                    "cachedProjectionBytes": 3452972,
                    "estimatedResponseBytes": 1200000,
                    "maxResponseBytes": 1048576,
                },
            }
        ).encode("utf-8")
        error = urllib.error.HTTPError(
            url="http://127.0.0.1:8893/snapshot",
            code=413,
            msg="response too large",
            hdrs=None,
            fp=BytesIO(body),
        )
        tailer = live.PluginSnapshotTailer("127.0.0.1", 8893, timeout=0.01)

        with mock.patch("urllib.request.urlopen", side_effect=error):
            records = tailer.read_new_records()

        self.assertEqual(records, [])
        self.assertTrue(tailer.snapshot_available)
        self.assertEqual(tailer.snapshot_status, "FAIL")
        self.assertEqual(tailer.snapshot_error_code, "response_too_large")
        self.assertEqual(tailer.snapshot_response_sizing["maxResponseBytes"], 1048576)
        self.assertIn("size limit", tailer.last_snapshot_incomplete_reason)

    def test_diagnose_plugin_snapshot_parses_structured_response_too_large(self):
        body = json.dumps(
            {
                "schema": "plugin_snapshot_response.v1",
                "status": "FAIL",
                "errorCode": "response_too_large",
                "warnings": ["responseTooLarge"],
                "responseSizing": {
                    "cachedProjectionBytes": 3452972,
                    "estimatedResponseBytes": 1200000,
                    "maxResponseBytes": 1048576,
                },
            }
        ).encode("utf-8")
        error = urllib.error.HTTPError(
            url="http://127.0.0.1:8893/snapshot",
            code=413,
            msg="response too large",
            hdrs=None,
            fp=BytesIO(body),
        )
        args = SimpleNamespace(
            host="127.0.0.1",
            port=8893,
            token="",
            timeout=0.1,
            max_age_ticks=5,
            max_projection_refs=500,
            include_geometry=False,
            response_mode="compact",
            projection_field_mode="compact",
        )

        with mock.patch("urllib.request.urlopen", side_effect=error):
            response, request_error, response_bytes = diagnose_snapshot.request_snapshot(args)

        snapshot_diag = {
            "available": bool(response and response.get("schema") in {"plugin_snapshot_response.v1", "plugin_snapshot_error.v1"}),
            "requestFailed": bool(response and (response.get("errorCode") or response.get("status") == "FAIL")),
            "errorCode": response.get("errorCode") if isinstance(response, dict) else None,
        }

        self.assertIsNone(request_error)
        self.assertGreater(response_bytes, 0)
        self.assertTrue(snapshot_diag["available"])
        self.assertTrue(snapshot_diag["requestFailed"])
        self.assertEqual(snapshot_diag["errorCode"], "response_too_large")
        self.assertEqual(
            diagnose_snapshot.conclusion(snapshot_diag, {}, {}),
            "snapshot endpoint available but response exceeded configured size limit",
        )

    def test_diagnose_plugin_snapshot_tier_sweep_reports_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="sweep-oak")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            args = SimpleNamespace(
                host="127.0.0.1",
                port=8893,
                token="",
                timeout=0.1,
                session=None,
                sessions_dir=None,
                latest_session=False,
                profile="woodcutting",
                tier="hot",
                max_projection_refs=None,
                max_age_ticks=5,
                include_geometry=False,
                response_mode="compact",
                projection_field_mode="compact",
                limit=100,
                dump_synthetic_shape=False,
                json=False,
            )

            with mock.patch.object(diagnose_snapshot, "request_snapshot", return_value=(response, None, len(json.dumps(response)))):
                payload = diagnose_snapshot.tier_sweep_payload(args, session)

            self.assertEqual(payload["schema"], "plugin_snapshot_tier_sweep.v1")
            self.assertIn("hot", payload["tiers"])
            self.assertIn(payload["recommendation"], {"hot", "expanded", "compact-packets"})

    def test_plugin_snapshot_unchanged_tick_does_not_emit_new_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-tree")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, first = processor.poll_once()
                _added, second = processor.poll_once()

            self.assertEqual(first["status"]["candidateCount"], 1)
            self.assertEqual(second["status"]["pluginSnapshotTicksSkippedAsUnchanged"], 1)
            self.assertEqual(second["status"]["rawRecordsFullyParsedThisPoll"], 0)
            self.assertEqual(second["status"]["processedNewTicks"], 0)
            self.assertTrue(second["status"]["pluginSnapshotCandidateOutputSkippedUnchanged"])
            self.assertEqual(second["status"]["candidateCount"], 1)
            self.assertIn(second["status"]["pluginSnapshotBottleneck"], {"endpoint_service", "http_request", "response_read", "json_parse", "output_serialize", "output_write", "unknown"})

    def test_plugin_snapshot_candidate_signature_skips_heavy_output_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="stable-snapshot-tree")
            session = make_session(Path(tmp), [])
            first_response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            second_response = snapshot_response_from_lines(compact_packet_lines(session, {2: [tree]}))

            with mock.patch.object(
                live.PluginSnapshotTailer,
                "_request_snapshot",
                side_effect=[(first_response, len(json.dumps(first_response))), (second_response, len(json.dumps(second_response)))],
            ):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, first = processor.poll_once()
                _added, second = processor.poll_once()

            self.assertEqual(first["status"]["candidateCount"], 1)
            self.assertEqual(second["status"]["candidateCount"], 1)
            self.assertTrue(second["status"]["pluginSnapshotCandidateOutputSkippedUnchanged"])
            self.assertTrue(second["status"]["pluginSnapshotCandidateSignature"])
            self.assertGreaterEqual(second["status"]["pluginSnapshotOutputBytesSkipped"], 0)

    def test_plugin_snapshot_prefilter_reduces_refs_without_losing_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="prefilter-oak")
            rock = raw_scene_object(11364, 3202, 3201, 130, 100, name="Copper rock", actions=["Mine"], object_key="prefilter-rock")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree, rock]}))

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            status = result["status"]
            self.assertEqual(status["pluginSnapshotRefsBeforePrefilter"], 2)
            self.assertEqual(status["pluginSnapshotRefsAfterPrefilter"], 1)
            self.assertEqual(status["worldTargetsBuilt"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "prefilter-oak")

    def test_plugin_snapshot_prefilter_keeps_service_candidate_for_service_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(10820, 3201, 3201, 110, 100, name="Oak tree", object_key="prefilter-oak")
            bank = raw_scene_object(10355, 3202, 3201, 130, 100, name="Bank booth", actions=["Bank"], object_key="prefilter-bank")
            rock = raw_scene_object(11364, 3203, 3201, 150, 100, name="Copper rock", actions=["Mine"], object_key="prefilter-rock")
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree, bank, rock]}))

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot", task_policy="woodcutting_bank"))
                _added, result = processor.poll_once()

            status = result["status"]
            object_keys = {candidate.get("objectKey") for candidate in result["candidates"]}
            self.assertEqual(status["pluginSnapshotRefsBeforePrefilter"], 3)
            self.assertEqual(status["pluginSnapshotRefsAfterPrefilter"], 2)
            self.assertIn("prefilter-oak", object_keys)
            self.assertIn("prefilter-bank", object_keys)
            self.assertNotIn("prefilter-rock", object_keys)

    def test_plugin_snapshot_prefilter_keeps_route_transition_primary_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            stair = raw_scene_object(
                56230,
                3204,
                3229,
                504,
                134,
                name="Staircase",
                actions=["Climb-up", "Top-floor"],
                object_key="prefilter-stair",
            )
            session = make_session(Path(tmp), [])
            response = snapshot_response_from_lines(compact_packet_lines(session, {1: [stair]}))

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot", preset="woodcut_bank"))
                _added, result = processor.poll_once()

            self.assertEqual(result["status"]["pluginSnapshotRefsBeforePrefilter"], 1)
            self.assertEqual(result["status"]["pluginSnapshotRefsAfterPrefilter"], 1)
            self.assertEqual(result["candidates"][0]["classId"], "route_transition")
            self.assertEqual(result["candidates"][0].get("targetName") or result["candidates"][0].get("name"), "Staircase")

    def test_plugin_snapshot_bottleneck_identifies_largest_bucket(self):
        self.assertEqual(
            live.plugin_snapshot_bottleneck(
                {
                    "pluginSnapshotEndpointServiceMillis": 5.0,
                    "pluginSnapshotHttpRequestMillis": 3.0,
                    "pluginSnapshotOutputWriteMillis": 8.0,
                }
            ),
            "output_write",
        )

    def test_plugin_snapshot_bad_projection_shape_retains_previous_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="snapshot-tree")
            session = make_session(Path(tmp), [])
            good_response = snapshot_response_from_lines(compact_packet_lines(session, {1: [tree]}))
            bad_response = snapshot_response_from_lines(compact_packet_lines(session, {2: [tree]}))
            bad_response["payloads"]["projection"] = {"sceneProjectionSummary": {"visibleObjectCount": 1}, "unexpectedRefs": [compact_scene_object(tree)]}

            with mock.patch.object(
                live.PluginSnapshotTailer,
                "_request_snapshot",
                side_effect=[(good_response, len(json.dumps(good_response))), (bad_response, len(json.dumps(bad_response)))],
            ):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, first = processor.poll_once()
                _added, second = processor.poll_once()

            self.assertEqual(first["status"]["candidateCount"], 1)
            self.assertEqual(second["status"]["candidateCount"], 1)
            self.assertEqual(second["candidates"][0]["objectKey"], "snapshot-tree")
            self.assertTrue(any("retaining previous candidates" in warning for warning in second["status"]["warnings"]))

    def test_compact_stream_incomplete_tick_waits_for_projection(self):
        baseline = compact_packet(
            "live_baseline_packet.v1",
            5,
            1,
            {"tick": 5, "gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3200, "plane": 0}},
        )
        line = json.dumps(baseline, separators=(",", ":")) + "\n"
        with NdjsonTestServer([line.encode("utf-8")]) as server:
            tailer = live.CompactStreamTailer("127.0.0.1", server.port, 0.05)
            records = tailer.read_new_records(realtime=True, max_records=1)
            tailer.close()

        self.assertEqual(records, [])
        self.assertEqual(tailer.last_raw_records_seen, 0)
        self.assertEqual(tailer.last_stream_tick_buffer_size, 1)
        self.assertEqual(tailer.last_stream_ticks_waiting_for_projection, 1)
        self.assertIn("live_projection_packet.v1", tailer.last_missing_required_types_for_latest_tick)

    def test_compact_stream_incomplete_tick_retains_previous_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="stream-tree")
            session = make_session(Path(tmp), [])
            first_tick = "".join(compact_packet_lines(session, {1: [tree]}))
            second_baseline = json.dumps(
                compact_packet(
                    "live_baseline_packet.v1",
                    2,
                    99,
                    {"tick": 2, "gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3200, "plane": 0}},
                ),
                separators=(",", ":"),
            ) + "\n"
            with NdjsonTestServer([first_tick.encode("utf-8"), second_baseline.encode("utf-8")], pause_after_first=True) as server:
                processor = live.LiveTargetProcessor(
                    session,
                    live_args(
                        input_source="compact-stream",
                        compact_stream_port=server.port,
                        compact_stream_timeout=0.05,
                    ),
                )

                _added, first_result = processor.poll_once()
                server.continue_event.set()
                _added, second_result = processor.poll_once()
                processor.tailer.close()

            self.assertEqual(first_result["status"]["candidateCount"], 1)
            self.assertEqual(second_result["status"]["candidateCount"], 1)
            self.assertEqual(second_result["candidates"][0]["objectKey"], "stream-tree")
            self.assertEqual(second_result["status"]["lastProcessedTick"], 1)
            self.assertEqual(second_result["status"]["compactStreamTickBufferSize"], 1)
            self.assertIn("live_projection_packet.v1", second_result["status"]["compactStreamMissingRequiredTypesForLatestTick"])
            self.assertTrue(any("retaining previous candidates" in warning for warning in second_result["status"]["warnings"]))

    def test_compact_stream_can_fallback_to_packet_files_when_projection_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="file-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})
            baseline = json.dumps(
                compact_packet(
                    "live_baseline_packet.v1",
                    2,
                    99,
                    {"tick": 2, "gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3200, "plane": 0}},
                ),
                separators=(",", ":"),
            ) + "\n"
            with NdjsonTestServer([baseline.encode("utf-8")]) as server:
                processor = live.LiveTargetProcessor(
                    session,
                    live_args(
                        input_source="compact-stream",
                        compact_stream_port=server.port,
                        compact_stream_timeout=0.05,
                        stream_fallback_to_compact_packets=True,
                        stream_required_types_timeout=0.001,
                    ),
                )
                _added, first_result = processor.poll_once()
                processor.tailer.first_packet_seen_at = time.monotonic() - 10
                _added, second_result = processor.poll_once()

            self.assertEqual(first_result["status"]["candidateCount"], 0)
            self.assertEqual(second_result["status"]["inputSourceActive"], "compact-packets")
            self.assertTrue(second_result["status"]["streamFallbackToFile"])
            self.assertIn("falling back to compact packet files", second_result["status"]["streamFallbackReason"])
            self.assertEqual(second_result["status"]["candidateCount"], 1)
            self.assertEqual(second_result["candidates"][0]["objectKey"], "file-tree")

    def test_timing_payload_excludes_stream_reconnect_from_active_ms(self):
        tailer = live.CompactStreamTailer("127.0.0.1", 1, 0.05)
        tailer.last_stream_reconnect_millis = 900.0
        payload = live.timing_payload(live.Timing(), 5.0, tailer)

        self.assertEqual(payload["streamReconnectMillis"], 900.0)
        self.assertEqual(payload["totalActiveMillis"], 5.0)

    def test_input_source_auto_prefers_compact_stream_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            active, compact_available, raw_available, reason = live.choose_input_source(
                session,
                "auto",
                {"available": True, "host": "127.0.0.1", "port": 8891},
            )

            self.assertEqual(active, "compact-stream")
            self.assertFalse(compact_available)
            self.assertTrue(raw_available)
            self.assertIn("experimental compact stream", reason)

    def test_input_source_auto_prefers_compact_packets_over_stream_when_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="compact-tree")
            session = make_session(Path(tmp), [raw_tick(1)])
            write_compact_packets(session, {2: [tree]})
            active, compact_available, raw_available, reason = live.choose_input_source(
                session,
                "auto",
                {"available": True, "host": "127.0.0.1", "port": 8891},
            )

            self.assertEqual(active, "compact-packets")
            self.assertTrue(compact_available)
            self.assertTrue(raw_available)
            self.assertIsNone(reason)

    def test_input_source_auto_does_not_prefer_plugin_snapshot_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            active, _compact_available, _raw_available, reason = live.choose_input_source(
                session,
                "auto",
                {"available": False},
                {"available": True, "host": "127.0.0.1", "port": 8893},
            )

            self.assertEqual(active, "raw-ticks")
            self.assertIn("compact live packets unavailable", reason)

    def test_input_source_auto_can_prefer_plugin_snapshot_with_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            active, _compact_available, _raw_available, reason = live.choose_input_source(
                session,
                "auto",
                {"available": False},
                {"available": True, "host": "127.0.0.1", "port": 8893},
                auto_prefer_plugin_snapshot=True,
            )

            self.assertEqual(active, "plugin-snapshot")
            self.assertIn("auto-prefer-plugin-snapshot", reason)

    def test_explicit_plugin_snapshot_does_not_fallback_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="file-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=TimeoutError("slow endpoint")):
                processor = live.LiveTargetProcessor(session, live_args(input_source="plugin-snapshot"))
                _added, result = processor.poll_once()

            self.assertEqual(result["status"]["inputSourceActive"], "plugin-snapshot")
            self.assertFalse(result["status"]["pluginSnapshotAvailable"])
            self.assertEqual(result["status"]["candidateCount"], 0)

    def test_plugin_snapshot_can_fallback_to_compact_packets_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="file-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=TimeoutError("slow endpoint")):
                processor = live.LiveTargetProcessor(
                    session,
                    live_args(input_source="plugin-snapshot", plugin_snapshot_fallback="compact-packets"),
                )
                _added, result = processor.poll_once()

            self.assertEqual(result["status"]["inputSourceActive"], "compact-packets")
            self.assertTrue(result["status"]["pluginSnapshotFallbackToFile"])
            self.assertIn("falling back to compact packet files", result["status"]["pluginSnapshotFallbackReason"])
            self.assertEqual(result["status"]["candidateCount"], 1)
            self.assertEqual(result["candidates"][0]["objectKey"], "file-tree")

    def test_compact_watch_values_packet_writes_live_watch_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="compact-watch-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]}, include_watch_values=True)
            processor = live.LiveTargetProcessor(session, live_args(input_source="compact-packets"))

            _added, result = processor.poll_once()
            watch_values = result["watchValues"]

            self.assertEqual(watch_values["schema"], "live_watch_values.v1")
            self.assertIn("inventory_summary", watch_values["valuesByAlias"])
            self.assertEqual(watch_values["valuesByAlias"]["test_varbit"]["value"], 7)
            self.assertIn("test_varbit", watch_values["changedAliases"])
            self.assertEqual(result["status"]["watchValueCount"], len(watch_values["valuesByAlias"]))
            watch_path = session / "interaction_geometry" / "live" / "live_watch_values.json"
            self.assertTrue(watch_path.exists())

    def test_compact_packet_clickbox_polygon_reaches_overlay_debug_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="compact-hull-tree")
            tree["clickboxPolygon"] = [
                {"x": 100, "y": 90},
                {"x": 120, "y": 90},
                {"x": 120, "y": 120},
                {"x": 100, "y": 120},
            ]
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})
            processor = live.LiveTargetProcessor(session, live_args(input_source="compact-packets", overlay_debug_target_limit=1))

            _added, result = processor.poll_once()
            candidate = result["candidates"][0]
            overlay_state = result["overlayDebug"]
            overlay_target = overlay_state["targets"][0]

            self.assertIn("clickboxPolygon", candidate["geometry"])
            self.assertEqual(candidate["geometry"]["preferredAimGeometryType"], "clickableHull")
            self.assertEqual(overlay_state["summary"]["clickableHullTargets"], 1)
            self.assertTrue(overlay_target["clickableHullAvailable"])
            self.assertEqual(overlay_target["geometrySource"], "clickableHull")
            self.assertIn("clickableHull", overlay_target)
            self.assertEqual(overlay_target["clickableHull"]["points"][0], {"x": 100, "y": 90})

    def test_candidate_hull_geometry_matches_by_object_key(self):
        candidate = {
            "objectKey": "tree-a",
            "id": 1276,
            "worldX": 3201,
            "worldY": 3201,
            "plane": 0,
            "geometry": {"aimPoint": {"x": 110, "y": 100}},
        }
        source = {
            "objectKey": "tree-a",
            "id": 1276,
            "worldX": 3201,
            "worldY": 3201,
            "plane": 0,
            "geometry": {"clickboxPolygon": [[100, 90], [120, 90], [120, 120], [100, 120]]},
        }

        stats = live.attach_candidate_hull_geometry([candidate], [source], [])

        self.assertEqual(stats["candidateHullDirectMatches"], 1)
        self.assertEqual(stats["candidateHullMissing"], 0)
        self.assertIn("clickableHull", candidate["geometry"])

    def test_candidate_hull_geometry_fallback_matches_by_world_tile(self):
        candidate = {
            "id": 1276,
            "worldX": 3201,
            "worldY": 3201,
            "plane": 0,
            "geometry": {"aimPoint": {"x": 110, "y": 100}},
        }
        source = {
            "id": 1276,
            "worldX": 3201,
            "worldY": 3201,
            "plane": 0,
            "geometry": {"clickboxPolygon": [[100, 90], [120, 90], [120, 120], [100, 120]]},
        }

        stats = live.attach_candidate_hull_geometry([candidate], [source], [])

        self.assertEqual(stats["candidateHullFallbackMatches"], 1)
        self.assertEqual(candidate["_hullGeometryMatch"]["keyType"], "idWorld")
        self.assertIn("clickboxPolygon", candidate["geometry"])

    def test_compact_refs_with_hull_do_not_displace_unmatched_top_candidate(self):
        candidate = {
            "objectKey": "top-tree",
            "id": 1276,
            "worldX": 3201,
            "worldY": 3201,
            "plane": 0,
            "geometry": {"aimPoint": {"x": 110, "y": 100}},
        }
        unrelated = {
            "objectKey": "corner-tree",
            "id": 1276,
            "worldX": 3220,
            "worldY": 3220,
            "plane": 0,
            "geometry": {"clickboxPolygon": [[1, 1], [20, 1], [20, 20], [1, 20]]},
        }

        stats = live.attach_candidate_hull_geometry([candidate], [unrelated], [])

        self.assertEqual(stats["candidateHullMissing"], 1)
        self.assertEqual(stats["compactHullRefsUnused"], 1)
        self.assertNotIn("clickboxPolygon", candidate["geometry"])

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
                    "--compact-stream-port",
                    "1",
                    "--once",
                    "--quiet",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("A compact live input is required", result.stdout)

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
            self.assertIn("A compact live input is required", result.stdout)

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

    def test_compare_stream_vs_file_detects_missing_projection_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="same-tree")
            session = make_session(Path(tmp), [])
            write_compact_packets(session, {1: [tree]})
            baseline = json.dumps(
                compact_packet(
                    "live_baseline_packet.v1",
                    1,
                    1,
                    {"tick": 1, "gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3200, "plane": 0}},
                ),
                separators=(",", ":"),
            ) + "\n"
            output = StringIO()

            with NdjsonTestServer([baseline.encode("utf-8")]) as server:
                with redirect_stdout(output):
                    code = live.compare_input_sources(
                        session,
                        live_args(
                            input_source="auto",
                            latest=1,
                            compare_input_sources="stream-vs-file",
                            compact_stream_port=server.port,
                            compact_stream_timeout=0.05,
                        ),
                    )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["mode"], "stream-vs-file")
            self.assertIn("stream has no projection packets", " ".join(payload["failures"]))
            self.assertTrue(payload["compactPackets"]["available"])

    def test_compare_plugin_snapshot_vs_file_passes_on_matching_synthetic_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="same-tree")
            session = make_session(Path(tmp), [])
            lines = compact_packet_lines(session, {1: [tree]})
            response = snapshot_response_from_lines(lines)
            output = StringIO()

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with redirect_stdout(output):
                    code = live.compare_input_sources(
                        session,
                        live_args(
                            input_source="auto",
                            latest=1,
                            compare_input_sources="plugin-snapshot-vs-file",
                        ),
                    )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertIn(payload["status"], {"PASS", "WARN"})
            self.assertEqual(payload["mode"], "plugin-snapshot-vs-file")
            self.assertTrue(payload["pluginSnapshot"]["available"])
            self.assertTrue(payload["compactPackets"]["available"])

    def test_compare_plugin_snapshot_vs_file_detects_missing_projection_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = raw_scene_object(1276, 3201, 3201, 110, 100, object_key="same-tree")
            session = make_session(Path(tmp), [])
            lines = compact_packet_lines(session, {1: [tree]})
            response = snapshot_response_from_lines(lines, omit_needs={"projection"})
            output = StringIO()

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with redirect_stdout(output):
                    code = live.compare_input_sources(
                        session,
                        live_args(
                            input_source="auto",
                            latest=1,
                            compare_input_sources="plugin-snapshot-vs-file",
                        ),
                    )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["mode"], "plugin-snapshot-vs-file")
            self.assertIn("plugin snapshot has no projection payload", " ".join(payload["failures"]))
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
        self.assertEqual(state["resourceCounts"]["normal_logs"]["count"], 700)
        self.assertEqual(state["resourceCounts"]["woodcutting_logs"]["matchedSlots"], [0])

    def test_inventory_resource_counts_preserve_high_slots(self):
        tick = {
            "inventory": {
                "known": True,
                "slotCount": 28,
                "freeSlots": 25,
                "filledSlots": 3,
                "items": [
                    {"slot": 27, "itemId": 1511, "quantity": 1},
                    {"slot": 0, "itemId": 1521, "quantity": 1},
                    {"slot": 13, "itemId": 995, "quantity": 100},
                ],
            }
        }
        state = live.inventory_state_for_ticks([tick], tick)
        logs = state["resourceCounts"]["woodcutting_logs"]
        self.assertEqual(logs["count"], 2)
        self.assertEqual(logs["matchedSlots"], [0, 27])
        self.assertTrue(state["slotDiagnostics"]["consistent"])

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

    def test_unknown_interacting_marker_is_not_busy_activity(self):
        tick = {
            "tickId": 8,
            "localPlayer": {"animation": None, "poseAnimation": -1, "interacting": None},
            "status": {"interactingType": "UNKNOWN"},
        }
        activity = live.apparent_activity_for_tick(tick, {}, {})
        self.assertEqual(activity["apparentState"], "unknown")
        self.assertIn("interacting unknown; not treated as busy", activity["evidence"])
        self.assertIn("no explicit busy evidence", activity["evidence"])

    def test_explicit_interacting_marker_is_busy_activity(self):
        tick = {
            "tickId": 8,
            "localPlayer": {"animation": -1, "poseAnimation": -1, "interacting": None},
            "status": {"interactingType": "sceneObject", "interactingName": "Tree"},
        }
        activity = live.apparent_activity_for_tick(tick, {}, {})
        self.assertEqual(activity["apparentState"], "interacting")
        self.assertIn("explicit interacting target present", activity["evidence"])

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

    def test_live_event_timeline_records_source_fallback_and_suppression_changes(self):
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
            base_status = {
                "latestTick": 1,
                "warningCount": 0,
                "budgetExceeded": False,
                "writeFailureCount": 0,
                "sourceCapHit": False,
                "inputSourceRequested": "auto",
                "inputSourceActive": "compact-packets",
                "compactPacketsAvailable": True,
                "compactPacketsRecent": True,
                "inputFallbackReason": None,
                "candidatesSuppressedByLiveness": 0,
                "candidatesSuppressedAsDepleted": 0,
                "candidatesRevivedAfterRespawn": 0,
            }
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 1},
                candidates=[candidate],
                inventory_state=inventory,
                activity=activity,
                status=base_status,
                processed_at="2026-01-01T00:00:00Z",
            )
            fallback_status = dict(
                base_status,
                latestTick=2,
                inputSourceActive="raw-ticks",
                compactPacketsAvailable=False,
                compactPacketsRecent=False,
                inputFallbackReason="auto mode falling back to raw ticks because compact packets were unavailable",
                candidatesSuppressedByLiveness=2,
                candidatesSuppressedAsDepleted=1,
                candidatesRevivedAfterRespawn=1,
            )
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 2},
                candidates=[candidate],
                inventory_state=inventory,
                activity=activity,
                status=fallback_status,
                processed_at="2026-01-01T00:00:01Z",
            )
            event_types = [event["eventType"] for event in processor.event_timeline]
            self.assertIn("input_source_changed", event_types)
            self.assertIn("compact_packet_fallback_changed", event_types)
            self.assertIn("liveness_suppressed_candidate", event_types)
            self.assertIn("depleted_candidate_suppressed", event_types)
            self.assertIn("candidate_revived", event_types)

    def test_live_event_timeline_records_activity_navigation_and_candidate_changes(self):
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
                "aimPointContext": {"canvasX": 100, "canvasY": 120, "source": "test"},
                "navigation": {"directReachability": "reachable", "targetInCollisionWindow": True},
            }
            inventory = {"signature": "a", "freeSlots": 20, "inventoryFull": False}
            activity = {
                "activityState": {"apparentState": "idle"},
                "woodcuttingState": {"woodcuttingState": "likely_idle", "confidence": 0.5},
                "player": {"animation": -1, "interacting": None},
            }
            status = {
                "latestTick": 1,
                "candidateCount": 1,
                "warningCount": 0,
                "budgetExceeded": False,
                "writeFailureCount": 0,
                "sourceCapHit": False,
                "liveFreshnessMillis": 100,
            }
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 1},
                candidates=[candidate],
                inventory_state=inventory,
                activity=activity,
                status=status,
                processed_at="2026-01-01T00:00:00Z",
                navigation={"collisionWindowAvailable": True},
            )

            changed_candidate = dict(candidate)
            changed_candidate["aimPointContext"] = {"canvasX": 190, "canvasY": 120, "source": "test"}
            changed_candidate["navigation"] = {"directReachability": "blocked", "targetInCollisionWindow": False}
            changed_activity = {
                "activityState": {"apparentState": "animating"},
                "woodcuttingState": {"woodcuttingState": "likely_chopping", "confidence": 0.7, "evidence": ["test animation"]},
                "player": {"animation": 879, "interacting": {"type": "sceneObject", "name": "Tree", "id": 1276}},
            }
            changed_status = dict(status, latestTick=2, candidateCount=5, liveFreshnessMillis=10000)
            processor.emit_timeline_events(
                latest_tick_record={"tickId": 2},
                candidates=[changed_candidate],
                inventory_state=inventory,
                activity=changed_activity,
                status=changed_status,
                processed_at="2026-01-01T00:00:01Z",
                navigation={"collisionWindowAvailable": False},
            )
            event_types = [event["eventType"] for event in processor.event_timeline]
            self.assertIn("candidate_count_changed", event_types)
            self.assertIn("best_candidate_aim_point_changed", event_types)
            self.assertIn("best_candidate_reachability_changed", event_types)
            self.assertIn("target_outside_collision_window", event_types)
            self.assertIn("activity_state_changed", event_types)
            self.assertIn("woodcutting_state_changed", event_types)
            self.assertIn("interacting_target_changed", event_types)
            self.assertIn("collision_window_availability_changed", event_types)
            self.assertIn("live_freshness_changed", event_types)

    def test_live_event_timeline_is_bounded_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", event_timeline_limit=2))
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

    def test_live_event_timeline_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            processor = live.LiveTargetProcessor(session, live_args(profile="woodcutting", disable_event_timeline=True))

            _added, result = processor.poll_once()

            self.assertFalse(result["status"]["eventTimelineEnabled"])
            self.assertEqual(result["status"]["eventTimelineCount"], 0)
            self.assertEqual(result["events"], [])
            events_path = session / "interaction_geometry" / "live" / "live_event_timeline.jsonl"
            self.assertTrue(events_path.exists())
            self.assertEqual(events_path.read_text(encoding="utf-8"), "")

    def test_event_timeline_cli_controls_parse(self):
        with mock.patch.object(
            sys,
            "argv",
            [
                str(LIVE_SCRIPT),
                "--event-timeline-limit",
                "7",
                "--disable-event-timeline",
            ],
        ):
            args = live.parse_args()

        self.assertEqual(args.event_timeline_limit, 7)
        self.assertTrue(args.disable_event_timeline)

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
                if index in (0, 2):
                    candidates[-1]["geometry"] = {
                        "clickboxPolygon": [[90 + index, 110], [110 + index, 110], [110 + index, 130], [90 + index, 130]],
                        "convexHullPolygon": [[88 + index, 108], [112 + index, 108], [112 + index, 132], [88 + index, 132]],
                    }

            state = live.overlay_debug_state_for(
                session,
                live_args(profile="woodcutting", overlay_debug_target_limit=2),
                {"tickId": 1, "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10}},
                candidates,
                {"collisionWindowAvailable": True, "collisionWindowRadius": 24, "playerSceneX": 10, "playerSceneY": 10},
                {"budgetExceeded": False, "writeFailureCount": 0, "warnings": []},
                "2026-01-01T00:00:00Z",
                [{"tick": 1, "eventType": "best_candidate_changed", "severity": "info", "summary": "Best candidate changed: Tree at 3201,3201"}],
            )

            self.assertEqual(state["schema"], "telemetry_overlay_debug_state.v1")
            self.assertEqual(state["summary"]["candidateCount"], 5)
            self.assertEqual(state["summary"]["targetsWritten"], 2)
            self.assertEqual(state["summary"]["targetsSuppressedByCap"], 3)
            self.assertEqual(state["summary"]["clickableHullTargets"], 1)
            self.assertEqual(state["summary"]["clickboxPolygonTargets"], 1)
            self.assertEqual(state["summary"]["convexHullTargets"], 1)
            self.assertEqual(state["latestEventSummary"], "Best candidate changed: Tree at 3201,3201")
            self.assertEqual(state["latestEventTick"], 1)
            self.assertEqual(state["lastEventTick"], 1)
            self.assertEqual(state["warningEventCount"], 0)
            self.assertEqual(len(state["targets"]), 2)
            self.assertEqual(state["targets"][0]["aimPoint"]["canvasX"], 100)
            self.assertEqual(state["targets"][0]["bounds"]["width"], 10)
            self.assertTrue(state["targets"][0]["clickableHullAvailable"])
            self.assertEqual(state["targets"][0]["geometrySource"], "clickableHull")
            self.assertIn("clickableHull", state["targets"][0])
            self.assertIn("clickboxPolygon", state["targets"][0])
            self.assertIn("points", state["targets"][0]["clickableHull"])
            self.assertNotIn("clickableHull", state["targets"][1])
            self.assertEqual(state["targets"][1]["geometrySource"], "bounds")
            self.assertEqual(state["targets"][0]["directReachability"], "reachable")
            self.assertEqual(state["targets"][0]["livenessInterpretation"], "assumed")
            self.assertEqual(state["targets"][0]["labelParts"]["reachability"], "R")
            self.assertEqual(state["targets"][0]["overlayColor"], "green")
            self.assertNotIn("BLOCK", state["targets"][0]["overlayLabel"])

    def test_overlay_debug_state_reports_invalid_sentinel_aimpoint_as_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            candidate = {
                "rank": 1,
                "classId": "tree",
                "name": "Oak tree",
                "id": 10820,
                "objectKey": "oak-edge",
                "worldX": 3189,
                "worldY": 3248,
                "plane": 0,
                "onScreen": True,
                "geometryAvailable": True,
                "qualityTier": "excellent",
                "qualityScore": 1.0,
                "targetLiveState": "live",
                "aimPointContext": {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"},
                "geometrySummary": {"bounds": {"x": 2147483647, "y": 2147483647, "width": 1, "height": 1}},
                "navigation": {"directReachability": "reachable", "reachabilityConfidence": 0.9},
            }

            state = live.overlay_debug_state_for(
                session,
                live_args(profile="woodcutting", overlay_debug_target_limit=1),
                {
                    "tickId": 1,
                    "canvasWidth": 765,
                    "canvasHeight": 503,
                    "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10},
                },
                [candidate],
                {"collisionWindowAvailable": True, "collisionWindowRadius": 24, "playerSceneX": 10, "playerSceneY": 10},
                {"budgetExceeded": False, "writeFailureCount": 0, "warnings": []},
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(state["summary"]["targetsWritten"], 1)
            self.assertEqual(state["summary"]["safeAimpoints"], 0)
            self.assertEqual(state["summary"]["executableTargets"], 0)
            self.assertEqual(state["summary"]["invalidAimpointTargets"], 1)
            self.assertFalse(state["summary"]["selectedSafeAimPoint"])
            self.assertIsNone(state["targets"][0]["aimPoint"])
            self.assertIsNone(state["targets"][0]["bounds"])
            self.assertFalse(state["targets"][0]["actionable"])
            self.assertEqual(state["targets"][0]["validButUnsafeReason"], "invalidAimPoint")

    def test_overlay_debug_state_hull_limit_is_applied_after_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(Path(tmp), [raw_tick(1)])
            candidates = []
            for index in range(4):
                candidates.append(
                    {
                        "rank": index + 1,
                        "classId": "tree",
                        "name": "Tree",
                        "id": 1276,
                        "objectKey": f"tree-{index}",
                        "worldX": 3200 + index,
                        "worldY": 3201,
                        "plane": 0,
                        "sceneX": index,
                        "sceneY": 1,
                        "distanceTiles": index + 1,
                        "onScreen": True,
                        "geometryAvailable": True,
                        "targetLiveState": "live_assumed",
                        "aimPointContext": {"canvasX": 100 + index, "canvasY": 120, "source": "test"},
                        "geometrySummary": {"bounds": {"x": 95 + index, "y": 115, "width": 10, "height": 10}},
                        "geometry": {
                            "clickboxPolygon": [[90 + index, 110], [110 + index, 110], [110 + index, 130], [90 + index, 130]],
                        },
                        "navigation": {"directReachability": "reachable", "reachabilityConfidence": 0.9},
                    }
                )

            state = live.overlay_debug_state_for(
                session,
                live_args(profile="woodcutting", overlay_debug_target_limit=4, overlay_debug_hull_limit=2),
                {"tickId": 1, "localPlayer": {"worldX": 3200, "worldY": 3200, "plane": 0, "sceneX": 10, "sceneY": 10}},
                candidates,
                {"collisionWindowAvailable": True, "collisionWindowRadius": 24, "playerSceneX": 10, "playerSceneY": 10},
                {"budgetExceeded": False, "writeFailureCount": 0, "warnings": []},
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(state["summary"]["hullLimit"], 2)
            self.assertEqual(state["summary"]["clickableHullTargets"], 2)
            self.assertTrue(state["summary"]["bestHullAvailable"])
            self.assertTrue(state["targets"][0]["clickableHullAvailable"])
            self.assertTrue(state["targets"][1]["clickableHullAvailable"])
            self.assertFalse(state["targets"][2]["clickableHullAvailable"])
            self.assertEqual(state["targets"][2]["clickableHullMissingReason"], "omitted by overlay hull cap")
            self.assertEqual(state["summary"]["hullRankBuckets"]["rank1"], 1)
            self.assertEqual(state["summary"]["hullRankBuckets"]["ranks2to5"], 1)
            self.assertEqual(state["summary"]["polygonTargetsSuppressedByHullCap"], 2)

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
            self.assertIn("summary", state)
            self.assertIn("targets", state)

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

        with mock.patch.object(
            sys,
            "argv",
            [
                "live_target_processor.py",
                "--session",
                "s",
                "--once",
                "--input-source",
                "plugin-snapshot",
                "--plugin-snapshot-port",
                "8893",
                "--plugin-snapshot-response-mode",
                "compact",
                "--plugin-snapshot-projection-field-mode",
                "compact",
            ],
        ):
            args = live.parse_args()
            self.assertEqual(args.input_source, "plugin-snapshot")
            self.assertEqual(args.plugin_snapshot_port, 8893)
            self.assertEqual(args.plugin_snapshot_projection_field_mode, "compact")
            self.assertFalse(args.auto_prefer_plugin_snapshot)

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
                    "--compact-stream-port",
                    "1",
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

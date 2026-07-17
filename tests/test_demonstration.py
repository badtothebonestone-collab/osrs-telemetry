from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image

from osrs_bot.demonstration import (
    DemonstrationError,
    DemonstrationLimitReached,
    DemonstrationRecorder,
    WORLD_MODEL_GAP_GRACE_SECONDS,
    _derive_summary,
    _derive_timing_profiles,
    _pose_delta,
    _read_events_unverified,
    _timeline_markdown,
    inspect_demonstration,
    record_live,
)
from osrs_bot.model import (
    CAMERA_YAW_UNITS,
    NearbyObject,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    WorldPoint,
)
from osrs_bot.observation import DemonstrationEvidenceSnapshot, parse_observation
from osrs_bot.screen_capture import CaptureMetadata


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "snapshot_loaded.json"


def _base_observation(*, tick: int = 174, plane: int = 1):
    observation = parse_observation(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return replace(
        observation,
        tick=tick,
        plane=plane,
        location=WorldPoint(3205, 3209 + (tick - 174), plane),
    )


def _hot(
    *samples: dict[str, Any], drops: tuple[int, int, int, int] = (0, 0, 0, 0)
):
    lanes = {
        "clientTickTail": [],
        "postMenuSortTail": [],
        "clickedTail": [],
        "cameraInputTail": [],
    }
    for sample in samples:
        lane = sample["eventLane"]
        wall_time = sample.get("wallTimeMillis")
        if isinstance(wall_time, int) and not isinstance(wall_time, bool):
            sample.setdefault("monotonicTimeNanos", wall_time * 1_000_000)
        if lane == "menu_option_clicked":
            sample.setdefault("consumed", False)
        key = {
            "client_tick": "clientTickTail",
            "post_menu_sort": "postMenuSortTail",
            "menu_option_clicked": "clickedTail",
            "camera_input": "cameraInputTail",
        }[lane]
        lanes[key].append(sample)
    return {
        "schema": "client_tick_hot.v1",
        "clientTick": 900,
        **lanes,
        "latency": {
            "droppedClientTickSamples": drops[0],
            "droppedPostMenuSortSamples": drops[1],
            "droppedClickedSamples": drops[2],
            "droppedCameraInputSamples": drops[3],
        },
    }


def _evidence(observation, hot: dict[str, Any]) -> DemonstrationEvidenceSnapshot:
    hot = json.loads(json.dumps(hot))
    sequences = []
    for key in (
        "clientTickTail",
        "postMenuSortTail",
        "clickedTail",
        "cameraInputTail",
    ):
        for sample in hot[key]:
            sample["sessionId"] = observation.session_id
            sample["clientProcessId"] = observation.client_process_id
            sequences.append(sample["eventSequence"])
    hot.update(
        sessionId=observation.session_id,
        clientProcessId=observation.client_process_id,
        latestEventSequence=max(sequences, default=0),
    )
    captured_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": "plugin_snapshot_response.v2",
        "sensorFrame": {"schema": "sensor_frame.v1"},
        "payloads": {
            "baseline": {
                "clientTick": 900,
                "player": {"sceneX": 45, "sceneY": 51, "localX": 5760, "localY": 6528},
                "cameraViewport": {
                    "cameraX": 100,
                    "cameraY": 200,
                    "cameraZ": 300,
                    "cameraYaw": observation.camera_yaw,
                    "cameraPitch": observation.camera_pitch,
                    "viewportXOffset": 0,
                    "viewportYOffset": 0,
                    "viewportWidth": 400,
                    "viewportHeight": 300,
                    "canvasWidth": 400,
                    "canvasHeight": 300,
                },
                "inputGeometry": {
                    "schema": "input_geometry.v1",
                    "sourceTick": observation.tick,
                    "geometryAvailable": True,
                    "sourceCanvasWidth": 400,
                    "sourceCanvasHeight": 300,
                    "canvasWidth": 800,
                    "canvasHeight": 600,
                    "canvasScreenX": 1000,
                    "canvasScreenY": 2000,
                    "coordinateSpace": "device_pixels",
                    "isCanvasShowing": True,
                    "isClientFocused": True,
                    "clientProcessId": observation.client_process_id,
                },
            },
            "client_tick_tail": hot,
            "scene_object_census": {
                "schema": "scene_object_census.v1",
                "sourceTick": observation.tick,
                "capturedAtUtc": captured_at,
                "sessionId": observation.session_id,
                "clientProcessId": observation.client_process_id,
                "geometryFrameId": observation.geometry_frame_id,
                "count": 64,
                "returned": 64,
                "capHit": False,
                "objectCensusCapHit": True,
                "objects": [],
            },
            "actor_census": {
                "schema": "world_model_actor_census.v1",
                "clientTick": 900,
                "sourceTick": observation.tick,
                "capturedAtUtc": captured_at,
                "sessionId": observation.session_id,
                "clientProcessId": observation.client_process_id,
                "geometryFrameId": observation.geometry_frame_id,
                "radiusTiles": 16,
                "count": 1,
                "returned": 1,
                "capHit": False,
                "actors": [
                    {
                        "type": "NPC",
                        "index": 4,
                        "id": 4626,
                        "name": "Hans",
                        "actions": ["Talk-to"],
                        "worldX": 3205,
                        "worldY": 3210,
                        "plane": observation.plane,
                        "sceneX": 45,
                        "sceneY": 52,
                        "localX": 5760,
                        "localY": 6656,
                        "distanceToPlayer": 1,
                    },
                    {"type": "PLAYER", "name": "must-not-leak"},
                ],
            },
            "collision_window": {
                "schema": "world_model_collision_window.v1",
                "clientTick": 900,
                "sourceTick": observation.tick,
                "capturedAtUtc": captured_at,
                "sessionId": observation.session_id,
                "clientProcessId": observation.client_process_id,
                "geometryFrameId": observation.geometry_frame_id,
                "collisionAvailable": True,
                "radiusTiles": 16,
                "cellCount": 1,
                "cellCapHit": True,
                "collisionHash": "fixture-collision",
                "cells": [
                    {
                        "worldX": 3205,
                        "worldY": 3210,
                        "plane": observation.plane,
                        "sceneX": 45,
                        "sceneY": 52,
                        "flags": 0,
                        "blockedMovement": False,
                    }
                ],
            },
        },
    }
    request = {
        "schema": "plugin_snapshot_request.v1",
        "needs": [
            "scene_object_census",
            "client_tick_tail",
            "actor_census",
            "collision_window",
        ],
        "menuEntryLimit": 16,
    }
    return DemonstrationEvidenceSnapshot(
        observation=observation,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        request_json=json.dumps(request, sort_keys=True, separators=(",", ":")),
        fetched_at_utc=datetime.now(timezone.utc),
    )


def _camera_pose(yaw: int, *, pitch: int = 300, frame: str = "camera-1"):
    return {
        "schema": "camera_pose.v1",
        "cameraX": 100,
        "cameraY": 200,
        "cameraZ": 300,
        "cameraYaw": yaw,
        "cameraPitch": pitch,
        "cameraYawTarget": yaw,
        "cameraPitchTarget": pitch,
        "zoom3d": 512,
        "viewportXOffset": 0,
        "viewportYOffset": 0,
        "viewportWidth": 400,
        "viewportHeight": 300,
        "canvasWidth": 400,
        "canvasHeight": 300,
        "geometryFrameId": frame,
    }


def _transient_world_model_evidence(
    observation, hot: dict[str, Any]
) -> DemonstrationEvidenceSnapshot:
    evidence = _evidence(observation, hot)
    payload = json.loads(evidence.payload_json)
    payload.update(
        status="WARN",
        warnings=["world_model_provenance_mismatch"],
        missingCapabilities=[
            "scene_object_census",
            "actor_census",
            "collision_window",
        ],
    )
    for name in ("scene_object_census", "actor_census", "collision_window"):
        payload["payloads"].pop(name)
    return replace(
        evidence,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _transient_interaction_hot_evidence(
    observation, hot: dict[str, Any]
) -> DemonstrationEvidenceSnapshot:
    evidence = _evidence(observation, hot)
    payload = json.loads(evidence.payload_json)
    interaction_hot = {
        "schema": "client_tick_hot.v1",
        "sourceTick": max(0, observation.tick - 1),
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sessionId": observation.session_id,
        "clientProcessId": observation.client_process_id,
        "clientTick": 900,
    }
    payload["payloads"]["interaction_hot"] = interaction_hot
    payload["clientTickHot"] = json.loads(json.dumps(interaction_hot))
    payload.update(
        status="WARN",
        warnings=["menu_evidence_provenance_mismatch_or_stale"],
        missingCapabilities=["interaction_hot"],
    )
    return replace(
        evidence,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _transient_dynamic_handoff_evidence(
    observation, hot: dict[str, Any]
) -> DemonstrationEvidenceSnapshot:
    evidence = _evidence(observation, hot)
    payload = json.loads(evidence.payload_json)
    interaction_hot = {
        "schema": "client_tick_hot.v1",
        "sourceTick": max(0, observation.tick - 1),
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sessionId": observation.session_id,
        "clientProcessId": observation.client_process_id,
        "clientTick": 900,
    }
    payload["payloads"]["interaction_hot"] = interaction_hot
    payload["clientTickHot"] = json.loads(json.dumps(interaction_hot))
    payload.update(
        status="WARN",
        warnings=[
            "world_model_provenance_mismatch",
            "menu_evidence_provenance_mismatch_or_stale",
        ],
        missingCapabilities=[
            "scene_object_census",
            "actor_census",
            "collision_window",
            "interaction_hot",
        ],
    )
    for name in ("scene_object_census", "actor_census", "collision_window"):
        payload["payloads"].pop(name)
    return replace(
        evidence,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _record_fixture(root: Path) -> Path:
    first = _evidence(
        _base_observation(),
        _hot(
            {
                "eventSequence": 1,
                "eventLane": "client_tick",
                "clientTick": 899,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1000,
                "mouseCanvasX": 20,
                "mouseCanvasY": 30,
                "isInCanvas": True,
            }
        ),
    )
    recorder = DemonstrationRecorder(
        "castle-stairs", output_root=root, screenshots_enabled=False
    )
    recorder.start(first)
    click = {
        "eventSequence": 2,
        "eventLane": "menu_option_clicked",
        "clientTick": 900,
        "gameTickAtSample": 174,
        "wallTimeMillis": 1100,
        "option": "Climb-up",
        "target": "<col=ffff00>Staircase</col>",
        "type": "GAME_OBJECT_FIRST_OPTION",
        "identifier": 16672,
        "param0": 45,
        "param1": 51,
        "mouseCanvasX": 80,
        "mouseCanvasY": 90,
        "isInCanvas": True,
    }
    assert recorder.add(_evidence(_base_observation(), _hot(click)))
    pointer_one = {
        "eventSequence": 3,
        "eventLane": "client_tick",
        "clientTick": 901,
        "gameTickAtSample": 175,
        "wallTimeMillis": 1200,
        "mouseCanvasX": 100,
        "mouseCanvasY": 110,
        "isInCanvas": True,
    }
    pointer_too_soon = {
        "eventSequence": 4,
        "eventLane": "client_tick",
        "clientTick": 902,
        "gameTickAtSample": 175,
        "wallTimeMillis": 1220,
        "mouseCanvasX": 101,
        "mouseCanvasY": 111,
        "isInCanvas": True,
    }
    after = replace(
        _base_observation(tick=175, plane=2),
        location=WorldPoint(3205, 3210, 2),
    )
    assert recorder.add(
        _evidence(after, _hot(click, pointer_one, pointer_too_soon))
    )
    return recorder.finish("test_complete")


def _rehash_file(artifact: Path, relative: str) -> None:
    path = artifact / relative
    hashes_path = artifact / "hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    for entry in hashes["files"]:
        if entry["path"] == relative:
            data = path.read_bytes()
            entry["sizeBytes"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            break
    else:
        raise AssertionError(f"missing hash entry for {relative}")
    hashes_path.write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class DemonstrationRecorderTests(unittest.TestCase):
    def test_full_fixed_point_camera_yaw_range_and_wraparound_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = replace(
                _base_observation(),
                camera_yaw=9_000,
                camera_pitch=1_024,
            )
            recorder = DemonstrationRecorder(
                "full-yaw-range",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            sample = {
                "eventSequence": 1,
                "eventLane": "client_tick",
                "clientTick": 901,
                "gameTickAtSample": observation.tick,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 10,
                "mouseCanvasY": 10,
                "isInCanvas": True,
                "cameraPose": _camera_pose(9_000, pitch=1_024),
            }

            recorder.start(_evidence(observation, _hot(sample)))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(
                128,
                _pose_delta(
                    {"cameraYaw": CAMERA_YAW_UNITS - 64},
                    {"cameraYaw": 64},
                )["yaw"],
            )

    def test_external_stop_finalizes_a_verified_artifact(self) -> None:
        class Client:
            disable_calls = 0

            @staticmethod
            def fetch_demonstration_evidence():
                return _evidence(_base_observation(), _hot())

            @classmethod
            def disable_demonstration_capture(cls):
                cls.disable_calls += 1

        with tempfile.TemporaryDirectory() as temporary:
            artifact = record_live(
                "facade-stop",
                Client(),  # type: ignore[arg-type]
                output_root=Path(temporary),
                duration_seconds=1.0,
                poll_seconds=0.02,
                screenshots_enabled=False,
                stop_requested=lambda: True,
            )
            result = inspect_demonstration(artifact)
            manifest = json.loads((artifact / "manifest.json").read_text())

            self.assertTrue(result.valid, result.errors)
            self.assertEqual("facade_stop_requested", result.stop_reason)
            self.assertEqual(1.0, result.requested_duration_seconds)
            self.assertEqual("facade_stop_requested", manifest["stopReason"])
            self.assertEqual(1.0, manifest["requestedDurationSeconds"])
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                1.0, events[0]["payload"]["requestedDurationSeconds"]
            )
            self.assertEqual(1, Client.disable_calls)

    def test_capture_disable_failure_is_a_gap_and_still_finalizes(self) -> None:
        class Client:
            @staticmethod
            def fetch_demonstration_evidence():
                return _evidence(_base_observation(), _hot())

            @staticmethod
            def disable_demonstration_capture():
                raise OSError("test disable failure")

        with tempfile.TemporaryDirectory() as temporary:
            artifact = record_live(
                "disable-failure",
                Client(),  # type: ignore[arg-type]
                output_root=Path(temporary),
                duration_seconds=1.0,
                poll_seconds=0.02,
                screenshots_enabled=False,
                stop_requested=lambda: True,
            )
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertIn("camera_capture_disable_failed", result.coverage_gaps)

    def test_record_live_retries_one_exact_startup_world_model_gap(self) -> None:
        class Client:
            calls = 0

            @classmethod
            def fetch_demonstration_evidence(cls):
                cls.calls += 1
                if cls.calls == 1:
                    unavailable = replace(
                        _base_observation(),
                        status="WARN",
                        missing_capabilities=(
                            "scene_object_census",
                            "actor_census",
                            "collision_window",
                        ),
                        warnings=("world_model_provenance_mismatch",),
                    )
                    return _transient_world_model_evidence(unavailable, _hot())
                return _evidence(_base_observation(), _hot())

        with tempfile.TemporaryDirectory() as temporary:
            artifact = record_live(
                "startup-world-model-retry",
                Client(),  # type: ignore[arg-type]
                output_root=Path(temporary),
                duration_seconds=1.0,
                poll_seconds=0.02,
                screenshots_enabled=False,
                stop_requested=lambda: Client.calls >= 2,
            )
            result = inspect_demonstration(artifact)

            self.assertEqual(2, Client.calls)
            self.assertTrue(result.valid, result.errors)

    def test_record_live_bounds_persistent_startup_world_model_gap(self) -> None:
        class Client:
            calls = 0

            @classmethod
            def fetch_demonstration_evidence(cls):
                cls.calls += 1
                unavailable = replace(
                    _base_observation(),
                    status="WARN",
                    missing_capabilities=(
                        "scene_object_census",
                        "actor_census",
                        "collision_window",
                    ),
                    warnings=("world_model_provenance_mismatch",),
                )
                return _transient_world_model_evidence(unavailable, _hot())

        with tempfile.TemporaryDirectory() as temporary, patch(
            "osrs_bot.demonstration.time.monotonic",
            side_effect=(0.0, WORLD_MODEL_GAP_GRACE_SECONDS + 0.01),
        ):
            with self.assertRaisesRegex(
                DemonstrationError,
                "world-model evidence remained unavailable",
            ):
                record_live(
                    "startup-world-model-timeout",
                    Client(),  # type: ignore[arg-type]
                    output_root=Path(temporary),
                    duration_seconds=1.0,
                    poll_seconds=0.02,
                    screenshots_enabled=False,
                )

            self.assertEqual(1, Client.calls)
            self.assertEqual([], list(Path(temporary).iterdir()))

    def test_records_deduplicated_semantic_before_after_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertIn(
                "clicked Staircase 16672 with Climb-up at plane 1, then observed plane 2",
                result.semantic_summary,
            )
            self.assertEqual(("Climb-up",), result.selected_menu_options)
            self.assertEqual(16672, result.interacted_entities[0]["menuIdentifier"])
            self.assertFalse(
                any(
                    value["kind"] == "interaction_fact_candidate"
                    for value in result.candidate_suggestions
                )
            )
            self.assertEqual(2, result.route_points[-1]["plane"])
            self.assertTrue(
                any(value["field"] == "player.plane" for value in result.state_changes)
            )
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                1, sum(event["kind"] == "pointer_sample" for event in events)
            )
            observation = next(event for event in events if event["kind"] == "observation")
            self.assertEqual("NPC", observation["payload"]["nearbyNpcs"][0]["type"])
            self.assertNotIn("must-not-leak", json.dumps(observation))
            self.assertFalse(observation["payload"]["collisionCells"][0]["blocked"])
            self.assertIn("collision window cell cap reached", result.coverage_gaps)
            self.assertIn("scene object acquisition cap reached", result.coverage_gaps)
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("client_tick_hot.v1", manifest["schemas"]["clientTickHot"])
            self.assertEqual(
                "world_model_actor_census.v1", manifest["schemas"]["actorCensus"]
            )
            self.assertEqual(
                "world_model_collision_window.v1",
                manifest["schemas"]["collisionWindow"],
            )

    def test_mixed_camera_input_links_to_exact_walk_and_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder = DemonstrationRecorder(
                "mixed-camera-walk",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(before, _hot()))
            samples = (
                {"eventSequence": 1, "eventLane": "client_tick", "clientTick": 900, "gameTickAtSample": 174, "wallTimeMillis": 1000, "mouseCanvasX": 10, "mouseCanvasY": 10, "isInCanvas": True, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "A", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1050, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 3, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "A", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 1150, "holdDurationMillis": 100, "cameraPose": _camera_pose(128)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 4, "eventLane": "camera_input", "inputKind": "middle_drag", "phase": "press", "control": "MIDDLE", "clientTick": 903, "gameTickAtSample": 174, "wallTimeMillis": 1160, "canvasX": 20, "canvasY": 20, "cameraPose": _camera_pose(128)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 5, "eventLane": "camera_input", "inputKind": "middle_drag", "phase": "drag", "control": "MIDDLE", "clientTick": 904, "gameTickAtSample": 174, "wallTimeMillis": 1200, "canvasX": 23, "canvasY": 24, "deltaX": 3, "deltaY": 4, "cameraPose": _camera_pose(192)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 6, "eventLane": "camera_input", "inputKind": "middle_drag", "phase": "release", "control": "MIDDLE", "clientTick": 905, "gameTickAtSample": 174, "wallTimeMillis": 1250, "holdDurationMillis": 90, "pathDistancePixels": 13.0, "totalDeltaX": 10, "totalDeltaY": 6, "dragSampleCount": 3, "cameraPose": _camera_pose(256)},
                {"eventSequence": 7, "eventLane": "client_tick", "clientTick": 906, "gameTickAtSample": 174, "wallTimeMillis": 1260, "mouseCanvasX": 20, "mouseCanvasY": 20, "isInCanvas": True, "cameraPose": _camera_pose(256)},
                {"eventSequence": 8, "eventLane": "client_tick", "clientTick": 907, "gameTickAtSample": 174, "wallTimeMillis": 1320, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(256)},
                {"eventSequence": 9, "eventLane": "post_menu_sort", "clientTick": 908, "gameTickAtSample": 174, "wallTimeMillis": 1350, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(256), "entryCount": 1, "entries": [{"option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 50, "param1": 52}]},
                {"eventSequence": 10, "eventLane": "post_menu_sort", "clientTick": 909, "gameTickAtSample": 174, "wallTimeMillis": 1390, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(256), "entryCount": 1, "entries": [{"option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 99, "param1": 99}]},
                {"eventSequence": 11, "eventLane": "menu_option_clicked", "clientTick": 910, "gameTickAtSample": 174, "wallTimeMillis": 1400, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(256), "geometryFrameId": "camera-1", "option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 50, "param1": 52, "resolvedTarget": {"schema": "plugin_click_target.v1", "resolution": "resolved", "confidence": "exact", "actionFamily": "walk_tile", "source": "menu_params", "worldTile": {"worldX": 3210, "worldY": 3220, "plane": 1}, "menuParamTile": {"x": 50, "y": 52}, "selectedSceneTile": {"x": 50, "y": 52}}},
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            after = replace(_base_observation(tick=175), camera_yaw=256, camera_pitch=300)
            self.assertTrue(recorder.add(_evidence(after, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            episode = result.camera_intent_episodes[0]
            self.assertEqual("action_linked", episode["classification"])
            self.assertEqual("mixed", episode["observedInputMethod"])
            self.assertEqual(256, episode["cameraPoseDelta"]["yaw"])
            self.assertTrue(episode["effectiveCameraChangeObserved"])
            self.assertEqual(13.0, episode["maxDragPathPixels"])
            self.assertEqual(100, episode["maxControlHoldMillis"])
            self.assertEqual(200, episode["episodeInputSpanMillis"])
            self.assertEqual(150, episode["lastCameraInputToClickMillis"])
            self.assertEqual({"x": 3210, "y": 3220, "plane": 1}, episode["target"]["world"])
            timing = result.timing_profiles[0]
            self.assertEqual(60, timing["pointerMovementDurationMillis"])
            self.assertEqual(140, timing["settleMillis"])
            self.assertEqual(50, timing["hoverToClickMillis"])

    def test_intervening_semantic_click_prevents_later_camera_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder = DemonstrationRecorder(
                "camera-intent-target-family",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(before, _hot()))
            samples = (
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 1,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "press",
                    "control": "RIGHT",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1000,
                    "cameraPose": _camera_pose(0),
                },
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 2,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "release",
                    "control": "RIGHT",
                    "clientTick": 902,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1100,
                    "holdDurationMillis": 100,
                    "cameraPose": _camera_pose(64),
                },
                {
                    "eventSequence": 3,
                    "eventLane": "menu_option_clicked",
                    "clientTick": 903,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1150,
                    "mouseCanvasX": 40,
                    "mouseCanvasY": 45,
                    "isInCanvas": True,
                    "cameraPose": _camera_pose(64),
                    "option": "Continue",
                    "target": "",
                    "type": "WIDGET_CONTINUE",
                    "identifier": 0,
                    "param0": 1,
                    "param1": 2,
                    "resolvedTarget": {
                        "schema": "plugin_click_target.v1",
                        "resolution": "exact",
                        "confidence": "exact",
                        "actionFamily": "widget",
                        "source": "test_fixture",
                    },
                },
                {
                    "eventSequence": 4,
                    "eventLane": "menu_option_clicked",
                    "clientTick": 904,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1250,
                    "mouseCanvasX": 80,
                    "mouseCanvasY": 90,
                    "isInCanvas": True,
                    "cameraPose": _camera_pose(64),
                    "geometryFrameId": "camera-1",
                    "option": "Walk here",
                    "target": "",
                    "type": "WALK",
                    "identifier": 0,
                    "param0": 50,
                    "param1": 52,
                    "resolvedTarget": {
                        "schema": "plugin_click_target.v1",
                        "resolution": "resolved",
                        "confidence": "exact",
                        "actionFamily": "walk_tile",
                        "source": "menu_params",
                        "worldTile": {
                            "worldX": 3210,
                            "worldY": 3220,
                            "plane": 1,
                        },
                    },
                },
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            after = replace(_base_observation(tick=175), camera_yaw=64, camera_pitch=300)
            self.assertTrue(recorder.add(_evidence(after, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(1, len(result.camera_intent_episodes))
            episode = result.camera_intent_episodes[0]
            self.assertEqual("exploratory_or_unassociated", episode["classification"])
            self.assertIsNone(episode["target"])
            self.assertEqual(64, episode["cameraPoseDelta"]["yaw"])
            self.assertEqual(2, len(result.timing_profiles))
            self.assertIsNone(result.timing_profiles[0]["inputMethod"])
            self.assertIsNone(result.timing_profiles[1]["inputMethod"])

    def test_high_confidence_resolved_walk_gets_review_only_camera_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder = DemonstrationRecorder(
                "camera-high-walk",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(before, _hot()))
            samples = (
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 1,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "press",
                    "control": "W",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1_000,
                    "cameraPose": _camera_pose(0),
                },
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 2,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "release",
                    "control": "W",
                    "clientTick": 902,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1_200,
                    "holdDurationMillis": 200,
                    "cameraPose": _camera_pose(96),
                },
                {
                    "eventSequence": 3,
                    "eventLane": "menu_option_clicked",
                    "clientTick": 903,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1_350,
                    "mouseCanvasX": 80,
                    "mouseCanvasY": 90,
                    "isInCanvas": True,
                    "cameraPose": _camera_pose(96),
                    "option": "Walk here",
                    "target": "",
                    "type": "WALK",
                    "identifier": 0,
                    "param0": 55,
                    "param1": 58,
                    "resolvedTarget": {
                        "schema": "plugin_click_target.v1",
                        "resolution": "resolved",
                        "confidence": "high",
                        "actionFamily": "walk_tile",
                        "source": "selected_scene_tile",
                        "selectedSceneTile": {"x": 55, "y": 58},
                    },
                },
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            episode = result.camera_intent_episodes[0]
            self.assertEqual("action_linked_candidate", episode["classification"])
            self.assertEqual("medium", episode["associationConfidence"])
            self.assertEqual("walk_tile", episode["target"]["actionFamily"])
            self.assertTrue(episode["reviewOnly"])
            self.assertFalse(episode["automaticConfigurationAllowed"])

    def test_overlapping_camera_keys_keep_press_release_in_one_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder = DemonstrationRecorder(
                "overlapping-camera-keys",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(observation, _hot()))
            samples = (
                {"schema": "plugin_camera_input.v1", "eventSequence": 1, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "LEFT", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1_000, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "UP", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 2_400, "cameraPose": _camera_pose(64)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 3, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "UP", "clientTick": 903, "gameTickAtSample": 174, "wallTimeMillis": 2_500, "holdDurationMillis": 100, "cameraPose": _camera_pose(96)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 4, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "LEFT", "clientTick": 904, "gameTickAtSample": 174, "wallTimeMillis": 2_761, "holdDurationMillis": 1_761, "cameraPose": _camera_pose(128)},
                {"eventSequence": 5, "eventLane": "menu_option_clicked", "clientTick": 905, "gameTickAtSample": 174, "wallTimeMillis": 2_900, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(128), "option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 55, "param1": 58, "resolvedTarget": {"schema": "plugin_click_target.v1", "resolution": "resolved", "confidence": "high", "actionFamily": "walk_tile", "source": "selected_scene_tile", "selectedSceneTile": {"x": 55, "y": 58}}},
            )
            self.assertTrue(recorder.add(_evidence(observation, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(1, len(result.camera_intent_episodes))
            episode = result.camera_intent_episodes[0]
            self.assertEqual("action_linked_candidate", episode["intentClassification"])
            self.assertEqual("keyboard", episode["observedInputMethod"])
            self.assertIsNotNone(episode["clickEventSequence"])
            self.assertEqual([3, 4, 5, 6], episode["cameraInputEventSequences"])
            self.assertEqual(7, episode["clickEventSequence"])
            self.assertEqual(128, episode["cameraPoseDelta"]["yaw"])
            self.assertEqual(1_761, episode["episodeInputSpanMillis"])
            self.assertEqual(1_761, episode["maxControlHoldMillis"])
            self.assertLessEqual(
                episode["maxControlHoldMillis"], episode["episodeInputSpanMillis"]
            )
            self.assertNotIn(
                "cancelled_or_ineffective",
                {value["intentClassification"] for value in result.camera_intent_episodes},
            )
            pattern = result.camera_review_episodes[0]["cameraControlPattern"]
            self.assertEqual("camera_control_pattern_review.v1", pattern["schema"])
            self.assertEqual("coarse_then_fine", pattern["patternClassification"])
            self.assertEqual(1, pattern["coarseHoldCount"])
            self.assertEqual(1, pattern["fineHoldCount"])
            self.assertEqual(1, pattern["yawPitchChordCount"])
            self.assertEqual(
                100,
                pattern["yawPitchChordIntervals"][0]["durationMillis"],
            )
            self.assertEqual(
                "action_linked_candidate",
                pattern["associationStatus"],
            )
            self.assertTrue(pattern["reviewOnly"])
            self.assertFalse(pattern["automaticConfigurationAllowed"])

    def test_exact_object_camera_link_allows_2501ms_but_walk_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = _base_observation()
            recorder = DemonstrationRecorder(
                "exact-object-camera-lookback",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(observation, _hot()))
            exact_object = {
                "schema": "plugin_click_target.v1",
                "resolution": "exact",
                "confidence": "exact",
                "actionFamily": "tile_object",
                "activationKind": "context_menu_row",
                "source": "menu_identifier_scene_coordinates",
                "object": {"objectKey": "bank-booth:47:53:18491", "id": 18491, "kind": "GAME_OBJECT", "worldX": 3207, "worldY": 3211, "plane": 1, "sceneX": 47, "sceneY": 53},
                "geometry": {"geometryFrameId": observation.geometry_frame_id, "source": "clickbox", "polygon": [{"x": 100, "y": 100}, {"x": 160, "y": 100}, {"x": 160, "y": 180}, {"x": 100, "y": 180}], "bounds": {"x": 100, "y": 100, "width": 60, "height": 80}, "clickInside": None},
            }
            samples = (
                {"schema": "plugin_camera_input.v1", "eventSequence": 1, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "RIGHT", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1_000, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "RIGHT", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 1_100, "holdDurationMillis": 100, "cameraPose": _camera_pose(64)},
                {"eventSequence": 3, "eventLane": "menu_option_clicked", "clientTick": 903, "gameTickAtSample": 174, "wallTimeMillis": 3_601, "mouseCanvasX": 350, "mouseCanvasY": 260, "isInCanvas": True, "cameraPose": _camera_pose(64), "geometryFrameId": observation.geometry_frame_id, "option": "Bank", "target": "Bank booth", "type": "GAME_OBJECT_SECOND_OPTION", "identifier": 18491, "param0": 47, "param1": 53, "resolvedTarget": exact_object},
                {"schema": "plugin_camera_input.v1", "eventSequence": 4, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "UP", "clientTick": 904, "gameTickAtSample": 174, "wallTimeMillis": 4_000, "cameraPose": _camera_pose(64)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 5, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "UP", "clientTick": 905, "gameTickAtSample": 174, "wallTimeMillis": 4_100, "holdDurationMillis": 100, "cameraPose": _camera_pose(96)},
                {"eventSequence": 6, "eventLane": "menu_option_clicked", "clientTick": 906, "gameTickAtSample": 174, "wallTimeMillis": 6_601, "mouseCanvasX": 80, "mouseCanvasY": 90, "isInCanvas": True, "cameraPose": _camera_pose(96), "option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 55, "param1": 58, "resolvedTarget": {"schema": "plugin_click_target.v1", "resolution": "resolved", "confidence": "high", "actionFamily": "walk_tile", "source": "selected_scene_tile", "selectedSceneTile": {"x": 55, "y": 58}}},
            )
            self.assertTrue(recorder.add(_evidence(observation, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            linked = [
                episode
                for episode in result.camera_intent_episodes
                if episode.get("clickEventSequence") is not None
            ]
            self.assertEqual(1, len(linked))
            self.assertEqual("tile_object", linked[0]["target"]["actionFamily"])
            self.assertEqual(2_501, linked[0]["lastCameraInputToClickMillis"])
            exploratory = [
                episode
                for episode in result.camera_intent_episodes
                if episode["intentClassification"] == "exploratory_or_unassociated"
            ]
            self.assertEqual(1, len(exploratory))
            self.assertIsNone(exploratory[0]["clickEventSequence"])

    def test_context_menu_timing_is_contiguous_and_direct_click_is_null(self) -> None:
        entry = {
            "option": "Top-floor",
            "target": "Staircase",
            "type": "GAME_OBJECT_SECOND_OPTION",
            "identifier": 56230,
            "param0": 60,
            "param1": 15,
        }

        def source(session: str) -> dict[str, object]:
            return {"sessionId": session, "pid": 7, "sourceTick": 10}

        def hover(
            sequence: int,
            timestamp: int,
            *,
            menu_open: bool = True,
            session: str = "client-a",
        ) -> dict[str, object]:
            return {
                "kind": "hover_menu",
                "recorderSequence": sequence,
                "source": source(session),
                "payload": {
                    "menuOpen": menu_open,
                    "entries": [entry],
                    "hoveredTarget": entry,
                    "pointer": {"monotonicTimeNanos": timestamp * 1_000_000},
                },
            }

        def click(
            sequence: int,
            timestamp: int,
            activation_kind: str,
        ) -> dict[str, object]:
            return {
                "kind": "menu_option_clicked",
                "recorderSequence": sequence,
                "source": source("client-a"),
                "payload": {
                    **entry,
                    "consumed": False,
                    "pointer": {
                        "canvasX": 100,
                        "canvasY": 100,
                        "monotonicTimeNanos": timestamp * 1_000_000,
                    },
                    "resolvedTarget": {
                        "actionFamily": "tile_object",
                        "resolution": "exact",
                        "confidence": "exact",
                        "activationKind": activation_kind,
                    },
                },
            }

        events = [
            hover(1, 1_000),
            hover(2, 1_100),
            hover(3, 1_200, menu_open=False),
            hover(4, 1_300),
            hover(5, 1_400),
            click(6, 1_500, "context_menu_row"),
            hover(7, 2_000),
            hover(8, 2_100, session="client-b"),
            hover(9, 2_200),
            click(10, 2_300, "context_menu_row"),
            hover(11, 2_500),
            click(12, 2_600, "object_geometry"),
        ]

        profiles = _derive_timing_profiles(
            events,
            [],
            modern_semantics=True,
            context_menu_semantics=True,
        )

        self.assertEqual(200, profiles[0]["contextMenuOpenToClickMillis"])
        self.assertEqual(100, profiles[1]["contextMenuOpenToClickMillis"])
        self.assertIsNone(profiles[2]["contextMenuOpenToClickMillis"])
        self.assertEqual(
            "last_matching_hover_observation_age",
            profiles[0]["hoverToClickSemantics"],
        )

    def test_zero_pose_delta_remains_candidate_control_not_positioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder = DemonstrationRecorder(
                "zero-camera-delta",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(before, _hot()))
            samples = (
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 1,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "press",
                    "control": "RIGHT",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1000,
                    "cameraPose": _camera_pose(0),
                },
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 2,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "release",
                    "control": "RIGHT",
                    "clientTick": 902,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1100,
                    "holdDurationMillis": 100,
                    "cameraPose": _camera_pose(0),
                },
                {
                    "eventSequence": 3,
                    "eventLane": "menu_option_clicked",
                    "clientTick": 903,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1200,
                    "mouseCanvasX": 80,
                    "mouseCanvasY": 90,
                    "isInCanvas": True,
                    "cameraPose": _camera_pose(0),
                    "geometryFrameId": "camera-1",
                    "option": "Walk here",
                    "target": "",
                    "type": "WALK",
                    "identifier": 0,
                    "param0": 50,
                    "param1": 52,
                    "resolvedTarget": {
                        "schema": "plugin_click_target.v1",
                        "resolution": "resolved",
                        "confidence": "exact",
                        "actionFamily": "walk_tile",
                        "source": "menu_params",
                        "worldTile": {
                            "worldX": 3210,
                            "worldY": 3220,
                            "plane": 1,
                        },
                        "menuParamTile": {
                            "worldX": 3210,
                            "worldY": 3220,
                            "plane": 1,
                        },
                    },
                },
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            self.assertTrue(
                recorder.add(_evidence(_base_observation(tick=175), _hot(*samples)))
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            episode = result.camera_intent_episodes[0]
            self.assertEqual("cancelled_or_ineffective", episode["classification"])
            self.assertEqual("low", episode["confidence"])
            self.assertFalse(episode["effectiveCameraChangeObserved"])
            self.assertIn(
                "no_observed_pose_change", episode["inference"]
            )
            self.assertIn("no observed camera pose change", episode["ambiguityReasons"])
            self.assertEqual(
                {"x": 3210, "y": 3220, "plane": 1},
                episode["target"]["menuParamTile"],
            )

    def test_consumed_click_is_not_interpreted_as_semantic_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "consumed-click",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder.start(_evidence(before, _hot()))
            click = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1100,
                "consumed": True,
                "mouseCanvasX": 80,
                "mouseCanvasY": 90,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 50,
                "param1": 52,
            }
            self.assertTrue(recorder.add(_evidence(before, _hot(click))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual((), result.selected_menu_options)
            self.assertEqual((), result.timing_profiles)
            self.assertIn("consumed", " ".join(result.ambiguities))

    def test_actual_hot_sequence_gap_is_reported_but_ring_eviction_alone_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "hot-sequence-gap",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            before = _base_observation()
            recorder.start(_evidence(before, _hot()))
            samples = (
                {
                    "eventSequence": 1,
                    "eventLane": "client_tick",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1000,
                    "mouseCanvasX": 10,
                    "mouseCanvasY": 10,
                    "isInCanvas": True,
                },
                {
                    "eventSequence": 3,
                    "eventLane": "client_tick",
                    "clientTick": 903,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1100,
                    "mouseCanvasX": 20,
                    "mouseCanvasY": 20,
                    "isInCanvas": True,
                },
            )
            self.assertTrue(
                recorder.add(
                    _evidence(before, _hot(*samples, drops=(1, 0, 0, 0)))
                )
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn("hot_event_sequence_gap", result.coverage_gaps)

    def test_same_tick_object_scene_correlation_recovers_exact_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = NearbyObject(
                key="tree:45:51:1276",
                object_id=1276,
                name="Tree",
                kind="GAME_OBJECT",
                actions=("Chop down",),
                location=WorldPoint(3205, 3209, 1),
                distance=0,
                geometry=TargetGeometry(
                    available=True,
                    on_screen=True,
                    visible=True,
                    actionable=True,
                    screen_point=ScreenPoint(1080, 2090),
                    screen_bounds=ScreenBounds(1060, 2060, 80, 120),
                    geometry_source="clickbox",
                    screen_polygon=(ScreenPoint(1060, 2060), ScreenPoint(1140, 2060), ScreenPoint(1140, 2180), ScreenPoint(1060, 2180)),
                ),
                scene_x=45,
                scene_y=51,
            )
            competing_tree = replace(
                tree,
                key="tree:46:52:1276",
                location=WorldPoint(3206, 3210, 1),
                scene_x=46,
                scene_y=52,
                geometry=replace(
                    tree.geometry,
                    screen_point=ScreenPoint(1280, 2290),
                    screen_bounds=ScreenBounds(1260, 2260, 80, 120),
                    screen_polygon=(
                        ScreenPoint(1260, 2260),
                        ScreenPoint(1340, 2260),
                        ScreenPoint(1340, 2380),
                        ScreenPoint(1260, 2380),
                    ),
                ),
            )
            before = replace(
                _base_observation(),
                nearby_objects=(tree, competing_tree),
                camera_yaw=0,
                camera_pitch=300,
            )
            recorder = DemonstrationRecorder("exact-tree", output_root=Path(temporary), screenshots_enabled=False)
            recorder.start(_evidence(before, _hot()))
            samples = (
                {"eventSequence": 1, "eventLane": "client_tick", "clientTick": 900, "gameTickAtSample": 174, "wallTimeMillis": 1000, "mouseCanvasX": 20, "mouseCanvasY": 20, "isInCanvas": True, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "D", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1020, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 3, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "D", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 1100, "holdDurationMillis": 80, "cameraPose": _camera_pose(64)},
                {"eventSequence": 4, "eventLane": "menu_option_clicked", "clientTick": 903, "gameTickAtSample": 174, "wallTimeMillis": 1200, "mouseCanvasX": 40, "mouseCanvasY": 45, "isInCanvas": True, "cameraPose": _camera_pose(64), "geometryFrameId": before.geometry_frame_id, "option": "Chop down", "target": "Tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1276, "param0": 44, "param1": 50, "resolvedTarget": {"schema": "plugin_click_target.v1", "resolution": "unsupported", "actionFamily": "tile_object"}},
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            self.assertTrue(recorder.add(_evidence(_base_observation(tick=175), _hot(*samples))))
            artifact = recorder.finish("test")
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertEqual("tree:45:51:1276", result.interacted_entities[0]["objectKey"])
            self.assertEqual("exact", result.interacted_entities[0]["resolution"])
            self.assertEqual(
                "same_tick_object_id_and_clickbox_containment",
                result.interacted_entities[0]["identitySource"],
            )
            self.assertEqual("action_linked", result.camera_intent_episodes[0]["classification"])
            self.assertEqual("keyboard", result.camera_intent_episodes[0]["observedInputMethod"])
            click = next(json.loads(line) for line in (artifact / "events.jsonl").read_text().splitlines() if '"kind":"menu_option_clicked"' in line)
            self.assertEqual(4, len(click["payload"]["entityEvidence"]["projection"]["screenPolygon"]))

    def test_context_menu_object_identity_is_not_treated_as_aim_geometry(self) -> None:
        for activation_kind in ("context_menu_row", "unverified"):
            with self.subTest(activation_kind=activation_kind), tempfile.TemporaryDirectory() as temporary:
                observation = _base_observation()
                recorder = DemonstrationRecorder(
                    f"context-object-{activation_kind}",
                    output_root=Path(temporary),
                    screenshots_enabled=False,
                )
                recorder.start(_evidence(observation, _hot()))
                click = {
                    "eventSequence": 1,
                    "eventLane": "menu_option_clicked",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1_000,
                    "mouseCanvasX": 350,
                    "mouseCanvasY": 260,
                    "isInCanvas": True,
                    "geometryFrameId": observation.geometry_frame_id,
                    "option": "Bank",
                    "target": "Bank booth",
                    "type": "GAME_OBJECT_SECOND_OPTION",
                    "identifier": 18491,
                    "param0": 47,
                    "param1": 53,
                    "resolvedTarget": {
                        "schema": "plugin_click_target.v1",
                        "resolution": "exact",
                        "confidence": "exact",
                        "actionFamily": "tile_object",
                        "activationKind": activation_kind,
                        "source": "menu_identifier_scene_coordinates",
                        "object": {
                            "objectKey": "bank-booth:47:53:18491",
                            "id": 18491,
                            "kind": "GAME_OBJECT",
                            "worldX": 3207,
                            "worldY": 3211,
                            "plane": 1,
                            "sceneX": 47,
                            "sceneY": 53,
                        },
                        "geometry": {
                            "geometryFrameId": observation.geometry_frame_id,
                            "source": "clickbox",
                            "polygon": [
                                {"x": 100, "y": 100},
                                {"x": 160, "y": 100},
                                {"x": 160, "y": 180},
                                {"x": 100, "y": 180},
                            ],
                            "bounds": {
                                "x": 100,
                                "y": 100,
                                "width": 60,
                                "height": 80,
                            },
                            "clickInside": None,
                        },
                    },
                }
                self.assertTrue(recorder.add(_evidence(observation, _hot(click))))
                artifact = recorder.finish("test")
                result = inspect_demonstration(artifact)

                self.assertTrue(result.valid, result.errors)
                self.assertEqual("exact", result.interacted_entities[0]["resolution"])
                interaction = next(
                    value
                    for value in result.candidate_suggestions
                    if value["kind"] == "interaction_fact_candidate"
                )
                self.assertEqual("exact", interaction["identityResolution"])
                stored_click = next(
                    json.loads(line)
                    for line in (artifact / "events.jsonl").read_text().splitlines()
                    if '"kind":"menu_option_clicked"' in line
                )
                target = stored_click["payload"]["resolvedTarget"]
                self.assertEqual(activation_kind, target["activationKind"])
                self.assertIsNone(target["geometry"]["clickInside"])
                self.assertNotIn("activationPoint", target)

    def test_context_menu_row_cannot_claim_click_inside_object_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = _base_observation()
            recorder = DemonstrationRecorder(
                "context-row-not-aim",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(observation, _hot()))
            click = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 350,
                "mouseCanvasY": 260,
                "isInCanvas": True,
                "geometryFrameId": observation.geometry_frame_id,
                "option": "Bank",
                "target": "Bank booth",
                "type": "GAME_OBJECT_SECOND_OPTION",
                "identifier": 18491,
                "param0": 47,
                "param1": 53,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "exact",
                    "confidence": "exact",
                    "actionFamily": "tile_object",
                    "activationKind": "context_menu_row",
                    "source": "menu_identifier_scene_coordinates",
                    "object": {
                        "objectKey": "bank-booth:47:53:18491",
                        "id": 18491,
                        "kind": "GAME_OBJECT",
                        "worldX": 3207,
                        "worldY": 3211,
                        "plane": 1,
                        "sceneX": 47,
                        "sceneY": 53,
                    },
                    "geometry": {
                        "geometryFrameId": observation.geometry_frame_id,
                        "source": "clickbox",
                        "polygon": [],
                        "bounds": {"x": 100, "y": 100, "width": 60, "height": 80},
                        "clickInside": True,
                    },
                },
            }
            self.assertTrue(recorder.add(_evidence(observation, _hot(click))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertFalse(result.valid)
            self.assertIn("being treated as aim geometry", " ".join(result.errors))

    def test_cancelled_camera_input_is_preserved_as_ambiguous_without_click(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder("camera-cancel", output_root=Path(temporary), screenshots_enabled=False)
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder.start(_evidence(before, _hot()))
            samples = (
                {"schema": "plugin_camera_input.v1", "eventSequence": 1, "eventLane": "camera_input", "inputKind": "middle_drag", "phase": "press", "control": "MIDDLE", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1000, "canvasX": 10, "canvasY": 10, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "middle_drag", "phase": "cancel", "control": "MIDDLE", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 1100, "holdDurationMillis": 100, "pathDistancePixels": 12.5, "cameraPose": _camera_pose(32)},
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples, drops=(0, 0, 0, 1)))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(
                "cancelled_or_ineffective",
                result.camera_intent_episodes[0]["classification"],
            )
            self.assertEqual(100, result.camera_intent_episodes[0]["maxControlHoldMillis"])
            self.assertEqual(12.5, result.camera_intent_episodes[0]["maxDragPathPixels"])
            self.assertNotIn("hot event sequence gap", result.coverage_gaps)

    def test_long_unlinked_camera_press_preserves_pose_delta_as_exploratory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "long-exploratory-camera",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            before = replace(_base_observation(), camera_yaw=0, camera_pitch=300)
            recorder.start(_evidence(before, _hot()))
            samples = (
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 1,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "press",
                    "control": "A",
                    "clientTick": 901,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 1_000,
                    "cameraPose": _camera_pose(0),
                },
                {
                    "schema": "plugin_camera_input.v1",
                    "eventSequence": 2,
                    "eventLane": "camera_input",
                    "inputKind": "key",
                    "phase": "release",
                    "control": "A",
                    "clientTick": 902,
                    "gameTickAtSample": 174,
                    "wallTimeMillis": 4_200,
                    "holdDurationMillis": 3_200,
                    "cameraPose": _camera_pose(192),
                },
            )
            self.assertTrue(recorder.add(_evidence(before, _hot(*samples))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(1, len(result.camera_intent_episodes))
            episode = result.camera_intent_episodes[0]
            self.assertEqual(
                "exploratory_or_unassociated", episode["intentClassification"]
            )
            self.assertEqual([3, 4], episode["cameraInputEventSequences"])
            self.assertEqual(192, episode["cameraPoseDelta"]["yaw"])
            self.assertEqual(3_200, episode["episodeInputSpanMillis"])
            self.assertEqual(3_200, episode["maxControlHoldMillis"])
            self.assertTrue(episode["effectiveCameraChangeObserved"])
            self.assertIsNone(episode["clickEventSequence"])

    def test_session_change_stops_and_is_reported_as_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = _evidence(_base_observation(), _hot())
            recorder = DemonstrationRecorder(
                "identity-change", output_root=Path(temporary), screenshots_enabled=False
            )
            recorder.start(first)
            changed = replace(_base_observation(), session_id="another-session")
            self.assertFalse(recorder.add(_evidence(changed, _hot())))
            artifact = recorder.finish("identity_changed")
            result = inspect_demonstration(artifact)
            self.assertTrue(result.valid)
            self.assertIn("session_or_process_changed", result.coverage_gaps)

    def test_transient_world_model_mismatch_skips_only_that_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "world-model-handoff",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            first = _base_observation()
            recorder.start(_evidence(first, _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )

            self.assertFalse(unavailable.loaded_scene)
            self.assertTrue(
                recorder.add(_transient_world_model_evidence(unavailable, _hot()))
            )
            self.assertTrue(
                recorder.add(_evidence(_base_observation(tick=176), _hot()))
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn(
                "demonstration_world_model_provenance_unavailable",
                result.coverage_gaps,
            )
            self.assertEqual(2, len(result.route_points))
            self.assertEqual(174, result.route_points[0]["sourceTick"])
            self.assertEqual(176, result.route_points[1]["sourceTick"])

    def test_sustained_world_model_handoff_continues_and_resets_after_full_frame(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            monotonic_values = iter(
                (10.0, 10.5, 10.0 + WORLD_MODEL_GAP_GRACE_SECONDS + 0.01, 30.0)
            )
            recorder = DemonstrationRecorder(
                "world-model-handoff-resume",
                output_root=Path(temporary),
                screenshots_enabled=False,
                monotonic=lambda: next(monotonic_values),
            )
            recorder.start(_evidence(_base_observation(), _hot()))

            for offset in range(1, 4):
                unavailable = replace(
                    _base_observation(tick=174 + offset),
                    status="WARN",
                    missing_capabilities=(
                        "scene_object_census",
                        "actor_census",
                        "collision_window",
                    ),
                    warnings=("world_model_provenance_mismatch",),
                )
                accepted = recorder.add(
                    _transient_world_model_evidence(unavailable, _hot())
                )
                self.assertTrue(accepted)

            self.assertTrue(
                recorder.add(_evidence(_base_observation(tick=178), _hot()))
            )
            later_unavailable = replace(
                _base_observation(tick=179),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )
            self.assertTrue(
                recorder.add(
                    _transient_world_model_evidence(later_unavailable, _hot())
                )
            )

            artifact = recorder.finish("test")
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text().splitlines()
            ]
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertIn(
                "demonstration_world_model_provenance_unavailable",
                result.coverage_gaps,
            )
            gaps = [
                event
                for event in events
                if event["kind"] == "coverage_gap"
                and event["payload"]["code"]
                == "demonstration_world_model_provenance_unavailable"
            ]
            self.assertEqual(4, len(gaps))
            self.assertGreater(
                gaps[2]["payload"]["elapsedMillis"],
                WORLD_MODEL_GAP_GRACE_SECONDS * 1000,
            )
            self.assertEqual(1, gaps[3]["payload"]["consecutivePolls"])
            self.assertEqual(0, gaps[3]["payload"]["elapsedMillis"])

    def test_combined_world_model_and_interaction_handoff_is_transient(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "combined-dynamic-handoff",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                    "interaction_hot",
                ),
                warnings=(
                    "world_model_provenance_mismatch",
                    "menu_evidence_provenance_mismatch_or_stale",
                ),
            )
            camera_input = {
                "schema": "plugin_camera_input.v1",
                "eventSequence": 1,
                "eventLane": "camera_input",
                "inputKind": "middle_drag",
                "phase": "release",
                "control": "MIDDLE",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1100,
                "holdDurationMillis": 320,
                "cameraPose": _camera_pose(128),
            }

            self.assertTrue(
                recorder.add(
                    _transient_dynamic_handoff_evidence(
                        unavailable, _hot(camera_input)
                    )
                )
            )
            self.assertTrue(
                recorder.add(_evidence(_base_observation(tick=176), _hot(camera_input)))
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn(
                "demonstration_dynamic_provenance_handoff",
                result.coverage_gaps,
            )
            self.assertEqual(
                "middle_drag", result.camera_intent_episodes[0]["observedInputMethod"]
            )

    def test_transient_world_model_poll_preserves_bound_walk_click(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "world-model-walk-click",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1100,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
            }

            self.assertTrue(
                recorder.add(
                    _transient_world_model_evidence(unavailable, _hot(walk))
                )
            )
            artifact = recorder.finish("test")
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text().splitlines()
            ]
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(
                1,
                sum(event["kind"] == "menu_option_clicked" for event in events),
            )
            self.assertEqual(("Walk here",), result.selected_menu_options)
            self.assertIn("missing_after_observation", result.coverage_gaps)
            self.assertEqual((), result.state_changes)

    def test_transient_walk_click_receives_later_complete_movement_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "world-model-walk-outcome",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1100,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
            }

            self.assertTrue(
                recorder.add(
                    _transient_world_model_evidence(unavailable, _hot(walk))
                )
            )
            self.assertTrue(
                recorder.add(_evidence(_base_observation(tick=176), _hot(walk)))
            )
            artifact = recorder.finish("test")
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text().splitlines()
            ]
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(
                1,
                sum(event["kind"] == "menu_option_clicked" for event in events),
            )
            self.assertNotIn("missing_after_observation", result.coverage_gaps)
            self.assertTrue(
                any(change["field"] == "player.world" for change in result.state_changes)
            )
            self.assertTrue(
                any(
                    value.startswith("selected Walk here")
                    for value in result.semantic_summary
                )
            )

    def test_other_loaded_scene_loss_remains_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "core-scene-loss",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=("inventory",),
                warnings=("sensor_fact_unavailable:inventory",),
            )

            self.assertFalse(recorder.add(_evidence(unavailable, _hot())))
            artifact = recorder.finish("test")
            result = inspect_demonstration(artifact)
            events = [
                json.loads(line)
                for line in (artifact / "events.jsonl").read_text().splitlines()
            ]
            terminal_gap = next(
                event
                for event in events
                if event["kind"] == "coverage_gap"
                and event["payload"]["code"] == "loaded_scene_lost"
            )

            self.assertTrue(result.valid, result.errors)
            self.assertIn("loaded_scene_lost", result.coverage_gaps)
            self.assertIn("client_tick_tail", terminal_gap["payload"]["payloadKeys"])

    def test_transient_interaction_hot_mismatch_retains_bound_camera_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "interaction-hot-handoff",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=("interaction_hot",),
                warnings=("menu_evidence_provenance_mismatch_or_stale",),
            )
            camera_input = {
                "schema": "plugin_camera_input.v1",
                "eventSequence": 1,
                "eventLane": "camera_input",
                "inputKind": "key",
                "phase": "release",
                "control": "LEFT",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1100,
                "holdDurationMillis": 80,
                "cameraPose": _camera_pose(64),
            }

            self.assertTrue(
                recorder.add(
                    _transient_interaction_hot_evidence(
                        unavailable, _hot(camera_input)
                    )
                )
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn(
                "demonstration_interaction_hot_provenance_unavailable",
                result.coverage_gaps,
            )
            self.assertEqual("keyboard", result.camera_intent_episodes[0]["observedInputMethod"])

    def test_transient_world_model_exception_requires_matching_raw_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "contradictory-world-model-handoff",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )

            evidence = _transient_world_model_evidence(unavailable, _hot())
            payload = json.loads(evidence.payload_json)
            full_payload = json.loads(_evidence(unavailable, _hot()).payload_json)
            payload["payloads"]["actor_census"] = full_payload["payloads"][
                "actor_census"
            ]
            contradictory = replace(
                evidence,
                payload_json=json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ),
            )

            self.assertFalse(recorder.add(contradictory))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn("loaded_scene_lost", result.coverage_gaps)
            self.assertNotIn(
                "demonstration_world_model_provenance_unavailable",
                result.coverage_gaps,
            )

    def test_transient_interaction_exception_rejects_unbound_payload(self) -> None:
        defects = (
            "missing_client_tick_tail",
            "wrong_schema",
            "wrong_session",
            "wrong_process",
            "root_copy_mismatch",
        )
        for defect in defects:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                recorder = DemonstrationRecorder(
                    f"contradictory-interaction-handoff-{defect}",
                    output_root=Path(temporary),
                    screenshots_enabled=False,
                )
                recorder.start(_evidence(_base_observation(), _hot()))
                unavailable = replace(
                    _base_observation(tick=175),
                    status="WARN",
                    missing_capabilities=("interaction_hot",),
                    warnings=("menu_evidence_provenance_mismatch_or_stale",),
                )

                evidence = _transient_interaction_hot_evidence(unavailable, _hot())
                payload = json.loads(evidence.payload_json)
                interaction_hot = payload["payloads"]["interaction_hot"]
                root_interaction_hot = payload["clientTickHot"]
                if defect == "missing_client_tick_tail":
                    payload["payloads"].pop("client_tick_tail")
                elif defect == "wrong_schema":
                    interaction_hot["schema"] = "client_tick_hot.unsupported"
                    root_interaction_hot["schema"] = "client_tick_hot.unsupported"
                elif defect == "wrong_session":
                    interaction_hot["sessionId"] = "different-session"
                    root_interaction_hot["sessionId"] = "different-session"
                elif defect == "wrong_process":
                    interaction_hot["clientProcessId"] = (
                        unavailable.client_process_id + 1
                    )
                    root_interaction_hot["clientProcessId"] = (
                        unavailable.client_process_id + 1
                    )
                else:
                    root_interaction_hot["clientTick"] += 1
                contradictory = replace(
                    evidence,
                    payload_json=json.dumps(
                        payload, sort_keys=True, separators=(",", ":")
                    ),
                )

                self.assertFalse(recorder.add(contradictory))
                result = inspect_demonstration(recorder.finish("test"))

                self.assertTrue(result.valid, result.errors)
                self.assertIn("loaded_scene_lost", result.coverage_gaps)
                self.assertNotIn(
                    "demonstration_interaction_hot_provenance_unavailable",
                    result.coverage_gaps,
                )

    def test_transient_world_model_mismatch_cannot_hide_tick_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "world-model-regression",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            first_unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )
            regressed = replace(
                _base_observation(tick=174),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )

            self.assertTrue(
                recorder.add(
                    _transient_world_model_evidence(first_unavailable, _hot())
                )
            )
            self.assertFalse(
                recorder.add(_transient_world_model_evidence(regressed, _hot()))
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn("source_tick_regressed", result.coverage_gaps)

    def test_transient_world_model_mismatch_cannot_hide_hot_sequence_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "world-model-hot-reset",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(
                _evidence(
                    _base_observation(),
                    _hot(
                        {
                            "eventSequence": 10,
                            "eventLane": "client_tick",
                            "clientTick": 900,
                            "gameTickAtSample": 174,
                            "wallTimeMillis": 1000,
                            "mouseCanvasX": 20,
                            "mouseCanvasY": 30,
                            "isInCanvas": True,
                        }
                    ),
                )
            )
            unavailable = replace(
                _base_observation(tick=175),
                status="WARN",
                missing_capabilities=(
                    "scene_object_census",
                    "actor_census",
                    "collision_window",
                ),
                warnings=("world_model_provenance_mismatch",),
            )
            reset_hot = _hot(
                {
                    "eventSequence": 9,
                    "eventLane": "client_tick",
                    "clientTick": 901,
                    "gameTickAtSample": 175,
                    "wallTimeMillis": 1100,
                    "mouseCanvasX": 21,
                    "mouseCanvasY": 31,
                    "isInCanvas": True,
                }
            )

            self.assertFalse(
                recorder.add(
                    _transient_world_model_evidence(unavailable, reset_hot)
                )
            )
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            self.assertIn("hot_event_sequence_reset", result.coverage_gaps)

    def test_tick_regression_stops_before_recording_an_invalid_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "tick-regression",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            self.assertFalse(
                recorder.add(_evidence(_base_observation(tick=173), _hot()))
            )
            result = inspect_demonstration(recorder.finish("tick_regressed"))
            self.assertTrue(result.valid, result.errors)
            self.assertIn("source_tick_regressed", result.coverage_gaps)
            self.assertEqual(1, len(result.route_points))

    def test_idle_ticks_coalesce_into_one_route_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "idle-route",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            first = _base_observation()
            recorder.start(_evidence(first, _hot()))
            second = replace(
                _base_observation(tick=175),
                location=first.location,
            )
            self.assertTrue(recorder.add(_evidence(second, _hot())))
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            self.assertEqual(1, len(result.route_points))
            self.assertEqual(174, result.route_points[0]["sourceTick"])
            self.assertEqual(175, result.route_points[0]["lastSourceTick"])

    def test_hover_menu_cap_is_explicit_coverage_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "menu-cap",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            entries = [
                {
                    "option": "Examine",
                    "target": f"Object {index}",
                    "type": "EXAMINE_OBJECT",
                    "identifier": 1000 + index,
                }
                for index in range(16)
            ]
            hover = {
                "eventSequence": 1,
                "eventLane": "post_menu_sort",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1200,
                "entryCount": 20,
                "entries": entries,
            }
            self.assertTrue(recorder.add(_evidence(_base_observation(), _hot(hover))))
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            self.assertIn("hover menu entry cap reached", result.coverage_gaps)

    def test_bank_pin_suppresses_screenshot_capture(self) -> None:
        calls: list[object] = []

        def capture(*args):
            calls.append(args)
            canvas = args[0]
            return Image.new("RGB", (1, 1)), CaptureMetadata(
                method="test",
                canvas_bounds=canvas,
                captured_bounds=ScreenBounds(canvas.x, canvas.y, 1, 1),
                relative_bounds=ScreenBounds(0, 0, 1, 1),
            )

        with tempfile.TemporaryDirectory() as temporary:
            observation = _base_observation()
            observation = replace(
                observation,
                widgets=replace(observation.widgets, bank_pin_open=True),
            )
            recorder = DemonstrationRecorder(
                "pin-gate", output_root=Path(temporary), capture=capture
            )
            recorder.start(_evidence(observation, _hot()))
            artifact = recorder.finish("test")
            self.assertEqual([], calls)
            self.assertEqual(0, json.loads((artifact / "manifest.json").read_text())["screenshotCount"])

    def test_rejects_tampering_before_emitting_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            with (artifact / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            result = inspect_demonstration(artifact)
            self.assertFalse(result.valid)
            self.assertEqual((), result.candidate_suggestions)
            self.assertIn("mismatch", " ".join(result.errors))

    def test_rejects_hash_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            hashes_path = artifact / "hashes.json"
            hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
            hashes["files"][0]["path"] = "../escape"
            hashes_path.write_text(json.dumps(hashes), encoding="utf-8")
            result = inspect_demonstration(artifact)
            self.assertFalse(result.valid)
            self.assertEqual((), result.candidate_suggestions)
            self.assertIn("safe relative path", " ".join(result.errors))

    def test_rejects_rehashed_cross_session_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            path = artifact / "events.jsonl"
            events = [json.loads(line) for line in path.read_text().splitlines()]
            click = next(
                event for event in events if event["kind"] == "menu_option_clicked"
            )
            click["source"]["sessionId"] = "different-session"
            path.write_text(
                "".join(
                    json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
                    for event in events
                ),
                encoding="utf-8",
            )
            _rehash_file(artifact, "events.jsonl")
            result = inspect_demonstration(artifact)
            self.assertFalse(result.valid)
            self.assertEqual((), result.candidate_suggestions)
            self.assertIn("manifest client", " ".join(result.errors))

    def test_rejects_rehashed_click_without_explicit_consumed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            path = artifact / "events.jsonl"
            events = [json.loads(line) for line in path.read_text().splitlines()]
            click = next(
                event for event in events if event["kind"] == "menu_option_clicked"
            )
            click["payload"]["consumed"] = None
            path.write_text(
                "".join(
                    json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
                    for event in events
                ),
                encoding="utf-8",
            )
            _rehash_file(artifact, "events.jsonl")
            result = inspect_demonstration(artifact)

            self.assertFalse(result.valid)
            self.assertEqual((), result.candidate_suggestions)
            self.assertIn("explicit consumed evidence", " ".join(result.errors))

    def test_rejects_rehashed_summary_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = _record_fixture(Path(temporary))
            path = artifact / "summary.json"
            summary = json.loads(path.read_text(encoding="utf-8"))
            summary["candidateSuggestions"].append(
                {"kind": "raw_replay", "mouse": {"x": 1, "y": 2}}
            )
            path.write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _rehash_file(artifact, "summary.json")
            result = inspect_demonstration(artifact)
            self.assertFalse(result.valid)
            self.assertEqual((), result.candidate_suggestions)
            self.assertIn("stored summary disagrees", " ".join(result.errors))

    def test_suggestions_never_contain_input_replay_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = inspect_demonstration(_record_fixture(Path(temporary)))
            suggestions = json.dumps(result.candidate_suggestions).lower()
            for prohibited in ("screen", "canvas", "mouse", "clickpoint", "param0", "param1"):
                self.assertNotIn(prohibited, suggestions)
            self.assertIn("never_automatic", suggestions)

    def test_multiple_clicks_do_not_claim_one_outcome_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "ambiguous-clicks",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            first = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 900,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1000,
                "option": "Climb-up",
                "target": "Staircase",
                "type": "GAME_OBJECT_FIRST_OPTION",
                "identifier": 16672,
            }
            second = {
                **first,
                "eventSequence": 2,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "wallTimeMillis": 1010,
            }
            self.assertTrue(
                recorder.add(_evidence(_base_observation(), _hot(first, second)))
            )
            after = _base_observation(tick=175, plane=2)
            self.assertTrue(recorder.add(_evidence(after, _hot(first, second))))
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            self.assertEqual((), result.semantic_summary)
            self.assertTrue(
                any("not uniquely attributable" in value for value in result.ambiguities)
            )
            self.assertFalse(
                any(
                    value.get("kind") == "plane_transition_candidate"
                    for value in result.candidate_suggestions
                )
            )

    def test_walk_click_is_route_evidence_not_an_entity_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "walk-only",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 900,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1000,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
            }
            self.assertTrue(recorder.add(_evidence(_base_observation(), _hot(walk))))
            self.assertTrue(
                recorder.add(
                    _evidence(_base_observation(tick=175), _hot(walk))
                )
            )
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            self.assertEqual((), result.interacted_entities)
            self.assertFalse(
                any(
                    value.get("kind") == "interaction_fact_candidate"
                    for value in result.candidate_suggestions
                )
            )
            self.assertTrue(
                any(value.startswith("selected Walk here") for value in result.semantic_summary)
            )

    def test_manual_walk_targets_preserve_sources_and_nonclaiming_quick_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "manual-route-targets",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            observation = _base_observation()
            recorder.start(_evidence(observation, _hot()))
            first = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 180,
                "mouseCanvasY": 190,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 55,
                "param1": 58,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "resolved",
                    "confidence": "high",
                    "source": "selected_scene_tile",
                    "actionFamily": "walk_tile",
                    "selectedSceneTile": {"x": 55, "y": 58},
                    "menuParamTile": {"x": 55, "y": 58},
                    "worldTile": {"worldX": 3433, "worldY": 3218, "plane": 1},
                },
            }
            second = {
                **first,
                "eventSequence": 2,
                "clientTick": 902,
                "wallTimeMillis": 1_450,
                "mouseCanvasX": 210,
                "mouseCanvasY": 190,
                "param0": 57,
                "resolvedTarget": {
                    **first["resolvedTarget"],
                    "selectedSceneTile": {"x": 57, "y": 58},
                    "menuParamTile": {"x": 57, "y": 58},
                },
            }
            self.assertTrue(
                recorder.add(_evidence(observation, _hot(first, second)))
            )
            artifact = recorder.finish("test")
            result = inspect_demonstration(artifact)

            self.assertTrue(result.valid, result.errors)
            self.assertEqual(2, len(result.manual_route_targets))
            first_target, second_target = result.manual_route_targets
            self.assertEqual(
                {"x": 3215, "y": 3216, "plane": 1},
                first_target["chosenTargetWorld"],
            )
            self.assertEqual(
                "selectedSceneTile+observationSceneOrigin",
                first_target["chosenTargetSource"],
            )
            self.assertEqual(
                {"x": 3433, "y": 3218, "plane": 1},
                first_target["targetSourceFields"]["worldTile"],
            )
            self.assertEqual(
                "possible_quick_followup",
                first_target["quickFollowup"]["classification"],
            )
            self.assertIn("does not label", first_target["quickFollowup"]["interpretation"])
            self.assertEqual(
                first_target["clickEventSequence"],
                second_target["possiblySupersedesClickEventSequence"],
            )
            self.assertEqual(2.0, second_target["distanceFromPreviousManualTarget"])
            self.assertNotEqual(
                result.route_points[0]["x"], first_target["chosenTargetWorld"]["x"]
            )
            stored_summary = json.loads(
                (artifact / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, len(stored_summary["manualRouteTargets"]))

    def test_manual_walk_distance_labels_prior_player_sample_and_coordinate_space(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "manual-route-stale-player",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            observation = _base_observation()
            recorder.start(_evidence(observation, _hot()))
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 180,
                "wallTimeMillis": 4_600,
                "mouseCanvasX": 180,
                "mouseCanvasY": 190,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 55,
                "param1": 58,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "resolved",
                    "confidence": "high",
                    "source": "menu_params",
                    "actionFamily": "walk_tile",
                    "selectedSceneTile": {"x": 55, "y": 58},
                    "worldTile": {"worldX": 3433, "worldY": 3218, "plane": 1},
                },
            }
            self.assertTrue(recorder.add(_evidence(observation, _hot(walk))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            target = result.manual_route_targets[0]
            self.assertEqual(
                "selectedSceneTile+observationSceneOrigin", target["targetSource"]
            )
            self.assertEqual("menu_params", target["reportedResolvedTargetSource"])
            self.assertEqual(
                "scene_plus_observed_origin", target["selectedTargetCoordinateSpace"]
            )
            self.assertEqual(6, target["playerSampleAgeTicks"])
            self.assertEqual(
                "latest_prior_observed_player_world",
                target["playerWorldAtClickSemantics"],
            )
            self.assertIsNotNone(target["distanceFromLastObservedPlayer"])
            self.assertIsNone(target["requestedTileDistance"])
            self.assertEqual(
                "not_claimed_from_prior_player_sample",
                target["requestedTileDistanceStatus"],
            )

    def test_manual_walk_one_tick_player_sample_is_a_qualified_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "manual-route-near-player",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            observation = _base_observation()
            recorder.start(_evidence(observation, _hot()))
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 180,
                "mouseCanvasY": 190,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 55,
                "param1": 58,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "resolved",
                    "confidence": "high",
                    "source": "menu_params",
                    "actionFamily": "walk_tile",
                    "selectedSceneTile": {"x": 55, "y": 58},
                },
            }
            self.assertTrue(recorder.add(_evidence(observation, _hot(walk))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            target = result.manual_route_targets[0]
            self.assertEqual(1, target["playerSampleAgeTicks"])
            self.assertIsNotNone(target["requestedTileDistance"])
            self.assertEqual(
                target["distanceFromLastObservedPlayer"],
                target["requestedTileDistance"],
            )
            self.assertEqual(
                "near_source_tick_player_sample_estimate",
                target["requestedTileDistanceStatus"],
            )

    def test_java_world_selected_scene_tile_is_authoritative_over_bogus_world_tile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "manual-route-java-world-tile",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            observation = _base_observation()
            recorder.start(_evidence(observation, _hot()))
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 168,
                "mouseCanvasY": 29,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 164,
                "param1": 25,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "resolved",
                    "confidence": "high",
                    "source": "menu_params",
                    "actionFamily": "walk_tile",
                    "selectedSceneTile": {"x": 3197, "y": 3237, "plane": 1},
                    "worldTile": {"x": 3433, "y": 3218, "plane": 1},
                    "menuParamTile": {"x": 3433, "y": 3218, "plane": 1},
                },
            }
            self.assertTrue(recorder.add(_evidence(observation, _hot(walk))))
            result = inspect_demonstration(recorder.finish("test"))

            self.assertTrue(result.valid, result.errors)
            target = result.manual_route_targets[0]
            self.assertEqual(
                {"x": 3197, "y": 3237, "plane": 1}, target["chosenTargetWorld"]
            )
            self.assertEqual("selectedSceneTile", target["targetSource"])
            self.assertEqual("world", target["selectedTargetCoordinateSpace"])
            self.assertEqual("menu_params", target["reportedResolvedTargetSource"])
            self.assertEqual(
                {"x": 3433, "y": 3218, "plane": 1},
                target["targetSourceFields"]["worldTile"],
            )
            self.assertEqual(29.12, target["requestedTileDistance"])
            self.assertEqual(
                "same_source_tick_player_sample",
                target["requestedTileDistanceStatus"],
            )

    def test_legacy_artifact_keeps_summary_timeline_bytes_with_ephemeral_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "legacy-compatible-review",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            observation = _base_observation()
            recorder.start(_evidence(observation, _hot()))
            walk = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1_000,
                "mouseCanvasX": 180,
                "mouseCanvasY": 190,
                "isInCanvas": True,
                "option": "Walk here",
                "target": "",
                "type": "WALK",
                "identifier": 0,
                "param0": 55,
                "param1": 58,
                "resolvedTarget": {
                    "schema": "plugin_click_target.v1",
                    "resolution": "resolved",
                    "confidence": "high",
                    "source": "selected_scene_tile",
                    "actionFamily": "walk_tile",
                    "selectedSceneTile": {"x": 55, "y": 58},
                },
            }
            self.assertTrue(recorder.add(_evidence(observation, _hot(walk))))
            artifact = recorder.finish("test")

            manifest_path = artifact / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for name in (
                "cameraIntentSemanticsV3",
                "cameraIntentSemanticsV4",
                "contextMenuActivationSemanticsV1",
                "contextMenuTimingSemanticsV1",
                "manualRouteIntentSemanticsV1",
                "manualRouteIntentSemanticsV2",
            ):
                manifest["evidenceCoverage"].pop(name)
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            events = _read_events_unverified(artifact / "events.jsonl")
            legacy_result = _derive_summary(events, manifest)
            summary_path = artifact / "summary.json"
            summary_path.write_text(
                json.dumps(legacy_result.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            timeline_path = artifact / "timeline.md"
            timeline_path.write_text(
                _timeline_markdown(manifest, events, legacy_result),
                encoding="utf-8",
            )
            for relative in ("manifest.json", "summary.json", "timeline.md"):
                _rehash_file(artifact, relative)
            summary_before = summary_path.read_bytes()
            timeline_before = timeline_path.read_bytes()

            inspected = inspect_demonstration(artifact)

            self.assertTrue(inspected.valid, inspected.errors)
            self.assertEqual(summary_before, summary_path.read_bytes())
            self.assertEqual(timeline_before, timeline_path.read_bytes())
            self.assertNotIn("manualRouteTargets", inspected.to_dict())
            self.assertEqual(1, len(inspected.manual_route_targets))
            self.assertNotIn("Review-only manual Walk targets", timeline_before.decode())

    def test_v3_artifact_bytes_remain_stable_with_v4_ephemeral_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = _base_observation()
            recorder = DemonstrationRecorder(
                "v3-compatible-review",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(observation, _hot()))
            bank_entry = {
                "option": "Bank",
                "target": "Bank booth",
                "type": "GAME_OBJECT_SECOND_OPTION",
                "identifier": 18491,
                "param0": 47,
                "param1": 53,
            }
            exact_bank = {
                "schema": "plugin_click_target.v1",
                "resolution": "exact",
                "confidence": "exact",
                "actionFamily": "tile_object",
                "activationKind": "context_menu_row",
                "source": "menu_identifier_scene_coordinates",
                "object": {"objectKey": "bank-booth:47:53:18491", "id": 18491, "kind": "GAME_OBJECT", "worldX": 3207, "worldY": 3211, "plane": 1, "sceneX": 47, "sceneY": 53},
                "geometry": {"geometryFrameId": observation.geometry_frame_id, "source": "clickbox", "polygon": [{"x": 100, "y": 100}, {"x": 160, "y": 100}, {"x": 160, "y": 180}, {"x": 100, "y": 180}], "bounds": {"x": 100, "y": 100, "width": 60, "height": 80}, "clickInside": None},
            }
            samples = (
                {"schema": "plugin_camera_input.v1", "eventSequence": 1, "eventLane": "camera_input", "inputKind": "key", "phase": "press", "control": "RIGHT", "clientTick": 901, "gameTickAtSample": 174, "wallTimeMillis": 1_000, "cameraPose": _camera_pose(0)},
                {"schema": "plugin_camera_input.v1", "eventSequence": 2, "eventLane": "camera_input", "inputKind": "key", "phase": "release", "control": "RIGHT", "clientTick": 902, "gameTickAtSample": 174, "wallTimeMillis": 1_100, "holdDurationMillis": 100, "cameraPose": _camera_pose(64)},
                {"eventSequence": 3, "eventLane": "post_menu_sort", "clientTick": 903, "gameTickAtSample": 174, "wallTimeMillis": 3_400, "mouseCanvasX": 350, "mouseCanvasY": 260, "isInCanvas": True, "menuOpen": True, "entryCount": 1, "entries": [bank_entry]},
                {"eventSequence": 4, "eventLane": "post_menu_sort", "clientTick": 904, "gameTickAtSample": 174, "wallTimeMillis": 3_500, "mouseCanvasX": 350, "mouseCanvasY": 260, "isInCanvas": True, "menuOpen": True, "entryCount": 1, "entries": [bank_entry]},
                {"eventSequence": 5, "eventLane": "menu_option_clicked", "clientTick": 905, "gameTickAtSample": 174, "wallTimeMillis": 3_601, "mouseCanvasX": 350, "mouseCanvasY": 260, "isInCanvas": True, "cameraPose": _camera_pose(64), "geometryFrameId": observation.geometry_frame_id, **bank_entry, "resolvedTarget": exact_bank},
                {"eventSequence": 6, "eventLane": "menu_option_clicked", "clientTick": 906, "gameTickAtSample": 180, "wallTimeMillis": 4_600, "mouseCanvasX": 180, "mouseCanvasY": 190, "isInCanvas": True, "option": "Walk here", "target": "", "type": "WALK", "identifier": 0, "param0": 55, "param1": 58, "resolvedTarget": {"schema": "plugin_click_target.v1", "resolution": "resolved", "confidence": "high", "source": "menu_params", "actionFamily": "walk_tile", "selectedSceneTile": {"x": 55, "y": 58}, "worldTile": {"worldX": 3433, "worldY": 3218, "plane": 1}}},
            )
            self.assertTrue(recorder.add(_evidence(observation, _hot(*samples))))
            artifact = recorder.finish("test")

            manifest_path = artifact / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for name in (
                "cameraIntentSemanticsV4",
                "contextMenuTimingSemanticsV1",
                "manualRouteIntentSemanticsV2",
            ):
                manifest["evidenceCoverage"].pop(name)
            self.assertTrue(manifest["evidenceCoverage"]["cameraIntentSemanticsV3"])
            self.assertTrue(manifest["evidenceCoverage"]["manualRouteIntentSemanticsV1"])
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            events = _read_events_unverified(artifact / "events.jsonl")
            v3_result = _derive_summary(events, manifest)
            summary_path = artifact / "summary.json"
            summary_path.write_text(
                json.dumps(v3_result.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            timeline_path = artifact / "timeline.md"
            timeline_path.write_text(
                _timeline_markdown(manifest, events, v3_result),
                encoding="utf-8",
            )
            for relative in ("manifest.json", "summary.json", "timeline.md"):
                _rehash_file(artifact, relative)
            summary_before = summary_path.read_bytes()
            timeline_before = timeline_path.read_bytes()

            inspected = inspect_demonstration(artifact)

            self.assertTrue(inspected.valid, inspected.errors)
            self.assertEqual(summary_before, summary_path.read_bytes())
            self.assertEqual(timeline_before, timeline_path.read_bytes())
            self.assertFalse(
                any(
                    value.get("clickEventSequence") is not None
                    for value in inspected.camera_intent_episodes
                )
            )
            self.assertTrue(
                any(
                    value.get("target", {}).get("actionFamily") == "tile_object"
                    and value.get("lastCameraInputToClickMillis") == 2_501
                    for value in inspected.camera_review_episodes
                )
            )
            self.assertNotIn(
                "contextMenuOpenToClickMillis", inspected.timing_profiles[0]
            )
            self.assertEqual(
                201,
                inspected.timing_review_profiles[0]["contextMenuOpenToClickMillis"],
            )
            self.assertEqual("menu_params", inspected.manual_route_targets[0]["targetSource"])
            self.assertEqual(
                "selectedSceneTile+observationSceneOrigin",
                inspected.manual_route_review_targets[0]["targetSource"],
            )
            self.assertIsNone(
                inspected.manual_route_review_targets[0]["requestedTileDistance"]
            )

    def test_npc_menu_index_must_correlate_to_stable_census_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "npc-correlation",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            click = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 900,
                "gameTickAtSample": 174,
                "wallTimeMillis": 1000,
                "option": "Talk-to",
                "target": "Hans",
                "type": "NPC_FIRST_OPTION",
                "identifier": 4,
            }
            self.assertTrue(recorder.add(_evidence(_base_observation(), _hot(click))))
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            candidate = next(
                value
                for value in result.candidate_suggestions
                if value["kind"] == "interaction_fact_candidate"
            )
            self.assertEqual("NPC", candidate["entity"]["kind"])
            self.assertEqual(4626, candidate["entity"]["stableId"])
            self.assertEqual(
                "actor_census_index_correlation", candidate["identitySource"]
            )

    def test_npc_index_from_another_tick_remains_uncorrelated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DemonstrationRecorder(
                "npc-wrong-tick",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            click = {
                "eventSequence": 1,
                "eventLane": "menu_option_clicked",
                "clientTick": 901,
                "gameTickAtSample": 175,
                "wallTimeMillis": 1000,
                "option": "Talk-to",
                "target": "Hans",
                "type": "NPC_FIRST_OPTION",
                "identifier": 4,
            }
            self.assertTrue(recorder.add(_evidence(_base_observation(), _hot(click))))
            result = inspect_demonstration(recorder.finish("test"))
            self.assertTrue(result.valid, result.errors)
            self.assertFalse(
                any(
                    value.get("kind") == "interaction_fact_candidate"
                    for value in result.candidate_suggestions
                )
            )
            self.assertTrue(
                any("same-tick census ID" in value for value in result.ambiguities)
            )

    def test_recording_limit_reserves_a_valid_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "osrs_bot.demonstration.MAX_RECORDING_EVENTS", 2
        ):
            recorder = DemonstrationRecorder(
                "bounded-finalize",
                output_root=Path(temporary),
                screenshots_enabled=False,
            )
            recorder.start(_evidence(_base_observation(), _hot()))
            with self.assertRaises(DemonstrationLimitReached):
                recorder._append(
                    "annotation",
                    {
                        "sessionId": "fixture-session",
                        "pid": 1234,
                        "sourceTick": 174,
                        "clientTick": 900,
                        "plane": 1,
                        "eventSequence": None,
                    },
                    {"text": "over limit"},
                )
            artifact = recorder.finish("evidence_limit_reached")
            result = inspect_demonstration(artifact)
            self.assertTrue(result.valid, result.errors)
            events = (artifact / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("recording_stopped", events)

    def test_name_rejects_paths_and_modules_have_no_input_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DemonstrationError):
                DemonstrationRecorder("../escape", output_root=Path(temporary))
        for relative in ("osrs_bot/demonstration.py", "osrs_bot/screen_capture.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            self.assertFalse(
                imports & {"arduino", "input_coordinator", "action", "runtime", "login"},
                relative,
            )
            self.assertNotIn("InputCoordinator", source)
            self.assertNotIn("pyautogui", source.lower())
            self.assertNotIn("pydirectinput", source.lower())

    def test_only_demonstration_module_uses_additive_evidence_fetch(self) -> None:
        callers = []
        for path in (ROOT / "osrs_bot").glob("*.py"):
            if "fetch_demonstration_evidence" in path.read_text(encoding="utf-8"):
                callers.append(path.name)
        self.assertEqual(["demonstration.py", "observation.py"], sorted(callers))

    def test_public_batch_commands_route_to_read_only_recorder_and_inspector(self) -> None:
        source = (ROOT / "run.cmd").read_text(encoding="utf-8").lower()
        self.assertIn('if /i "%mode%"=="record-demo" goto record_demo', source)
        self.assertIn('if /i "%mode%"=="inspect-demo" goto inspect_demo', source)
        self.assertIn("python -m osrs_bot.demonstration record", source)
        self.assertIn("python -m osrs_bot.demonstration inspect", source)
        recorder_block = source.split(":record_demo", 1)[1].split(":inspect_demo", 1)[0]
        for prohibited in ("--execute", "--arduino-port", "inputcoordinator"):
            self.assertNotIn(prohibited, recorder_block)


if __name__ == "__main__":
    unittest.main()

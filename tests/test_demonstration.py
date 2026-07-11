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
    inspect_demonstration,
    record_live,
)
from osrs_bot.model import ScreenBounds, WorldPoint
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


def _hot(*samples: dict[str, Any], drops: tuple[int, int, int] = (0, 0, 0)):
    lanes = {
        "clientTickTail": [],
        "postMenuSortTail": [],
        "clickedTail": [],
    }
    for sample in samples:
        lane = sample["eventLane"]
        key = {
            "client_tick": "clientTickTail",
            "post_menu_sort": "postMenuSortTail",
            "menu_option_clicked": "clickedTail",
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
        },
    }


def _evidence(observation, hot: dict[str, Any]) -> DemonstrationEvidenceSnapshot:
    hot = json.loads(json.dumps(hot))
    sequences = []
    for key in ("clientTickTail", "postMenuSortTail", "clickedTail"):
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
    def test_external_stop_finalizes_a_verified_artifact(self) -> None:
        class Client:
            @staticmethod
            def fetch_demonstration_evidence():
                return _evidence(_base_observation(), _hot())

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
            self.assertEqual("facade_stop_requested", manifest["stopReason"])

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
            interaction_candidate = next(
                value
                for value in result.candidate_suggestions
                if value["kind"] == "interaction_fact_candidate"
            )
            self.assertEqual(16672, interaction_candidate["entity"]["stableId"])
            self.assertNotIn("menuIdentifier", interaction_candidate["entity"])
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

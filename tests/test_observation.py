from __future__ import annotations

import copy
import json
import re
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from osrs_bot.model import ScreenBounds, ScreenPoint, WorldPoint
from osrs_bot.observation import (
    CANONICAL_NEEDS,
    DEMONSTRATION_NEEDS,
    DemonstrationEvidenceSnapshot,
    ObservationClient,
    ObservationDecodeError,
    ObservationSchemaError,
    parse_observation,
)


FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_loaded.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class ObservationParsingTests(unittest.TestCase):
    def test_python_contract_tracks_java_response_and_frame_schemas(self) -> None:
        root = Path(__file__).parents[1]
        endpoint_source = (root / "src/main/java/com/osrstelemetry/PluginSnapshotEndpoint.java").read_text(encoding="utf-8")
        frame_source = (root / "src/main/java/com/osrstelemetry/SensorFrame.java").read_text(encoding="utf-8")
        response_match = re.search(r'RESPONSE_SCHEMA\s*=\s*"([^"]+)"', endpoint_source)
        frame_match = re.search(r'SCHEMA\s*=\s*"([^"]+)"', frame_source)

        self.assertIsNotNone(response_match)
        self.assertIsNotNone(frame_match)
        self.assertEqual(response_match.group(1), load_fixture()["schema"])
        self.assertEqual(frame_match.group(1), load_fixture()["sensorFrame"]["schema"])
        self.assertTrue(parse_observation(load_fixture()).source_coherent)

    def test_neutral_scene_census_produces_entity_facts(self) -> None:
        payload = load_fixture()
        payloads = payload["payloads"]
        scene = copy.deepcopy(payloads["resource_object_census"])
        scene["schema"] = "scene_object_census.v1"
        scene["objects"] = [
            *scene["objects"],
            *payloads["service_object_census"]["objects"],
        ]
        payloads["scene_object_census"] = scene
        for name in (
            "resource_object_census",
            "route_object_census",
            "service_object_census",
        ):
            del payloads[name]

        observation = parse_observation(payload)

        self.assertIsNotNone(observation.object_by_key("tree:3193:3244:1276"))
        self.assertIsNotNone(observation.object_by_key("bank:3208:3220:6943"))
        self.assertTrue(observation.source_coherent)

    def test_always_hot_login_baseline_exposes_exact_client_without_a_scene(self) -> None:
        payload = load_fixture()
        payload["status"] = "WARN"
        payload["warnings"] = ["scene payloads unavailable at login"]
        payload["missingCapabilities"] = ["inventory", "activity", "bank_ui", "dialogue_state"]
        payload["payloads"]["baseline"] = {
            "gameState": "LOGIN_SCREEN",
            "scenePlayable": False,
            "player": {},
            "inputGeometry": {
                "geometryAvailable": False,
                "isClientFocused": True,
                "clientProcessId": 4242,
            },
        }
        for name in ("inventory", "activity", "bank_ui", "dialogue_state"):
            del payload["payloads"][name]
            payload["sensorFrame"]["facts"][name]["available"] = False
            payload["sensorFrame"]["facts"][name]["errors"] = ["unavailable_at_login"]
        payload["sensorFrame"].update({
            "complete": False,
            "sessionId": "plugin-4242-test",
            "clientProcessId": 4242,
            "availableFacts": ["baseline"],
            "unavailableFacts": ["inventory", "activity", "bank_ui", "dialogue_state"],
        })
        payload["payloads"]["interaction_hot"].update({
            "sessionId": "plugin-4242-test",
            "clientProcessId": 4242,
        })

        observation = parse_observation(payload)

        self.assertFalse(observation.loaded_scene)
        self.assertEqual("LOGIN_SCREEN", observation.game_state)
        self.assertEqual(4242, observation.client_process_id)
        self.assertTrue(observation.client_focused)
        self.assertIsNone(observation.location)
        self.assertFalse(observation.inventory.known)

        del payload["payloads"]["baseline"]["inputGeometry"]
        fallback = parse_observation(payload)
        self.assertEqual(4242, fallback.client_process_id)

    def test_loaded_snapshot_becomes_one_immutable_observation(self) -> None:
        observation = parse_observation(load_fixture())

        self.assertTrue(observation.loaded_scene)
        self.assertEqual(WorldPoint(3192, 3244, 0), observation.location)
        self.assertEqual(174, observation.tick)
        self.assertEqual("fixture-session:174", observation.frame_id)
        self.assertEqual("fixture-geometry-174", observation.geometry_frame_id)
        self.assertEqual(
            datetime.fromisoformat("2026-07-10T16:06:57.694873+00:00"),
            observation.timestamp,
        )
        self.assertEqual(
            datetime.fromisoformat("2026-07-10T16:06:57.700000+00:00"),
            observation.assembled_at,
        )
        self.assertTrue(observation.source_coherent)
        self.assertTrue(observation.menu_fresh)
        self.assertEqual(879, observation.player.animation)
        self.assertEqual(2, observation.inventory.quantity(1511))
        self.assertEqual("fixture-session", observation.session_id)
        self.assertEqual(ScreenBounds(1000, 2000, 800, 600), observation.canvas_bounds)
        self.assertFalse(hasattr(observation, "__dict__"))
        self.assertFalse(hasattr(observation.location, "__dict__"))
        self.assertFalse(hasattr(observation.inventory, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            observation.tick = 175

    def test_welcome_screen_snapshot_is_not_a_loaded_scene(self) -> None:
        payload = load_fixture()
        payload["payloads"]["baseline"]["scenePlayable"] = False

        observation = parse_observation(payload)

        self.assertFalse(observation.loaded_scene)

    def test_fact_tick_mismatch_invalidates_source_coherence(self) -> None:
        payload = load_fixture()
        payload["sensorFrame"]["facts"]["inventory"]["sourceTick"] = 173

        observation = parse_observation(payload)

        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_baseline_process_identity_must_match_frame_owner(self) -> None:
        payload = load_fixture()
        payload["payloads"]["baseline"]["inputGeometry"]["clientProcessId"] = 9999

        observation = parse_observation(payload)

        self.assertEqual(9999, observation.client_process_id)
        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_dynamic_geometry_from_another_frame_fails_closed(self) -> None:
        payload = load_fixture()
        payload["payloads"]["resource_object_census"]["geometryFrameId"] = "old-camera"

        observation = parse_observation(payload)

        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_dynamic_capture_before_frame_completion_fails_closed(self) -> None:
        payload = load_fixture()
        payload["payloads"]["route_object_census"]["capturedAtUtc"] = (
            "2026-07-10T16:06:57.695000000Z"
        )

        observation = parse_observation(payload)

        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_demonstration_dynamic_evidence_must_match_atomic_frame(self) -> None:
        payload = load_fixture()
        provenance = payload["payloads"]["resource_object_census"]
        common = {
            "sourceTick": provenance["sourceTick"],
            "capturedAtUtc": provenance["capturedAtUtc"],
            "sessionId": provenance["sessionId"],
            "clientProcessId": provenance["clientProcessId"],
            "geometryFrameId": provenance["geometryFrameId"],
        }
        payload["payloads"]["actor_census"] = {
            "schema": "world_model_actor_census.v1",
            **common,
            "actors": [],
        }
        payload["payloads"]["collision_window"] = {
            "schema": "world_model_collision_window.v1",
            **common,
            "cells": [],
        }
        self.assertTrue(parse_observation(payload).source_coherent)

        for name, field, value in (
            ("actor_census", "sessionId", "other-session"),
            ("collision_window", "sourceTick", 173),
        ):
            with self.subTest(name=name, field=field):
                mismatched = copy.deepcopy(payload)
                mismatched["payloads"][name][field] = value
                observation = parse_observation(mismatched)
                self.assertFalse(observation.source_coherent)
                self.assertFalse(observation.loaded_scene)

    def test_dedupes_objects_strips_menu_tags_and_scales_safe_geometry(self) -> None:
        observation = parse_observation(load_fixture())

        self.assertEqual(2, len(observation.nearby_objects))
        tree = observation.object_by_key("tree:3193:3244:1276")
        self.assertIsNotNone(tree)
        self.assertEqual(("Chop down", "Examine"), tree.actions)
        self.assertTrue(tree.resource_candidate)
        self.assertTrue(tree.geometry.actionable)
        self.assertEqual(ScreenPoint(100, 50), tree.geometry.canvas_point)
        self.assertEqual(ScreenPoint(1200, 2100), tree.geometry.screen_point)
        self.assertEqual(ScreenBounds(1160, 2060, 80, 120), tree.geometry.screen_bounds)
        self.assertEqual("Chop down", observation.menus[0].option)
        self.assertEqual("Tree", observation.menus[0].target)

    def test_actionable_geometry_requires_exact_device_pixel_coordinate_space(self) -> None:
        for coordinate_space in (None, "awt_user_space", "logical_pixels"):
            with self.subTest(coordinate_space=coordinate_space):
                payload = load_fixture()
                geometry = payload["payloads"]["baseline"]["inputGeometry"]
                if coordinate_space is None:
                    geometry.pop("coordinateSpace")
                else:
                    geometry["coordinateSpace"] = coordinate_space

                with self.assertRaisesRegex(
                    ObservationSchemaError,
                    "coordinateSpace must be device_pixels",
                ):
                    parse_observation(payload)

    def test_unnamed_census_object_is_omitted_without_losing_named_objects(self) -> None:
        payload = load_fixture()
        unnamed = dict(payload["payloads"]["resource_object_census"]["objects"][0])
        unnamed.update({"objectKey": "unnamed:27270", "id": 27270, "name": ""})
        payload["payloads"]["route_object_census"]["objects"].append(unnamed)

        observation = parse_observation(payload)

        self.assertIsNone(observation.object_by_key("unnamed:27270"))
        self.assertIsNotNone(observation.object_by_key("tree:3193:3244:1276"))

    def test_open_menu_exposes_screen_bounds_for_each_visual_row(self) -> None:
        payload = load_fixture()
        menu = payload["payloads"]["interaction_hot"]["postMenuSort"]
        menu["menuOpen"] = True
        menu["menuBounds"] = {"x": 50, "y": 60, "width": 100, "height": 52}

        observation = parse_observation(payload)

        self.assertEqual(ScreenBounds(1100, 2120, 200, 104), observation.menu_bounds)
        self.assertEqual(ScreenBounds(1102, 2158, 198, 30), observation.menus[0].row_bounds)
        self.assertEqual(ScreenBounds(1102, 2188, 198, 30), observation.menus[1].row_bounds)

    def test_parses_bank_widget_targets_in_screen_coordinates(self) -> None:
        payload = load_fixture()
        payload["payloads"]["bank_ui"]["keyboardClosePossible"] = True
        widgets = parse_observation(payload).widgets

        self.assertTrue(widgets.bank_known)
        self.assertTrue(widgets.bank_open)
        self.assertFalse(widgets.bank_pin_open)
        self.assertTrue(widgets.bank_readable)
        self.assertTrue(widgets.keyboard_close_possible)
        self.assertEqual(ScreenBounds(1600, 2500, 80, 40), widgets.deposit_inventory.screen_bounds)
        self.assertEqual(ScreenPoint(1640, 2520), widgets.deposit_inventory.screen_point)
        self.assertEqual(ScreenBounds(1760, 2020, 32, 32), widgets.close_bank.screen_bounds)

        payload = load_fixture()
        payload["payloads"]["bank_ui"].pop("known")
        self.assertFalse(parse_observation(payload).widgets.bank_known)

    def test_out_of_canvas_aim_point_fails_closed(self) -> None:
        payload = load_fixture()
        tree = payload["payloads"]["resource_object_census"]["objects"][0]
        tree["projection"]["aimPoint"] = {"canvasX": 999, "canvasY": 50}
        tree["projection"]["canvasLocation"] = {"x": 999, "y": 50}
        payload["payloads"]["resource_object_census"]["objects"] = [tree]

        geometry = parse_observation(payload).object_by_key(tree["objectKey"]).geometry
        self.assertFalse(geometry.actionable)
        self.assertIsNone(geometry.screen_point)

    def test_missing_or_false_geometry_flags_never_become_actionable(self) -> None:
        for label, flags in (
            ("missing", {}),
            ("false", {
                "geometryAvailable": False,
                "onScreen": False,
                "visible": False,
                "actionableByCanvas": False,
            }),
        ):
            with self.subTest(label=label):
                payload = load_fixture()
                tree = payload["payloads"]["resource_object_census"]["objects"][0]
                projection = tree["projection"]
                for key in (
                    "geometryAvailable", "onScreen", "visible", "actionableByCanvas"
                ):
                    projection.pop(key, None)
                projection.update(flags)
                payload["payloads"]["resource_object_census"]["objects"] = [tree]

                geometry = parse_observation(payload).object_by_key(tree["objectKey"]).geometry

                self.assertFalse(geometry.actionable)

    def test_rejects_wrong_response_schema(self) -> None:
        payload = load_fixture()
        payload["schema"] = "historical_context.v99"
        with self.assertRaisesRegex(ObservationSchemaError, "expected schema"):
            parse_observation(payload)

    def test_pass_tile_cannot_override_false_geometry_flags(self) -> None:
        payload = load_fixture()
        point = WorldPoint(3205, 3229, 0)
        payload["payloads"]["tile_projection"] = {
            "tiles": [{
                "status": "PASS",
                "label": "route:false-tile",
                "worldX": point.x,
                "worldY": point.y,
                "plane": point.plane,
                "aimPoint": {"canvasX": 200, "canvasY": 100},
                "geometryAvailable": False,
                "onScreen": False,
                "visible": False,
                "actionable": False,
            }]
        }

        route = parse_observation(
            payload, (("route:false-tile", point),)
        ).object_by_key("route:false-tile")

        self.assertIsNotNone(route)
        self.assertFalse(route.geometry.actionable)

    def test_known_inventory_requires_complete_unique_slot_accounting(self) -> None:
        for label, mutate in (
            (
                "hidden occupied slots",
                lambda inventory: inventory.update({
                    "items": [{"slot": 0, "itemId": 1511, "quantity": 1}],
                    "occupiedSlots": 28,
                    "filledSlots": 28,
                    "freeSlots": 0,
                }),
            ),
            (
                "duplicate slot",
                lambda inventory: inventory.update({
                    "items": [
                        {"slot": 0, "itemId": 1511, "quantity": 1},
                        {"slot": 0, "itemId": 1511, "quantity": 1},
                    ],
                    "occupiedSlots": 2,
                    "filledSlots": 2,
                    "freeSlots": 26,
                }),
            ),
        ):
            with self.subTest(label=label):
                payload = load_fixture()
                mutate(payload["payloads"]["inventory"]["inventory"])
                with self.assertRaises(ObservationSchemaError):
                    parse_observation(payload)


class ObservationClientTests(unittest.TestCase):
    @patch("osrs_bot.observation.urlopen")
    def test_posts_canonical_snapshot_request_and_adds_navigation_tile(self, mocked_open) -> None:
        payload = copy.deepcopy(load_fixture())
        payload["payloads"]["tile_projection"] = {
            "schema": "tile_projection_response.v1",
            "status": "PASS",
            "tiles": [{
                "status": "PASS",
                "label": "route:castle-door",
                "worldX": 3205,
                "worldY": 3229,
                "plane": 0,
                "sceneX": 60,
                "sceneY": 45,
                "aimPoint": {"canvasX": 200, "canvasY": 100},
                "geometryAvailable": True,
                "onScreen": True,
                "visible": True,
                "actionable": True
            }],
        }
        mocked_open.return_value = FakeResponse(json.dumps(payload).encode("utf-8"))
        point = WorldPoint(3205, 3229, 0)

        observation = ObservationClient(auth_token="secret").fetch((("route:castle-door", point),))

        request = mocked_open.call_args.args[0]
        request_payload = json.loads(request.data)
        self.assertEqual("POST", request.method)
        self.assertEqual("http://127.0.0.1:8893/snapshot", request.full_url)
        self.assertEqual(list(CANONICAL_NEEDS), request_payload["needs"])
        self.assertEqual(0, request_payload["maxAgeTicks"])
        self.assertEqual(2000, request_payload["maxSourceAgeMillis"])
        self.assertEqual(0, request_payload["maxClientTickSamples"])
        self.assertEqual(0, request_payload["maxMenuSamples"])
        self.assertEqual(0, request_payload["maxClickedSamples"])
        self.assertEqual(16, request_payload["menuEntryLimit"])
        self.assertNotIn("maxMenuEntries", request_payload)
        self.assertEqual(
            [{"label": "route:castle-door", "worldX": 3205, "worldY": 3229, "plane": 0}],
            request_payload["tileProjectionRequests"],
        )
        self.assertEqual("secret", request.get_header("X-plugin-snapshot-token"))
        route = observation.object_by_key("route:castle-door")
        self.assertEqual(0, route.object_id)
        self.assertEqual("route:castle-door", route.name)
        self.assertEqual("NAVIGATION_TILE", route.kind)
        self.assertEqual(("Walk here",), route.actions)
        self.assertEqual(point, route.location)
        self.assertEqual((60, 45), (route.scene_x, route.scene_y))
        self.assertTrue(route.route_candidate)
        self.assertTrue(route.geometry.actionable)
        self.assertEqual(ScreenPoint(1400, 2200), route.geometry.screen_point)

    @patch("osrs_bot.observation.urlopen")
    def test_invalid_json_fails_explicitly(self, mocked_open) -> None:
        mocked_open.return_value = FakeResponse(b"{not valid json")
        with self.assertRaisesRegex(ObservationDecodeError, "invalid JSON"):
            ObservationClient().fetch()

    @patch("osrs_bot.observation.urlopen")
    def test_demonstration_fetch_reuses_endpoint_with_bounded_read_only_evidence(self, mocked_open) -> None:
        payload = copy.deepcopy(load_fixture())
        provenance = payload["payloads"]["resource_object_census"]
        payload["payloads"]["client_tick_tail"] = {
            "schema": "client_tick_hot.v1",
            "sessionId": "fixture-session",
            "clientProcessId": 1234,
            "latestEventSequence": 12,
            "clientTickTail": [{
                "eventSequence": 10,
                "eventLane": "client_tick",
                "clientTick": 44,
                "sessionId": "fixture-session",
                "clientProcessId": 1234,
            }],
            "postMenuSortTail": [{
                "eventSequence": 11,
                "eventLane": "post_menu_sort",
                "clientTick": 44,
                "sessionId": "fixture-session",
                "clientProcessId": 1234,
            }],
            "clickedTail": [{
                "eventSequence": 12,
                "eventLane": "menu_option_clicked",
                "clientTick": 44,
                "sessionId": "fixture-session",
                "clientProcessId": 1234,
            }],
        }
        common = {
            "sourceTick": provenance["sourceTick"],
            "capturedAtUtc": provenance["capturedAtUtc"],
            "sessionId": provenance["sessionId"],
            "clientProcessId": provenance["clientProcessId"],
            "geometryFrameId": provenance["geometryFrameId"],
        }
        payload["payloads"]["actor_census"] = {
            "schema": "world_model_actor_census.v1",
            **common,
            "actors": [],
        }
        payload["payloads"]["collision_window"] = {
            "schema": "world_model_collision_window.v1",
            **common,
            "cells": [],
        }
        mocked_open.return_value = FakeResponse(json.dumps(payload).encode("utf-8"))

        evidence = ObservationClient(auth_token="never-persist-this").fetch_demonstration_evidence()

        self.assertIsInstance(evidence, DemonstrationEvidenceSnapshot)
        self.assertTrue(evidence.observation.loaded_scene)
        request = mocked_open.call_args.args[0]
        request_payload = json.loads(request.data)
        self.assertEqual(list(DEMONSTRATION_NEEDS), request_payload["needs"])
        self.assertEqual(64, request_payload["maxClientTickSamples"])
        self.assertEqual(32, request_payload["maxMenuSamples"])
        self.assertEqual(32, request_payload["maxClickedSamples"])
        self.assertEqual(16, request_payload["menuEntryLimit"])
        self.assertTrue(request_payload["includeCollisionWindow"])
        self.assertTrue(request_payload["worldModel"]["includeActors"])
        self.assertTrue(request_payload["worldModel"]["includeCollision"])
        self.assertEqual(512, request_payload["worldModel"]["maxCollisionTiles"])
        self.assertEqual(
            12,
            evidence.payload()["payloads"]["client_tick_tail"][
                "latestEventSequence"
            ],
        )
        self.assertEqual(
            "world_model_actor_census.v1",
            evidence.payload()["payloads"]["actor_census"]["schema"],
        )
        self.assertNotIn("never-persist-this", evidence.request_json)
        self.assertNotIn("never-persist-this", evidence.payload_json)

        first = evidence.payload()
        first["status"] = "MUTATED"
        self.assertNotEqual("MUTATED", evidence.payload()["status"])
        self.assertEqual(request_payload, evidence.request())
        with self.assertRaises(FrozenInstanceError):
            evidence.payload_json = "{}"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

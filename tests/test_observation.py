from __future__ import annotations

import copy
import itertools
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
    MAX_SCENE_OBJECT_ROWS,
    MAX_SNAPSHOT_RESPONSE_BYTES,
    DemonstrationEvidenceSnapshot,
    ObservationClient,
    ObservationDecodeError,
    ObservationRequestError,
    ObservationTransportError,
    ObservationSchemaError,
    _convex_screen_hull,
    build_snapshot_request,
    parse_observation,
)
from osrs_bot.task_contract import ObservationRequest


FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_loaded.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def add_scene_census_v2(
    payload: dict,
    *,
    count: int | None = None,
    cap_hit: bool = False,
    source_cap_hit: bool = False,
    priority_object_ids: tuple[int, ...] = (),
    priority_object_keys: tuple[str, ...] = (),
    scene_coverage_complete: bool = True,
) -> dict:
    census = payload["payloads"]["scene_object_census"]
    rows = census["objects"]
    returned_ids = {
        row.get("id") for row in rows if isinstance(row, dict)
    }
    returned_priority_ids = [
        object_id
        for object_id in priority_object_ids
        if object_id in returned_ids
    ]
    returned_keys = {
        row.get("objectKey") for row in rows if isinstance(row, dict)
    }
    returned_priority_keys = [
        object_key
        for object_key in priority_object_keys
        if object_key in returned_keys
    ]
    complete = not source_cap_hit and scene_coverage_complete
    census.update(
        schema="scene_object_census.v2",
        count=(len(rows) + (1 if cap_hit else 0)) if count is None else count,
        returned=len(rows),
        capHit=cap_hit,
        responseCapHit=cap_hit,
        objectCensusCapHit=source_cap_hit,
        priorityObjectIds=list(priority_object_ids),
        priorityObjectKeys=list(priority_object_keys),
        returnedPriorityObjectIds=returned_priority_ids,
        returnedPriorityObjectKeys=returned_priority_keys,
        priorityObjectsComplete=(
            len(returned_priority_ids) == len(priority_object_ids)
            and len(returned_priority_keys) == len(priority_object_keys)
        ),
        centerWorldLocation={"x": 3193, "y": 3244, "plane": 0},
        anchorSource="player",
        radiusTiles=32,
        requestedTileCount=4225,
        scannedTileSlots=4225,
        scannedTiles=4200,
        missingTileCount=25,
        discoveredObjectCount=len(rows),
        duplicateObjectCount=1,
        indexedObjectCount=2,
        enrichedObjectCount=2,
        projectedObjectCount=2,
        sceneCoverageComplete=scene_coverage_complete,
        censusComplete=complete,
        authoritativeAbsenceEligible=complete and not cap_hit,
        priorityAbsenceEligible=complete,
    )
    return payload


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_size: int | None = None

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_size = size
        return self.body if size < 0 else self.body[:size]


class ObservationParsingTests(unittest.TestCase):
    def test_text_input_state_is_optional_typed_authoritative_evidence(self) -> None:
        legacy = load_fixture()
        self.assertIsNone(parse_observation(legacy).text_input_active)

        inactive = load_fixture()
        inactive["payloads"]["baseline"]["textInputActive"] = False
        self.assertIs(False, parse_observation(inactive).text_input_active)

        active = load_fixture()
        active["payloads"]["baseline"]["textInputActive"] = True
        self.assertIs(True, parse_observation(active).text_input_active)

        malformed = load_fixture()
        malformed["payloads"]["baseline"]["textInputActive"] = 1
        with self.assertRaisesRegex(
            ObservationSchemaError, "baseline.textInputActive must be a boolean"
        ):
            parse_observation(malformed)

    def test_canvas_tile_hull_normalizes_crossing_corner_order(self) -> None:
        points = (
            ScreenPoint(1681, 1086),
            ScreenPoint(1580, 993),
            ScreenPoint(1636, 1056),
            ScreenPoint(1749, 1156),
        )

        self.assertEqual(
            (
                ScreenPoint(1580, 993),
                ScreenPoint(1681, 1086),
                ScreenPoint(1749, 1156),
                ScreenPoint(1636, 1056),
            ),
            _convex_screen_hull(points),
        )

    def test_client_window_bounds_are_optional_but_must_contain_canvas(self) -> None:
        payload = load_fixture()
        geometry = payload["payloads"]["baseline"]["inputGeometry"]
        geometry.update(
            clientWindowX=980,
            clientWindowY=1980,
            clientWindowWidth=840,
            clientWindowHeight=640,
        )

        observation = parse_observation(payload)

        self.assertEqual(
            ScreenBounds(980, 1980, 840, 640),
            observation.client_window_bounds,
        )
        geometry["clientWindowWidth"] = 700
        with self.assertRaisesRegex(
            ObservationSchemaError, "does not contain the canvas"
        ):
            parse_observation(payload)

    def test_local_player_canvas_point_uses_baseline_input_geometry(self) -> None:
        payload = load_fixture()
        payload["payloads"]["baseline"]["player"].update(canvasX=100, canvasY=50)

        observation = parse_observation(payload)

        self.assertEqual(ScreenPoint(1200, 2100), observation.player_screen_point)

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

    def test_java_sensor_sources_do_not_assign_task_meaning(self) -> None:
        root = Path(__file__).parents[1]
        sources = "\n".join(
            (root / relative).read_text(encoding="utf-8")
            for relative in (
                "src/main/java/com/osrstelemetry/PluginSnapshotEndpoint.java",
                "src/main/java/com/osrstelemetry/TelemetryPlugin.java",
                "src/main/java/com/osrstelemetry/WorldModelCache.java",
            )
        )
        forbidden = (
            "resourceCandidate",
            "routeObjectCandidate",
            "serviceObjectCandidate",
            "resource_object_census",
            "route_object_census",
            "service_object_census",
            "profileHint",
            "taskHint",
            "classHint",
            "desiredClasses",
            "nameContains",
            "actionContains",
            "objectKinds",
            "Skill.WOODCUTTING",
            "isDialoguePromptText",
            "isDialogueOptionText",
        )

        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, sources)

    def test_neutral_scene_census_produces_entity_facts(self) -> None:
        observation = parse_observation(load_fixture())

        self.assertIsNotNone(observation.object_by_key("tree:3193:3244:1276"))
        self.assertIsNotNone(observation.object_by_key("bank:3208:3220:6943"))
        self.assertTrue(observation.source_coherent)

    def test_legacy_semantic_censuses_cannot_supply_objects(self) -> None:
        payload = load_fixture()
        scene = payload["payloads"].pop("scene_object_census")
        for name, schema in (
            ("resource_object_census", "resource_object_census.v1"),
            ("route_object_census", "route_object_census.v1"),
            ("service_object_census", "service_object_census.v1"),
        ):
            payload["payloads"][name] = {
                **copy.deepcopy(scene),
                "schema": schema,
            }

        observation = parse_observation(payload)

        self.assertEqual((), observation.nearby_objects)
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
        payload = load_fixture()
        payload["payloads"]["baseline"]["cameraViewport"].update(
            cameraYaw=1234,
            cameraPitch=1024,
            zoom3d=384,
            viewportWidth=300,
            viewportHeight=200,
            viewportXOffset=50,
            viewportYOffset=25,
        )
        observation = parse_observation(payload)

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
        self.assertEqual(1234, observation.camera_yaw)
        self.assertEqual(1024, observation.camera_pitch)
        self.assertEqual(384, observation.camera_zoom)
        self.assertEqual(ScreenBounds(1000, 2000, 800, 600), observation.canvas_bounds)
        self.assertEqual(
            ScreenBounds(1100, 2050, 600, 400),
            observation.viewport_bounds,
        )
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
        payload["payloads"]["scene_object_census"]["geometryFrameId"] = "old-camera"

        observation = parse_observation(payload)

        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_dynamic_capture_before_frame_completion_fails_closed(self) -> None:
        payload = load_fixture()
        payload["payloads"]["scene_object_census"]["capturedAtUtc"] = (
            "2026-07-10T16:06:57.695000000Z"
        )

        observation = parse_observation(payload)

        self.assertFalse(observation.source_coherent)
        self.assertFalse(observation.loaded_scene)

    def test_demonstration_dynamic_evidence_must_match_atomic_frame(self) -> None:
        payload = load_fixture()
        provenance = payload["payloads"]["scene_object_census"]
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
        self.assertFalse(hasattr(tree, "resource_candidate"))
        self.assertTrue(tree.geometry.actionable)
        self.assertEqual(ScreenPoint(100, 50), tree.geometry.canvas_point)
        self.assertEqual(ScreenPoint(1200, 2100), tree.geometry.screen_point)
        self.assertEqual(ScreenBounds(1160, 2060, 80, 120), tree.geometry.screen_bounds)
        self.assertEqual("Chop down", observation.menus[0].option)
        self.assertEqual("Tree", observation.menus[0].target)

    def test_parses_authoritative_polygon_and_preserves_bounds_precedence(self) -> None:
        payload = load_fixture()
        tree = payload["payloads"]["scene_object_census"]["objects"][0]
        tree["projection"].update({
            "authoritativeGeometrySource": "clickbox",
            "authoritativePolygon": [
                {"x": 80, "y": 30},
                {"x": 120, "y": 30},
                {"x": 120, "y": 90},
                {"x": 80, "y": 90},
            ],
            "convexHullBounds": {"x": 40, "y": 20, "w": 100, "h": 100},
            "canvasTileBounds": {"x": 0, "y": 0, "w": 200, "h": 150},
        })
        payload["payloads"]["scene_object_census"]["objects"] = [tree]

        geometry = parse_observation(payload).object_by_key(tree["objectKey"]).geometry

        self.assertEqual("clickbox", geometry.geometry_source)
        self.assertEqual(
            (
                ScreenPoint(1160, 2060),
                ScreenPoint(1240, 2060),
                ScreenPoint(1240, 2180),
                ScreenPoint(1160, 2180),
            ),
            geometry.screen_polygon,
        )
        self.assertEqual(
            ScreenBounds(1160, 2060, 80, 120), geometry.screen_bounds
        )
        with self.assertRaises(FrozenInstanceError):
            geometry.screen_polygon = ()  # type: ignore[misc]

    def test_authoritative_source_without_polygon_is_not_actionable(self) -> None:
        payload = load_fixture()
        tree = payload["payloads"]["scene_object_census"]["objects"][0]
        tree["projection"].update({
            "authoritativeGeometrySource": "clickbox",
            "authoritativePolygon": None,
            "actionableByCanvas": True,
        })
        payload["payloads"]["scene_object_census"]["objects"] = [tree]

        geometry = parse_observation(payload).object_by_key(tree["objectKey"]).geometry

        self.assertEqual("clickbox", geometry.geometry_source)
        self.assertEqual((), geometry.screen_polygon)
        self.assertFalse(geometry.actionable)

    def test_rejects_malformed_authoritative_polygon(self) -> None:
        for label, source, polygon, message in (
            ("source", "bounds", [[1, 1], [2, 1], [1, 2]], "geometry source"),
            ("point", "clickbox", [[1, 1], [2], [1, 2]], "must be an x/y object"),
            ("degenerate", "clickbox", [[1, 1], [2, 2], [3, 3]], "non-zero area"),
        ):
            with self.subTest(label=label):
                payload = load_fixture()
                tree = payload["payloads"]["scene_object_census"]["objects"][0]
                tree["projection"].update({
                    "authoritativeGeometrySource": source,
                    "authoritativePolygon": polygon,
                })
                payload["payloads"]["scene_object_census"]["objects"] = [tree]
                with self.assertRaisesRegex(ObservationSchemaError, message):
                    parse_observation(payload)

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
        unnamed = dict(payload["payloads"]["scene_object_census"]["objects"][0])
        unnamed.update({"objectKey": "unnamed:27270", "id": 27270, "name": ""})
        payload["payloads"]["scene_object_census"]["objects"].append(unnamed)

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
        tree = payload["payloads"]["scene_object_census"]["objects"][0]
        tree["projection"]["aimPoint"] = {"canvasX": 999, "canvasY": 50}
        tree["projection"]["canvasLocation"] = {"x": 999, "y": 50}
        payload["payloads"]["scene_object_census"]["objects"] = [tree]

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
                tree = payload["payloads"]["scene_object_census"]["objects"][0]
                projection = tree["projection"]
                for key in (
                    "geometryAvailable", "onScreen", "visible", "actionableByCanvas"
                ):
                    projection.pop(key, None)
                projection.update(flags)
                payload["payloads"]["scene_object_census"]["objects"] = [tree]

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

    def test_warn_tile_retains_exact_identity_for_camera_recovery(self) -> None:
        payload = load_fixture()
        point = WorldPoint(3200, 3238, 0)
        payload["payloads"]["tile_projection"] = {
            "schema": "tile_projection_response.v1",
            "status": "WARN",
            "sourceTick": 174,
            "capturedAtUtc": "2026-07-10T16:06:57.697000000Z",
            "sessionId": "fixture-session",
            "clientProcessId": 1234,
            "geometryFrameId": "fixture-geometry-174",
            "tiles": [{
                "status": "WARN",
                "label": "route:west-approach",
                "worldX": point.x,
                "worldY": point.y,
                "plane": point.plane,
                "sceneX": 56,
                "sceneY": 38,
                "geometryAvailable": False,
                "onScreen": False,
                "visible": False,
                "actionable": False,
                "reason": "tile projection returned no canvas geometry",
            }]
        }

        observation = parse_observation(
            payload, (("route:west-approach", point),)
        )
        route = observation.object_by_key("route:west-approach")

        self.assertIsNotNone(route)
        self.assertTrue(observation.source_coherent)
        self.assertTrue(observation.loaded_scene)
        self.assertEqual("NAVIGATION_TILE", route.kind)
        self.assertEqual(point, route.location)
        self.assertEqual((56, 38), (route.scene_x, route.scene_y))
        self.assertFalse(route.geometry.actionable)

    def test_tile_projection_must_match_an_exact_requested_world_point(self) -> None:
        point = WorldPoint(3200, 3238, 0)
        for label, requested in (
            ("unrequested label", ()),
            ("contradictory location", (("route:west-approach", point),)),
        ):
            with self.subTest(label=label):
                payload = load_fixture()
                payload["payloads"]["tile_projection"] = {
                    "tiles": [{
                        "status": "WARN",
                        "label": "route:west-approach",
                        "worldX": point.x + (1 if requested else 0),
                        "worldY": point.y,
                        "plane": point.plane,
                        "sceneX": 56,
                        "sceneY": 38,
                        "geometryAvailable": False,
                        "onScreen": False,
                        "visible": False,
                        "actionable": False,
                    }]
                }
                with self.assertRaises(ObservationSchemaError):
                    parse_observation(payload, requested)

    def test_camera_pose_rejects_invalid_fixed_point_values(self) -> None:
        payload = load_fixture()
        payload["payloads"]["baseline"]["cameraViewport"]["cameraYaw"] = 16_384

        with self.assertRaisesRegex(ObservationSchemaError, "cameraYaw"):
            parse_observation(payload)

    def test_camera_zoom_is_optional_and_rejects_invalid_values(self) -> None:
        payload = load_fixture()
        camera = payload["payloads"]["baseline"]["cameraViewport"]
        self.assertIsNone(parse_observation(payload).camera_zoom)

        camera["zoom3d"] = -1
        with self.assertRaisesRegex(ObservationSchemaError, "zoom3d"):
            parse_observation(payload)

        camera["zoom3d"] = True
        with self.assertRaisesRegex(ObservationSchemaError, "zoom3d"):
            parse_observation(payload)

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


    def test_legacy_census_defaults_unknown_and_builds_immutable_exact_indexes(self) -> None:
        observation = parse_observation(load_fixture())

        self.assertFalse(observation.scene_census.metadata_present)
        self.assertIsNone(observation.scene_census.complete)
        self.assertIsNone(
            observation.scene_census.authoritative_absence_eligible
        )
        self.assertEqual(1, observation.scene_census.duplicate_row_count)
        self.assertEqual(1, observation.scene_census.duplicate_group_count)
        tree = observation.object_by_key("tree:3193:3244:1276")
        self.assertIs(tree, observation.scene_index.by_key[tree.key])
        self.assertEqual((tree,), observation.objects_by_id(1276))
        with self.assertRaises(TypeError):
            observation.scene_index.by_key["mutated"] = tree  # type: ignore[index]

        legacy_metadata = load_fixture()
        legacy_metadata["payloads"]["scene_object_census"].update(
            count=3,
            returned=3,
            capHit=False,
            objectCensusCapHit=False,
            priorityObjectIds=[],
            returnedPriorityObjectIds=[],
            priorityObjectsComplete=True,
        )
        legacy_census = parse_observation(legacy_metadata).scene_census
        self.assertTrue(legacy_census.metadata_present)
        self.assertIsNone(legacy_census.complete)
        self.assertIsNone(legacy_census.authoritative_absence_eligible)

        retained_v1_metadata = load_fixture()
        retained_v1_metadata["payloads"]["scene_object_census"].update(
            count=3,
            returned=3,
            capHit=False,
            objectCensusCapHit=False,
        )
        retained_v1_census = parse_observation(
            retained_v1_metadata,
            priority_object_ids=(1276,),
            priority_object_keys=("tree:3193:3244:1276",),
        ).scene_census
        self.assertTrue(retained_v1_census.metadata_present)
        self.assertIsNone(retained_v1_census.complete)
        self.assertIsNone(retained_v1_census.scene_coverage_complete)
        self.assertIsNone(retained_v1_census.authoritative_absence_eligible)
        self.assertFalse(retained_v1_census.priority_absence_eligible)
        self.assertEqual(3, retained_v1_census.returned)

    def test_v2_census_and_pipeline_evidence_are_typed_and_complete(self) -> None:
        payload = add_scene_census_v2(
            load_fixture(),
            priority_object_ids=(1276, 6943),
            priority_object_keys=("tree:3193:3244:1276",),
        )
        payload.update(
            requestId="request-174",
            serviceTimingMillis=7,
            pipeline={
                "schema": "world_model_pipeline.v1",
                "cacheHit": True,
                "cacheMiss": False,
                "cacheEntries": 2,
                "cacheHits": 8,
                "cacheMisses": 1,
                "querySequence": 9,
                "sourceTick": 174,
                "clientTick": 44,
                "sessionId": "fixture-session",
                "clientProcessId": 1234,
                "geometryFrameId": "fixture-geometry-174",
                "rawCacheKey": "fixture-session|174|fixture-geometry-174",
                "refreshSequence": 12,
                "reason": "same_source_identity",
                "refreshDurationMillis": 0,
                "queryDurationMillis": 3,
                "scannedTiles": 4200,
                "enrichedObjectCount": 2,
                "operationCounts": {
                    "definitionLookups": 2,
                    "serializationPasses": 1,
                },
            },
            worldModelQuality={"worldModelAgeMs": 4},
            worldModel={
                "queryDiagnostics": {
                    "schema": "client_thread_query_diagnostics.v1",
                    "lane": "world_model",
                    "requestStatus": "PASS",
                    "requestCoalesced": True,
                    "workExecuted": False,
                    "timeoutMillis": 1000,
                    "queueWaitMillis": 2,
                    "executionMillis": 0,
                    "activeRequestCount": 1,
                    "pendingRequestCount": 1,
                    "maxDepth": 2,
                    "submittedCount": 10,
                    "executedCount": 7,
                    "coalescedCount": 2,
                    "supersededCount": 1,
                    "timedOutCount": 0,
                    "expiredBeforeExecutionCount": 0,
                    "lateResultCount": 0,
                    "failedCount": 0,
                }
            },
            responseSizing={
                "maxResponseBytes": 1048576,
                "requestedProjectionRefs": 16,
                "effectiveProjectionRefs": 16,
                "projectionRefsBeforeCap": 3,
                "projectionRefsAfterCap": 2,
                "trimmedProjectionRefs": 1,
                "projectionRefsCapped": True,
                "serializationPasses": 1,
                "serializedBytesReusedForWrite": True,
            },
            endpointQueueDiagnostics={
                "schema": "plugin_snapshot_endpoint_queue_diagnostics.v1",
                "workerLimit": 4,
                "pendingCapacity": 8,
                "activeWorkerCount": 1,
                "pendingRequestCount": 2,
                "pendingRemainingCapacity": 6,
                "largestWorkerCount": 4,
                "completedRequestCount": 100,
                "executionRejectionCount": 1,
                "rejectionPolicy": "CALLER_RUNS_BACKPRESSURE",
                "snapshotRequestActive": True,
                "snapshotBusyRejectionCount": 3,
                "executorState": "RUNNING",
            },
        )
        payload["worldModel"]["pipeline"] = payload.pop("pipeline")

        observation = parse_observation(
            payload,
            priority_object_ids=(1276, 6943),
            priority_object_keys=("tree:3193:3244:1276",),
        )

        census = observation.scene_census
        self.assertTrue(census.metadata_present)
        self.assertTrue(census.complete)
        self.assertTrue(census.authoritative_absence_eligible)
        self.assertEqual(WorldPoint(3193, 3244, 0), census.center_world_location)
        self.assertEqual(4200, census.scanned_tiles)
        self.assertEqual(2, census.parsed_object_count)
        self.assertEqual((1276, 6943), census.reported_priority_object_ids)
        self.assertEqual(
            ("tree:3193:3244:1276",),
            census.requested_priority_object_keys,
        )
        pipeline = observation.pipeline
        self.assertEqual("request-174", pipeline.request_id)
        self.assertTrue(pipeline.cache_hit)
        self.assertEqual(12, pipeline.refresh_sequence)
        self.assertEqual(7.0, pipeline.service_timing_millis)
        operation_counts = dict(pipeline.operation_counts)
        self.assertEqual(2, operation_counts["definitionLookups"])
        self.assertEqual(1, operation_counts["serializationPasses"])
        self.assertEqual(4200, operation_counts["scannedTiles"])
        self.assertEqual(2, operation_counts["enrichedObjectCount"])
        self.assertEqual(9, pipeline.query_sequence)
        self.assertEqual("fixture-session", pipeline.session_id)
        self.assertEqual(3.0, pipeline.query_duration_millis)
        self.assertIsNotNone(pipeline.parse_millis)
        self.assertIsNotNone(pipeline.index_millis)
        self.assertEqual(
            "client_thread_query_diagnostics.v1",
            pipeline.query_diagnostics_schema,
        )
        self.assertEqual(2, pipeline.max_queue_depth)
        self.assertEqual(1, pipeline.pending_request_count)
        self.assertEqual(2, pipeline.coalesced_request_count)
        self.assertEqual(1, pipeline.serialization_passes)
        self.assertTrue(pipeline.serialized_bytes_reused_for_write)
        self.assertEqual(4, pipeline.endpoint_worker_limit)
        self.assertEqual(2, pipeline.endpoint_pending_request_count)
        self.assertEqual(3, pipeline.endpoint_busy_rejection_count)
        with self.assertRaisesRegex(
            ObservationSchemaError, "priorityObjectKeys disagree with the request"
        ):
            parse_observation(
                payload,
                priority_object_ids=(1276, 6943),
                priority_object_keys=("different:key",),
            )

        isolated = add_scene_census_v2(load_fixture())
        isolated_census = isolated["payloads"]["scene_object_census"]
        isolated_census.update(
            contradictoryDuplicateCount=1,
            contradictoryObjectKeys=["isolated:conflict"],
            authoritativeAbsenceEligible=False,
            priorityAbsenceEligible=True,
        )
        isolated_evidence = parse_observation(isolated).scene_census
        self.assertTrue(isolated_evidence.complete)
        self.assertFalse(isolated_evidence.authoritative_absence_eligible)
        self.assertTrue(isolated_evidence.priority_absence_eligible)
        self.assertEqual(
            ("isolated:conflict",),
            isolated_evidence.conflicting_duplicate_keys,
        )

    def test_capped_census_is_incomplete_and_contradictory_caps_fail(self) -> None:
        capped = add_scene_census_v2(
            load_fixture(), cap_hit=True, scene_coverage_complete=False
        )
        census = parse_observation(capped).scene_census
        self.assertFalse(census.complete)
        self.assertFalse(census.authoritative_absence_eligible)
        self.assertTrue(census.response_cap_hit)

        malformed = copy.deepcopy(capped)
        malformed["payloads"]["scene_object_census"]["capHit"] = False
        with self.assertRaises(ObservationSchemaError):
            parse_observation(malformed)

        malformed = copy.deepcopy(capped)
        malformed["payloads"]["scene_object_census"]["returned"] = 2
        with self.assertRaisesRegex(
            ObservationSchemaError, "returned disagrees with objects length"
        ):
            parse_observation(malformed)

    def test_duplicate_resolution_is_permutation_stable_and_never_composite(self) -> None:
        fixture_rows = load_fixture()["payloads"]["scene_object_census"]["objects"]
        first, second, bank = fixture_rows
        selected_rows = []
        for order in itertools.permutations((first, second)):
            payload = load_fixture()
            payload["payloads"]["scene_object_census"]["objects"] = [
                *copy.deepcopy(order),
                copy.deepcopy(bank),
            ]
            observation = parse_observation(payload)
            selected_rows.append(
                observation.object_by_key("tree:3193:3244:1276")
            )
        self.assertEqual(selected_rows[0], selected_rows[1])
        self.assertEqual(("Chop down", "Examine"), selected_rows[0].actions)
        self.assertIsNone(selected_rows[0].geometry.visible_area_ratio)

        contradictory = copy.deepcopy(second)
        contradictory.update(
            id=9999,
            name="Impostor",
            worldX=3300,
            worldY=3300,
            sceneX=77,
            sceneY=88,
            actions=["Talk-to"],
        )
        observed_keys = []
        for order in itertools.permutations((first, contradictory)):
            payload = load_fixture()
            payload["payloads"]["scene_object_census"]["objects"] = [
                *copy.deepcopy(order),
                copy.deepcopy(bank),
            ]
            observation = parse_observation(payload)
            self.assertIsNone(
                observation.object_by_key("tree:3193:3244:1276")
            )
            self.assertEqual(
                ("tree:3193:3244:1276",),
                observation.scene_census.conflicting_duplicate_keys,
            )
            observed_keys.append(tuple(item.key for item in observation.nearby_objects))
        self.assertEqual(observed_keys[0], observed_keys[1])

    def test_scene_rows_are_bounded_and_exact_id_index_handles_dense_results(self) -> None:
        base = load_fixture()["payloads"]["scene_object_census"]["objects"][0]

        def payload_with_rows(count: int) -> dict:
            payload = load_fixture()
            rows = []
            for index in range(count):
                row = copy.deepcopy(base)
                row.update(
                    objectKey=f"dense:{index}:1276",
                    worldX=3193 + index,
                    distanceToPlayer=index,
                )
                rows.append(row)
            payload["payloads"]["scene_object_census"]["objects"] = rows
            return payload

        observation = parse_observation(payload_with_rows(MAX_SCENE_OBJECT_ROWS))
        self.assertEqual(MAX_SCENE_OBJECT_ROWS, len(observation.nearby_objects))
        self.assertEqual(
            MAX_SCENE_OBJECT_ROWS, len(observation.objects_by_id(1276))
        )
        capped = add_scene_census_v2(
            payload_with_rows(MAX_SCENE_OBJECT_ROWS),
            count=100,
            cap_hit=True,
            scene_coverage_complete=True,
        )
        capped_census = parse_observation(capped).scene_census
        self.assertTrue(capped_census.complete)
        self.assertFalse(capped_census.authoritative_absence_eligible)
        self.assertTrue(capped_census.priority_absence_eligible)
        with self.assertRaisesRegex(ObservationSchemaError, "maximum of 64 rows"):
            parse_observation(payload_with_rows(MAX_SCENE_OBJECT_ROWS + 1))

    def test_other_parsed_row_collections_are_structurally_bounded(self) -> None:
        menu_payload = load_fixture()
        menu = menu_payload["payloads"]["interaction_hot"]["postMenuSort"]
        menu["entries"] = [copy.deepcopy(menu["entries"][0]) for _ in range(17)]

        inventory_payload = load_fixture()
        inventory_payload["payloads"]["inventory"]["inventory"]["items"] = [
            {"slot": index, "itemId": 1511, "quantity": 1}
            for index in range(29)
        ]

        dialogue_payload = load_fixture()
        dialogue_payload["payloads"]["dialogue_state"]["options"] = [
            {"index": index, "key": str(index), "text": f"Option {index}"}
            for index in range(17)
        ]

        tile_payload = load_fixture()
        tile_payload["payloads"]["tile_projection"] = {"tiles": [{}] * 17}
        requested_tiles = tuple(
            (f"tile:{index}", WorldPoint(3200 + index, 3200, 0))
            for index in range(16)
        )

        for label, payload, tiles in (
            ("menu", menu_payload, None),
            ("inventory", inventory_payload, None),
            ("dialogue", dialogue_payload, None),
            ("tiles", tile_payload, requested_tiles),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(ObservationSchemaError, "maximum"):
                    parse_observation(payload, tiles)


class ObservationClientTests(unittest.TestCase):
    def test_builds_bounded_phase_specific_world_model_query(self) -> None:
        center = WorldPoint(3200, 3201, 0)

        request = build_snapshot_request(
            priority_object_ids=(1276,),
            priority_object_keys=("exact:tree",),
            center_world_location=center,
            radius_tiles=4,
            max_objects=16,
            max_projection_objects=8,
            purpose="resource_target_verification",
        )

        world_model = request["worldModel"]
        self.assertEqual([1276], world_model["priorityObjectIds"])
        self.assertEqual(["exact:tree"], world_model["priorityObjectKeys"])
        self.assertEqual(
            {"worldX": 3200, "worldY": 3201, "plane": 0},
            world_model["centerWorldLocation"],
        )
        self.assertEqual(4, world_model["radiusTiles"])
        self.assertEqual(16, world_model["maxObjects"])
        self.assertEqual(8, world_model["maxProjectionObjects"])
        self.assertEqual(
            "resource_target_verification", world_model["purpose"]
        )
        with self.assertRaises(ObservationRequestError):
            build_snapshot_request(max_objects=4, max_projection_objects=5)

    @patch("osrs_bot.observation.urlopen")
    def test_fetch_planned_transmits_exact_lock_and_budget(self, mocked_open) -> None:
        mocked_open.return_value = FakeResponse(
            json.dumps(load_fixture()).encode("utf-8")
        )
        plan = ObservationRequest(
            priority_object_ids=(1276,),
            priority_object_keys=("exact:tree",),
            center_world_location=WorldPoint(3200, 3200, 0),
            radius_tiles=4,
            max_objects=16,
            max_projection_objects=8,
            purpose="resource_target_verification",
        )

        observation = ObservationClient(auth_token="secret").fetch_planned(plan)

        request_payload = json.loads(mocked_open.call_args.args[0].data)
        self.assertEqual(
            ["exact:tree"],
            request_payload["worldModel"]["priorityObjectKeys"],
        )
        self.assertEqual(4, request_payload["worldModel"]["radiusTiles"])
        self.assertEqual(
            ("exact:tree",),
            observation.scene_census.requested_priority_object_keys,
        )

    @patch("osrs_bot.observation.urlopen")
    def test_explicitly_disables_demonstration_camera_capture(self, mocked_open) -> None:
        mocked_open.return_value = FakeResponse(b'{"status":"OK"}')

        ObservationClient(auth_token="secret").disable_demonstration_capture()

        request = mocked_open.call_args.args[0]
        request_payload = json.loads(request.data)
        self.assertTrue(request_payload["disableCameraInputCapture"])
        self.assertEqual(0, request_payload["maxCameraInputSamples"])
        self.assertEqual("POST", request.method)

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
                "canvasTilePolygon": [
                    [180, 90], [220, 90], [220, 110], [180, 110]
                ],
                "geometryAvailable": True,
                "onScreen": True,
                "visible": True,
                "actionable": True
                ,"sceneSupported": True
                ,"collisionSupported": True
                ,"shortcutClear": True
            }],
        }
        mocked_open.return_value = FakeResponse(json.dumps(payload).encode("utf-8"))
        point = WorldPoint(3205, 3229, 0)

        observation = ObservationClient(auth_token="secret").fetch(
            (("route:castle-door", point),),
            (1276, 18491),
        )

        request = mocked_open.call_args.args[0]
        request_payload = json.loads(request.data)
        route = observation.object_by_key("route:castle-door")
        self.assertIsNotNone(route)
        self.assertTrue(route.geometry.scene_supported)
        self.assertTrue(route.geometry.collision_supported)
        self.assertTrue(route.geometry.shortcut_clear)
        self.assertEqual("POST", request.method)
        self.assertEqual("http://127.0.0.1:8893/snapshot", request.full_url)
        self.assertEqual(list(CANONICAL_NEEDS), request_payload["needs"])
        self.assertEqual(0, request_payload["maxAgeTicks"])
        self.assertEqual(2000, request_payload["maxSourceAgeMillis"])
        self.assertEqual(0, request_payload["maxClientTickSamples"])
        self.assertEqual(0, request_payload["maxMenuSamples"])
        self.assertEqual(0, request_payload["maxClickedSamples"])
        self.assertEqual(0, request_payload["maxCameraInputSamples"])
        self.assertEqual(16, request_payload["menuEntryLimit"])
        self.assertEqual(
            [1276, 18491],
            request_payload["worldModel"]["priorityObjectIds"],
        )
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
        self.assertTrue(route.geometry.actionable)
        self.assertEqual(ScreenPoint(1400, 2200), route.geometry.screen_point)
        self.assertEqual("canvas_tile", route.geometry.geometry_source)
        self.assertEqual(
            (
                ScreenPoint(1360, 2180),
                ScreenPoint(1440, 2180),
                ScreenPoint(1440, 2220),
                ScreenPoint(1360, 2220),
            ),
            route.geometry.screen_polygon,
        )

    def test_priority_object_request_is_bounded_positive_and_unique(self) -> None:
        self.assertNotIn("priorityObjectIds", build_snapshot_request()["worldModel"])
        for invalid in (
            (1276, 1276),
            (0,),
            (-1,),
            (True,),
            ("1276",),
            tuple(range(1, 34)),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ObservationRequestError):
                    build_snapshot_request(priority_object_ids=invalid)  # type: ignore[arg-type]

    @patch("osrs_bot.observation.urlopen")
    def test_invalid_json_fails_explicitly(self, mocked_open) -> None:
        mocked_open.return_value = FakeResponse(b"{not valid json")
        with self.assertRaisesRegex(ObservationDecodeError, "invalid JSON"):
            ObservationClient().fetch()

    @patch("osrs_bot.observation.urlopen")
    def test_snapshot_transport_reads_at_most_limit_plus_one(self, mocked_open) -> None:
        exact = FakeResponse(
            b"{}" + b" " * (MAX_SNAPSHOT_RESPONSE_BYTES - 2)
        )
        mocked_open.return_value = exact
        self.assertEqual({}, ObservationClient()._post_snapshot({}))
        self.assertEqual(MAX_SNAPSHOT_RESPONSE_BYTES + 1, exact.read_size)

        oversized = FakeResponse(b" " * (MAX_SNAPSHOT_RESPONSE_BYTES + 1))
        mocked_open.return_value = oversized
        with self.assertRaisesRegex(
            ObservationTransportError, "4194304-byte limit"
        ):
            ObservationClient()._post_snapshot({})
        self.assertEqual(MAX_SNAPSHOT_RESPONSE_BYTES + 1, oversized.read_size)

    @patch("osrs_bot.observation.urlopen")
    def test_fetch_records_local_transport_bytes_and_timing(self, mocked_open) -> None:
        body = json.dumps(load_fixture()).encode("utf-8")
        mocked_open.return_value = FakeResponse(body)

        observation = ObservationClient().fetch()

        self.assertEqual(len(body), observation.pipeline.response_bytes)
        self.assertIsNotNone(observation.pipeline.http_millis)
        self.assertIsNotNone(observation.pipeline.decode_millis)
        self.assertIsNotNone(observation.pipeline.parse_millis)

    @patch("osrs_bot.observation.urlopen")
    def test_demonstration_fetch_reuses_endpoint_with_bounded_read_only_evidence(self, mocked_open) -> None:
        payload = copy.deepcopy(load_fixture())
        provenance = payload["payloads"]["scene_object_census"]
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
        self.assertEqual(64, request_payload["maxCameraInputSamples"])
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

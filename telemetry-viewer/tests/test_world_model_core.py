import sys
import unittest
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import world_model_client
import world_model_core


def test_extract_world_model_payloads_from_snapshot_response():
    response = {
        "schema": "plugin_snapshot_response.v1",
        "payloads": {
            "world_model_summary": {"schema": "world_model_summary.v1"},
            "resource_object_census": {"schema": "resource_object_census.v1", "count": 2, "objects": []},
            "inventory": {"inventory": {"items": [{"slot": 0, "itemId": 1511, "quantity": 1}]}},
        },
        "worldModelQuality": {"worldModelAvailable": True, "collisionAvailable": True},
    }

    payloads = world_model_core.extract_world_model_payloads(response)

    assert payloads["world_model_summary"]["schema"] == "world_model_summary.v1"
    assert payloads["resource_object_census"]["count"] == 2
    assert payloads["inventory"]["inventory"]["items"][0]["itemId"] == 1511
    assert payloads["quality"]["worldModelAvailable"] is True


def test_world_model_resource_candidate_rejects_low_level_oak_but_allows_tree():
    census = {
        "objects": [
            {
                "name": "Oak",
                "id": 10820,
                "actions": ["Chop down"],
                "worldX": 3200,
                "worldY": 3200,
                "plane": 0,
                "resourceCandidate": True,
                "resourceType": "oak",
                "requiredSkill": "WOODCUTTING",
                "requiredLevel": 15,
                "playerLevelKnown": True,
                "playerLevel": 1,
                "levelRequirementMet": False,
                "visibleButNotExecutable": True,
                "targetTemporarilyLockedReason": "insufficient_woodcutting_level",
                "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
            },
            {
                "name": "Tree",
                "id": 1276,
                "actions": ["Chop down"],
                "worldX": 3201,
                "worldY": 3200,
                "plane": 0,
                "resourceCandidate": True,
                "resourceType": "basic_tree",
                "requiredSkill": "WOODCUTTING",
                "requiredLevel": 1,
                "playerLevelKnown": False,
                "levelRequirementMet": True,
                "visibleButNotExecutable": False,
                "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
            },
        ]
    }

    oak, tree = world_model_core.census_to_candidates(census, source_lane="worldModelResourceObjectCensus")

    assert oak["actionability"] == "blocked"
    assert oak["rejectionReason"] == "insufficient_woodcutting_level"
    assert tree["classId"] == "tree"
    assert tree["actionability"] == "needs_hover_confirmation"


def test_route_service_candidates_are_deduped_from_censuses():
    payloads = {
        "service_object_census": {
            "objects": [
                {"objectKey": "bank", "id": 1, "name": "Bank booth", "serviceObjectCandidate": True, "routeObjectCandidate": True}
            ]
        },
        "route_object_census": {
            "objects": [
                {"objectKey": "bank", "id": 1, "name": "Bank booth", "serviceObjectCandidate": True, "routeObjectCandidate": True},
                {"objectKey": "stairs", "id": 2, "name": "Staircase", "routeObjectCandidate": True, "routeObjectKind": "route_transition"},
            ]
        },
    }

    candidates = world_model_core.route_service_candidates_from_payloads(payloads)

    assert [candidate["objectKey"] for candidate in candidates] == ["bank", "stairs"]
    assert candidates[1]["_routeObjectScanSource"] == "worldModelRouteObjectCensus"


def test_status_fields_exposes_world_model_camera_viewport():
    payloads = {
        "world_model_summary": {
            "metadata": {
                "cameraYaw": 1947,
                "cameraPitch": 383,
                "viewport": {
                    "viewportWidth": 512,
                    "viewportHeight": 334,
                    "viewportXOffset": 4,
                    "viewportYOffset": 4,
                    "canvasWidth": 765,
                    "canvasHeight": 503,
                },
            },
            "quality": {"worldModelAvailable": True},
        }
    }

    fields = world_model_core.status_fields(payloads)

    viewport = fields["worldModelCameraViewport"]
    assert viewport["viewportWidth"] == 512
    assert viewport["viewportHeight"] == 334
    assert viewport["cameraYaw"] == 1947
    assert viewport["cameraPitch"] == 383


def test_client_build_request_keeps_world_model_bounded():
    request = world_model_client.build_request(max_objects=99, radius_tiles=24, include_projection=True)

    assert "world_model_summary" in request["needs"]
    assert request["worldModel"]["maxObjects"] == 99
    assert request["worldModel"]["radiusTiles"] == 24
    assert request["worldModel"]["includeProjection"] is True


def test_client_normalizes_snapshot_base_url():
    assert world_model_client.normalize_snapshot_url("http://127.0.0.1:8893") == "http://127.0.0.1:8893/snapshot"
    assert world_model_client.normalize_snapshot_url("http://127.0.0.1:8893/") == "http://127.0.0.1:8893/snapshot"
    assert world_model_client.normalize_snapshot_url("http://127.0.0.1:8893/snapshot") == "http://127.0.0.1:8893/snapshot"


class WorldModelCoreTest(unittest.TestCase):
    def test_extract_world_model_payloads_from_snapshot_response(self):
        test_extract_world_model_payloads_from_snapshot_response()

    def test_world_model_resource_candidate_rejects_low_level_oak_but_allows_tree(self):
        test_world_model_resource_candidate_rejects_low_level_oak_but_allows_tree()

    def test_route_service_candidates_are_deduped_from_censuses(self):
        test_route_service_candidates_are_deduped_from_censuses()

    def test_status_fields_exposes_world_model_camera_viewport(self):
        test_status_fields_exposes_world_model_camera_viewport()

    def test_client_build_request_keeps_world_model_bounded(self):
        test_client_build_request_keeps_world_model_bounded()

    def test_client_normalizes_snapshot_base_url(self):
        test_client_normalizes_snapshot_base_url()


if __name__ == "__main__":
    unittest.main()

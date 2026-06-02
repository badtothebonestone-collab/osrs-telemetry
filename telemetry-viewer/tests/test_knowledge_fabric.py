import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import knowledge_fabric
import mcp_server
import external_knowledge
import external_knowledge_cache


def synthetic_payloads():
    return {
        "quality": {
            "worldModelAvailable": True,
            "worldModelAgeMs": 12,
            "sourceTick": 42,
            "collisionAvailable": True,
            "projectionAuditAvailable": True,
        },
        "world_model_summary": {
            "schema": "world_model_summary.v1",
            "tick": 42,
            "loadedSceneOnly": True,
            "fullWorldLoaded": False,
        },
        "resource_object_census": {
            "schema": "resource_object_census.v1",
            "count": 2,
            "objects": [
                {
                    "objectKey": "oak-1",
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
                    "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True, "edgeDistancePx": 40},
                },
                {
                    "objectKey": "tree-1",
                    "name": "Tree",
                    "id": 1276,
                    "actions": ["Chop down"],
                    "worldX": 3202,
                    "worldY": 3200,
                    "plane": 0,
                    "resourceCandidate": True,
                    "resourceType": "basic_tree",
                    "requiredSkill": "WOODCUTTING",
                    "requiredLevel": 1,
                    "levelRequirementMet": True,
                    "visibleButNotExecutable": False,
                    "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True, "edgeDistancePx": 80},
                },
            ],
        },
        "service_object_census": {
            "schema": "service_object_census.v1",
            "count": 2,
            "objects": [
                {
                    "objectKey": "bank-booth",
                    "name": "Bank booth",
                    "actions": ["Bank"],
                    "worldX": 3208,
                    "worldY": 3220,
                    "plane": 2,
                    "serviceObjectCandidate": True,
                    "serviceObjectType": "bank_booth",
                    "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
                },
                {
                    "objectKey": "deposit-box",
                    "name": "Deposit box",
                    "actions": ["Deposit"],
                    "worldX": 3209,
                    "worldY": 3221,
                    "plane": 2,
                    "serviceObjectCandidate": True,
                    "serviceObjectType": "deposit_box",
                    "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
                },
            ],
        },
        "route_object_census": {
            "schema": "route_object_census.v1",
            "count": 1,
            "objectCensusCapHit": True,
            "objects": [
                {
                    "objectKey": "stairs-1",
                    "name": "Staircase",
                    "actions": ["Climb-up", "Climb-down"],
                    "worldX": 3205,
                    "worldY": 3229,
                    "plane": 0,
                    "routeObjectCandidate": True,
                    "routeObjectKind": "route_transition",
                    "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
                }
            ],
        },
        "pathing_frontier": {
            "schema": "pathing_frontier.v1",
            "frontier": [
                {"worldX": 3201, "worldY": 3201, "plane": 0, "progressScore": 1.0},
                {"worldX": 3202, "worldY": 3201, "plane": 0, "progressScore": 0.8},
            ],
        },
        "projection_audit": {"schema": "projection_audit.v1", "auditedObjects": 5, "projectionCapHit": False},
        "view_quality_inputs": {"schema": "view_quality_inputs.v1", "resourceCount": 2, "routeCount": 1, "serviceCount": 2},
    }


def make_fabric(tmp_path=None):
    status = {
        "schema": "context_status.v1",
        "latestTick": 42,
        "sessionPath": str(tmp_path) if tmp_path else None,
        "worldModelPayloads": synthetic_payloads(),
    }
    return knowledge_fabric.KnowledgeFabric.from_status(status)


def make_route_blocker_fabric():
    status = {
        "schema": "context_status.v1",
        "latestTick": 43,
        "gameState": "LOGGED_IN",
        "worldModelPayloads": synthetic_payloads(),
        "brain": {
            "genericTaskState": {"phase": "inventory_full", "cycleStage": "needs_service", "activeIntent": "route_to_service"},
            "inventoryContext": {"freeSlots": 0, "occupiedSlots": 28, "inventoryFull": True},
            "goalProgress": {"heldResourceCount": 15, "resourceGroup": "logs"},
            "serviceRouteContext": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "currentNodeId": "lumbridge_castle_west_approach",
                "currentStepIndex": 2,
                "nextEdgeType": "walk_to",
            },
            "pathingContext": {
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3208, "worldY": 3220, "plane": 2},
                "nextWaypointTile": {"worldX": 3204, "worldY": 3224, "plane": 0},
                "wallLoopDetected": True,
                "rejectedApproachTileReasons": [{"reason": "wrong_side_of_wall"}],
            },
        },
        "playerLocation": {"worldX": 3203, "worldY": 3223, "plane": 0},
        "playerLocationSource": "plugin_snapshot_baseline_player",
        "inputIntegrityStatus": {
            "status": "PASS",
            "monitorPass": True,
            "injectionFlags": {"mouseInjectedCount": 0, "keyboardInjectedCount": 0},
            "backend": {"directBackendBypassCount": 0},
        },
    }
    return knowledge_fabric.KnowledgeFabric.from_status(status)


def loaded_liveness_status():
    return {
        "schema": "context_status.v1",
        "status": "ok",
        "latestTick": 99,
        "gameState": "LOGGED_IN",
        "clientTickHot": {
            "gameState": "LOGGED_IN",
            "sourceEvent": "PostMenuSort",
            "latency": {"ageMillis": 5, "postMenuSortAgeMillis": 5},
        },
    }


def loaded_world_summary_payloads(total=7619):
    return {
        "quality": {
            "worldModelAvailable": True,
            "worldModelAgeMs": 8,
            "sourceTick": 99,
            "collisionAvailable": True,
            "projectionAuditAvailable": True,
        },
        "world_model_summary": {
            "schema": "world_model_summary.v1",
            "tick": 99,
            "quality": {
                "worldModelAvailable": True,
                "worldModelAgeMs": 8,
                "sourceTick": 99,
                "collisionAvailable": True,
                "projectionAuditAvailable": True,
            },
            "objects": {"total": total},
        },
    }


def passing_action_readiness():
    return {
        "currentIntent": "resource_object_action",
        "proposedAction": "select_resource_target",
        "readinessPassed": True,
        "actionReadiness": {"status": "PASS", "executionAllowed": True, "blockers": [], "warnings": []},
    }


def executable_resource_proposal():
    return {
        "proposedAction": "select_resource_target",
        "executable": True,
        "actionTargetSource": "live_resource_candidate",
        "actionability": "needs_hover_confirmation",
        "targetExplanation": {
            "name": "Tree",
            "worldLocation": {"worldX": 3213, "worldY": 3238, "plane": 0},
            "safeAimPoint": {"status": "PASS", "actionable": True},
        },
    }


def test_indexes_build_from_synthetic_world_model():
    fabric = make_fabric()
    status = fabric.status()

    assert status["indexesBuilt"] is True
    assert status["liveWorldIndex"]["spatialIndexSummary"]["objectCount"] == 5
    assert status["liveWorldIndex"]["resourceIndexSummary"]["count"] == 2
    assert status["liveWorldIndex"]["serviceIndexSummary"]["count"] == 2
    assert status["liveWorldIndex"]["routeObjectIndexSummary"]["count"] == 1


def test_spatial_query_filters_by_radius_and_plane():
    fabric = make_fabric()

    result = fabric.query_objects_near({"worldX": 3201, "worldY": 3200, "plane": 0}, radius=2, filters={"plane": 0})

    names = [item["name"] for item in result["data"]["objects"]]
    assert "Tree" in names
    assert "Oak" in names
    assert "Bank booth" not in names


def test_action_query_finds_climb_bank_and_chop_actions():
    fabric = make_fabric()

    assert fabric.query_actions("Climb-up")["data"]["objects"][0]["name"] == "Staircase"
    assert fabric.query_actions("Bank")["data"]["objects"][0]["name"] == "Bank booth"
    chop_names = {item["name"] for item in fabric.query_actions("Chop")["data"]["objects"]}
    assert {"Tree", "Oak"} <= chop_names


def test_resource_query_demotes_oak_when_insufficient_level_and_prefers_tree():
    fabric = make_fabric()

    result = fabric.query_resource_candidates()
    objects = result["data"]["objects"]

    assert objects[0]["name"] == "Tree"
    oak = next(item for item in objects if item["name"] == "Oak")
    assert oak["executable"] is False
    assert oak["rejectionReason"] == "insufficient_woodcutting_level"
    assert result["data"]["oakRejectedInsufficientLevelCount"] == 1


def test_resource_query_keeps_no_action_stump_non_executable_even_with_static_enrichment():
    payloads = synthetic_payloads()
    payloads["resource_object_census"]["objects"].append(
        {
            "objectKey": "stump-1",
            "name": "Tree stump",
            "id": 1342,
            "actions": [],
            "worldX": 3201,
            "worldY": 3200,
            "plane": 0,
            "resourceCandidate": True,
            "resourceType": "basic_tree",
            "requiredSkill": "WOODCUTTING",
            "requiredLevel": 1,
            "levelRequirementMet": True,
            "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True, "edgeDistancePx": 90},
        }
    )
    fabric = knowledge_fabric.KnowledgeFabric.from_status(
        {"schema": "context_status.v1", "latestTick": 42, "worldModelPayloads": payloads}
    )

    result = fabric.query_resource_candidates()
    objects = result["data"]["objects"]
    stump = next(item for item in objects if item["name"] == "Tree stump")

    assert objects[0]["name"] == "Tree"
    assert stump["executable"] is False
    assert stump["actionability"] == "blocked_no_matching_action"
    assert "resource_stump_no_live_action" in stump["rejectionReason"]
    assert "external_knowledge_advisory_only" in stump["rejectionReason"]
    assert stump["externalKnowledge"]["externalKnowledgeAvailable"] is True


def test_service_query_finds_bank_booth_banker_or_deposit_box():
    fabric = make_fabric()

    result = fabric.query_service_candidates("bank")
    names = {item.get("name") for item in result["data"]["objects"]}

    assert "Bank booth" in names
    assert any("Deposit" in str(name) for name in names)


def test_route_query_finds_staircase_independent_of_resource_caps():
    fabric = make_fabric()

    result = fabric.query_route_objects()

    assert result["data"]["objects"][0]["name"] == "Staircase"
    assert result["data"]["objects"][0]["staticPriorExecutable"] is False


def test_path_frontier_query_returns_capped_structured_result():
    fabric = make_fabric()

    result = fabric.query_path_frontier(limit=1)

    assert result["schema"] == "knowledge_fabric_path_frontier.v1"
    assert len(result["data"]["frontier"]["frontier"]) == 1
    assert result["capHit"] is True


def test_path_frontier_diagnoses_stale_missing_player_location_reason():
    payloads = synthetic_payloads()
    payloads["pathing_frontier"] = {
        "schema": "pathing_frontier.v1",
        "frontier": {"status": "WARN", "reason": "player_location_unavailable", "candidates": []},
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(
        {
            "schema": "context_status.v1",
            "latestTick": 44,
            "worldModelPayloads": payloads,
            "playerLocation": {"worldX": 3206, "worldY": 3229, "plane": 1},
            "playerLocationSource": "plugin_snapshot_baseline_player",
            "brain": {
                "serviceRouteContext": {
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "currentNodeId": "lumbridge_first_floor_stairs",
                    "currentStepIndex": 4,
                },
                "pathingContext": {
                    "pathingNeeded": True,
                    "nextWaypointTile": {"worldX": 3206, "worldY": 3228, "plane": 1},
                    "pathTargetTile": {"worldX": 3205, "worldY": 3228, "plane": 1},
                    "destinationTile": {"worldX": 3204, "worldY": 3229, "plane": 1},
                    "predictedPathTiles": [
                        {"worldX": 3206, "worldY": 3228, "plane": 1},
                        {"worldX": 3205, "worldY": 3228, "plane": 1},
                    ],
                },
            },
        }
    )

    result = fabric.query_path_frontier()
    data = result["data"]
    diagnosis = data["frontierDiagnosis"]

    assert result["status"] == "WARN"
    assert "frontier_player_location_unavailable_but_daemon_location_present" in result["warnings"]
    assert data["playerLocation"]["worldLocation"] == {"worldX": 3206, "worldY": 3229, "plane": 1}
    assert diagnosis["staleFrontierPlayerLocation"] is True
    assert diagnosis["playerLocationAvailable"] is True
    assert diagnosis["routeContextHasPredictedPath"] is True
    assert diagnosis["routeContextCanGuideDiagnosis"] is True
    assert diagnosis["frontierUsableForNavigation"] is False
    assert diagnosis["noGlobalPathfindingAdded"] is True


def test_current_debug_context_includes_expected_sections():
    fabric = make_fabric()

    result = fabric.query_current_debug_context(limit=3)
    data = result["data"]

    assert result["schema"] == "knowledge_fabric_current_debug_context.v1"
    for key in (
        "liveStatus",
        "readiness",
        "worldModelSummary",
        "knowledgeFabricStatus",
        "currentBlocker",
        "actionProposal",
        "resourceCandidates",
        "routeObjects",
        "serviceObjects",
        "pathingFrontier",
        "viewQuality",
        "input_integrity_status",
        "inputIntegrityPhaseReport",
        "phaseAwareInputIntegrity",
        "sessionMemorySummary",
        "staticProfileSummary",
    ):
        assert key in data
    assert "get_current_debug_context" in data["queryFirstWorkflow"]


def test_current_debug_context_exposes_phase_aware_input_integrity():
    fabric = knowledge_fabric.KnowledgeFabric(
        world_model_payloads=synthetic_payloads(),
        daemon_status={
            "schema": "context_status.v1",
            "latestTick": 77,
            "clientTickHot": {"gameState": "LOGIN_SCREEN"},
            "inputIntegrityStatus": {
                "status": "FAIL",
                "monitorPass": False,
                "blockers": ["injected_input_detected"],
                "injectionFlags": {
                    "mouseInjectedCount": 5,
                    "keyboardInjectedCount": 0,
                    "mouseLowerIlInjectedCount": 0,
                    "keyboardLowerIlInjectedCount": 0,
                },
                "backend": {"directBackendBypassCount": 0, "liveInputBackend": "arduino"},
            },
        },
    )

    result = fabric.query_current_debug_context(limit=3)
    data = result["data"]
    input_status = data["input_integrity_status"]
    phase = input_status["phaseCounts"]
    assessment = data["phaseAwareInputIntegrity"]

    assert data["inputIntegrity"]["blockers"] == ["injected_input_detected"]
    assert phase["operator_phase"]["operatorInjectedEvents"] == 5
    assert phase["operator_phase"]["blocking"] is False
    assert phase["live_action_phase"]["injectedEventsDelta"] == 0
    assert phase["live_action_phase"]["lowerIlInjectedEventsDelta"] == 0
    assert phase["live_action_phase"]["directBackendBypassCountDelta"] == 0
    assert phase["live_action_phase"]["hardBlocker"] is False
    assert assessment["operatorInjectedEventsAreBlocking"] is False
    assert assessment["liveActionHardBlocker"] is False
    assert assessment["directBackendBypassCount"] == 0
    assert input_status["rawMonitorBlockersArePhaseQualified"] is True
    assert input_status["noLiveInput"] is True


def test_current_debug_context_reads_nested_context_status_shape():
    live_status = make_route_blocker_fabric().daemon_status
    raw_context_status = {
        "schema": "live_context_service_response.v1",
        "status": live_status,
        "worldModelPayloads": synthetic_payloads(),
        "warnings": [{"reason": "context_warning"}],
    }

    fabric = knowledge_fabric.KnowledgeFabric.from_status(raw_context_status)
    result = fabric.query_current_debug_context(limit=3)
    data = result["data"]

    assert data["liveStatus"]["status"] is None or isinstance(data["liveStatus"]["status"], str)
    assert data["liveStatus"]["phase"]["phase"] == "inventory_full"
    assert data["liveStatus"]["phase"]["cycleStage"] == "needs_service"
    assert data["liveStatus"]["location"]["worldLocation"]["worldX"] == 3203
    assert data["liveStatus"]["inventory"]["resourceCount"] == 15
    assert fabric.explain_current_blocker()["data"]["primaryBlockerCategory"] == "route/pathing"


def test_current_debug_context_liveness_uses_world_summary_total_without_census_objects():
    fabric = knowledge_fabric.KnowledgeFabric(
        world_model_payloads=loaded_world_summary_payloads(total=7619),
        daemon_status=loaded_liveness_status(),
        source="live_8890_8893",
    )

    with patch.object(fabric, "_readiness_report", wraps=fabric._readiness_report), patch.object(
        fabric, "_action_proposal", return_value=executable_resource_proposal()
    ):
        result = fabric.query_current_debug_context(limit=3)

    data = result["data"]
    assert data["loadedSceneVerified"] is True
    assert data["livenessState"] == "loaded_scene"
    assert data["loadedSceneProof"]["worldModelObjectTotal"] == 7619
    assert data["readiness"]["loadedSceneProof"]["worldModelObjectTotal"] == 7619
    assert data["readiness"]["loadedSceneProof"]["loadedSceneVerified"] is True


def test_fabric_reads_saved_context_response_world_model_keys():
    raw_context_response = {
        "schema": "context_response.v1",
        "status": "PASS",
        "worldModelSummary": synthetic_payloads()["world_model_summary"],
        "resourceObjectCensus": synthetic_payloads()["resource_object_census"],
        "serviceObjectCensus": synthetic_payloads()["service_object_census"],
        "routeObjectCensus": synthetic_payloads()["route_object_census"],
        "pathingFrontier": synthetic_payloads()["pathing_frontier"],
        "projectionAudit": synthetic_payloads()["projection_audit"],
    }

    fabric = knowledge_fabric.KnowledgeFabric.from_status(raw_context_response)

    assert fabric.status()["performanceStats"]["objectCount"] == 5
    assert fabric.query_resource_candidates()["data"]["count"] == 2


def test_current_blocker_ignores_generic_candidate_warning_when_readiness_passes():
    status = loaded_liveness_status()
    status["warnings"] = [
        "plugin snapshot currently builds live candidates from loaded-scene projection data",
        "projection refs capped; increase maxProjectionRefs if candidate refs are missing",
    ]
    fabric = knowledge_fabric.KnowledgeFabric(
        world_model_payloads=loaded_world_summary_payloads(total=7619),
        daemon_status=status,
        source="live_8890_8893",
    )

    with patch.object(fabric, "_readiness_report", return_value=passing_action_readiness()), patch.object(
        fabric, "_action_proposal", return_value=executable_resource_proposal()
    ):
        result = fabric.explain_current_blocker()

    data = result["data"]
    assert data["primaryBlockerCategory"] == "ready"
    assert data["safeToRunBoundedLiveAction"] is True
    assert data["evidence"]["bootstrapState"]["loadedSceneVerified"] is True


def test_fabric_from_live_uses_minimal_liveness_fallback_when_broad_fetch_times_out():
    minimal_snapshot = {
        "schema": "world_model_query_response.v1",
        "status": "PASS",
        "latestTick": 99,
        "clientTickHot": loaded_liveness_status()["clientTickHot"],
        "payloads": {
            "baseline": {"tick": 99, "gameState": "LOGGED_IN"},
            "world_model_summary": loaded_world_summary_payloads(total=7619)["world_model_summary"],
        },
        "worldModelQuality": loaded_world_summary_payloads(total=7619)["quality"],
    }

    with patch("knowledge_fabric.fetch_json", return_value=loaded_liveness_status()), patch(
        "knowledge_fabric.world_model_client.fetch",
        side_effect=[TimeoutError("timed out"), minimal_snapshot],
    ) as fetch_mock:
        fabric = knowledge_fabric.fabric_from_live(
            daemon_url="http://daemon.test:8890",
            snapshot_url="http://snapshot.test:8893/snapshot",
            timeout=1.0,
        )

    assert fetch_mock.call_count == 2
    assert fabric.world_model_payloads["world_model_summary"]["objects"]["total"] == 7619
    assert fabric.daemon_status["worldModelObjectTotal"] == 7619
    assert fabric.daemon_status["broadFetchTimedOut"] is True
    assert fabric.daemon_status["minimalLiveLivenessFallbackUsed"] is True
    assert fabric.daemon_status["worldModelSummarySource"] == "minimal_live_liveness_fallback"
    assert fabric.daemon_status["clientTickHot"]["gameState"] == "LOGGED_IN"
    assert fabric.daemon_status["clientTickHotSource"] == "minimal_live_liveness_fallback"
    assert fabric.daemon_status["gameState"] == "LOGGED_IN"


def test_seen_inventory_items_parse_snapshot_inventory_and_external_names():
    status = {
        "schema": "context_status.v1",
        "latestTick": 44,
        "worldModelPayloads": {
            **synthetic_payloads(),
            "inventory": {
                "inventory": {
                    "items": [
                        {"slot": 0, "itemId": 1511, "quantity": 1},
                        {"slot": 1, "itemId": 995, "quantity": 42},
                    ]
                }
            },
        },
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(status)

    def fake_lookup(item_id):
        names = {1511: "Logs", 995: "Coins"}
        return {
            "status": "PASS",
            "data": {
                "item": {
                    "itemId": item_id,
                    "name": names.get(int(item_id)),
                    "provenance": {"source": "test_cache"},
                }
            },
        }

    with patch("external_knowledge.lookup_item_id", side_effect=fake_lookup):
        result = fabric.list_seen_inventory_items(limit=10)

    items = result["data"]["items"]
    assert items[0]["slot"] == 0
    assert items[0]["id"] == 1511
    assert items[0]["name"] == "Logs"
    assert items[1]["name"] == "Coins"


def test_explain_current_blocker_returns_structured_route_category():
    fabric = make_route_blocker_fabric()

    result = fabric.explain_current_blocker()
    data = result["data"]

    assert result["schema"] == "knowledge_fabric_current_blocker_explanation.v1"
    assert data["primaryBlockerCategory"] == "route/pathing"
    assert data["inventory"]["inventoryFull"] is True
    assert data["evidence"]["routeContext"]["currentNodeId"] == "lumbridge_castle_west_approach"
    assert data["evidence"]["pathingFrontier"]["wallHuggingRisk"]["status"] == "WARN"


def test_projection_cap_warning_does_not_override_allowed_route_action():
    status = {
        "schema": "context_status.v1",
        "latestTick": 44,
        "gameState": "LOGGED_IN",
        "warnings": ["projection refs capped"],
        "worldModelPayloads": synthetic_payloads(),
        "brain": {
            "genericTaskState": {"phase": "inventory_full", "cycleStage": "pathing_to_service", "activeIntent": "needs_service"},
            "inventoryContext": {"freeSlots": 0, "occupiedSlots": 28, "inventoryFull": True},
            "goalProgress": {"heldResourceCount": 15, "resourceGroup": "logs"},
            "serviceRouteContext": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "currentNodeId": "lumbridge_castle_entrance_or_courtyard",
                "currentStepIndex": 1,
                "nextEdgeType": "goal_directed_walk_to",
            },
            "pathingContext": {
                "pathingNeeded": True,
                "destinationTile": {"worldX": 3205, "worldY": 3232, "plane": 0},
                "nextWaypointTile": {"worldX": 3203, "worldY": 3221, "plane": 0},
            },
        },
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(status)
    readiness = {
        "currentIntent": "navigation_waypoint_action",
        "actionReadiness": {"status": "PASS", "executionAllowed": True, "blockers": [], "warnings": []},
    }
    proposal = {"actionTargetSource": "local_frontier_waypoint", "actionability": "needs_live_projection", "reason": "pathing_to_service"}

    with patch.object(fabric, "_readiness_report", return_value=readiness), patch.object(fabric, "_action_proposal", return_value=proposal):
        result = fabric.explain_current_blocker()

    data = result["data"]
    assert data["primaryBlockerCategory"] == "route/pathing"
    assert data["safeToRunBoundedLiveAction"] is True
    assert data["codeChangeLikelyNeeded"] is False


def test_client_tick_hot_blocker_is_freshness_not_hover_menu():
    fabric = make_fabric()
    readiness = {
        "currentIntent": "navigation_waypoint_action",
        "actionReadiness": {
            "status": "FAIL",
            "executionAllowed": False,
            "blockers": [
                {
                    "code": "client_tick_hot_unavailable",
                    "message": "client-tick hot interaction state is unavailable; wait for RuneLite client ticks/PostMenuSort",
                }
            ],
            "warnings": [],
        },
    }

    with patch.object(fabric, "_readiness_report", return_value=readiness), patch.object(fabric, "_action_proposal", return_value={}):
        result = fabric.explain_current_blocker()

    assert result["data"]["primaryBlockerCategory"] == "plugin/daemon freshness"
    assert result["data"]["safeToRunBoundedLiveAction"] is False


def test_stale_route_context_is_warning_only_during_resource_collection():
    status = {
        "schema": "context_status.v1",
        "latestTick": 45,
        "gameState": "LOGGED_IN",
        "warnings": ["route anchor missing from previous service route"],
        "worldModelPayloads": synthetic_payloads(),
        "brain": {
            "genericTaskState": {
                "phase": "target_selected",
                "cycleStage": "collecting_resources",
                "activeIntent": "select_target",
            },
            "inventoryContext": {"freeSlots": 14, "occupiedSlots": 14, "inventoryFull": False},
            "goalProgress": {"heldResourceCount": 2, "resourceGroup": "logs"},
            "serviceRouteContext": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "currentNodeId": "lumbridge_castle_west_approach",
                "currentStepIndex": 0,
                "nextEdgeType": "walk_to",
                "routeStepStatus": "route_anchor_missing",
            },
        },
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(status)
    readiness = {
        "currentIntent": "resource_object_action",
        "proposedAction": "select_resource_target",
        "actionNeed": {
            "cycleStage": "collecting_resources",
            "phase": "target_selected",
            "activeIntent": "select_target",
            "inventoryFreeSlots": 14,
            "needsService": False,
        },
        "actionReadiness": {"status": "PASS", "executionAllowed": True, "blockers": [], "warnings": []},
        "readinessPassed": True,
    }
    proposal = {
        "proposedAction": "select_resource_target",
        "executable": True,
        "actionTargetSource": "live_resource_candidate",
        "actionability": "needs_hover_confirmation",
        "targetExplanation": {
            "name": "Tree",
            "worldLocation": {"worldX": 3213, "worldY": 3238, "plane": 0},
            "safeAimPoint": {"status": "PASS", "actionable": True},
        },
    }

    with patch.object(fabric, "_readiness_report", return_value=readiness), patch.object(fabric, "_action_proposal", return_value=proposal):
        result = fabric.explain_current_blocker()

    data = result["data"]
    assert data["primaryBlockerCategory"] == "ready"
    assert data["safeToRunBoundedLiveAction"] is True
    assert data["routeContextPresent"] is True
    assert data["routeContextApplicable"] is False
    assert data["routeContextWarningOnly"] is True
    assert data["staleRouteContextSuppressed"] is True
    assert data["routeContextApplicabilityReason"] == "collecting_resources_resource_target_ready"


def test_current_blocker_safe_to_run_follows_action_readiness_failure():
    fabric = make_fabric()
    readiness = {
        "currentIntent": "resource_object_action",
        "proposedAction": "select_resource_target",
        "actionReadiness": {
            "status": "FAIL",
            "executionAllowed": False,
            "blockers": [{"code": "selected_target_not_actionable", "message": "safe aim point missing"}],
            "warnings": [],
        },
    }
    proposal = {
        "proposedAction": "select_resource_target",
        "executable": True,
        "actionTargetSource": "live_resource_candidate",
        "targetExplanation": {"name": "Tree", "safeAimPoint": {"status": "PASS"}},
    }

    with patch.object(fabric, "_readiness_report", return_value=readiness), patch.object(fabric, "_action_proposal", return_value=proposal):
        result = fabric.explain_current_blocker()

    assert result["data"]["primaryBlockerCategory"] == "readiness"
    assert result["data"]["safeToRunBoundedLiveAction"] is False
    assert "selected_target_not_actionable" in " ".join(result["data"]["blockers"])


def test_pathing_blocker_query_includes_route_context_and_frontier():
    fabric = make_route_blocker_fabric()

    result = fabric.query_path_frontier(limit=1)
    data = result["data"]

    assert data["routeContext"]["currentNodeId"] == "lumbridge_castle_west_approach"
    assert data["statusPathing"]["rejectedApproachTileReasons"] == [{"reason": "wrong_side_of_wall"}]
    assert len(data["frontier"]["frontier"]) == 1


def test_view_quality_query_includes_camera_recommendation_fields():
    fabric = make_fabric()

    result = fabric.query_view_quality(intent="route_to_service")
    data = result["data"]

    assert "camera" in data
    assert "cameraRecommendation" in data
    assert "routeObjectVisibility" in data
    assert "serviceObjectVisibility" in data
    assert "resourceCandidateSummary" in data


def test_view_quality_reports_service_camera_recovery_when_service_object_offscreen():
    payloads = synthetic_payloads()
    payloads["service_object_census"]["objects"][0]["projection"] = {
        "visible": False,
        "onScreen": False,
        "geometryAvailable": True,
        "actionableByCanvas": False,
        "classification": "offscreen",
    }
    payloads["service_object_census"]["objects"][1]["projection"] = {
        "visible": False,
        "onScreen": False,
        "geometryAvailable": True,
        "actionableByCanvas": False,
        "classification": "offscreen",
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(
        {
            "schema": "context_status.v1",
            "latestTick": 42,
            "worldModelPayloads": payloads,
        }
    )

    result = fabric.query_view_quality(intent="open_service")
    data = result["data"]

    assert data["serviceViewClassification"] == "service_object_loaded_offscreen"
    assert data["recommendedServiceCameraAction"] == "camera_reacquire_service_target"
    assert data["serviceViewRecoveryAvailable"] is True
    assert data["serviceObjectVisibility"]["offscreenCount"] == 2
    assert data["targetKind"] == "service_object"
    assert data["targetViewClassification"] == "target_loaded_offscreen"
    assert data["recommendedCameraAction"] == "camera_reacquire_target"
    assert data["targetViewPolicy"]["schema"] == "target_view_policy.v1"


def test_view_quality_reports_service_camera_recovery_for_edge_sliver():
    payloads = synthetic_payloads()
    payloads["service_object_census"]["objects"][0]["projection"] = {
        "visible": True,
        "onScreen": True,
        "geometryAvailable": True,
        "actionableByCanvas": True,
        "classification": "actionable",
        "aimPoint": {"canvasX": 6, "canvasY": 93},
    }
    payloads["service_object_census"]["objects"][0]["safeAimPoint"] = {
        "status": "PASS",
        "actionable": True,
        "canvasX": 6,
        "canvasY": 93,
        "distanceToViewportEdgePx": 6,
        "clippedVisibleAreaPx": 756.0,
        "clippedVisibleAreaRatio": 0.75,
    }
    payloads["service_object_census"]["objects"][1]["projection"] = {
        "visible": False,
        "onScreen": False,
        "geometryAvailable": True,
        "actionableByCanvas": False,
        "classification": "offscreen",
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(
        {
            "schema": "context_status.v1",
            "latestTick": 42,
            "worldModelPayloads": payloads,
        }
    )

    result = fabric.query_view_quality(intent="open_service")
    data = result["data"]

    assert data["serviceViewClassification"] == "service_object_edge_sliver"
    assert data["recommendedServiceCameraAction"] == "camera_reacquire_service_target"
    selected = data["serviceObjectVisibility"]["selectedServiceObject"]
    assert selected["serviceTargetExposure"]["usableExposureThresholdMet"] is False
    assert selected["serviceTargetExposure"]["edgeSliverVisible"] is True
    assert data["targetViewClassification"] == "target_edge_sliver"
    assert data["targetVisibility"]["edgeSliver"] is True


def test_script_authoring_helper_queries_return_compact_data():
    fabric = make_fabric()

    assert fabric.list_available_profiles()["schema"] == "knowledge_fabric_profiles.v1"
    assert fabric.list_target_classes("woodcutting")["schema"] == "knowledge_fabric_target_classes.v1"
    assert fabric.list_known_actions("Tree")["schema"] == "knowledge_fabric_known_actions.v1"
    assert fabric.list_service_routes("woodcutting")["schema"] == "knowledge_fabric_service_routes.v1"
    assert fabric.explain_required_telemetry_for_task("woodcutting bank")["data"]["requiredTelemetry"]
    scene = fabric.query_scene_for_new_task_keywords("Tree")
    assert any(item["name"] == "Tree" for item in scene["data"]["objects"])
    skeleton = fabric.suggest_profile_skeleton_from_scene(description="Tree task", keywords="Tree")
    assert skeleton["data"]["profileSkeleton"]["candidateNameHints"]
    assert fabric.list_seen_objects_by_action("Chop")["data"]["count"] >= 2
    assert fabric.list_seen_objects_by_name("Bank")["data"]["count"] >= 1
    assert fabric.export_task_context_bundle(profile="woodcutting", limit=3)["schema"] == "knowledge_fabric_task_context_bundle.v1"
    assert fabric.list_seen_widgets(limit=3)["schema"] == "knowledge_fabric_seen_widgets.v1"
    assert fabric.list_seen_inventory_items(limit=3)["schema"] == "knowledge_fabric_seen_inventory_items.v1"
    assert fabric.list_seen_npcs(limit=3)["schema"] == "knowledge_fabric_seen_npcs.v1"
    assert fabric.list_seen_ground_items(limit=3)["schema"] == "knowledge_fabric_seen_ground_items.v1"


def test_data_source_inventory_and_query_coverage_include_expected_sources():
    fabric = make_fabric()

    inventory = fabric.data_source_inventory()
    coverage = fabric.query_coverage_matrix()

    assert inventory["schema"] == "data_source_inventory.v1"
    source_names = [item["sourceName"] for item in inventory["data"]["sources"]]
    assert "8893 PluginSnapshotEndpoint" in source_names
    assert "external OSRS knowledge cache" in source_names
    assert "action_input_visibility_context" in source_names
    assert inventory["data"]["livePacketArchiveRemoved"] is True
    assert coverage["schema"] == "query_coverage_matrix.v1"
    assert any("item/object/NPC ID" in row["question"] for row in coverage["data"]["rows"])
    assert any("planned click/input" in row["question"] for row in coverage["data"]["rows"])


def test_external_source_registry_cache_and_lookup_behaviour():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "external"
        status = external_knowledge.knowledge_status(root=root)
        item = external_knowledge.lookup_item_id(1511, root=root)
        search = external_knowledge.search_item("logs", root=root)
        unknown = external_knowledge.lookup_item_id(999999, root=root)
        oak = external_knowledge.get_skill_requirement("Oak", root=root)
        area = external_knowledge.lookup_area("Lumbridge Castle bank", root=root)

        assert status["data"]["externalApiEnabledByDefault"] is False
        assert status["data"]["hotRuntimeExternalApiCallsAllowed"] is False
        assert status["data"]["userAgentRequired"] is True
        assert item["data"]["item"]["canonicalName"] == "Logs"
        assert search["data"]["items"]
        assert unknown["status"] == "WARN"
        assert unknown["data"]["cacheMisses"]
        assert oak["data"]["requirement"]["requiredLevel"] == 15
        assert area["data"]["area"]["advisoryOnly"] is True
        assert external_knowledge_cache.cache_status(root)["cacheSizeMb"] >= 0


def test_external_wiki_search_is_cache_first_unless_refresh_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "external"
        result = external_knowledge.search_wiki("Lumbridge Castle bank", root=root, allow_refresh=False)

        assert result["status"] == "WARN"
        assert result["data"]["externalApiCalled"] is False
        assert "external_refresh_not_allowed" in result["warnings"]


def test_external_facts_enrich_resource_candidates_without_overriding_live_names():
    fabric = make_fabric()

    result = fabric.query_resource_candidates(profile="woodcutting", limit=5)
    oak = next(item for item in result["data"]["objects"] if item["name"] == "Oak")

    assert oak["name"] == "Oak"
    assert oak["externalKnowledge"]["requiredLevel"] == 15
    assert oak["visibleButNotExecutable"] is True


def test_task_probe_uses_external_requirements_and_suggests_profile():
    fabric = make_fabric()

    result = fabric.probe_task("woodcutting and bank logs", limit=5)
    data = result["data"]

    assert result["schema"] == "task_probe_report.v1"
    assert data["noLiveInput"] is True
    assert data["suggestedNewProfile"]["schema"] == "target_profile_skeleton.v1"
    assert data["candidateActions"]
    assert any(req.get("requiredSkill") == "WOODCUTTING" for req in data["requirements"])


def test_coverage_report_distinguishes_missing_data_from_nonblocking_context():
    fabric = make_fabric()

    result = fabric.coverage_report(intent="route_to_service", limit=2)

    assert result["schema"] == "coverage_report.v1"
    assert "collisionAvailable" in result["data"]["presentData"]
    assert result["data"]["recommendedNextQuery"] == "query_path_frontier"


def test_data_quality_derives_client_tick_freshness_from_latency():
    status = {
        "schema": "context_status.v1",
        "latestTick": 45,
        "worldModelPayloads": synthetic_payloads(),
        "clientTickHot": {
            "gameState": "LOGGED_IN",
            "latency": {"ageMillis": 25, "postMenuSortAgeMillis": 25},
        },
    }
    fabric = knowledge_fabric.KnowledgeFabric.from_status(status)

    quality = fabric.data_quality_report()
    coverage = fabric.coverage_report(intent="bank_service")

    assert quality["data"]["clientTickFresh"] is True
    assert coverage["data"]["presentData"]["clientTickFresh"] is True


def test_external_queries_and_authoring_paths_do_not_create_live_packets():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session"
        session.mkdir()
        fabric = knowledge_fabric.KnowledgeFabric.from_status(
            {
                "schema": "context_status.v1",
                "sessionPath": str(session),
                "latestTick": 42,
                "worldModelPayloads": synthetic_payloads(),
            }
        )

        fabric.external_lookup_item_id(1511)
        fabric.probe_task("woodcutting and bank logs", limit=3)
        fabric.capture_script_authoring_context(profile="woodcutting", task_name="no_packet_probe", output_root=Path(tmp) / "artifacts")
        fabric.capture_replay_scenario(profile="woodcutting", reason="no_packet_probe", output_root=Path(tmp) / "artifacts")

        assert not (session / "live_packets").exists()
        assert not list(session.rglob("*.ndjson"))
        assert not list(session.rglob("*.jsonl"))


def test_data_quality_report_detects_missing_and_capped_data():
    fabric = make_fabric()

    result = fabric.data_quality_report(limit=2)
    data = result["data"]

    assert result["schema"] == "data_quality_report.v1"
    assert data["worldModelFresh"] is True
    assert data["objectCount"] == 5
    assert "scene_object_census" in data["missingExpectedSections"]
    assert data["capHits"]
    assert data["confidence"] == "medium"


def test_script_authoring_context_contains_expected_sections():
    with tempfile.TemporaryDirectory() as tmp:
        fabric = make_route_blocker_fabric()

        result = fabric.capture_script_authoring_context(
            profile="woodcutting",
            task_name="woodcut_bank",
            reason="route_wall_hugging",
            limit=3,
            output_root=Path(tmp),
        )

        manifest = result["data"]["manifest"]
        bundle_dir = Path(result["data"]["bundlePath"])
        assert result["schema"] == "script_authoring_context_capture.v1"
        assert manifest["schema"] == "script_authoring_context.v1"
        assert manifest["blockerCategory"] == "route/pathing"
        assert "current_debug_context.json" in manifest["files"]
        assert "data_quality_report.json" in manifest["files"]
        assert manifest["queryTimes"]["current_debug_context.json"] >= 0
        assert (bundle_dir / "manifest.json").exists()
        assert (bundle_dir / "pathing_frontier.json").exists()


def test_replay_scenario_can_replay_blocker_offline():
    with tempfile.TemporaryDirectory() as tmp:
        fabric = make_route_blocker_fabric()
        capture = fabric.capture_replay_scenario(profile="woodcutting", reason="route_wall_hugging", limit=3, output_root=Path(tmp))
        scenario_path = capture["data"]["scenarioPath"]

        replay = knowledge_fabric.replay_scenario(scenario_path, limit=3)
        data = replay["data"]

        assert replay["schema"] == "replay_scenario_result.v1"
        assert data["noLiveInput"] is True
        assert data["storedBlockerCategory"] == "route/pathing"
        assert data["replayedBlockerCategory"] == "route/pathing"
        assert data["replayMatchesStoredBlocker"] is True


def test_diff_debug_context_reports_route_and_blocker_changes():
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "first"
        second = Path(tmp) / "second"
        first.mkdir()
        second.mkdir()
        (first / "current_debug_context.json").write_text(
            json.dumps(make_fabric().query_current_debug_context(limit=3)),
            encoding="utf-8",
        )
        (second / "current_debug_context.json").write_text(
            json.dumps(make_route_blocker_fabric().query_current_debug_context(limit=3)),
            encoding="utf-8",
        )

        diff = knowledge_fabric.diff_debug_context(first, second)

    assert diff["schema"] == "debug_context_diff.v1"
    assert diff["data"]["differenceCount"] > 0
    assert "routeNode" in diff["data"]["differences"]


def test_handoff_summary_includes_blocker_bundle_and_next_step():
    fabric = make_route_blocker_fabric()

    result = fabric.handoff_summary()
    data = result["data"]

    assert result["schema"] == "knowledge_fabric_handoff_summary.v1"
    assert data["currentBlocker"]["category"] == "route/pathing"
    assert data["recommendedNextDiagnosticQuery"] == "query_path_frontier"
    assert data["testsIfCodeChanges"]


def test_static_route_prior_remains_advisory_until_live_verified():
    result = knowledge_fabric.query_static_library(search="lumbridge_castle_bank", limit=10)

    anchors = [item for item in result["data"]["items"] if item.get("_libraryKind") == "serviceAnchors"]
    assert anchors
    assert all(anchor.get("advisoryOnly") is True for anchor in anchors)


def test_session_memory_stores_observed_anchor_and_rejects_stale_session_for_execution():
    with tempfile.TemporaryDirectory() as tmp:
        memory = knowledge_fabric.record_session_observation(
            tmp,
            "service_anchor",
            {"name": "Bank booth", "worldLocation": {"worldX": 3208, "worldY": 3220, "plane": 2}},
            source_tick=12,
        )

        assert memory["observedServiceAnchors"][0]["name"] == "Bank booth"
        assert knowledge_fabric.session_memory_is_current(memory, tmp) is True
        assert knowledge_fabric.session_memory_is_current(memory, str(Path(tmp) / "other")) is False
        fabric = knowledge_fabric.KnowledgeFabric(world_model_payloads=synthetic_payloads(), daemon_status={}, session_path=tmp)
        session_query = fabric.query_session_memory(kind="service")
        assert session_query["data"]["canUseForExecution"] is False


def test_mcp_server_lists_tools_resources_and_searches_static_library():
    tools = mcp_server.tool_definitions()
    resources = mcp_server.resource_definitions()

    assert any(tool["name"] == "query_resource_candidates" for tool in tools)
    assert any(tool["name"] == "get_current_debug_context" for tool in tools)
    assert any(tool["name"] == "get_action_input_visibility" for tool in tools)
    assert any(tool["name"] == "capture_script_authoring_context" for tool in tools)
    assert any(tool["name"] == "get_data_quality_report" for tool in tools)
    assert any(tool["name"] == "get_data_source_inventory" for tool in tools)
    assert any(tool["name"] == "probe_task" for tool in tools)
    assert any(tool["name"] == "external_lookup_item_id" for tool in tools)
    assert any(tool["name"] == "get_handoff_summary" for tool in tools)
    assert not any(tool["name"].startswith(("execute_", "click_", "input_")) for tool in tools)
    forbidden_raw_names = {"mouseDown", "mouseUp", "keyDown", "keyUp", "click", "raw_click", "raw_mouse_down"}
    assert forbidden_raw_names.isdisjoint({tool["name"] for tool in tools})
    assert any(resource["uri"] == "osrs://library/routes" for resource in resources)
    assert any(resource["uri"] == "osrs://live/current-debug-context" for resource in resources)
    assert any(resource["uri"] == "osrs://debug/action-input-visibility" for resource in resources)
    assert any(resource["uri"] == "osrs://debug/navigation-decision-trace" for resource in resources)
    assert any(resource["uri"] == "osrs://debug/data-quality-report" for resource in resources)
    assert any(resource["uri"] == "osrs://library/data-sources" for resource in resources)
    assert any(resource["uri"] == "osrs://external/source-status" for resource in resources)

    result = mcp_server.call_tool("search_static_library", {"search": "Oak", "limit": 5})
    payload = json.loads(result["content"][0]["text"])
    assert payload["schema"] == "knowledge_fabric_static_library_query.v1"
    assert payload["data"]["count"] >= 1

    item_result = mcp_server.call_tool("external_lookup_item_id", {"itemId": 1511})
    item_payload = json.loads(item_result["content"][0]["text"])
    assert item_payload["schema"] == "external_item_lookup.v1"
    assert item_payload["data"]["item"]["canonicalName"] == "Logs"


def test_mcp_current_debug_context_matches_direct_shape():
    fabric = make_fabric()
    direct = fabric.query_current_debug_context(limit=3)

    with patch.object(mcp_server, "_fabric", return_value=fabric):
        result = mcp_server.call_tool("get_current_debug_context", {"limit": 3})
    payload = json.loads(result["content"][0]["text"])

    assert payload["schema"] == direct["schema"]
    assert set(direct["data"]).issubset(set(payload["data"]))


def test_mcp_new_tools_return_structured_compact_json():
    fabric = make_route_blocker_fabric()

    with patch.object(mcp_server, "_fabric", return_value=fabric):
        quality_result = mcp_server.call_tool("get_data_quality_report", {"limit": 3})
        handoff_result = mcp_server.call_tool("get_handoff_summary", {})
        coverage_result = mcp_server.call_tool("get_coverage_report", {"intent": "route_to_service", "limit": 3})
        probe_result = mcp_server.call_tool("probe_task", {"taskDescription": "woodcutting and bank logs", "limit": 3})
        visibility_result = mcp_server.call_tool("get_action_input_visibility", {})
        navigation_result = mcp_server.call_tool(
            "query_navigation_decision_trace",
            {
                "records": [
                    {
                        "schema": "navigation_decision_trace.v1",
                        "decision": "wait",
                        "reason": "navigation_in_progress",
                    }
                ]
            },
        )
    quality = json.loads(quality_result["content"][0]["text"])
    handoff = json.loads(handoff_result["content"][0]["text"])
    coverage = json.loads(coverage_result["content"][0]["text"])
    probe = json.loads(probe_result["content"][0]["text"])
    visibility = json.loads(visibility_result["content"][0]["text"])
    navigation = json.loads(navigation_result["content"][0]["text"])

    assert quality["schema"] == "data_quality_report.v1"
    assert handoff["schema"] == "knowledge_fabric_handoff_summary.v1"
    assert coverage["schema"] == "coverage_report.v1"
    assert probe["schema"] == "task_probe_report.v1"
    assert visibility["schema"] == "action_input_visibility_context.v1"
    assert navigation["schema"] == "navigation_decision_trace_summary.v1"
    assert navigation["data"]["decisionCounts"] == {"wait": 1}
    assert handoff["data"]["currentBlocker"]["category"] == "route/pathing"


def test_navigation_decision_trace_summary_exposes_suspicious_route_decision():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session"
        live_dir = session / "interaction_geometry" / "live"
        live_dir.mkdir(parents=True)
        trace = {
            "actionTraceSchema": "action_trace.v2",
            "proposedAction": "navigate_to_service",
            "finalClassification": "navigation_no_progress",
            "navigationDecisionTrace": [
                {
                    "schema": "navigation_decision_trace.v1",
                    "tick": 100,
                    "decision": "wait",
                    "reason": "navigation_in_progress",
                    "routeStep": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentNodeId": "courtyard",
                        "currentStepIndex": 3,
                        "routeStepStatus": "moving_to_staircase",
                    },
                    "distances": {"currentDistanceToGoal": 8, "distanceDelta": -2, "distanceImproving": True},
                    "pending": {"nextActionAllowed": False, "observedResult": "movement_pending"},
                    "chosenSubgoal": {"targetName": "Staircase", "targetTile": {"worldX": 3204, "worldY": 3229, "plane": 1}, "executable": True},
                },
                {
                    "schema": "navigation_decision_trace.v1",
                    "tick": 101,
                    "decision": "click",
                    "reason": "daemon_latest_tick_missing",
                    "routeStep": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentNodeId": "courtyard",
                        "currentStepIndex": 3,
                        "routeStepStatus": "stale_state",
                    },
                    "distances": {"currentDistanceToGoal": 8, "distanceDelta": 0, "distanceImproving": False},
                    "pending": {"nextActionAllowed": True, "observedResult": "stale_status"},
                    "chosenSubgoal": {"targetName": "Staircase", "targetTile": {"worldX": 3204, "worldY": 3229, "plane": 1}, "executable": True},
                },
            ],
        }
        (live_dir / "last_action_trace.json").write_text(json.dumps(trace), encoding="utf-8")
        fabric = knowledge_fabric.KnowledgeFabric.from_status(
            {
                "schema": "context_status.v1",
                "sessionPath": str(session),
                "latestTick": 101,
                "worldModelPayloads": synthetic_payloads(),
                "brain": {
                    "serviceRouteContext": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentNodeId": "courtyard",
                        "currentStepIndex": 3,
                    }
                },
            }
        )

        summary = fabric.query_navigation_decision_trace(limit=5)
        current = fabric.query_current_debug_context(limit=3)
        visibility = fabric.query_action_input_visibility()
        with patch.object(mcp_server, "_fabric", return_value=fabric):
            mcp_payload = json.loads(mcp_server.call_tool("query_navigation_decision_trace", {"limit": 5})["content"][0]["text"])
            resource_payload = json.loads(mcp_server.read_resource("osrs://debug/navigation-decision-trace")["contents"][0]["text"])

    data = summary["data"]
    assert summary["schema"] == "navigation_decision_trace_summary.v1"
    assert summary["status"] == "WARN"
    assert data["tracePresent"] is True
    assert data["decisionCounts"] == {"click": 1, "wait": 1}
    assert data["firstSuspiciousDecision"]["issue"] == "stale_state_allowed_click"
    assert data["contextRows"][1]["reason"] == "daemon_latest_tick_missing"
    assert data["latestDecision"]["decision"] == "click"
    assert data["noLiveInput"] is True
    assert current["data"]["navigationDecisionTrace"]["schema"] == "navigation_decision_trace_summary.v1"
    assert visibility["data"]["navigationDecisionTrace"]["schema"] == "navigation_decision_trace_summary.v1"
    assert mcp_payload["data"]["firstSuspiciousDecision"]["issue"] == "stale_state_allowed_click"
    assert resource_payload["data"]["decisionCounts"] == {"click": 1, "wait": 1}


def test_action_input_visibility_exposes_trace_input_and_phase_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session"
        live_dir = session / "interaction_geometry" / "live"
        live_dir.mkdir(parents=True)
        trace = {
            "actionTraceSchema": "action_trace.v2",
            "selectedTarget": {
                "name": "Tree",
                "classId": "tree",
                "targetType": "sceneObject",
                "worldX": 3213,
                "worldY": 3238,
                "plane": 0,
                "actionTargetSource": "live_resource_candidate",
                "actionability": "needs_hover_confirmation",
                "targetViewState": {"viewQualityClassification": "target_actionable"},
                "hoverConfirmedTopExpected": True,
            },
            "proposedAction": "select_resource_target",
            "actionIntentType": "resource_object_action",
            "intendedPoint": {
                "canvas": {"x": 123, "y": 234},
                "screen": {"x": 1123, "y": 734},
                "coordinateSpace": "canvas",
                "displayScaleApplied": True,
                "displayScaleReason": "snapshot_display_scale",
                "clickPointResolution": {
                    "screenPointAfterScaling": {"x": 1123, "y": 734},
                    "movementSafetyPreflight": {"status": "PASS", "source": "pointer_calibration_record", "allowedWindow": "runelite"},
                },
            },
            "mouseMove": {
                "startScreenPoint": {"x": 900, "y": 700},
                "plannedEndScreenPoint": {"x": 1123, "y": 734},
                "endScreenPoint": {"x": 1122, "y": 735},
            },
            "clientTick": {
                "acceptedHoverSample": {"topOption": "Chop down", "topTarget": "Tree"},
                "rejectedHoverSamples": [],
                "hoverConfirmationSamples": [{"topOption": "Chop down", "topTarget": "Tree"}],
                "lastMenuOptionClickedAfter": {"option": "Chop down", "target": "Tree", "sourceEvent": "MenuOptionClicked"},
                "clickedMenuClassification": "clicked_expected_action",
            },
            "humanInput": {
                "profile": "steady",
                "movementGenerator": "minimum_jerk",
                "liveInputBackend": "arduino",
                "directBackendBypassCount": 0,
            },
            "inputIntegrityStatusBefore": {
                "monitorPass": True,
                "injectionFlags": {
                    "mouseInjectedCount": 2,
                    "keyboardInjectedCount": 0,
                    "mouseLowerIlInjectedCount": 0,
                    "keyboardLowerIlInjectedCount": 0,
                },
                "backend": {"directBackendBypassCount": 0},
            },
            "inputIntegrityStatusAfter": {
                "monitorPass": True,
                "injectionFlags": {
                    "mouseInjectedCount": 2,
                    "keyboardInjectedCount": 0,
                    "mouseLowerIlInjectedCount": 0,
                    "keyboardLowerIlInjectedCount": 0,
                },
                "backend": {"directBackendBypassCount": 0},
            },
            "mouseInjectedCountDelta": 0,
            "keyboardInjectedCountDelta": 0,
            "lowerIlInjectedCountDelta": 0,
            "directBackendBypassCountDelta": 0,
            "actionReadiness": {"status": "PASS", "executionAllowed": True, "blockers": [], "warnings": []},
            "finalClassification": "clicked_expected_action",
        }
        (live_dir / "last_action_trace.json").write_text(json.dumps(trace), encoding="utf-8")
        fabric = knowledge_fabric.KnowledgeFabric.from_status(
            {
                "schema": "context_status.v1",
                "sessionPath": str(session),
                "latestTick": 42,
                "worldModelPayloads": synthetic_payloads(),
                "clientTickHot": {"lastMenuOptionClicked": {"option": "Chop down", "target": "Tree"}},
            }
        )

        result = fabric.query_action_input_visibility()
        current = fabric.query_current_debug_context(limit=3)
        with patch.object(mcp_server, "_fabric", return_value=fabric):
            mcp_payload = json.loads(mcp_server.call_tool("get_action_input_visibility", {})["content"][0]["text"])

    data = result["data"]
    phase = data["input_integrity_status"]["phaseCounts"]
    assert result["schema"] == "action_input_visibility_context.v1"
    assert data["plannedAction"] == "select_resource_target"
    assert data["plannedTarget"]["name"] == "Tree"
    assert data["plannedScreenPoint"] == {"x": 1123, "y": 734}
    assert data["displayScaleApplied"] is True
    assert data["arduinoCalibrationStatus"]["movementSafetyStatus"] == "PASS"
    assert data["humanInputController"]["liveInputBackend"] == "arduino"
    assert data["cursorMovementTrace"]["lastMovementProof"]["endScreenPoint"] == {"x": 1122, "y": 735}
    assert data["hoverConfirmationEvidence"]["acceptedHoverSample"]["topOption"] == "Chop down"
    assert data["menuOptionClickedEvidence"]["sourceEvent"] == "MenuOptionClicked"
    assert phase["operator_phase"]["operatorInjectedEvents"] == 2
    assert phase["operator_phase"]["blocking"] is False
    assert phase["live_action_phase"]["hardBlocker"] is False
    assert data["directBackendBypassCount"] == 0
    assert data["rawInputBypassToolsExposed"] is False
    assert current["data"]["actionInputVisibility"]["schema"] == "action_input_visibility_context.v1"
    assert mcp_payload["data"]["plannedTarget"]["name"] == "Tree"


def test_action_input_visibility_derives_proposal_point_without_live_input():
    fabric = knowledge_fabric.KnowledgeFabric(
        world_model_payloads=synthetic_payloads(),
        daemon_status={
            "schema": "context_status.v1",
            "latestTick": 77,
            "clientTickHot": {"gameState": "LOGIN_SCREEN"},
            "inputIntegrityStatus": {
                "monitorPass": True,
                "injectionFlags": {
                    "mouseInjectedCount": 4,
                    "keyboardInjectedCount": 0,
                    "mouseLowerIlInjectedCount": 0,
                    "keyboardLowerIlInjectedCount": 0,
                },
                "backend": {"directBackendBypassCount": 0},
            },
        },
    )
    proposal = {
        "proposedAction": "interact_service_route_object",
        "targetExplanation": {
            "name": "Staircase",
            "targetKey": "route-step-4",
            "actionTargetSource": "live_route_object",
            "canvasAimPoint": {"x": 100, "y": 80},
        },
    }
    readiness = {
        "schema": "live_readiness.v2",
        "status": "FAIL",
        "actionReadiness": {
            "status": "FAIL",
            "executionAllowed": False,
            "blockReason": "manual_login_required",
            "blockers": [{"code": "manual_login_required"}],
        },
        "inputGeometry": {
            "inputGeometryAvailable": True,
            "canvasScreenOrigin": {"x": 1000, "y": 2000},
            "canvasSize": {"width": 1530, "height": 1006},
            "sourceCanvasSize": {"width": 765, "height": 503},
            "displayScale": {"x": 1.0, "y": 1.0},
        },
    }

    with patch.object(fabric, "_action_proposal", return_value=proposal), patch.object(
        fabric,
        "_readiness_report",
        return_value=readiness,
    ):
        result = fabric.query_action_input_visibility()

    data = result["data"]
    trace = data["coordinateConversionTrace"]
    assert data["plannedAction"] == "interact_service_route_object"
    assert data["plannedTarget"]["name"] == "Staircase"
    assert data["plannedScreenPoint"] == {"x": 1200, "y": 2160}
    assert data["displayScaleApplied"] is False
    assert data["displayScaleReason"] == "display_scale_identity"
    assert trace["source"] == "current_action_proposal"
    assert trace["method"] == "dynamic_input_geometry"
    assert trace["inputPoint"] == {"x": 100, "y": 80}
    assert trace["inputPointSpace"] == "canvas"
    assert trace["targetKey"] == "route-step-4"
    assert trace["noLiveInput"] is True
    assert data["blockedReason"] == "manual_login_required"
    assert data["inputBlockEvidence"]["blocked"] is True
    assert data["inputBlockEvidence"]["blockedReason"] == "manual_login_required"
    assert data["inputBlockEvidence"]["operatorInjectedEventsBlocking"] is False
    assert data["arduinoCalibrationStatus"]["movementSafetyStatus"] == "NOT_EVALUATED"
    assert data["arduinoCalibrationStatus"]["movementSafetyEvaluated"] is False
    assert data["arduinoCalibrationStatus"]["inputGeometryAvailable"] is True
    assert data["arduinoCalibrationStatus"]["operatorInjectedEvents"] == 4
    assert data["humanInputController"]["controllerInstantiated"] is False
    assert data["humanInputController"]["requiredLivePipeline"] == "HumanInputController -> ArduinoHIDBackend"
    assert data["humanInputController"]["liveInputBackend"] == "arduino"
    assert data["cursorMovementTrace"]["movementExecuted"] is False
    assert data["cursorMovementTrace"]["plannedEndScreenPoint"] == {"x": 1200, "y": 2160}
    assert data["input_integrity_status"]["phaseCounts"]["operator_phase"]["operatorInjectedEvents"] == 4
    assert data["input_integrity_status"]["phaseCounts"]["live_action_phase"]["hardBlocker"] is False
    assert data["input_integrity_status"]["phaseAwareAssessment"]["liveActionHardBlocker"] is False
    assert data["directBackendBypassCount"] == 0
    assert data["rawInputBypassToolsExposed"] is False


def test_mcp_jsonrpc_lists_tools():
    response = mcp_server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    assert response["id"] == 1
    assert any(tool["name"] == "explain_current_blocker" for tool in response["result"]["tools"])
    assert any(tool["name"] == "get_current_debug_context" for tool in response["result"]["tools"])
    assert any(tool["name"] == "get_action_input_visibility" for tool in response["result"]["tools"])
    assert any(tool["name"] == "query_navigation_decision_trace" for tool in response["result"]["tools"])


def test_performance_caps_report_cap_hit_and_truncated():
    fabric = make_fabric()

    result = fabric.query_objects_near({"worldX": 3200, "worldY": 3200, "plane": 0}, radius=99, limit=1)

    assert result["capHit"] is True
    assert result["truncated"] is True
    assert result["performanceStats"]["queryTimeMs"] >= 0
    assert result["performanceStats"]["responseBytes"] > 0


class KnowledgeFabricTest(unittest.TestCase):
    def test_indexes_build_from_synthetic_world_model(self):
        test_indexes_build_from_synthetic_world_model()

    def test_spatial_query_filters_by_radius_and_plane(self):
        test_spatial_query_filters_by_radius_and_plane()

    def test_action_query_finds_climb_bank_and_chop_actions(self):
        test_action_query_finds_climb_bank_and_chop_actions()

    def test_resource_query_demotes_oak_when_insufficient_level_and_prefers_tree(self):
        test_resource_query_demotes_oak_when_insufficient_level_and_prefers_tree()

    def test_service_query_finds_bank_booth_banker_or_deposit_box(self):
        test_service_query_finds_bank_booth_banker_or_deposit_box()

    def test_route_query_finds_staircase_independent_of_resource_caps(self):
        test_route_query_finds_staircase_independent_of_resource_caps()

    def test_path_frontier_query_returns_capped_structured_result(self):
        test_path_frontier_query_returns_capped_structured_result()

    def test_path_frontier_diagnoses_stale_missing_player_location_reason(self):
        test_path_frontier_diagnoses_stale_missing_player_location_reason()

    def test_current_debug_context_includes_expected_sections(self):
        test_current_debug_context_includes_expected_sections()

    def test_current_debug_context_exposes_phase_aware_input_integrity(self):
        test_current_debug_context_exposes_phase_aware_input_integrity()

    def test_current_debug_context_reads_nested_context_status_shape(self):
        test_current_debug_context_reads_nested_context_status_shape()

    def test_fabric_reads_saved_context_response_world_model_keys(self):
        test_fabric_reads_saved_context_response_world_model_keys()

    def test_seen_inventory_items_parse_snapshot_inventory_and_external_names(self):
        test_seen_inventory_items_parse_snapshot_inventory_and_external_names()

    def test_explain_current_blocker_returns_structured_route_category(self):
        test_explain_current_blocker_returns_structured_route_category()

    def test_projection_cap_warning_does_not_override_allowed_route_action(self):
        test_projection_cap_warning_does_not_override_allowed_route_action()

    def test_client_tick_hot_blocker_is_freshness_not_hover_menu(self):
        test_client_tick_hot_blocker_is_freshness_not_hover_menu()

    def test_pathing_blocker_query_includes_route_context_and_frontier(self):
        test_pathing_blocker_query_includes_route_context_and_frontier()

    def test_view_quality_query_includes_camera_recommendation_fields(self):
        test_view_quality_query_includes_camera_recommendation_fields()

    def test_view_quality_reports_service_camera_recovery_when_service_object_offscreen(self):
        test_view_quality_reports_service_camera_recovery_when_service_object_offscreen()

    def test_view_quality_reports_service_camera_recovery_for_edge_sliver(self):
        test_view_quality_reports_service_camera_recovery_for_edge_sliver()

    def test_script_authoring_helper_queries_return_compact_data(self):
        test_script_authoring_helper_queries_return_compact_data()

    def test_data_source_inventory_and_query_coverage_include_expected_sources(self):
        test_data_source_inventory_and_query_coverage_include_expected_sources()

    def test_external_source_registry_cache_and_lookup_behaviour(self):
        test_external_source_registry_cache_and_lookup_behaviour()

    def test_external_wiki_search_is_cache_first_unless_refresh_allowed(self):
        test_external_wiki_search_is_cache_first_unless_refresh_allowed()

    def test_external_facts_enrich_resource_candidates_without_overriding_live_names(self):
        test_external_facts_enrich_resource_candidates_without_overriding_live_names()

    def test_task_probe_uses_external_requirements_and_suggests_profile(self):
        test_task_probe_uses_external_requirements_and_suggests_profile()

    def test_coverage_report_distinguishes_missing_data_from_nonblocking_context(self):
        test_coverage_report_distinguishes_missing_data_from_nonblocking_context()

    def test_data_quality_derives_client_tick_freshness_from_latency(self):
        test_data_quality_derives_client_tick_freshness_from_latency()

    def test_external_queries_and_authoring_paths_do_not_create_live_packets(self):
        test_external_queries_and_authoring_paths_do_not_create_live_packets()

    def test_data_quality_report_detects_missing_and_capped_data(self):
        test_data_quality_report_detects_missing_and_capped_data()

    def test_script_authoring_context_contains_expected_sections(self):
        test_script_authoring_context_contains_expected_sections()

    def test_replay_scenario_can_replay_blocker_offline(self):
        test_replay_scenario_can_replay_blocker_offline()

    def test_diff_debug_context_reports_route_and_blocker_changes(self):
        test_diff_debug_context_reports_route_and_blocker_changes()

    def test_handoff_summary_includes_blocker_bundle_and_next_step(self):
        test_handoff_summary_includes_blocker_bundle_and_next_step()

    def test_static_route_prior_remains_advisory_until_live_verified(self):
        test_static_route_prior_remains_advisory_until_live_verified()

    def test_session_memory_stores_observed_anchor_and_rejects_stale_session_for_execution(self):
        test_session_memory_stores_observed_anchor_and_rejects_stale_session_for_execution()

    def test_mcp_server_lists_tools_resources_and_searches_static_library(self):
        test_mcp_server_lists_tools_resources_and_searches_static_library()

    def test_mcp_jsonrpc_lists_tools(self):
        test_mcp_jsonrpc_lists_tools()

    def test_mcp_current_debug_context_matches_direct_shape(self):
        test_mcp_current_debug_context_matches_direct_shape()

    def test_mcp_new_tools_return_structured_compact_json(self):
        test_mcp_new_tools_return_structured_compact_json()

    def test_action_input_visibility_exposes_trace_input_and_phase_evidence(self):
        test_action_input_visibility_exposes_trace_input_and_phase_evidence()

    def test_action_input_visibility_derives_proposal_point_without_live_input(self):
        test_action_input_visibility_derives_proposal_point_without_live_input()

    def test_performance_caps_report_cap_hit_and_truncated(self):
        test_performance_caps_report_cap_hit_and_truncated()


if __name__ == "__main__":
    unittest.main()

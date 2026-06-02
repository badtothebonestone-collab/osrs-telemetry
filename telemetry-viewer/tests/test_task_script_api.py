import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import knowledge_fabric
import mcp_server
import task_script_api


EXAMPLE_PATH = VIEWER_DIR / "examples" / "woodcut_bank_task_script.json"


def load_example() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def make_fabric() -> knowledge_fabric.KnowledgeFabric:
    return knowledge_fabric.KnowledgeFabric.from_status(
        {
            "schema": "context_status.v1",
            "latestTick": 42,
            "gameState": "LOGGED_IN",
            "playerLocation": {"worldX": 3202, "worldY": 3200, "plane": 0},
            "clientTickHot": {
                "gameState": "LOGGED_IN",
                "latency": {"ageMillis": 25, "postMenuSortAgeMillis": 25},
                "postMenuSort": {"topOption": "Chop down", "topTarget": "Tree"},
                "lastMenuOptionClicked": {"option": "Chop down", "target": "Tree", "sourceEvent": "MenuOptionClicked"},
            },
            "inputIntegrityStatus": {
                "status": "FAIL",
                "monitorPass": False,
                "injectedEvents": 3,
                "lowerIlInjectedEvents": 0,
                "backend": {"directBackendBypassCount": 0},
            },
            "brain": {
                "genericTaskState": {"phase": "collecting", "cycleStage": "collect", "activeIntent": "select_target"},
                "inventoryContext": {"freeSlots": 12, "occupiedSlots": 16, "inventoryFull": False},
                "goalProgress": {"heldResourceCount": 16, "resourceGroup": "logs"},
                "bankUiContext": {"bankOpen": False, "depositInventoryAvailable": False},
                "serviceRouteContext": {
                    "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                    "currentNodeId": "lumbridge_west_trees",
                    "currentStepIndex": 0,
                    "routeStepStatus": "at_resource_area",
                },
                "pathingContext": {
                    "pathingNeeded": False,
                    "nextWaypointTile": {"worldX": 3203, "worldY": 3201, "plane": 0},
                    "pathLengthTiles": 1,
                },
            },
            "worldModelPayloads": {
                "quality": {
                    "worldModelAvailable": True,
                    "worldModelAgeMs": 12,
                    "sourceTick": 42,
                    "collisionAvailable": True,
                    "projectionAuditAvailable": True,
                },
                "resource_object_census": {
                    "schema": "resource_object_census.v1",
                    "objects": [
                        {
                            "objectKey": "tree-1",
                            "name": "Tree",
                            "id": 1276,
                            "actions": ["Chop down"],
                            "worldX": 3202,
                            "worldY": 3200,
                            "plane": 0,
                            "resourceCandidate": True,
                            "requiredSkill": "WOODCUTTING",
                            "requiredLevel": 1,
                            "levelRequirementMet": True,
                            "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
                        }
                    ],
                },
                "service_object_census": {
                    "schema": "service_object_census.v1",
                    "objects": [
                        {
                            "objectKey": "bank-booth",
                            "name": "Bank booth",
                            "id": 18491,
                            "actions": ["Bank"],
                            "worldX": 3208,
                            "worldY": 3220,
                            "plane": 2,
                            "serviceObjectCandidate": True,
                            "serviceObjectType": "bank",
                            "projection": {"visible": True, "geometryAvailable": True, "actionableByCanvas": True},
                        }
                    ],
                },
                "pathing_frontier": {"schema": "pathing_frontier.v1", "frontier": [{"worldX": 3203, "worldY": 3201, "plane": 0}]},
                "projection_audit": {"schema": "projection_audit.v1", "auditedObjects": 2, "projectionCapHit": False},
            },
        }
    )


def runtime_snapshot(variables: dict) -> dict:
    default_input = {
        "phaseCounts": {
            "live_action_phase": {
                "injectedEventsDelta": 0,
                "lowerIlInjectedEventsDelta": 0,
                "directBackendBypassCountDelta": 0,
                "hardBlocker": False,
            }
        },
        "current": {"directBackendBypassCount": 0},
    }
    wrapped = {
        name: {"observed": True, "value": value, "source": "test"}
        for name, value in variables.items()
    }
    wrapped.setdefault("inputIntegrity", {"observed": True, "value": default_input, "source": "test"})
    return {
        "schema": "task_runtime_evidence.v1",
        "data": {
            "runtimeVariables": wrapped,
            "readinessSummary": {"manualLoginRequired": False},
            "liveValidationPossibleNow": True,
        },
    }


def action_visibility_snapshot(*, execution_allowed: bool = True, planned_action: str = "deposit_inventory") -> dict:
    return {
        "schema": "action_input_visibility_context.v1",
        "status": "PASS" if execution_allowed else "WARN",
        "data": {
            "plannedAction": planned_action,
            "plannedTarget": {"name": "Bank booth", "actionTargetSource": "live_service_candidate"},
            "plannedScreenPoint": {"x": 1000, "y": 700},
            "coordinateConversionTrace": {"source": "test", "screenPoint": {"x": 1000, "y": 700}},
            "displayScaleApplied": False,
            "displayScaleReason": "test_no_scale",
            "arduinoCalibrationStatus": {"calibrated": True, "source": "test"},
            "humanInputController": {"movementProfile": "test_profile"},
            "cursorMovementTrace": {"samples": [], "lastMovementProof": {"landedAtTarget": True}},
            "hoverConfirmationEvidence": {"acceptedHoverSample": {"topOption": "Bank", "topTarget": "Bank booth"}},
            "menuOptionClickedEvidence": {"option": "Bank", "target": "Bank booth"},
            "lastClickProof": {"menuOptionClicked": True},
            "inputBlockEvidence": {
                "blocked": not execution_allowed,
                "blockedReason": None if execution_allowed else "manual_login_required",
                "phaseAwareLiveInputHardBlocker": False,
                "operatorInjectedEventsBlocking": False,
            },
            "latestActionTraceSummary": {"actionTraceSchema": "action_trace.v2", "finalClassification": "pass"},
            "latestDebugBundle": {"bundleDir": "test-bundle"},
            "livenessRecoveryActions": {"recommended": False, "available": True},
            "boundedWatcherDecisions": [],
            "target_view_state": {"currentlyOnScreen": True},
            "serviceResourceRouteCandidateState": {"routeContext": {"routeId": "test_route"}},
            "actionReadiness": {
                "status": "PASS" if execution_allowed else "FAIL",
                "executionAllowed": execution_allowed,
                "blockers": [] if execution_allowed else [{"code": "manual_login_required"}],
                "warnings": [],
            },
            "readinessActionEvidence": {"status": "PASS" if execution_allowed else "WARN"},
            "readiness": {"manualLoginRequired": False, "loadedSceneProof": {"loadedSceneVerified": True}},
        },
    }


def clean_failure_classification() -> dict:
    return {
        "schema": "task_failure_classification.v1",
        "status": "PASS",
        "primaryClassification": None,
        "blockers": [],
        "inputIntegrityAssessment": {
            "liveActionHardBlocker": False,
            "directBackendBypassCount": 0,
            "operatorNoiseOnly": False,
        },
    }


def navigation_trace_snapshot(*, suspicious: bool = False, trace_present: bool = True) -> dict:
    return {
        "schema": "navigation_decision_trace_summary.v1",
        "status": "WARN" if suspicious or not trace_present else "PASS",
        "warnings": ["navigation_decision_trace_missing"] if not trace_present else [],
        "data": {
            "source": "test_navigation_trace",
            "tracePresent": trace_present,
            "latestActionTraceCount": 1 if trace_present else 0,
            "decisionCount": 1 if trace_present else 0,
            "firstSuspiciousDecision": {"issue": "stale_state_allowed_click"} if suspicious else None,
            "latestDecision": {"decision": "wait", "reason": "navigation_in_progress"} if trace_present else None,
            "routeContext": {
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "currentNodeId": "lumbridge_first_floor_stairs",
                "currentStepIndex": 4,
                "predictedPathTiles": [
                    {"worldX": 3206, "worldY": 3228, "plane": 1},
                    {"worldX": 3205, "worldY": 3228, "plane": 1},
                ],
            },
            "pathingFrontier": {
                "frontierDiagnosis": {
                    "schema": "path_frontier_diagnosis.v1",
                    "frontierStatus": "WARN" if not trace_present else "PASS",
                    "frontierReason": "player_location_unavailable" if not trace_present else None,
                    "staleFrontierPlayerLocation": not trace_present,
                    "routeContextCanGuideDiagnosis": True,
                    "noGlobalPathfindingAdded": True,
                },
                "playerLocation": {
                    "worldLocation": {"worldX": 3206, "worldY": 3229, "plane": 1},
                    "source": "plugin_snapshot_baseline_player",
                },
            },
            "diagnosisRules": ["stale_state_allowed_click"],
        },
    }


class TaskScriptApiTest(unittest.TestCase):
    def test_spec_lists_required_primitives_and_no_raw_input_contract(self):
        spec = task_script_api.script_api_spec()

        self.assertEqual(spec["schema"], "task_script_api_spec.v1")
        self.assertEqual(set(task_script_api.ALLOWED_PRIMITIVES), set(spec["allowedPrimitives"]))
        for primitive in (
            "collect",
            "interact",
            "walk_to",
            "bank",
            "deposit",
            "close_bank",
            "return_to_resource",
            "wait_for_evidence",
            "recover_loaded_scene",
            "repeat_until",
        ):
            self.assertIn(primitive, spec["allowedPrimitives"])
        self.assertIn("click", spec["forbiddenRawInputPrimitives"])
        self.assertIn("HumanInputController", " ".join(spec["canonicalPipeline"]))
        self.assertTrue(spec["externalKnowledgePolicy"]["advisoryOnly"])
        self.assertFalse(spec["externalKnowledgePolicy"]["hotExecutorExternalCallsAllowed"])
        self.assertIn("inventory", spec["runtimeEvidenceVariables"])
        self.assertIn("resourceCount", spec["runtimeEvidenceVariables"])
        self.assertIn("menuOptionClicked", spec["runtimeEvidenceVariables"])
        self.assertEqual(spec["runtimeEvidenceComparisonSchema"], "task_runtime_evidence_comparison.v1")
        self.assertEqual(spec["failureClassificationSchema"], "task_failure_classification.v1")
        self.assertEqual(spec["stepReadinessSchema"], "task_step_readiness.v1")
        self.assertEqual(spec["taskRunReadinessSchema"], "task_run_readiness.v1")

    def test_woodcut_bank_example_validates_and_compiles_to_existing_actions(self):
        script = load_example()

        validation = task_script_api.validate_task_script(script)
        plan = task_script_api.compile_task_script(script)
        data = plan["data"]

        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(plan["status"], "PASS")
        self.assertTrue(data["noLiveInput"])
        self.assertEqual(data["executorContract"]["directBackendBypassCountMustRemain"], 0)
        self.assertFalse(data["executorContract"]["rawArbitraryInputToolsAllowed"])
        self.assertEqual(data["taskPolicy"]["bankOperation"], "deposit_inventory")
        evidence_plan = data["runtimeEvidencePlan"]
        self.assertEqual(evidence_plan["schema"], "task_script_evidence_plan.v1")
        self.assertEqual(evidence_plan["missingLifecycleVariables"], [])
        self.assertIn("inventory", evidence_plan["coveredVariables"])
        self.assertIn("bankOpen", evidence_plan["coveredVariables"])
        self.assertIn("hoverTarget", evidence_plan["coveredVariables"])
        self.assertIn("routeProgress", evidence_plan["coveredVariables"])
        self.assertIn("select_resource_target", data["actionProposalActions"])
        self.assertIn("open_service", data["actionProposalActions"])
        self.assertIn("deposit_inventory", data["actionProposalActions"])
        self.assertIn("close_bank", data["actionProposalActions"])
        self.assertIn("return_to_resource_area", data["actionProposalActions"])
        primitives = [step["primitive"] for step in data["flattenedActionPlan"]]
        self.assertIn("collect", primitives)
        self.assertIn("wait_for_evidence", primitives)
        self.assertIn("repeat_until", primitives)

    def test_validation_rejects_raw_input_and_unbounded_repeat(self):
        raw_click = {
            "schema": "task_script.v1",
            "name": "bad_click",
            "steps": [{"primitive": "click", "screenPoint": {"x": 1, "y": 2}}],
        }
        unbounded = {
            "schema": "task_script.v1",
            "name": "bad_loop",
            "steps": [{"primitive": "repeat_until", "condition": "inventory_full", "steps": [{"primitive": "wait_for_evidence", "evidence": ["tick"]}]}],
        }

        raw_result = task_script_api.validate_task_script(raw_click)
        loop_result = task_script_api.validate_task_script(unbounded)

        self.assertEqual(raw_result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "raw_input_bypass_forbidden" for error in raw_result["errors"]))
        self.assertEqual(loop_result["status"], "FAIL")
        self.assertTrue(any(error["code"] == "unbounded_loop_forbidden" for error in loop_result["errors"]))

    def test_explain_includes_phase_external_and_failure_policies(self):
        explanation = task_script_api.explain_script_plan(load_example())
        data = explanation["data"]

        self.assertEqual(explanation["schema"], "task_script_explanation.v1")
        self.assertIn("live_action_phase", data["phaseAwareInputIntegrityPolicy"])
        self.assertTrue(data["externalKnowledgePolicy"]["cacheFirst"])
        self.assertIn("coordinate_transform_error", data["failureClassificationPolicy"])
        self.assertIn("target/hover/menu mismatch", data["failureClassificationPolicy"])
        self.assertIn("runtimeEvidencePlan", data)
        self.assertIn("snapshotProtocol", data["runtimeEvidencePlan"])

    def test_evidence_plan_names_live_variables_and_snapshot_protocol(self):
        evidence = task_script_api.build_task_script_evidence_plan(load_example())
        data = evidence["data"]

        self.assertEqual(evidence["schema"], "task_script_evidence_plan.v1")
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(data["missingLifecycleVariables"], [])
        self.assertIn("menuOptionClicked", data["coveredVariables"])
        self.assertIn("hoverTarget", data["coveredVariables"])
        self.assertIn("routeProgress", data["coveredVariables"])
        self.assertIn("get_action_input_visibility", data["snapshotProtocol"]["before"])

    def test_runtime_evidence_comparison_proves_deposit_progress(self):
        before = runtime_snapshot(
            {
                "inventory": {"freeSlots": 0, "resourceItems": ["Logs"] * 16},
                "resourceCount": 16,
                "bankOpen": True,
                "menuOptionClicked": {"option": "Bank", "target": "Bank booth"},
                "phaseIntent": {"phase": "banking", "bankingComplete": False},
            }
        )
        after = runtime_snapshot(
            {
                "inventory": {"freeSlots": 28, "resourceItems": []},
                "resourceCount": 0,
                "bankOpen": True,
                "menuOptionClicked": {"option": "Deposit inventory", "target": ""},
                "phaseIntent": {"phase": "banking", "bankingComplete": True},
            }
        )

        comparison = task_script_api.compare_task_runtime_evidence_snapshots(
            before,
            after,
            script=load_example(),
            primitive="deposit",
        )
        data = comparison["data"]

        self.assertEqual(comparison["schema"], "task_runtime_evidence_comparison.v1")
        self.assertEqual(comparison["status"], "PASS")
        self.assertIn("resourceCount", data["expectedVariablesChanged"])
        self.assertIn("inventory", data["expectedVariablesChanged"])
        self.assertIn("phaseIntent", data["expectedVariablesChanged"])
        self.assertEqual(data["inputIntegrityHardBlockers"], [])

    def test_runtime_evidence_comparison_blocks_live_input_integrity_delta(self):
        before = runtime_snapshot({"resourceCount": 16})
        after = runtime_snapshot({"resourceCount": 17})
        after["data"]["runtimeVariables"]["inputIntegrity"]["value"]["phaseCounts"]["live_action_phase"] = {
            "injectedEventsDelta": 1,
            "lowerIlInjectedEventsDelta": 0,
            "directBackendBypassCountDelta": 0,
            "hardBlocker": True,
        }

        comparison = task_script_api.compare_task_runtime_evidence_snapshots(before, after, primitive="collect")

        self.assertEqual(comparison["status"], "FAIL")
        self.assertIn("live_action_input_integrity_hard_blocker", comparison["blockers"])

    def test_failure_classifier_labels_operator_phase_noise_as_non_blocking(self):
        evidence = runtime_snapshot({"resourceCount": 1})
        phase = evidence["data"]["runtimeVariables"]["inputIntegrity"]["value"]["phaseCounts"]
        phase["operator_phase"] = {
            "operatorInjectedEvents": 4,
            "operatorLowerIlInjectedEvents": 0,
            "blocking": False,
            "classification": "operatorInjectedEvents",
        }

        classification = task_script_api.classify_task_failure({"runtimeEvidence": evidence})

        self.assertEqual(classification["schema"], "task_failure_classification.v1")
        self.assertEqual(classification["status"], "PASS")
        self.assertEqual(classification["primaryClassification"], "operator-phase injected-input noise")
        self.assertTrue(classification["inputIntegrityAssessment"]["operatorNoiseOnly"])
        self.assertFalse(classification["inputIntegrityAssessment"]["liveActionHardBlocker"])

    def test_failure_classifier_prioritizes_manual_login_over_operator_noise(self):
        runtime = runtime_snapshot({"resourceCount": 1})
        runtime["data"]["readinessSummary"]["manualLoginRequired"] = True
        runtime["data"]["readinessSummary"]["livenessState"] = "login_screen"
        phase = runtime["data"]["runtimeVariables"]["inputIntegrity"]["value"]["phaseCounts"]
        phase["operator_phase"] = {"operatorInjectedEvents": 280, "operatorLowerIlInjectedEvents": 0, "blocking": False}

        classification = task_script_api.classify_task_failure({"runtimeEvidence": runtime})

        self.assertEqual(classification["status"], "WARN")
        self.assertEqual(classification["primaryClassification"], "game-state/user-login blocker")
        self.assertIn("operator-phase injected-input noise", classification["secondaryClassifications"])
        self.assertIn("manual_login_required", classification["blockers"])

    def test_failure_classifier_hard_blocks_live_input_integrity_delta(self):
        before = runtime_snapshot({"resourceCount": 1})
        after = runtime_snapshot({"resourceCount": 2})
        after["data"]["runtimeVariables"]["inputIntegrity"]["value"]["phaseCounts"]["live_action_phase"] = {
            "injectedEventsDelta": 1,
            "lowerIlInjectedEventsDelta": 0,
            "directBackendBypassCountDelta": 0,
            "hardBlocker": True,
        }
        comparison = task_script_api.compare_task_runtime_evidence_snapshots(before, after, primitive="collect")

        classification = task_script_api.classify_task_failure({"comparison": comparison})

        self.assertEqual(classification["status"], "FAIL")
        self.assertEqual(classification["primaryClassification"], "code/data truth bug")
        self.assertIn("failure_classification_hard_blocker", classification["blockers"])

    def test_failure_classifier_distinguishes_coordinate_arduino_and_menu_mismatch(self):
        coordinate = task_script_api.classify_task_failure({"actionTrace": {"actionTraceSchema": "action_trace.v2", "clickFailureBucket": "coordinate_transform_error"}})
        arduino = task_script_api.classify_task_failure({"actionTrace": {"actionTraceSchema": "action_trace.v2", "clickFailureBucket": "arduino_movement_error"}})
        hover = task_script_api.classify_task_failure({"actionTrace": {"actionTraceSchema": "action_trace.v2", "finalClassification": "hover_mismatch_skipped"}})

        self.assertEqual(coordinate["primaryClassification"], "coordinate_transform_error")
        self.assertEqual(arduino["primaryClassification"], "arduino_movement_error")
        self.assertEqual(hover["primaryClassification"], "target/hover/menu mismatch")

    def test_step_readiness_allows_ready_deposit_through_bounded_request(self):
        readiness = task_script_api.assess_task_step_readiness(
            load_example(),
            primitive="deposit",
            runtime_evidence=runtime_snapshot({"inventory": {"freeSlots": 0}, "resourceCount": 28, "bankOpen": True}),
            action_input_visibility=action_visibility_snapshot(execution_allowed=True, planned_action="deposit_inventory"),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        data = readiness["data"]

        self.assertEqual(readiness["schema"], "task_step_readiness.v1")
        self.assertEqual(readiness["status"], "PASS")
        self.assertTrue(data["requestAllowedNow"])
        self.assertEqual(data["primitive"], "deposit")
        self.assertEqual(data["boundedOperatorRequest"], "request_bounded_live_step")
        self.assertTrue(data["liveCapablePrimitive"])
        self.assertFalse(data["rawInputBypassToolsExposed"])
        self.assertIn("HumanInputController", data["canonicalPipeline"])
        self.assertEqual(
            data["navigationDecisionTrace"]["routeContext"]["routeId"],
            "lumbridge_west_trees_to_lumbridge_castle_bank",
        )
        self.assertEqual(data["navigationDecisionTrace"]["pathingFrontierStatus"], "PASS")
        self.assertTrue(data["navigationDecisionTrace"]["routeContextCanGuideDiagnosis"])

    def test_step_readiness_blocks_manual_login_and_failed_action_readiness(self):
        runtime = runtime_snapshot({"bankOpen": False})
        runtime["data"]["readinessSummary"]["manualLoginRequired"] = True
        runtime["data"]["readinessSummary"]["livenessState"] = "login_screen"
        visibility = action_visibility_snapshot(execution_allowed=False, planned_action="open_service")
        visibility["data"]["readiness"]["manualLoginRequired"] = True
        failure = task_script_api.classify_task_failure({"runtimeEvidence": runtime, "actionInputVisibility": visibility})

        readiness = task_script_api.assess_task_step_readiness(
            load_example(),
            primitive="bank",
            runtime_evidence=runtime,
            action_input_visibility=visibility,
            failure_classification=failure,
            navigation_decision_trace=navigation_trace_snapshot(),
        )

        self.assertEqual(readiness["status"], "WARN")
        self.assertFalse(readiness["data"]["requestAllowedNow"])
        self.assertIn("manual_login_required", readiness["blockers"])
        self.assertIn("action_readiness_not_pass", readiness["blockers"])

    def test_run_readiness_infers_deposit_and_delegates_step_gate(self):
        readiness = task_script_api.assess_task_run_readiness(
            load_example(),
            runtime_evidence=runtime_snapshot(
                {
                    "inventory": {"freeSlots": 0},
                    "resourceCount": 12,
                    "bankOpen": True,
                    "loadedScene": {"loadedSceneVerified": True},
                }
            ),
            action_input_visibility=action_visibility_snapshot(
                execution_allowed=True,
                planned_action="deposit_inventory",
            ),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        data = readiness["data"]

        self.assertEqual(readiness["schema"], "task_run_readiness.v1")
        self.assertEqual(readiness["status"], "PASS")
        self.assertEqual(data["inferredNextPrimitive"]["primitive"], "deposit")
        self.assertTrue(data["requestAllowedNow"])
        self.assertEqual(data["nextStepReadiness"]["schema"], "task_step_readiness.v1")
        self.assertEqual(data["boundedOperatorRequest"], "request_bounded_live_step")
        self.assertFalse(data["rawInputBypassToolsExposed"])
        self.assertEqual(data["directBackendBypassCountMustRemain"], 0)
        self.assertEqual(data["actionInputVisibilityEvidence"]["plannedAction"], "deposit_inventory")
        self.assertEqual(data["actionInputVisibilityEvidence"]["displayScaleApplied"], False)
        self.assertEqual(
            data["actionInputVisibilityEvidence"]["arduinoCalibrationStatus"]["source"],
            "test",
        )
        self.assertFalse(data["actionInputVisibilityEvidence"]["inputBlockEvidence"]["blocked"])
        self.assertEqual(data["actionInputVisibilityEvidence"]["actionReadiness"]["status"], "PASS")
        self.assertEqual(
            data["navigationDecisionTrace"]["routeContext"]["routeId"],
            "lumbridge_west_trees_to_lumbridge_castle_bank",
        )
        self.assertEqual(data["navigationDecisionTrace"]["pathingPlayerLocation"]["worldLocation"]["worldX"], 3206)
        self.assertEqual(data["currentLifecycle"]["evidenceIntegrity"]["status"], "PASS")
        self.assertTrue(data["currentLifecycle"]["evidenceIntegrity"]["liveTruthUsableForGameplay"])

    def test_run_readiness_treats_missing_loaded_scene_proof_as_recovery_only(self):
        visibility = action_visibility_snapshot(execution_allowed=True, planned_action="deposit_inventory")
        visibility["data"]["readiness"].pop("loadedSceneProof")

        readiness = task_script_api.assess_task_run_readiness(
            load_example(),
            runtime_evidence=runtime_snapshot(
                {"inventory": {"freeSlots": 0}, "resourceCount": 12, "bankOpen": True}
            ),
            action_input_visibility=visibility,
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        data = readiness["data"]
        integrity = data["currentLifecycle"]["evidenceIntegrity"]

        self.assertEqual(readiness["status"], "WARN")
        self.assertEqual(data["inferredNextPrimitive"]["primitive"], "recover_loaded_scene")
        self.assertEqual(data["boundedOperatorRequest"], "request_liveness_recovery")
        self.assertTrue(data["requestAllowedNow"])
        self.assertFalse(data["nextStepReadiness"]["data"]["liveCapablePrimitive"])
        self.assertFalse(integrity["liveTruthUsableForGameplay"])
        self.assertTrue(integrity["advisoryOnlyUntilLoadedSceneVerified"])
        self.assertIn("inventory", integrity["advisoryLifecycleFields"])
        self.assertIn("loaded_scene_proof_missing_or_unverified", readiness["warnings"])

    def test_run_readiness_recommends_recover_loaded_scene_but_blocks_manual_login(self):
        runtime = runtime_snapshot(
            {
                "bankOpen": False,
                "loadedScene": {"loadedSceneVerified": False},
                "routeProgress": {"routeId": "test_route", "currentStepIndex": 4},
                "phaseIntent": {"phase": "inventory_full", "activeIntent": "needs_service"},
            }
        )
        runtime["data"]["readinessSummary"]["manualLoginRequired"] = True
        runtime["data"]["readinessSummary"]["livenessState"] = "login_screen"
        runtime["data"]["readinessSummary"]["loadedSceneProof"] = {"loadedSceneVerified": False}
        visibility = action_visibility_snapshot(execution_allowed=False, planned_action="open_service")
        visibility["data"]["readiness"]["manualLoginRequired"] = True
        visibility["data"]["readiness"]["loadedSceneProof"] = {"loadedSceneVerified": False}
        failure = task_script_api.classify_task_failure({"runtimeEvidence": runtime, "actionInputVisibility": visibility})

        readiness = task_script_api.assess_task_run_readiness(
            load_example(),
            runtime_evidence=runtime,
            action_input_visibility=visibility,
            failure_classification=failure,
            navigation_decision_trace=navigation_trace_snapshot(trace_present=False),
        )

        self.assertEqual(readiness["schema"], "task_run_readiness.v1")
        self.assertEqual(readiness["status"], "WARN")
        self.assertEqual(readiness["data"]["inferredNextPrimitive"]["primitive"], "recover_loaded_scene")
        self.assertFalse(readiness["data"]["requestAllowedNow"])
        self.assertIn("manual_login_required", readiness["blockers"])
        integrity = readiness["data"]["currentLifecycle"]["evidenceIntegrity"]
        self.assertEqual(integrity["schema"], "task_lifecycle_evidence_integrity.v1")
        self.assertEqual(integrity["status"], "WARN")
        self.assertFalse(integrity["liveTruthUsableForGameplay"])
        self.assertTrue(integrity["advisoryOnlyUntilLoadedSceneVerified"])
        self.assertIn("routeProgress", integrity["advisoryLifecycleFields"])
        self.assertIn("phaseIntent", integrity["advisoryLifecycleFields"])
        self.assertIn("route_progress_present_while_liveness_unverified", readiness["warnings"])
        self.assertIn("phase_intent_present_while_liveness_unverified", readiness["warnings"])
        self.assertIn("planned_action_present_while_liveness_unverified", readiness["warnings"])
        nav = readiness["data"]["navigationDecisionTrace"]
        self.assertFalse(nav["tracePresent"])
        self.assertEqual(nav["traceMissingReason"], "latest_action_trace_missing")
        self.assertTrue(nav["pathingFrontierDiagnosis"]["staleFrontierPlayerLocation"])
        self.assertTrue(nav["pathingFrontierDiagnosis"]["noGlobalPathfindingAdded"])
        self.assertEqual(nav["pathingPlayerLocation"]["worldLocation"]["plane"], 1)

    def test_knowledge_fabric_direct_methods_expose_script_api(self):
        fabric = make_fabric()

        validation = fabric.validate_task_script(load_example())
        plan = fabric.compile_task_script(load_example())
        evidence_plan = fabric.task_script_evidence_plan(load_example())
        runtime = fabric.query_task_script_runtime_evidence(load_example())
        comparison = fabric.compare_task_script_runtime_evidence(
            runtime_snapshot({"resourceCount": 1}),
            runtime_snapshot({"resourceCount": 2}),
            primitive="collect",
        )
        classification = fabric.classify_task_failure({"runtimeEvidence": runtime})
        step_readiness = fabric.assess_task_script_step(
            load_example(),
            primitive="deposit",
            runtime_evidence=runtime_snapshot({"bankOpen": True, "resourceCount": 12}),
            action_input_visibility=action_visibility_snapshot(),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        run_readiness = fabric.assess_task_script_run(
            load_example(),
            runtime_evidence=runtime_snapshot({"bankOpen": True, "resourceCount": 12}),
            action_input_visibility=action_visibility_snapshot(),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        template = fabric.suggest_task_template("woodcutting and bank logs", profile="woodcutting")
        scene_probe = fabric.probe_task_from_scene("woodcutting and bank logs", profile="woodcutting", limit=5)

        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(plan["schema"], "task_script_plan.v1")
        self.assertEqual(evidence_plan["schema"], "task_script_evidence_plan.v1")
        self.assertEqual(runtime["schema"], "task_runtime_evidence.v1")
        self.assertEqual(comparison["schema"], "task_runtime_evidence_comparison.v1")
        self.assertEqual(classification["schema"], "task_failure_classification.v1")
        self.assertEqual(step_readiness["schema"], "task_step_readiness.v1")
        self.assertEqual(run_readiness["schema"], "task_run_readiness.v1")
        self.assertTrue(runtime["data"]["runtimeVariables"]["inventory"]["observed"])
        self.assertEqual(runtime["data"]["runtimeVariables"]["resourceCount"]["value"], 16)
        self.assertEqual(runtime["data"]["runtimeVariables"]["bankOpen"]["value"], False)
        self.assertTrue(runtime["data"]["runtimeVariables"]["menuOptionClicked"]["observed"])
        input_integrity = runtime["data"]["runtimeVariables"]["inputIntegrity"]["value"]
        self.assertEqual(input_integrity["phaseCounts"]["operator_phase"]["operatorInjectedEvents"], 3)
        self.assertFalse(input_integrity["phaseCounts"]["operator_phase"]["blocking"])
        self.assertFalse(input_integrity["phaseCounts"]["live_action_phase"]["hardBlocker"])
        self.assertIn("phaseIntent", runtime["data"]["coveredVariablesObservedNow"])
        self.assertEqual(template["data"]["templateName"], "woodcut_bank")
        self.assertEqual(scene_probe["schema"], "task_scene_probe.v1")
        self.assertTrue(scene_probe["data"]["noLiveInput"])

    def test_mcp_exposes_script_tools_and_resources_without_raw_tools(self):
        tools = mcp_server.tool_definitions()
        resources = mcp_server.resource_definitions()
        tool_names = {tool["name"] for tool in tools}
        resource_uris = {resource["uri"] for resource in resources}
        forbidden_raw_names = {"mouseDown", "mouseUp", "keyDown", "keyUp", "click", "raw_click", "raw_mouse_down"}

        self.assertIn("get_task_script_api_spec", tool_names)
        self.assertIn("validate_task_script", tool_names)
        self.assertIn("compile_task_script", tool_names)
        self.assertIn("explain_script_plan", tool_names)
        self.assertIn("get_task_script_evidence_plan", tool_names)
        self.assertIn("get_task_script_runtime_evidence", tool_names)
        self.assertIn("compare_task_script_runtime_evidence", tool_names)
        self.assertIn("classify_task_failure", tool_names)
        self.assertIn("assess_task_script_step", tool_names)
        self.assertIn("assess_task_script_run", tool_names)
        self.assertIn("suggest_task_template", tool_names)
        self.assertIn("probe_task_from_scene", tool_names)
        self.assertTrue(forbidden_raw_names.isdisjoint(tool_names))
        self.assertIn("osrs://script-api/spec", resource_uris)
        self.assertIn("osrs://script-api/woodcut-bank-example", resource_uris)
        self.assertIn("osrs://script-api/woodcut-bank-evidence-plan", resource_uris)
        self.assertIn("osrs://script-api/runtime-evidence", resource_uris)
        self.assertIn("osrs://script-api/failure-classification", resource_uris)
        self.assertIn("osrs://script-api/step-readiness", resource_uris)
        self.assertIn("osrs://script-api/run-readiness", resource_uris)

        fabric = make_fabric()
        with patch.object(mcp_server, "_fabric", return_value=fabric):
            spec_payload = json.loads(mcp_server.call_tool("get_task_script_api_spec", {})["content"][0]["text"])
            compile_payload = json.loads(mcp_server.call_tool("compile_task_script", {"script": load_example()})["content"][0]["text"])
            evidence_payload = json.loads(mcp_server.call_tool("get_task_script_evidence_plan", {"script": load_example()})["content"][0]["text"])
            runtime_payload = json.loads(mcp_server.call_tool("get_task_script_runtime_evidence", {"script": load_example()})["content"][0]["text"])
            comparison_payload = json.loads(
                mcp_server.call_tool(
                    "compare_task_script_runtime_evidence",
                    {
                        "before": runtime_snapshot({"resourceCount": 1}),
                        "after": runtime_snapshot({"resourceCount": 2}),
                        "primitive": "collect",
                    },
                )["content"][0]["text"]
            )
            classification_payload = json.loads(
                mcp_server.call_tool("classify_task_failure", {"evidence": {"runtimeEvidence": runtime_payload}})["content"][0]["text"]
            )
            step_readiness_payload = json.loads(
                mcp_server.call_tool(
                    "assess_task_script_step",
                    {
                        "script": load_example(),
                        "primitive": "deposit",
                        "runtimeEvidence": runtime_snapshot({"bankOpen": True, "resourceCount": 12}),
                        "actionInputVisibility": action_visibility_snapshot(),
                        "failureClassification": clean_failure_classification(),
                        "navigationDecisionTrace": navigation_trace_snapshot(),
                    },
                )["content"][0]["text"]
            )
            run_readiness_payload = json.loads(
                mcp_server.call_tool(
                    "assess_task_script_run",
                    {
                        "script": load_example(),
                        "runtimeEvidence": runtime_snapshot({"bankOpen": True, "resourceCount": 12}),
                        "actionInputVisibility": action_visibility_snapshot(),
                        "failureClassification": clean_failure_classification(),
                        "navigationDecisionTrace": navigation_trace_snapshot(),
                    },
                )["content"][0]["text"]
            )
            run_readiness_resource = json.loads(
                mcp_server.read_resource("osrs://script-api/run-readiness")["contents"][0]["text"]
            )
            probe_payload = json.loads(
                mcp_server.call_tool("probe_task_from_scene", {"taskDescription": "woodcutting and bank logs", "limit": 3})["content"][0]["text"]
            )

        self.assertEqual(spec_payload["schema"], "task_script_api_spec.v1")
        self.assertEqual(compile_payload["schema"], "task_script_plan.v1")
        self.assertEqual(evidence_payload["schema"], "task_script_evidence_plan.v1")
        self.assertEqual(runtime_payload["schema"], "task_runtime_evidence.v1")
        self.assertEqual(comparison_payload["schema"], "task_runtime_evidence_comparison.v1")
        self.assertEqual(classification_payload["schema"], "task_failure_classification.v1")
        self.assertEqual(step_readiness_payload["schema"], "task_step_readiness.v1")
        self.assertEqual(run_readiness_payload["schema"], "task_run_readiness.v1")
        self.assertEqual(run_readiness_resource["schema"], "task_run_readiness.v1")
        self.assertEqual(probe_payload["schema"], "task_scene_probe.v1")


if __name__ == "__main__":
    unittest.main()

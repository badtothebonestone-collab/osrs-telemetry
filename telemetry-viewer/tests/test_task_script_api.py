import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import knowledge_fabric
import mcp_server
import task_script_api


EXAMPLE_PATH = VIEWER_DIR / "examples" / "woodcut_bank_task_script.json"


BANKING_LIFECYCLE = {
    "schema": "banking_lifecycle.v1",
    "status": "PASS",
    "phase": "complete",
    "confidence": 0.95,
    "bankLikeInterface": "bank",
    "depositConfirmationLevel": "bank_container_delta_confirmed",
    "bankContainerDeltaAvailable": True,
    "bank": {
        "openSeen": True,
        "depositBoxOpenSeen": False,
        "widgetRootSeen": True,
        "containerAvailable": True,
        "bankUiPresent": True,
        "bankUiSnapshotCount": 2,
        "bankContainerDeltaAvailable": True,
    },
    "inventory": {
        "freeSlotsBefore": 0,
        "freeSlotsAfter": 16,
        "freeSlotDelta": 16,
        "normalLogsBefore": 16,
        "normalLogsAfter": 0,
    },
    "deposit": {
        "detected": True,
        "totalDepositedCount": 16,
        "items": [{"id": 1511, "name": "Logs", "quantity": 16, "confirmationLevel": "bank_container_delta_confirmed"}],
    },
    "withdraw": {"detected": False, "items": [], "totalWithdrawnCount": 0},
    "missingCapabilities": [],
    "warnings": [],
}


COMBAT_DAMAGE_SUMMARY = {
    "schema": "combat_damage_summary.v1",
    "status": "PASS",
    "combatObserved": True,
    "primaryOpponent": {"name": "Mugger", "kind": "npc", "id": 513, "confidence": 0.95},
    "damageTaken": {"total": 5, "hitsplatCount": 23, "sources": [{"name": "Mugger", "total": 5}]},
    "damageDealt": {"total": 9, "hitsplatCount": 14, "targets": [{"name": "Mugger", "total": 9}]},
    "health": {"hpBefore": 10, "hpAfter": 7, "lowestObservedHp": 6, "healthChanged": True},
    "hitsplats": {"total": 37, "localPlayerHitsplats": 23, "opponentHitsplats": 14, "ambiguousHitsplats": 0},
    "actorDeaths": [{"name": "Mugger", "kind": "npc"}],
    "taskResume": {"taskResumed": True},
    "confidence": 0.95,
    "warnings": [],
    "missingCapabilities": [],
}


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


def navigation_trace_snapshot(*, suspicious: bool = False, trace_present: bool = True, blocking_eligible: bool = True) -> dict:
    return {
        "schema": "navigation_decision_trace_summary.v1",
        "status": "WARN" if suspicious or not trace_present else "PASS",
        "warnings": ["navigation_decision_trace_missing"] if not trace_present else [],
        "data": {
            "source": "test_navigation_trace",
            "diagnosticOnly": not blocking_eligible,
            "blockingEligible": blocking_eligible,
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
    def test_script_banking_helpers_expose_deposit_result(self):
        self.assertTrue(task_script_api.is_bank_open(BANKING_LIFECYCLE))
        self.assertFalse(task_script_api.is_deposit_box_open(BANKING_LIFECYCLE))
        self.assertEqual(task_script_api.get_active_bank_like_interface(BANKING_LIFECYCLE), "bank")
        result = task_script_api.get_deposit_result(BANKING_LIFECYCLE)
        self.assertTrue(result["depositComplete"])
        self.assertEqual(result["depositedItems"][0]["id"], 1511)
        self.assertTrue(task_script_api.did_deposit_item(BANKING_LIFECYCLE, 1511))
        self.assertEqual(task_script_api.get_banking_missing_capabilities(BANKING_LIFECYCLE), [])

    def test_script_combat_damage_helpers_expose_damage_result(self):
        compact = task_script_api.get_combat_damage_summary(COMBAT_DAMAGE_SUMMARY)
        self.assertEqual(compact["damageTakenTotal"], 5)
        self.assertEqual(compact["damageDealtTotal"], 9)
        self.assertEqual(task_script_api.get_damage_taken(COMBAT_DAMAGE_SUMMARY)["hpAfter"], 7)
        self.assertEqual(task_script_api.get_primary_opponent(COMBAT_DAMAGE_SUMMARY)["name"], "Mugger")
        self.assertTrue(task_script_api.did_take_damage(COMBAT_DAMAGE_SUMMARY))
        self.assertTrue(task_script_api.did_deal_damage(COMBAT_DAMAGE_SUMMARY))

    def test_deposit_evidence_plan_includes_rich_banking_variables(self):
        script = {"name": "deposit-only", "steps": [{"primitive": "deposit"}]}
        plan = task_script_api.build_task_script_evidence_plan(script)
        covered = plan["data"]["coveredVariables"]
        self.assertIn("bankState", covered)
        self.assertIn("bankingLifecycle", covered)
        self.assertIn("inventoryDelta", covered)
        self.assertIn("depositResult", covered)

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
        self.assertIn("bankState", evidence_plan["coveredVariables"])
        self.assertIn("depositResult", evidence_plan["coveredVariables"])
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
                "inventoryDelta": {"freeSlotDelta": 0, "depositedItems": []},
                "resourceCount": 16,
                "bankOpen": True,
                "bankState": {"bankOpen": True, "bankContainerAvailable": True},
                "bankingLifecycle": {"status": "PASS", "phase": "bank_open", "confidence": 0.8},
                "depositResult": {"depositComplete": False, "depositedItems": [], "depositConfirmationLevel": "none"},
                "menuOptionClicked": {"option": "Bank", "target": "Bank booth"},
                "phaseIntent": {"phase": "banking", "bankingComplete": False},
            }
        )
        after = runtime_snapshot(
            {
                "inventory": {"freeSlots": 28, "resourceItems": []},
                "inventoryDelta": {"freeSlotDelta": 16, "depositedItems": [{"id": 1511, "quantity": 16}]},
                "resourceCount": 0,
                "bankOpen": True,
                "bankState": {"bankOpen": True, "bankContainerAvailable": True, "bankContainerDeltaAvailable": True},
                "bankingLifecycle": {"status": "PASS", "phase": "complete", "confidence": 0.95},
                "depositResult": {"depositComplete": True, "depositedItems": [{"id": 1511, "quantity": 16}], "depositConfirmationLevel": "bank_container_delta_confirmed"},
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
        self.assertIn("resourceCount", data["expectedVariablesChangedAndProofEligible"])

    def test_runtime_evidence_comparison_warns_when_changed_variables_are_advisory_only(self):
        before = runtime_snapshot(
            {
                "inventory": {"freeSlots": 0, "resourceItems": ["Logs"] * 16},
                "inventoryDelta": {"freeSlotDelta": 0, "depositedItems": []},
                "resourceCount": 16,
                "bankOpen": True,
                "bankState": {"bankOpen": True, "bankContainerAvailable": True},
                "bankingLifecycle": {"status": "PASS", "phase": "bank_open", "confidence": 0.8},
                "depositResult": {"depositComplete": False, "depositedItems": []},
                "phaseIntent": {"phase": "banking", "bankingComplete": False},
            }
        )
        after = runtime_snapshot(
            {
                "inventory": {"freeSlots": 28, "resourceItems": []},
                "inventoryDelta": {"freeSlotDelta": 16, "depositedItems": [{"id": 1511, "quantity": 16}]},
                "resourceCount": 0,
                "bankOpen": True,
                "bankState": {"bankOpen": True, "bankContainerAvailable": True, "bankContainerDeltaAvailable": True},
                "bankingLifecycle": {"status": "PASS", "phase": "complete", "confidence": 0.95},
                "depositResult": {"depositComplete": True, "depositedItems": [{"id": 1511, "quantity": 16}]},
                "phaseIntent": {"phase": "banking", "bankingComplete": True},
            }
        )
        after["data"]["liveValidationPossibleNow"] = False
        after["data"]["runtimeEvidenceIntegrity"] = {
            "schema": "task_runtime_evidence_integrity.v1",
            "status": "WARN",
            "liveValidationPossibleNow": False,
            "proofBlockers": ["loaded_scene_not_verified"],
            "variableIntegrity": {
                "inventory": {"proofEligibleNow": False, "advisoryOnly": True},
                "resourceCount": {"proofEligibleNow": False, "advisoryOnly": True},
                "phaseIntent": {"proofEligibleNow": False, "advisoryOnly": True},
            },
        }

        comparison = task_script_api.compare_task_runtime_evidence_snapshots(
            before,
            after,
            script=load_example(),
            primitive="deposit",
        )
        data = comparison["data"]

        self.assertEqual(comparison["status"], "WARN")
        self.assertIn("resourceCount", data["expectedVariablesChanged"])
        self.assertIn("resourceCount", data["expectedVariablesProofBlockedAfter"])
        self.assertNotIn("resourceCount", data["expectedVariablesChangedAndProofEligible"])
        self.assertIn("expected_variable_not_proof_eligible_after:resourceCount", comparison["warnings"])

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

    def test_failure_classifier_does_not_treat_policy_text_as_manual_login(self):
        runtime = runtime_snapshot({"resourceCount": 16})
        runtime["data"]["readinessSummary"] = {
            "manualLoginRequired": False,
            "livenessState": "loaded_scene",
            "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
        }
        visibility = action_visibility_snapshot(execution_allowed=True, planned_action="interact_service_route_object")
        visibility["data"]["failureClassificationHints"] = ["manual_login_required"]
        visibility["data"]["livenessRecovery"] = {"manualLoginRequiredIsBlocker": True}

        classification = task_script_api.classify_task_failure(
            {
                "runtimeEvidence": runtime,
                "actionInputVisibility": visibility,
                "debugContext": {
                    "data": {
                        "readiness": {
                            "manualLoginRequired": False,
                            "livenessState": "loaded_scene",
                            "loadedSceneProof": {"loadedSceneVerified": True, "gameState": "LOGGED_IN"},
                        },
                        "failureClassificationPolicy": ["manual_login_required"],
                    }
                },
            }
        )

        self.assertNotEqual(classification["primaryClassification"], "game-state/user-login blocker")
        self.assertNotIn("manual_login_required", classification["blockers"])

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

    def test_step_readiness_blocks_when_expected_variables_are_advisory_only(self):
        runtime = runtime_snapshot(
            {
                "inventory": {"freeSlots": 0},
                "resourceCount": 12,
                "bankOpen": True,
                "menuOptionClicked": {"option": "Bank", "target": "Bank booth"},
                "phaseIntent": {"phase": "banking"},
            }
        )
        runtime["data"]["runtimeEvidenceIntegrity"] = {
            "schema": "task_runtime_evidence_integrity.v1",
            "status": "WARN",
            "proofBlockers": ["loaded_scene_not_verified"],
            "variableIntegrity": {
                "bankOpen": {"proofEligibleNow": False, "advisoryOnly": True},
                "menuOptionClicked": {"proofEligibleNow": False, "advisoryOnly": True},
                "inventory": {"proofEligibleNow": False, "advisoryOnly": True},
                "resourceCount": {"proofEligibleNow": False, "advisoryOnly": True},
                "phaseIntent": {"proofEligibleNow": False, "advisoryOnly": True},
            },
        }

        readiness = task_script_api.assess_task_step_readiness(
            load_example(),
            primitive="deposit",
            runtime_evidence=runtime,
            action_input_visibility=action_visibility_snapshot(execution_allowed=True, planned_action="deposit_inventory"),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )
        data = readiness["data"]

        self.assertEqual(readiness["status"], "WARN")
        self.assertFalse(data["requestAllowedNow"])
        self.assertIn("expected_runtime_variable_not_proof_eligible", readiness["blockers"])
        self.assertIn("resourceCount", data["proofBlockedExpectedRuntimeVariablesNow"])
        self.assertIn("resourceCount", data["advisoryExpectedRuntimeVariablesNow"])
        self.assertFalse(data["expectedRuntimeVariableProof"]["variableIntegrity"]["resourceCount"]["proofEligibleNow"])
        self.assertIn("expected_runtime_variable_not_proof_eligible:resourceCount", readiness["warnings"])

    def test_step_readiness_treats_session_jsonl_navigation_trace_as_diagnostic_only(self):
        readiness = task_script_api.assess_task_step_readiness(
            load_example(),
            primitive="return_to_resource",
            runtime_evidence=runtime_snapshot(
                {
                    "location": {"worldX": 3204, "worldY": 3229, "plane": 1},
                    "routeProgress": {
                        "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                        "currentStepIndex": 4,
                    },
                    "phaseIntent": {"phase": "return_to_resource", "activeIntent": "return_to_resource_area"},
                }
            ),
            action_input_visibility=action_visibility_snapshot(
                execution_allowed=True,
                planned_action="interact_service_route_object",
            ),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(suspicious=True, blocking_eligible=False),
        )

        self.assertEqual(readiness["status"], "WARN")
        self.assertTrue(readiness["data"]["requestAllowedNow"])
        self.assertNotIn("suspicious_navigation_decision_trace", readiness["blockers"])
        self.assertIn("diagnostic_navigation_decision_trace_not_blocking", readiness["warnings"])
        self.assertFalse(readiness["data"]["navigationDecisionTrace"]["blockingEligible"])

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
        self.assertIn("bankState", runtime["data"]["runtimeVariables"])
        self.assertIn("depositResult", runtime["data"]["runtimeVariables"])
        self.assertTrue(runtime["data"]["runtimeVariables"]["menuOptionClicked"]["observed"])
        integrity = runtime["data"]["runtimeEvidenceIntegrity"]
        self.assertEqual(integrity["schema"], "task_runtime_evidence_integrity.v1")
        self.assertIn("menuOptionClicked", runtime["data"]["advisoryVariablesNow"])
        self.assertFalse(integrity["variableIntegrity"]["menuOptionClicked"]["proofEligibleNow"])
        self.assertIn("inputIntegrity", runtime["data"]["proofEligibleVariablesNow"])
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
        self.assertIn("get_banking_state", tool_names)
        self.assertIn("get_banking_lifecycle", tool_names)
        self.assertIn("get_inventory_delta", tool_names)
        self.assertIn("get_deposit_result", tool_names)
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

    def test_woodcutting_loop_helpers_expose_next_phase(self):
        source = {
            "woodcutting_loop_lifecycle": {
                "schema": "woodcutting_loop_lifecycle.v1",
                "status": "PASS",
                "loopState": "inventory_full",
                "confidence": 0.95,
                "currentPhase": {"phase": "inventory_full", "label": "Inventory full"},
                "nextExpectedPhase": {"phase": "route_to_bank", "label": "Route to bank"},
                "detectedPhases": [{"phase": "cutting"}, {"phase": "inventory_full"}],
                "woodcutting": {"inventoryFull": True, "freeSlotsEnd": 0},
                "banking": {},
                "interruptions": {},
                "warnings": [],
                "missingCapabilities": [],
            }
        }
        self.assertEqual(task_script_api.get_current_task_phase(source), "inventory_full")
        self.assertEqual(task_script_api.get_next_expected_phase(source), "route_to_bank")
        self.assertTrue(task_script_api.is_inventory_full_for_woodcutting(source))
        self.assertTrue(task_script_api.should_route_to_bank(source))

    def test_script_helpers_expose_woodcutting_and_route_monitor(self):
        source = {
            "woodcutting_lifecycle": {
                "schema": "woodcutting_lifecycle.v1",
                "status": "PASS",
                "phase": "cutting",
                "confidence": 0.9,
                "inventory": {"normalLogsGained": 3, "inventoryFull": False, "freeSlotsEnd": 25},
                "animation": {"activeSnapshotCount": 2},
                "clicks": {"freshChopClickCount": 1},
                "current": {"normalLogs": 3, "freeSlots": 25, "animationActive": True},
            },
            "route_monitor": {
                "schema": "route_monitor_status.v1",
                "status": "PASS",
                "routeName": "Bank_to_Woodcutting_area",
                "routeState": "in_progress",
                "currentSegmentIndex": 1,
                "currentSegmentLabel": "Walk toward staircase",
                "nextExpectedSegment": {"segmentIndex": 2, "segmentType": "stair_transition", "label": "Climb-up Staircase"},
                "completedSegmentCount": 1,
                "remainingSegmentCount": 4,
                "offRoute": False,
            },
        }
        woodcutting = task_script_api.get_woodcutting_lifecycle(source)
        route_status = task_script_api.get_route_monitor_status(source)

        self.assertEqual(woodcutting["phase"], "cutting")
        self.assertEqual(route_status["routeState"], "in_progress")
        self.assertFalse(task_script_api.is_off_route(source))
        self.assertEqual(task_script_api.get_current_route_segment(source)["label"], "Walk toward staircase")
        self.assertEqual(task_script_api.get_next_route_segment(source)["segmentType"], "stair_transition")

    def test_route_demonstration_helpers_resolve_next_recorded_step(self):
        with TemporaryDirectory() as tmp:
            guide_path = Path(tmp) / "woodcutting_area_to_bank.route_guide.json"
            guide_path.write_text(
                json.dumps(
                    {
                        "schema": "route_demonstration_guide.v1",
                        "status": "PASS",
                        "routeName": "woodcutting_area_to_bank",
                        "sourceRecordings": ["synthetic"],
                        "pathPoints": [
                            {"orderIndex": 0, "world": {"worldX": 3203, "worldY": 3238, "plane": 0}, "reachedToleranceTiles": 2},
                            {"orderIndex": 1, "world": {"worldX": 3208, "worldY": 3212, "plane": 0}, "reachedToleranceTiles": 2},
                        ],
                        "interactionSteps": [
                            {
                                "orderIndex": 0,
                                "segmentIndex": 1,
                                "action": "Climb-down",
                                "target": {"name": "Trapdoor"},
                                "world": {"worldX": 3209, "worldY": 3216, "plane": 0},
                                "planeBefore": 0,
                                "planeAfter": 2,
                                "cameraHints": [{"segmentId": "cam_001"}],
                                "postcondition": {"type": "plane_change", "planeChanged": True},
                            }
                        ],
                        "planeChanges": [{"startPlane": 0, "endPlane": 2}],
                        "cameraHints": [{"segmentId": "cam_001"}],
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )

            guide = task_script_api.get_route_demonstration_guide("woodcutting_area_to_bank", guide_dir=tmp)
            progress = task_script_api.get_route_guide_progress(
                "woodcutting_area_to_bank",
                {"worldX": 3203, "worldY": 3238, "plane": 0},
                guide_dir=tmp,
            )
            reentry = task_script_api.get_route_guide_reentry(
                "woodcutting_area_to_bank",
                {"worldX": 3203, "worldY": 3238, "plane": 1},
                guide_dir=tmp,
            )

        self.assertTrue(guide["routeGuideLoaded"])
        self.assertEqual(guide["pathPointCount"], 2)
        self.assertEqual(guide["interactionSteps"][0]["targetName"], "Trapdoor")
        self.assertEqual(progress["status"], "PASS")
        self.assertEqual(progress["nextGuidePoint"]["world"], {"worldX": 3208, "worldY": 3212, "plane": 0})
        self.assertIn(0, progress["skippedReachedGuidePoints"])
        self.assertEqual(reentry["status"], "WARN")
        self.assertEqual(reentry["blocker"], "route_guide_no_same_plane_reentry")
        self.assertTrue(reentry["routeGuideReentryAttempted"])

    def test_click_planning_helpers_return_warn_without_target_and_plan_with_target(self):
        missing = task_script_api.get_next_click_plan({"humanClickProfile": {"status": "PASS", "landing": {"medianAimDistancePx": 20}}})
        self.assertEqual(missing["status"], "WARN")
        self.assertIn("target_missing", missing["readiness"]["blockedReasons"])

        plan = task_script_api.get_human_click_plan(
            target={
                "name": "Tree",
                "targetQuality": "strong",
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"x": 100, "y": 120},
            },
            action="Chop down",
            activity="woodcutting",
            source={"humanClickProfile": {"status": "PASS", "landing": {"medianAimDistancePx": 20, "p75AimDistancePx": 30}}},
        )
        self.assertEqual(plan["status"], "PASS")
        self.assertNotEqual(plan["aim"]["basePoint"], plan["aim"]["plannedPoint"])

    def test_run_readiness_uses_woodcutting_loop_next_phase(self):
        runtime = runtime_snapshot(
            {
                "loadedScene": {"loadedSceneVerified": True},
                "inventory": {"freeSlots": 0, "inventoryFull": True},
                "bankOpen": False,
                "woodcuttingLoopLifecycle": {
                    "status": "PASS",
                    "loopState": "inventory_full",
                    "currentPhase": "inventory_full",
                    "nextExpectedPhase": "route_to_bank",
                },
            }
        )
        readiness = task_script_api.assess_task_run_readiness(
            load_example(),
            runtime_evidence=runtime,
            action_input_visibility=action_visibility_snapshot(planned_action="Chop down"),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )

        inferred = readiness["data"]["inferredNextPrimitive"]
        self.assertEqual(inferred["primitive"], "bank")
        self.assertEqual(inferred["reason"], "woodcutting_loop_next_expected_phase_route_to_bank")

    def test_run_readiness_reports_off_route_before_next_phase(self):
        runtime = runtime_snapshot(
            {
                "loadedScene": {"loadedSceneVerified": True},
                "routeMonitor": {"routeState": "off_route", "offRoute": True},
                "woodcuttingLoopLifecycle": {
                    "status": "PASS",
                    "loopState": "routing_to_bank",
                    "currentPhase": "routing_to_bank",
                    "nextExpectedPhase": "banking_deposit",
                },
            }
        )
        readiness = task_script_api.assess_task_run_readiness(
            load_example(),
            runtime_evidence=runtime,
            action_input_visibility=action_visibility_snapshot(planned_action="walk_to"),
            failure_classification=clean_failure_classification(),
            navigation_decision_trace=navigation_trace_snapshot(),
        )

        inferred = readiness["data"]["inferredNextPrimitive"]
        self.assertEqual(inferred["primitive"], "wait_for_evidence")
        self.assertEqual(inferred["reason"], "route_monitor_reports_off_route")


if __name__ == "__main__":
    unittest.main()

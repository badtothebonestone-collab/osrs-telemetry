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

    def test_knowledge_fabric_direct_methods_expose_script_api(self):
        fabric = make_fabric()

        validation = fabric.validate_task_script(load_example())
        plan = fabric.compile_task_script(load_example())
        template = fabric.suggest_task_template("woodcutting and bank logs", profile="woodcutting")
        scene_probe = fabric.probe_task_from_scene("woodcutting and bank logs", profile="woodcutting", limit=5)

        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(plan["schema"], "task_script_plan.v1")
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
        self.assertIn("suggest_task_template", tool_names)
        self.assertIn("probe_task_from_scene", tool_names)
        self.assertTrue(forbidden_raw_names.isdisjoint(tool_names))
        self.assertIn("osrs://script-api/spec", resource_uris)
        self.assertIn("osrs://script-api/woodcut-bank-example", resource_uris)

        fabric = make_fabric()
        with patch.object(mcp_server, "_fabric", return_value=fabric):
            spec_payload = json.loads(mcp_server.call_tool("get_task_script_api_spec", {})["content"][0]["text"])
            compile_payload = json.loads(mcp_server.call_tool("compile_task_script", {"script": load_example()})["content"][0]["text"])
            probe_payload = json.loads(
                mcp_server.call_tool("probe_task_from_scene", {"taskDescription": "woodcutting and bank logs", "limit": 3})["content"][0]["text"]
            )

        self.assertEqual(spec_payload["schema"], "task_script_api_spec.v1")
        self.assertEqual(compile_payload["schema"], "task_script_plan.v1")
        self.assertEqual(probe_payload["schema"], "task_scene_probe.v1")


if __name__ == "__main__":
    unittest.main()

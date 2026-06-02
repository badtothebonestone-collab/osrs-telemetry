from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import task_policy


TASK_SCRIPT_SCHEMA = "task_script.v1"
TASK_SCRIPT_SPEC_SCHEMA = "task_script_api_spec.v1"
TASK_SCRIPT_VALIDATION_SCHEMA = "task_script_validation.v1"
TASK_SCRIPT_PLAN_SCHEMA = "task_script_plan.v1"
TASK_SCRIPT_EXPLANATION_SCHEMA = "task_script_explanation.v1"
TASK_TEMPLATE_SUGGESTION_SCHEMA = "task_template_suggestion.v1"

CANONICAL_PIPELINE = [
    "action proposal",
    "readiness",
    "hover/menu proof",
    "HumanInputController",
    "ArduinoHIDBackend",
    "input integrity",
    "lifecycle verification",
]

PHASE_AWARE_INPUT_POLICY = {
    "operator_phase": "Computer Use/manual/debug input may create operatorInjectedEvents; these do not fail the script.",
    "pre_live_phase": "STOP_ALL/DISARM/STATUS, reset or rebaseline input integrity, verify Arduino/Raw Input/COM status.",
    "live_action_phase": "All live input must go through HumanInputController -> ArduinoHIDBackend; injected/lower-IL deltas or direct backend bypasses are hard blockers.",
    "post_live_phase": "STOP_ALL/DISARM/STATUS, report injected/lower-IL deltas only for the live action window.",
}

EXTERNAL_KNOWLEDGE_POLICY = {
    "advisoryOnly": True,
    "liveTruth": "RuneLite / 8893 / WorldModel / 8890",
    "cacheFirst": True,
    "explicitRefreshOnly": True,
    "hotExecutorExternalCallsAllowed": False,
    "mustNotOverrideFreshLiveEvidence": True,
}

FAILURE_CLASSIFICATIONS = [
    "code/data truth bug",
    "coordinate_transform_error",
    "arduino_movement_error",
    "target_aimpoint_error",
    "target/hover/menu mismatch",
    "stale liveness/plugin bug",
    "game-state/user-login blocker",
    "external knowledge/cache miss",
    "operator-phase injected-input noise",
    "runtime file/disk issue",
]

BOUNDED_OPERATOR_REQUESTS = {
    "collect": "request_bounded_live_step",
    "interact": "request_bounded_live_step",
    "walk_to": "request_bounded_live_step",
    "bank": "request_bounded_live_step",
    "deposit": "request_bounded_live_step",
    "close_bank": "request_bounded_live_step",
    "return_to_resource": "request_bounded_live_step",
    "wait_for_evidence": "request_watcher_step",
    "recover_loaded_scene": "request_liveness_recovery",
    "repeat_until": "request_watcher_step",
}

PRIMITIVE_SPECS: dict[str, dict[str, Any]] = {
    "collect": {
        "description": "Select a live resource candidate from an existing target profile.",
        "required": ["targetProfile"],
        "optional": ["targetClass", "resource", "action", "evidence", "until"],
        "emitsActionProposal": "select_resource_target",
        "engineIntent": "resource_object_action",
        "defaultEvidence": ["resource_candidate_live", "safe_aimpoint_pass", "hover_menu_contains_resource_action", "resource_progress_or_inventory_delta"],
    },
    "interact": {
        "description": "Interact with a live object/NPC/widget already proven by candidate/readiness evidence.",
        "required": ["target"],
        "optional": ["action", "targetClass", "serviceType", "routeId", "evidence"],
        "emitsActionProposal": "interact_service_route_object",
        "engineIntent": "service_object_action",
        "defaultEvidence": ["target_candidate_live", "hover_menu_matches_action", "MenuOptionClicked_expected"],
    },
    "walk_to": {
        "description": "Ask the existing navigation/route stack for a bounded waypoint action.",
        "required": ["destination"],
        "optional": ["routeId", "arrivalRadiusTiles", "evidence"],
        "emitsActionProposal": "navigate_to_service",
        "engineIntent": "navigation_waypoint_action",
        "defaultEvidence": ["path_frontier_available", "waypoint_projected", "position_or_route_progress"],
    },
    "bank": {
        "description": "Navigate to and open a bank/deposit service through service-route evidence.",
        "required": [],
        "optional": ["serviceType", "routeId", "evidence"],
        "emitsActionProposal": "open_service",
        "engineIntent": "service_object_action",
        "defaultEvidence": ["service_candidate_live", "bank_ui_open"],
    },
    "deposit": {
        "description": "Run the existing bank operation analyzer action.",
        "required": [],
        "optional": ["operation", "evidence"],
        "emitsActionProposal": "deposit_inventory",
        "engineIntent": "bank_operation_action",
        "defaultEvidence": ["bank_ui_open", "deposit_inventory_available", "no_resource_items_held"],
    },
    "close_bank": {
        "description": "Close the bank interface through existing close-bank context.",
        "required": [],
        "optional": ["evidence"],
        "emitsActionProposal": "close_bank",
        "engineIntent": "close_bank_action",
        "defaultEvidence": ["close_bank_ready", "bank_ui_closed"],
    },
    "return_to_resource": {
        "description": "Return from service to the remembered resource/worksite area.",
        "required": [],
        "optional": ["resourceProfile", "routeId", "resourceArea", "evidence"],
        "emitsActionProposal": "return_to_resource_area",
        "engineIntent": "return_to_resource_area",
        "defaultEvidence": ["return_destination_available", "worksite_reacquired", "resource_candidate_live"],
    },
    "wait_for_evidence": {
        "description": "Wait for live evidence rather than issuing input.",
        "required": ["evidence"],
        "optional": ["timeoutTicks"],
        "emitsActionProposal": "wait_for_context",
        "engineIntent": "evidence_wait",
        "defaultEvidence": [],
    },
    "recover_loaded_scene": {
        "description": "Request bounded liveness recovery such as ensure_loaded_scene.",
        "required": [],
        "optional": ["when", "evidence"],
        "emitsActionProposal": "none",
        "engineIntent": "liveness_recovery",
        "defaultEvidence": ["loaded_scene_verified", "client_tick_fresh", "world_model_fresh"],
    },
    "repeat_until": {
        "description": "Bounded control flow over high-level primitives.",
        "required": ["condition", "maxIterations", "steps"],
        "optional": ["evidence"],
        "emitsActionProposal": "wait_for_context",
        "engineIntent": "bounded_loop",
        "defaultEvidence": ["loop_condition_rechecked_from_live_state"],
    },
}

ALLOWED_PRIMITIVES = tuple(PRIMITIVE_SPECS.keys())
RAW_INPUT_PRIMITIVES = {
    "click",
    "raw_click",
    "mouse_click",
    "mouseDown",
    "mouseUp",
    "mouse_down",
    "mouse_up",
    "keyDown",
    "keyUp",
    "key_down",
    "key_up",
    "press",
    "type",
    "pyautogui",
    "pydirectinput",
    "arduino_write",
}
RAW_INPUT_FIELDS = {
    "screenPoint",
    "clickPoint",
    "plannedScreenPoint",
    "canvasPoint",
    "mouseDown",
    "mouseUp",
    "keyDown",
    "keyUp",
    "key",
    "keys",
    "hotkey",
    "pixels",
}
RAW_INPUT_PRIMITIVE_NAMES = {item.lower() for item in RAW_INPUT_PRIMITIVES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _norm(value: Any) -> str:
    return _str(value).strip().lower()


def _error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _warning(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _primitive_name(step: dict[str, Any]) -> str:
    text = str(step.get("primitive") or step.get("op") or "").strip()
    lower = text.lower()
    if text in PRIMITIVE_SPECS:
        return text
    if lower in PRIMITIVE_SPECS:
        return lower
    return text


def _loads_script(script: dict[str, Any] | str | Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if isinstance(script, dict):
        return deepcopy(script), []
    if isinstance(script, Path):
        try:
            return _loads_script(script.read_text(encoding="utf-8"))
        except OSError as error:
            return {}, [_error("$", "runtime_file_disk_issue", f"could not read script: {error}")]
    if isinstance(script, str):
        text = script.strip()
        if not text:
            return {}, [_error("$", "missing_script", "script is empty")]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            return {}, [_error("$", "invalid_json", f"script is not valid JSON: {error}")]
        if isinstance(value, dict):
            return value, []
        return {}, [_error("$", "invalid_script_type", "script JSON must be an object")]
    return {}, [_error("$", "invalid_script_type", "script must be a dict, JSON string, or path")]


def _step_path(parent: str, index: int) -> str:
    return f"{parent}.steps[{index}]" if parent else f"steps[{index}]"


def _evidence_list(step: dict[str, Any], primitive: str) -> list[str]:
    evidence = step.get("evidence")
    if isinstance(evidence, str):
        return [evidence]
    if isinstance(evidence, list):
        return [str(item) for item in evidence if str(item or "").strip()]
    return list(PRIMITIVE_SPECS[primitive].get("defaultEvidence") or [])


def _validate_step(step: Any, path: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]) -> None:
    if not isinstance(step, dict):
        errors.append(_error(path, "invalid_step_type", "step must be an object"))
        return
    primitive = str(step.get("primitive") or step.get("op") or "").strip()
    primitive_norm = _primitive_name(step)
    if primitive_norm in RAW_INPUT_PRIMITIVES or primitive_norm.lower() in RAW_INPUT_PRIMITIVE_NAMES:
        errors.append(_error(f"{path}.primitive", "raw_input_bypass_forbidden", "raw input primitives bypass tracing and are not allowed"))
        return
    if primitive_norm not in PRIMITIVE_SPECS:
        errors.append(_error(f"{path}.primitive", "unknown_primitive", f"unknown primitive: {primitive or '<missing>'}"))
        return
    for key in sorted(RAW_INPUT_FIELDS.intersection(step.keys())):
        errors.append(_error(f"{path}.{key}", "raw_input_field_forbidden", "high-level scripts may not carry raw screen/canvas/key input fields"))
    spec = PRIMITIVE_SPECS[primitive_norm]
    for field in spec.get("required", []):
        value = step.get(field)
        missing = value is None or value == "" or value == []
        if missing:
            errors.append(_error(f"{path}.{field}", "missing_required_field", f"{primitive_norm} requires {field}"))
    evidence = step.get("evidence")
    if evidence is not None and not isinstance(evidence, (str, list)):
        errors.append(_error(f"{path}.evidence", "invalid_evidence", "evidence must be a string or list of strings"))
    if primitive_norm == "wait_for_evidence":
        if not _evidence_list(step, primitive_norm):
            errors.append(_error(f"{path}.evidence", "missing_evidence", "wait_for_evidence needs at least one evidence gate"))
    if primitive_norm == "repeat_until":
        max_iterations = step.get("maxIterations")
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1 or max_iterations > 100:
            errors.append(_error(f"{path}.maxIterations", "unbounded_loop_forbidden", "repeat_until requires maxIterations between 1 and 100"))
        children = step.get("steps")
        if not isinstance(children, list) or not children:
            errors.append(_error(f"{path}.steps", "missing_loop_steps", "repeat_until requires a non-empty steps list"))
        else:
            for index, child in enumerate(children):
                _validate_step(child, _step_path(path, index), errors, warnings)
    if primitive_norm in {"collect", "interact", "bank", "deposit", "close_bank", "return_to_resource"} and not _evidence_list(step, primitive_norm):
        warnings.append(_warning(f"{path}.evidence", "default_evidence_applied", f"{primitive_norm} will use default evidence gates"))
    if step.get("allowExternalRefreshInLivePath") is True:
        errors.append(_error(f"{path}.allowExternalRefreshInLivePath", "external_refresh_in_hot_path_forbidden", "external refresh is not allowed in the live executor path"))
    if primitive_norm == "walk_to" and isinstance(step.get("destination"), dict):
        destination = _dict(step.get("destination"))
        if {"screenX", "screenY", "canvasX", "canvasY"}.intersection(destination.keys()):
            errors.append(_error(f"{path}.destination", "screen_coordinate_destination_forbidden", "walk_to destinations must be world/route/service labels, not screen or canvas points"))
    if step.get("routeId"):
        warnings.append(_warning(f"{path}.routeId", "static_route_is_advisory", "routeId is a static/session prior until live route evidence verifies it"))


def validate_task_script(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    payload, load_errors = _loads_script(script)
    errors = list(load_errors)
    warnings: list[dict[str, str]] = []
    if payload:
        schema = payload.get("schema")
        if schema and schema != TASK_SCRIPT_SCHEMA:
            warnings.append(_warning("$.schema", "unexpected_schema", f"expected {TASK_SCRIPT_SCHEMA}, got {schema}"))
        if not str(payload.get("name") or "").strip():
            errors.append(_error("$.name", "missing_name", "script needs a stable name"))
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(_error("$.steps", "missing_steps", "script needs a non-empty steps list"))
        else:
            for index, step in enumerate(steps):
                _validate_step(step, _step_path("$", index), errors, warnings)
        if payload.get("allowExternalRefreshInLivePath") is True:
            errors.append(_error("$.allowExternalRefreshInLivePath", "external_refresh_in_hot_path_forbidden", "external refresh is not allowed in the live executor path"))
        if payload.get("rawInputAllowed") is True:
            errors.append(_error("$.rawInputAllowed", "raw_input_bypass_forbidden", "raw arbitrary input is not part of the task script API"))
    return {
        "schema": TASK_SCRIPT_VALIDATION_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "generatedAtUtc": utc_now(),
        "valid": not errors,
        "scriptName": payload.get("name") if payload else None,
        "primitiveCount": len(_list(payload.get("steps"))) if payload else 0,
        "allowedPrimitives": list(ALLOWED_PRIMITIVES),
        "errors": errors,
        "warnings": warnings,
        "noLiveInput": True,
        "rawInputBypassToolsExposed": False,
        "directBackendBypassCountRequired": 0,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
    }


def _policy_for(script: dict[str, Any]) -> task_policy.TaskPolicy:
    return task_policy.resolve_task_policy(
        script.get("policy") or script.get("taskPolicy"),
        task=script.get("task"),
        profile=script.get("profile"),
    )


def _primitive_target(step: dict[str, Any], primitive: str) -> dict[str, Any]:
    if primitive == "collect":
        return {
            "profile": step.get("targetProfile"),
            "targetClass": step.get("targetClass"),
            "resource": step.get("resource"),
            "action": step.get("action") or "Chop down",
        }
    if primitive == "walk_to":
        return {"destination": step.get("destination"), "routeId": step.get("routeId"), "arrivalRadiusTiles": step.get("arrivalRadiusTiles")}
    if primitive == "bank":
        return {"serviceType": step.get("serviceType") or "bank", "routeId": step.get("routeId")}
    if primitive == "deposit":
        return {"operation": step.get("operation") or "deposit_inventory"}
    if primitive == "return_to_resource":
        return {"resourceProfile": step.get("resourceProfile"), "resourceArea": step.get("resourceArea"), "routeId": step.get("routeId")}
    if primitive == "interact":
        return {"target": step.get("target"), "targetClass": step.get("targetClass"), "action": step.get("action"), "routeId": step.get("routeId")}
    if primitive == "recover_loaded_scene":
        return {"when": step.get("when") or "loaded_scene_not_verified"}
    if primitive == "wait_for_evidence":
        return {"evidence": _evidence_list(step, primitive), "timeoutTicks": step.get("timeoutTicks")}
    return {}


def _action_for_step(step: dict[str, Any], primitive: str) -> str:
    if primitive == "interact":
        text = " ".join([_norm(step.get("target")), _norm(step.get("targetClass")), _norm(step.get("action")), _norm(step.get("serviceType"))])
        if any(token in text for token in ("bank", "deposit", "service")):
            return "open_service"
        if any(token in text for token in ("tree", "oak", "willow", "chop")):
            return "select_resource_target"
    return str(PRIMITIVE_SPECS[primitive]["emitsActionProposal"])


def _compile_step(step: dict[str, Any], path: str, index: int) -> dict[str, Any]:
    primitive = _primitive_name(step)
    spec = PRIMITIVE_SPECS[primitive]
    compiled: dict[str, Any] = {
        "stepIndex": index,
        "sourcePath": path,
        "primitive": primitive,
        "engineIntent": spec.get("engineIntent"),
        "actionProposalAction": _action_for_step(step, primitive),
        "boundedOperatorRequest": BOUNDED_OPERATOR_REQUESTS[primitive],
        "target": _primitive_target(step, primitive),
        "requiredEvidence": _evidence_list(step, primitive),
        "readinessGate": {
            "query": "get_action_input_visibility" if primitive not in {"wait_for_evidence", "recover_loaded_scene"} else "get_current_debug_context",
            "mustPassBeforeLiveInput": primitive not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"},
            "directBackendBypassCountMustRemain": 0,
        },
        "canonicalPipeline": CANONICAL_PIPELINE if primitive not in {"wait_for_evidence", "recover_loaded_scene", "repeat_until"} else [],
        "failureClassificationHints": FAILURE_CLASSIFICATIONS,
        "liveTruthSources": ["RuneLite 8893", "WorldModel", "8890 daemon", "client_tick_hot"],
        "externalFactsMayEnrich": True,
        "externalFactsAdvisoryOnly": True,
    }
    if primitive == "recover_loaded_scene":
        compiled["livenessRecovery"] = {
            "requestedAction": "ensure_loaded_scene",
            "manualLoginRequiredIsBlocker": True,
            "knownFlowsShouldUse": "request_liveness_recovery",
        }
    if primitive == "repeat_until":
        children = _list(step.get("steps"))
        compiled["condition"] = step.get("condition")
        compiled["maxIterations"] = step.get("maxIterations")
        compiled["steps"] = [_compile_step(child, _step_path(path, child_index), child_index) for child_index, child in enumerate(children) if isinstance(child, dict)]
    return compiled


def _flatten_plan_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for step in steps:
        flattened.append(step)
        flattened.extend(_flatten_plan_steps(_list(step.get("steps"))))
    return flattened


def compile_task_script(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    payload, load_errors = _loads_script(script)
    validation = validate_task_script(payload if payload else script)
    if load_errors and not validation.get("errors"):
        validation["errors"] = load_errors
        validation["valid"] = False
        validation["status"] = "FAIL"
    if validation.get("status") != "PASS":
        return {
            "schema": TASK_SCRIPT_PLAN_SCHEMA,
            "status": "FAIL",
            "generatedAtUtc": utc_now(),
            "validation": validation,
            "actionPlan": [],
            "noLiveInput": True,
        }
    policy = _policy_for(payload)
    steps = [_compile_step(step, _step_path("$", index), index) for index, step in enumerate(_list(payload.get("steps"))) if isinstance(step, dict)]
    flattened = _flatten_plan_steps(steps)
    action_intents = list(dict.fromkeys(str(step.get("actionProposalAction")) for step in flattened if step.get("actionProposalAction") not in {None, ""}))
    data = {
        "script": {
            "schema": payload.get("schema") or TASK_SCRIPT_SCHEMA,
            "name": payload.get("name"),
            "description": payload.get("description"),
            "profile": payload.get("profile") or policy.profile,
            "task": payload.get("task") or policy.task,
        },
        "taskPolicy": policy.to_dict(),
        "actionPlan": steps,
        "flattenedActionPlan": flattened,
        "actionProposalActions": action_intents,
        "executorContract": {
            "usesExistingExecutorOnly": True,
            "liveInputBackend": "HumanInputController -> ArduinoHIDBackend",
            "directBackendBypassCountMustRemain": 0,
            "rawArbitraryInputToolsAllowed": False,
            "boundedOperatorRequestsAllowed": list(dict.fromkeys(BOUNDED_OPERATOR_REQUESTS.values())),
            "canonicalPipeline": CANONICAL_PIPELINE,
        },
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "noLiveInput": True,
    }
    return {
        "schema": TASK_SCRIPT_PLAN_SCHEMA,
        "status": "PASS",
        "generatedAtUtc": utc_now(),
        "validation": validation,
        "data": data,
        "noLiveInput": True,
    }


def explain_script_plan(script: dict[str, Any] | str | Path) -> dict[str, Any]:
    plan = compile_task_script(script)
    data = {
        "summary": "High-level task primitives compile into existing profile/action proposal intents; live motor control stays in the canonical Arduino-backed executor pipeline.",
        "validation": plan.get("validation"),
        "plan": plan.get("data") or {"actionPlan": []},
        "primitiveReference": PRIMITIVE_SPECS,
        "canonicalPipeline": CANONICAL_PIPELINE,
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassificationPolicy": FAILURE_CLASSIFICATIONS,
        "readinessChecklist": [
            "loaded scene verified from RuneLite/8893/WorldModel/8890",
            "actionReadiness PASS for the proposed live action",
            "hover/menu evidence matches the intended target/action",
            "input integrity reset or rebaseline completed before live action",
            "directBackendBypassCount remains 0",
            "post-live lifecycle proof observed",
        ],
        "noLiveInput": True,
    }
    return {
        "schema": TASK_SCRIPT_EXPLANATION_SCHEMA,
        "status": plan.get("status", "WARN"),
        "generatedAtUtc": utc_now(),
        "data": data,
        "noLiveInput": True,
    }


def woodcut_bank_template() -> dict[str, Any]:
    return {
        "schema": TASK_SCRIPT_SCHEMA,
        "name": "woodcut_bank",
        "description": "Collect woodcutting resources, bank them, close the bank, and return to the resource area through the existing engine.",
        "profile": "woodcutting",
        "task": "woodcutting",
        "policy": "woodcutting_bank",
        "externalKnowledge": {
            "use": "advisory_static_enrichment_only",
            "sourceExamples": ["OSRS Wiki item/object IDs", "skill requirements", "area labels", "route/service priors"],
            "cacheFirst": True,
            "hotExecutorExternalCallsAllowed": False,
        },
        "steps": [
            {
                "primitive": "recover_loaded_scene",
                "when": "loaded_scene_stale",
                "evidence": ["loaded_scene_verified", "client_tick_fresh", "world_model_fresh"],
            },
            {
                "primitive": "repeat_until",
                "condition": "inventory_full",
                "maxIterations": 28,
                "evidence": ["inventory_state_rechecked_from_live_context"],
                "steps": [
                    {
                        "primitive": "collect",
                        "targetProfile": "woodcutting",
                        "targetClass": "tree",
                        "action": "Chop down",
                        "evidence": [
                            "resource_candidate_live",
                            "safe_aimpoint_pass",
                            "hover_menu_contains_Chop_down",
                            "MenuOptionClicked_Chop_down_or_resource_progress",
                        ],
                    },
                    {
                        "primitive": "wait_for_evidence",
                        "evidence": ["resource_progress", "inventory_free_slots_decrease", "target_depleted_or_next_candidate"],
                        "timeoutTicks": 12,
                    },
                ],
            },
            {
                "primitive": "bank",
                "serviceType": "bank",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "evidence": ["service_candidate_live", "route_or_waypoint_progress", "bank_ui_open"],
            },
            {
                "primitive": "deposit",
                "operation": "deposit_inventory",
                "evidence": ["bank_ui_open", "deposit_inventory_available", "no_resource_items_held", "banking_complete"],
            },
            {
                "primitive": "close_bank",
                "evidence": ["close_bank_ready", "bank_ui_closed"],
            },
            {
                "primitive": "return_to_resource",
                "resourceProfile": "woodcutting",
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "evidence": ["return_destination_available", "worksite_reacquired", "resource_candidate_live"],
            },
            {
                "primitive": "wait_for_evidence",
                "evidence": ["action_readiness_pass", "resource_target_visible"],
                "timeoutTicks": 10,
            },
        ],
    }


def suggest_task_template(task_description: str | None = None, *, profile: str | None = None) -> dict[str, Any]:
    text = f"{task_description or ''} {profile or ''}".lower()
    if any(token in text for token in ("woodcut", "tree", "logs", "bank", "deposit")):
        template = woodcut_bank_template()
        template_name = "woodcut_bank"
        reason = "description_matches_woodcut_bank"
    else:
        template = {
            "schema": TASK_SCRIPT_SCHEMA,
            "name": "new_task",
            "description": task_description or "New high-level task",
            "profile": profile or "observe",
            "task": profile or "observe",
            "policy": task_policy.default_policy_name(profile=profile),
            "steps": [
                {"primitive": "recover_loaded_scene", "when": "loaded_scene_stale"},
                {"primitive": "wait_for_evidence", "evidence": ["loaded_scene_verified", "action_readiness_pass"], "timeoutTicks": 10},
            ],
        }
        template_name = "generic_observe_first"
        reason = "generic_safe_observe_template"
    validation = validate_task_script(template)
    plan = compile_task_script(template)
    return {
        "schema": TASK_TEMPLATE_SUGGESTION_SCHEMA,
        "status": validation["status"],
        "generatedAtUtc": utc_now(),
        "data": {
            "templateName": template_name,
            "reason": reason,
            "template": template,
            "validation": validation,
            "compiledPlanSummary": {
                "schema": plan.get("schema"),
                "status": plan.get("status"),
                "actionProposalActions": _dict(plan.get("data")).get("actionProposalActions"),
                "noLiveInput": True,
            },
            "source": {
                "type": "built_in_template",
                "provenance": "telemetry-viewer/task_script_api.py",
                "externalKnowledge": EXTERNAL_KNOWLEDGE_POLICY,
            },
        },
        "noLiveInput": True,
    }


def script_api_spec() -> dict[str, Any]:
    return {
        "schema": TASK_SCRIPT_SPEC_SCHEMA,
        "generatedAtUtc": utc_now(),
        "scriptSchema": TASK_SCRIPT_SCHEMA,
        "allowedPrimitives": list(ALLOWED_PRIMITIVES),
        "primitives": PRIMITIVE_SPECS,
        "canonicalPipeline": CANONICAL_PIPELINE,
        "boundedOperatorRequests": BOUNDED_OPERATOR_REQUESTS,
        "forbiddenRawInputPrimitives": sorted(RAW_INPUT_PRIMITIVES),
        "forbiddenRawInputFields": sorted(RAW_INPUT_FIELDS),
        "phaseAwareInputIntegrityPolicy": PHASE_AWARE_INPUT_POLICY,
        "externalKnowledgePolicy": EXTERNAL_KNOWLEDGE_POLICY,
        "failureClassifications": FAILURE_CLASSIFICATIONS,
        "example": woodcut_bank_template(),
    }

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

import brain_core
import intent_stabilizer
import task_policy
from analyzers import intent_overlay_analyzer
from analyzers import bank_operation_analyzer
from analyzers import bank_ui_analyzer
from analyzers import navigation_intent_analyzer
from analyzers import process_inventory_analyzer
from analyzers import return_to_resource_analyzer
from analyzers import service_analyzer
from analyzers.live_state import BankOperationContext, BankUiContext, InventoryContext, NavigationContext, ProcessInventoryContext, ReturnToResourceContext, ServiceContext, TargetContext


SCHEMA = "task_transition_diagnostic.v1"

SCENARIOS = (
    "woodcutting_inventory_full",
    "woodcutting_not_full",
    "service_visible",
    "service_visible_not_arrived",
    "service_visible_arrived",
    "service_ready_from_daemon",
    "service_ready_bank_closed",
    "service_open",
    "service_complete",
    "service_complete_no_target",
    "bank_pin_required",
    "service_missing",
    "firemake_ready",
    "firemake_no_tree_candidates",
    "firemake_full_inventory_no_candidates_live_style",
    "drop_ready",
    "drop_no_tree_candidates",
    "combat_full_inventory",
)

def tree_candidate() -> dict[str, Any]:
    return {
        "objectKey": "tree-1278-3156-3237",
        "targetType": "sceneObject",
        "classId": "tree",
        "targetName": "Tree",
        "name": "Tree",
        "id": 1278,
        "worldX": 3156,
        "worldY": 3237,
        "plane": 0,
        "sceneX": 52,
        "sceneY": 53,
        "distanceTiles": 2,
        "onScreen": True,
        "geometryAvailable": True,
        "targetLiveState": "live_assumed",
        "livenessInterpretation": "assumed",
        "qualityScore": 90,
        "qualityTier": "excellent",
        "navigation": {
            "directReachability": "reachable",
            "pathLengthTiles": 2,
            "targetInCollisionWindow": True,
        },
        "aimPoint": {"canvasX": 220, "canvasY": 180, "source": "clickboxCenter"},
    }


def bank_booth_candidate() -> dict[str, Any]:
    return {
        "objectKey": "bank-booth-10355-3208-3219",
        "targetType": "sceneObject",
        "classId": "bank_booth",
        "targetName": "Bank booth",
        "name": "Bank booth",
        "id": 10355,
        "worldX": 3208,
        "worldY": 3219,
        "plane": 0,
        "sceneX": 44,
        "sceneY": 45,
        "distanceTiles": 6,
        "onScreen": True,
        "geometryAvailable": True,
        "targetLiveState": "live_assumed",
        "qualityScore": 95,
        "navigation": {
            "directReachability": "reachable",
            "pathLengthTiles": 6,
            "targetInCollisionWindow": True,
        },
        "aimPoint": {"canvasX": 310, "canvasY": 200, "source": "clickboxCenter"},
    }


def log_item(slot: int) -> dict[str, Any]:
    return {"slot": slot, "itemId": 1511, "quantity": 1}


def tinderbox_item(slot: int) -> dict[str, Any]:
    return {"slot": slot, "itemId": 590, "quantity": 1}


def inventory_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    filled = len(items)
    return {
        "known": True,
        "freeSlots": max(0, 28 - filled),
        "filledSlots": filled,
        "inventoryFull": filled >= 28,
        "inventorySlotCount": 28,
        "slotCount": 28,
        "changedRecently": False,
        "inventorySignature": "|".join(f"{item.get('slot')}:{item.get('itemId')}:{item.get('quantity', 1)}" for item in items),
        "items": items,
    }


def inventory_for_scenario(scenario: str, *, tinderbox_present: bool = True) -> dict[str, Any]:
    if scenario == "woodcutting_not_full":
        return inventory_from_items([log_item(slot) for slot in range(5)])
    if scenario in {"firemake_ready", "firemake_no_tree_candidates", "firemake_full_inventory_no_candidates_live_style"}:
        items = [log_item(slot) for slot in range(27)]
        if tinderbox_present:
            items.append(tinderbox_item(27))
        else:
            items.append(log_item(27))
        return inventory_from_items(items)
    if scenario in {"service_complete", "service_complete_no_target"}:
        return inventory_from_items([{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)])
    return inventory_from_items([log_item(slot) for slot in range(28)])


def candidates_for_scenario(scenario: str) -> list[dict[str, Any]]:
    if scenario in {"firemake_no_tree_candidates", "firemake_full_inventory_no_candidates_live_style", "drop_no_tree_candidates"}:
        return []
    if scenario == "service_complete_no_target":
        return [bank_booth_candidate()]
    candidates = [tree_candidate()]
    if scenario in {"service_visible", "service_visible_not_arrived", "service_visible_arrived", "service_ready_from_daemon", "service_ready_bank_closed", "service_open", "service_complete", "bank_pin_required"}:
        candidates.append(bank_booth_candidate())
    return candidates


def context_response_for_scenario(scenario: str, *, tinderbox_present: bool = True) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = candidates_for_scenario(scenario)
    tree = candidates[0] if candidates and candidates[0].get("classId") == "tree" else None
    response = {
        "schema": "context_response.v1",
        "status": "FAIL" if scenario == "firemake_full_inventory_no_candidates_live_style" else "PASS",
        "latestTick": 42,
        "freshness": {
            "freshByTicks": scenario not in {"firemake_no_tree_candidates", "firemake_full_inventory_no_candidates_live_style", "drop_no_tree_candidates"},
            "freshByMillis": True,
        },
        "baseline": {"player": {"worldX": 3155, "worldY": 3236, "plane": 0, "sceneX": 51, "sceneY": 52}},
        "inventory": inventory_for_scenario(scenario, tinderbox_present=tinderbox_present),
        "activity": {"apparentState": "idle", "animation": -1, "interacting": None},
        "navigationReadiness": {
            "status": "local",
            "collisionKnown": True,
            "collisionWindowAvailable": True,
            "reachabilityComputed": True,
        },
        "navigation": {"collisionWindowAvailable": True, "collisionKnown": True},
        "reachabilitySummary": {"tree": {"candidateCount": 1 if tree else 0, "reachableCount": 1 if tree else 0, "blockedCount": 0, "unknownCount": 0}},
        "liveness": {"livenessMode": "delta", "suppressedCandidateCount": 0, "livenessDegraded": False},
        "warnings": [],
        "missingCapabilities": ["target.candidates"] if scenario == "firemake_full_inventory_no_candidates_live_style" else ["navigation.full_pathfinding"],
        "recentEvents": [],
    }
    if tree:
        response["bestCandidates"] = {"tree": tree}
        response["nearestCandidates"] = {"tree": tree}
    return response, candidates


def scenario_task(policy_name: str, scenario: str) -> str:
    if policy_name == "combat_default" or scenario == "combat_full_inventory":
        return "combat"
    if policy_name == "observe_only":
        return "woodcutting"
    return "woodcutting"


def expected_for(policy_name: str, scenario: str, *, tinderbox_present: bool = True) -> dict[str, Any]:
    if scenario == "woodcutting_not_full":
        return {"phase": "target_selected", "activeIntent": "continue_current_target", "overlay": "selected_tree"}
    if policy_name == "combat_default" or scenario == "combat_full_inventory":
        return {"phase": "target_selected", "activeIntent": "continue_task", "overlay": "selected_tree"}
    if policy_name == "observe_only":
        return {"phase": "observe", "activeIntent": "observe", "overlay": "none"}
    if policy_name == "woodcutting_firemake" or scenario in {"firemake_ready", "firemake_no_tree_candidates", "firemake_full_inventory_no_candidates_live_style"}:
        return {"phase": "inventory_full", "activeIntent": "process_inventory", "overlay": "none"}
    if policy_name == "woodcutting_drop" or scenario in {"drop_ready", "drop_no_tree_candidates"}:
        return {"phase": "inventory_full", "activeIntent": "process_inventory", "overlay": "none"}
    if scenario in {"service_visible", "service_visible_not_arrived"}:
        return {"phase": "inventory_full", "activeIntent": "needs_service", "overlay": "selected_service"}
    if scenario == "service_open":
        return {"phase": "service_open", "activeIntent": "bank_operation_pending", "overlay": "selected_service"}
    if scenario == "service_complete":
        return {"phase": "target_selected", "activeIntent": "select_target", "overlay": "selected_tree"}
    if scenario == "service_complete_no_target":
        return {"phase": "needs_more_context", "activeIntent": "select_target", "overlay": "none"}
    if scenario == "bank_pin_required":
        return {"phase": "blocked", "activeIntent": "needs_user_resolution", "overlay": "selected_service"}
    if scenario in {"service_visible_arrived", "service_ready_from_daemon", "service_ready_bank_closed"}:
        return {"phase": "service_available", "activeIntent": "service_available", "overlay": "selected_service"}
    return {"phase": "inventory_full", "activeIntent": "needs_service", "overlay": "none"}


def target_label(target: dict[str, Any] | None) -> str | None:
    if not isinstance(target, dict) or not target:
        return None
    return str(target.get("targetName") or target.get("name") or target.get("classId") or target.get("id") or "target")


def compact_service_summary(context: ServiceContext) -> dict[str, Any]:
    payload = context.to_dict()
    best = payload.get("bestServiceCandidate") if isinstance(payload.get("bestServiceCandidate"), dict) else None
    return {
        "serviceNeeded": payload.get("serviceNeeded"),
        "serviceTypeNeeded": payload.get("serviceTypeNeeded"),
        "candidateCount": payload.get("candidateCount"),
        "best": target_label(best),
        "serviceReady": payload.get("serviceReady"),
        "serviceReadyReason": payload.get("serviceReadyReason"),
        "serviceReadyStableForTicks": payload.get("serviceReadyStableForTicks"),
        "reachableCount": payload.get("reachableCount"),
        "warnings": payload.get("warnings", []),
    }


def compact_process_summary(context: ProcessInventoryContext) -> dict[str, Any]:
    payload = context.to_dict()
    return {
        "processRequired": payload.get("processRequired"),
        "processTypeNeeded": payload.get("processTypeNeeded"),
        "resourceDisposition": payload.get("resourceDisposition"),
        "resourcesAvailable": payload.get("resourcesAvailable"),
        "heldResourceCount": payload.get("heldResourceCount"),
        "tinderboxStatus": payload.get("tinderboxStatus"),
        "warnings": payload.get("warnings", []),
    }


def compact_navigation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "navigationNeeded": payload.get("navigationNeeded"),
        "navigationReason": payload.get("navigationReason"),
        "targetKind": payload.get("targetKind"),
        "destination": target_label(payload.get("destinationTarget")),
        "directReachability": payload.get("directReachability"),
        "collisionWindowAvailable": payload.get("collisionWindowAvailable"),
        "missingCapabilities": payload.get("missingCapabilities", []),
    }


def compact_bank_ui_summary(context: BankUiContext) -> dict[str, Any]:
    payload = context.to_dict()
    return {
        "bankOpen": payload.get("bankOpen"),
        "bankReadable": payload.get("bankReadable"),
        "bankPinOpen": payload.get("bankPinOpen"),
        "bankRootVisible": payload.get("bankRootVisible"),
        "bankContainerVisible": payload.get("bankContainerVisible"),
        "bankInventoryVisible": payload.get("bankInventoryVisible"),
        "depositInventoryButtonVisible": payload.get("depositInventoryButtonVisible"),
        "closeButtonVisible": payload.get("closeButtonVisible"),
        "inventorySummary": payload.get("inventorySummary") or {},
        "bankSummary": payload.get("bankSummary") or {},
        "warnings": payload.get("warnings", []),
    }


def compact_bank_operation_summary(context: BankOperationContext) -> dict[str, Any]:
    payload = context.to_dict()
    return {
        "operationNeeded": payload.get("operationNeeded"),
        "operationType": payload.get("operationType"),
        "resourceItemsHeld": payload.get("resourceItemsHeld"),
        "resourceItemSlots": payload.get("resourceItemSlots") or [],
        "resourceItemQuantity": payload.get("resourceItemQuantity"),
        "nonResourceItemsHeld": payload.get("nonResourceItemsHeld"),
        "inventoryFreeSlots": payload.get("inventoryFreeSlots"),
        "depositInventoryAvailable": payload.get("depositInventoryAvailable"),
        "bankReadable": payload.get("bankReadable"),
        "bankingComplete": payload.get("bankingComplete"),
        "completionReason": payload.get("completionReason"),
        "warnings": payload.get("warnings", []),
        "missingCapabilities": payload.get("missingCapabilities", []),
    }


def compact_return_to_resource_summary(context: ReturnToResourceContext) -> dict[str, Any]:
    payload = context.to_dict()
    return {
        "returnNeeded": payload.get("returnNeeded"),
        "returnReady": payload.get("returnReady"),
        "serviceComplete": payload.get("serviceComplete"),
        "reason": payload.get("reason"),
        "resourceTargetAvailable": payload.get("resourceTargetAvailable"),
        "bestResourceTarget": target_label(payload.get("bestResourceTarget")),
        "resourcePathingNeeded": payload.get("resourcePathingNeeded"),
        "inventoryFreeSlots": payload.get("inventoryFreeSlots"),
        "inventoryFull": payload.get("inventoryFull"),
        "bankingComplete": payload.get("bankingComplete"),
        "warnings": payload.get("warnings", []),
        "missingCapabilities": payload.get("missingCapabilities", []),
    }


def compact_overlay_marker(marker: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(marker, dict) or not marker:
        return None
    keys = (
        "markerType",
        "label",
        "role",
        "selected",
        "targetType",
        "classId",
        "targetName",
        "name",
        "id",
        "objectKey",
        "worldX",
        "worldY",
        "plane",
        "distanceTiles",
        "navigationNeeded",
        "navigationReason",
        "navigationStatus",
    )
    return {key: marker.get(key) for key in keys if marker.get(key) is not None}


def stable_intent_for(generic_state: dict[str, Any], candidates: list[dict[str, Any]]) -> intent_stabilizer.IntentResult | None:
    active_intent = str(generic_state.get("activeIntent") or "")
    if active_intent not in {"target_selected", "continue_current_target", "continue_task", "select_target", "wait_for_result"}:
        return None
    active_target = generic_state.get("activeIntentTarget") if isinstance(generic_state.get("activeIntentTarget"), dict) else None
    stabilizer = intent_stabilizer.IntentStabilizer()
    return stabilizer.choose(
        candidates,
        {
            "activeTask": generic_state.get("task") or "woodcutting",
            "activeIntent": active_intent,
            "profile": generic_state.get("task") or "woodcutting",
            "latestTick": 42,
            "rawBestTarget": active_target or (candidates[0] if candidates else {}),
            "intentPriority": intent_stabilizer.PRIORITY_SELECTED_TARGET,
            "requireReachability": True,
            "requireAimPoint": False,
        },
    )


def build_overlay(decision: dict[str, Any], candidates: list[dict[str, Any]], stable: intent_stabilizer.IntentResult | None) -> dict[str, Any]:
    return intent_overlay_analyzer.build_intent_overlay_state(
        {"status": {"lastProcessedTick": 42}, "candidates": candidates},
        decision,
        SimpleNamespace(brain_task=decision.get("task"), overlay_backup_candidates=2),
        "2026-01-01T00:00:00Z",
        stable,
    )


def evaluate_transition_scenario(
    policy_name: str,
    scenario: str,
    *,
    tinderbox_present: bool = True,
) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    policy = task_policy.resolve_task_policy(policy_name, task=scenario_task(policy_name, scenario), profile=scenario_task(policy_name, scenario))
    response, candidates = context_response_for_scenario(scenario, tinderbox_present=tinderbox_present)
    task = scenario_task(policy_name, scenario)
    decision, _state = brain_core.evaluate_brain(
        response,
        brain_core.default_state(task, 5),
        task=task,
        goal_count=5,
        max_events=5,
        task_policy=policy,
    )
    generic = decision.get("genericTaskState") if isinstance(decision.get("genericTaskState"), dict) else {}
    inventory_context = InventoryContext(
        inventory=response.get("inventory") if isinstance(response.get("inventory"), dict) else {},
        progress={"currentHeldCount": len(response.get("inventory", {}).get("items", []))},
        source_tick=42,
    )
    service_context = ServiceContext(source_tick=42)
    process_context = ProcessInventoryContext(source_tick=42)
    bank_ui_context = BankUiContext(source_tick=42)
    bank_operation_context = BankOperationContext(source_tick=42)
    return_context = ReturnToResourceContext(source_tick=42)
    if generic.get("activeIntent") == "needs_service" or scenario in {"service_complete", "service_complete_no_target"}:
        service_context = service_analyzer.analyze_service_context(policy, candidates=candidates, source_tick=42)
        if service_context.best_service_candidate:
            active_target = dict(service_context.best_service_candidate)
            generic["activeIntentTarget"] = active_target
            generic["selectedTargetKey"] = intent_stabilizer.build_target_key(active_target, str(active_target.get("targetType") or "sceneObject"))
            decision["genericTaskState"] = generic
        if scenario in {"service_visible_arrived", "service_ready_from_daemon", "service_ready_bank_closed", "service_open", "service_complete", "service_complete_no_target", "bank_pin_required"} and service_context.best_service_candidate:
            active_target = dict(service_context.best_service_candidate)
            service_context.service_ready = True
            service_context.service_ready_reason = "arrived_at_service"
            service_context.service_ready_stable_for_ticks = 2
            service_context.selected_service_target_name = target_label(active_target)
            service_context.selected_service_target_tile = {
                "worldX": active_target.get("worldX"),
                "worldY": active_target.get("worldY"),
                "plane": active_target.get("plane"),
            }
            service_context.distance_to_service_target = 1
            service_context.arrived_at_final_approach = True
            service_context.arrived_near_destination = True
            service_context.distance_to_final_approach = 0
            generic["phase"] = "service_available"
            generic["activeIntent"] = "service_available"
            generic["activeIntentTarget"] = active_target
            generic["selectedTargetKey"] = intent_stabilizer.build_target_key(active_target, str(active_target.get("targetType") or "sceneObject"))
            generic["serviceReady"] = True
            generic["serviceReadyReason"] = "arrived_at_service"
            decision["phase"] = "service_available"
            decision["genericTaskState"] = generic
            if scenario in {"service_ready_bank_closed", "service_open", "service_complete", "service_complete_no_target", "bank_pin_required"}:
                bank_payload = {
                    "bankOpen": False,
                    "bankPinOpen": False,
                    "bankRootVisible": False,
                    "inventorySummary": (
                        {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0}
                        if scenario in {"service_complete", "service_complete_no_target"}
                        else {"freeSlots": 0, "occupiedSlots": 28, "matchingResourceCount": 28}
                    ),
                }
                if scenario in {"service_open", "service_complete", "service_complete_no_target"}:
                    bank_payload.update(
                        {
                            "bankOpen": True,
                            "bankRootVisible": True,
                            "bankContainerVisible": True,
                            "bankInventoryVisible": True,
                            "depositInventoryButtonVisible": True,
                            "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]},
                        }
                    )
                elif scenario == "bank_pin_required":
                    bank_payload.update({"bankOpen": True, "bankPinOpen": True, "bankRootVisible": True})
                bank_ui_context = bank_ui_analyzer.analyze_bank_ui_context(
                    policy,
                    bank_ui_payload=bank_payload,
                    inventory_context=inventory_context,
                    service_context=service_context,
                    source_tick=42,
                )
                bank_operation_context = bank_operation_analyzer.analyze_bank_operation_context(
                    policy,
                    bank_ui_context=bank_ui_context,
                    inventory_context=inventory_context,
                    resource_definition=brain_core.task_resource_group(task),
                    current_task_state=generic,
                    source_tick=42,
                )
                if bank_ui_context.bank_pin_open:
                    generic["phase"] = "blocked"
                    generic["activeIntent"] = "needs_user_resolution"
                    generic["blockingConditions"] = ["bank_pin_required"]
                    decision["phase"] = "blocked"
                    decision["genericTaskState"] = generic
                elif bank_ui_context.bank_open and bank_ui_context.bank_readable:
                    if bank_operation_context.banking_complete:
                        generic["phase"] = "service_complete"
                        generic["activeIntent"] = "resume_resource_collection"
                        generic["activeIntentTarget"] = None
                        generic["selectedTargetKey"] = None
                    else:
                        generic["phase"] = "service_open"
                        generic["activeIntent"] = "bank_operation_pending"
                    generic["bankOperationNeeded"] = bank_operation_context.operation_needed
                    generic["bankOperationType"] = bank_operation_context.operation_type
                    generic["bankingComplete"] = bank_operation_context.banking_complete
                    generic["bankOperationCompletionReason"] = bank_operation_context.completion_reason
                    decision["phase"] = generic["phase"]
                    decision["genericTaskState"] = generic
                return_context = return_to_resource_analyzer.analyze_return_to_resource_context(
                    policy,
                    bank_operation_context=bank_operation_context,
                    inventory_context=inventory_context,
                    target_context=TargetContext(
                        candidates=[candidate for candidate in candidates if candidate.get("classId") == "tree"],
                        raw_best_target=tree_candidate() if scenario == "service_complete" else None,
                        candidate_count=1 if scenario == "service_complete" else 0,
                        source_tick=42,
                    ),
                    current_task_state=generic,
                    source_tick=42,
                )
                if return_context.return_needed:
                    resource_target = return_context.best_resource_target if isinstance(return_context.best_resource_target, dict) else None
                    generic["returnNeeded"] = return_context.return_needed
                    generic["returnReady"] = return_context.return_ready
                    generic["returnToResourceReason"] = return_context.reason
                    generic["resourceTargetAvailable"] = return_context.resource_target_available
                    if return_context.return_ready and resource_target:
                        generic["phase"] = "target_selected"
                        generic["activeIntent"] = "select_target"
                        generic["activeIntentTarget"] = resource_target
                        generic["selectedTargetKey"] = intent_stabilizer.build_target_key(resource_target, str(resource_target.get("targetType") or "sceneObject"))
                        generic.pop("blockingConditions", None)
                        decision["phase"] = "target_selected"
                    else:
                        generic["phase"] = "needs_more_context"
                        generic["activeIntent"] = "select_target"
                        generic["activeIntentTarget"] = None
                        generic["selectedTargetKey"] = None
                        blocking = [str(item) for item in generic.get("blockingConditions") or [] if item]
                        if "no_target_observed" not in blocking:
                            blocking.append("no_target_observed")
                        generic["blockingConditions"] = blocking
                        decision["phase"] = "no_target_observed"
                    decision["genericTaskState"] = generic
    if generic.get("activeIntent") == "process_inventory":
        process_context = process_inventory_analyzer.analyze_process_inventory_context(policy, inventory_context, source_tick=42)
    decision["serviceContext"] = service_context.to_dict()
    decision["processInventoryContext"] = process_context.to_dict()
    decision["bankUiContext"] = bank_ui_context.to_dict()
    decision["bankOperationContext"] = bank_operation_context.to_dict()
    decision["returnToResourceContext"] = return_context.to_dict()
    decision.update(brain_core.context_domain_summary(decision, response=response, policy=policy))
    resource_candidates = [candidate for candidate in candidates if candidate.get("classId") == "tree"]
    target_context = TargetContext(
        candidates=resource_candidates,
        profile_candidates=resource_candidates,
        broad_candidates=candidates,
        raw_best_target=generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else (resource_candidates[0] if resource_candidates else None),
        candidate_count=len(resource_candidates),
        source_tick=42,
    )
    navigation_context = navigation_intent_analyzer.analyze_navigation_intent(
        policy,
        target_context=target_context,
        service_context=service_context,
        process_inventory_context=process_context,
        navigation_context=NavigationContext(collision_window_available=True, collision_known=True, source_tick=42),
        generic_task_state=generic,
        source_tick=42,
    )
    decision["navigationIntentContext"] = navigation_context.to_dict()
    overlay_candidates = resource_candidates if generic.get("activeIntent") in {"select_target", "target_selected", "continue_current_target"} else candidates
    stable = stable_intent_for(generic, overlay_candidates)
    overlay = build_overlay(decision, overlay_candidates, stable)
    selected_marker = compact_overlay_marker(next((marker for marker in overlay.get("markers", []) if isinstance(marker, dict) and marker.get("markerType") == "selected_target"), None))
    expected = expected_for(policy_name, scenario, tinderbox_present=tinderbox_present)
    failures: list[str] = []
    if generic.get("phase") != expected["phase"]:
        failures.append(f"expected phase {expected['phase']}, got {generic.get('phase')}")
    if generic.get("activeIntent") != expected["activeIntent"]:
        failures.append(f"expected active intent {expected['activeIntent']}, got {generic.get('activeIntent')}")
    overlay_expectation = expected["overlay"]
    if overlay_expectation == "selected_tree" and (not selected_marker or selected_marker.get("classId") != "tree"):
        failures.append("expected selected tree marker")
    if overlay_expectation == "selected_service" and (not selected_marker or selected_marker.get("classId") not in {"bank_service", "banker", "bank_booth", "bank_chest", "deposit_box", "deposit_chest"}):
        failures.append("expected selected service marker")
    if overlay_expectation == "none" and selected_marker:
        failures.append("expected no selected overlay marker")
    payload = {
        "schema": SCHEMA,
        "policy": policy.name,
        "scenario": scenario,
        "expectedPhase": expected["phase"],
        "actualPhase": generic.get("phase"),
        "expectedActiveIntent": expected["activeIntent"],
        "actualActiveIntent": generic.get("activeIntent"),
        "expectedAnalyzers": {
            "service": expected["activeIntent"] in {"needs_service", "service_available"},
            "processInventory": expected["activeIntent"] == "process_inventory",
            "bankOperation": expected["activeIntent"] in {"bank_operation_pending", "resume_resource_collection", "select_target"},
            "returnToResource": expected["activeIntent"] == "select_target" and bool(return_context.return_needed),
            "navigation": expected["activeIntent"] in {"needs_service", "target_selected", "continue_current_target", "continue_task", "select_target"},
        },
        "serviceAnalyzerRuns": bool(service_context.service_required),
        "processInventoryAnalyzerRuns": bool(process_context.process_required),
        "bankOperationAnalyzerRuns": bool(bank_operation_context.operation_needed or bank_operation_context.banking_complete or bank_operation_context.bank_readable),
        "navigationAnalyzerRuns": bool(navigation_context.navigation_reason),
        "serviceContextSummary": compact_service_summary(service_context),
        "processContextSummary": compact_process_summary(process_context),
        "bankUiContextSummary": compact_bank_ui_summary(bank_ui_context),
        "bankOperationContextSummary": compact_bank_operation_summary(bank_operation_context),
        "returnToResourceContextSummary": compact_return_to_resource_summary(return_context),
        "navigationContextSummary": compact_navigation_summary(navigation_context.to_dict()),
        "freshnessDomains": decision.get("freshnessDomains") if isinstance(decision.get("freshnessDomains"), dict) else {},
        "inventoryFreshness": (decision.get("freshnessDomains") or {}).get("inventoryFreshness") if isinstance(decision.get("freshnessDomains"), dict) else None,
        "targetCandidateFreshness": (decision.get("freshnessDomains") or {}).get("targetCandidateFreshness") if isinstance(decision.get("freshnessDomains"), dict) else None,
        "processInventoryFreshness": (decision.get("freshnessDomains") or {}).get("processInventoryFreshness") if isinstance(decision.get("freshnessDomains"), dict) else None,
        "requiredContextDomains": decision.get("requiredContextDomains") if isinstance(decision.get("requiredContextDomains"), list) else [],
        "missingRequiredContextDomains": decision.get("missingRequiredContextDomains") if isinstance(decision.get("missingRequiredContextDomains"), list) else [],
        "optionalMissingContextDomains": decision.get("optionalMissingContextDomains") if isinstance(decision.get("optionalMissingContextDomains"), list) else [],
        "targetCandidatesRequired": bool(decision.get("targetCandidatesRequired")),
        "candidateAbsenceExplanation": (
            "target candidates are required for this phase"
            if decision.get("targetCandidatesRequired")
            else "target candidates are optional for this policy phase"
        ),
        "overlaySelectedMarkerExpectation": overlay_expectation,
        "overlaySelectedMarker": selected_marker,
        "blockingConditions": list(generic.get("blockingConditions") or []),
        "noActionEmitted": bool(decision.get("noActionEmitted") and generic.get("noActionEmitted", True)),
        "failures": failures,
        "warnings": [],
    }
    payload["status"] = "FAIL" if payload["failures"] else "PASS"
    return payload


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def build_from_daemon(status: dict[str, Any], *, policy_name: str) -> dict[str, Any]:
    brain = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    generic = brain.get("genericTaskState") if isinstance(brain.get("genericTaskState"), dict) else {}
    service_context = brain.get("serviceContext") if isinstance(brain.get("serviceContext"), dict) else {}
    process_context = brain.get("processInventoryContext") if isinstance(brain.get("processInventoryContext"), dict) else {}
    bank_operation_context = brain.get("bankOperationContext") if isinstance(brain.get("bankOperationContext"), dict) else {}
    return_context = brain.get("returnToResourceContext") if isinstance(brain.get("returnToResourceContext"), dict) else {}
    navigation_context = brain.get("navigationIntentContext") if isinstance(brain.get("navigationIntentContext"), dict) else {}
    freshness_domains = brain.get("freshnessDomains") if isinstance(brain.get("freshnessDomains"), dict) else {}
    overlay_selected_type = None
    active_target = generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else None
    if active_target:
        overlay_selected_type = active_target.get("classId") or active_target.get("targetType")
    payload = {
        "schema": SCHEMA,
        "source": "daemon-memory",
        "policy": status.get("brainTaskPolicy") or policy_name,
        "scenario": "daemon_current",
        "actualPhase": generic.get("phase"),
        "actualActiveIntent": generic.get("activeIntent"),
        "serviceContextSummary": {
            "serviceNeeded": service_context.get("serviceNeeded"),
            "serviceTypeNeeded": service_context.get("serviceTypeNeeded"),
            "candidateCount": service_context.get("candidateCount"),
            "best": target_label(service_context.get("bestServiceCandidate") if isinstance(service_context.get("bestServiceCandidate"), dict) else None),
            "serviceReady": service_context.get("serviceReady", status.get("serviceReady")),
            "serviceReadyReason": service_context.get("serviceReadyReason", status.get("serviceReadyReason")),
            "serviceReadyStableForTicks": service_context.get("serviceReadyStableForTicks", status.get("serviceReadyStableForTicks")),
        },
        "processContextSummary": {
            "processRequired": process_context.get("processRequired"),
            "processTypeNeeded": process_context.get("processTypeNeeded"),
            "tinderboxStatus": process_context.get("tinderboxStatus"),
        },
        "bankOperationContextSummary": {
            "operationNeeded": bank_operation_context.get("operationNeeded", status.get("bankOperationNeeded")),
            "operationType": bank_operation_context.get("operationType", status.get("bankOperationType")),
            "resourceItemsHeld": bank_operation_context.get("resourceItemsHeld", status.get("bankResourceItemsHeld")),
            "resourceItemQuantity": bank_operation_context.get("resourceItemQuantity", status.get("bankResourceItemQuantity")),
            "depositInventoryAvailable": bank_operation_context.get("depositInventoryAvailable", status.get("bankDepositInventoryAvailable")),
            "bankingComplete": bank_operation_context.get("bankingComplete", status.get("bankingComplete")),
            "completionReason": bank_operation_context.get("completionReason", status.get("bankOperationCompletionReason")),
        },
        "returnToResourceContextSummary": {
            "returnNeeded": return_context.get("returnNeeded", status.get("returnToResourceNeeded")),
            "returnReady": return_context.get("returnReady", status.get("returnToResourceReady")),
            "resourceTargetAvailable": return_context.get("resourceTargetAvailable", status.get("returnResourceTargetAvailable")),
            "bestResourceTarget": target_label(return_context.get("bestResourceTarget") if isinstance(return_context.get("bestResourceTarget"), dict) else status.get("returnBestResourceTarget")),
            "resourcePathingNeeded": return_context.get("resourcePathingNeeded", status.get("returnResourcePathingNeeded")),
            "inventoryFreeSlots": return_context.get("inventoryFreeSlots", status.get("returnInventoryFreeSlots")),
            "reason": return_context.get("reason", status.get("returnToResourceReason")),
        },
        "navigationContextSummary": compact_navigation_summary(navigation_context),
        "freshnessDomains": freshness_domains,
        "inventoryFreshness": freshness_domains.get("inventoryFreshness"),
        "targetCandidateFreshness": freshness_domains.get("targetCandidateFreshness"),
        "processInventoryFreshness": freshness_domains.get("processInventoryFreshness"),
        "requiredContextDomains": brain.get("requiredContextDomains") if isinstance(brain.get("requiredContextDomains"), list) else status.get("requiredContextDomains", []),
        "missingRequiredContextDomains": brain.get("missingRequiredContextDomains") if isinstance(brain.get("missingRequiredContextDomains"), list) else status.get("missingRequiredContextDomains", []),
        "optionalMissingContextDomains": brain.get("optionalMissingContextDomains") if isinstance(brain.get("optionalMissingContextDomains"), list) else status.get("optionalMissingContextDomains", []),
        "targetCandidatesRequired": bool(brain.get("targetCandidatesRequired", status.get("targetCandidatesRequired", False))),
        "candidateAbsenceExplanation": (
            "target candidates are required for this phase"
            if bool(brain.get("targetCandidatesRequired", status.get("targetCandidatesRequired", False)))
            else "target candidates are optional for this policy phase"
        ),
        "overlaySelectedMarkerType": overlay_selected_type,
        "noActionEmitted": bool(brain.get("noActionEmitted")),
        "status": "PASS",
        "warnings": [],
        "failures": [],
    }
    return payload


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        "TASK TRANSITION DIAGNOSTIC",
        "",
        f"Status: {payload.get('status')}",
        f"Policy: {payload.get('policy')}",
        f"Scenario: {payload.get('scenario')}",
        f"Expected phase: {payload.get('expectedPhase', 'n/a')}",
        f"Actual phase: {payload.get('actualPhase')}",
        f"Expected active intent: {payload.get('expectedActiveIntent', 'n/a')}",
        f"Actual active intent: {payload.get('actualActiveIntent')}",
        f"Service analyzer: {'yes' if payload.get('serviceAnalyzerRuns') else 'no'}",
        f"Process inventory analyzer: {'yes' if payload.get('processInventoryAnalyzerRuns') else 'no'}",
        f"Navigation needed: {'yes' if (payload.get('navigationContextSummary') or {}).get('navigationNeeded') else 'no'}",
        f"Inventory freshness: {payload.get('inventoryFreshness') or (payload.get('freshnessDomains') or {}).get('inventoryFreshness') or 'unknown'}",
        f"Target candidate freshness: {payload.get('targetCandidateFreshness') or (payload.get('freshnessDomains') or {}).get('targetCandidateFreshness') or 'unknown'}",
        f"Process inventory freshness: {payload.get('processInventoryFreshness') or (payload.get('freshnessDomains') or {}).get('processInventoryFreshness') or 'unknown'}",
        f"Required context domains: {', '.join(payload.get('requiredContextDomains') or []) or 'none'}",
        f"Missing required domains: {', '.join(payload.get('missingRequiredContextDomains') or []) or 'none'}",
        f"Optional missing domains: {', '.join(payload.get('optionalMissingContextDomains') or []) or 'none'}",
        f"Candidate absence: {payload.get('candidateAbsenceExplanation') or 'unknown'}",
        f"Overlay selected: {target_label(payload.get('overlaySelectedMarker')) or payload.get('overlaySelectedMarkerType') or 'none'}",
        f"noActionEmitted: {str(payload.get('noActionEmitted')).lower()}",
    ]
    service = payload.get("serviceContextSummary") if isinstance(payload.get("serviceContextSummary"), dict) else {}
    process = payload.get("processContextSummary") if isinstance(payload.get("processContextSummary"), dict) else {}
    bank_operation = payload.get("bankOperationContextSummary") if isinstance(payload.get("bankOperationContextSummary"), dict) else {}
    return_context = payload.get("returnToResourceContextSummary") if isinstance(payload.get("returnToResourceContextSummary"), dict) else {}
    navigation = payload.get("navigationContextSummary") if isinstance(payload.get("navigationContextSummary"), dict) else {}
    lines.extend(
        [
            "",
            "Service/process/navigation:",
            f"  service: needed={service.get('serviceNeeded')} best={service.get('best')} ready={service.get('serviceReady')} reason={service.get('serviceReadyReason')}",
            f"  process: needed={process.get('processRequired')} type={process.get('processTypeNeeded')} tinderbox={process.get('tinderboxStatus')}",
            f"  bank operation: needed={bank_operation.get('operationNeeded')} type={bank_operation.get('operationType')} complete={bank_operation.get('bankingComplete')} reason={bank_operation.get('completionReason')}",
            f"  return: needed={return_context.get('returnNeeded')} ready={return_context.get('returnReady')} target={return_context.get('bestResourceTarget')} reason={return_context.get('reason')}",
            f"  navigation: reason={navigation.get('navigationReason')} target={navigation.get('destination')} reachability={navigation.get('directReachability')}",
        ]
    )
    failures = payload.get("failures") or []
    warnings = payload.get("warnings") or []
    lines.append("")
    lines.append("Findings:")
    if not failures and not warnings:
        lines.append("  none")
    for failure in failures:
        lines.append(f"  FAIL: {failure}")
    for warning in warnings:
        lines.append(f"  WARN: {warning}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only policy transition diagnostic. Synthetic mode does not read sessions or write files.")
    parser.add_argument("--policy", choices=task_policy.policy_names(), default="woodcutting_bank")
    parser.add_argument("--scenario", choices=SCENARIOS, default="woodcutting_not_full")
    parser.add_argument("--from-daemon", action="store_true", help="Read current daemon /status only.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.from_daemon:
        try:
            payload = build_from_daemon(fetch_json(args.daemon_url.rstrip("/") + "/status", timeout=args.timeout), policy_name=args.policy)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            payload = {
                "schema": SCHEMA,
                "source": "daemon-memory",
                "policy": args.policy,
                "scenario": "daemon_current",
                "status": "FAIL",
                "warnings": [],
                "failures": [f"daemon status unavailable: {type(error).__name__}: {error}"],
                "noActionEmitted": True,
            }
    else:
        payload = evaluate_transition_scenario(args.policy, args.scenario)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    return 0 if payload.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

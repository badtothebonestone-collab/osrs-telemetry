from __future__ import annotations

import argparse
import json
from copy import deepcopy
from typing import Any, Callable

import diagnose_woodcut_bank_cycle
from analyzers import service_analyzer


SCHEMA = "woodcut_bank_scenario_suite.v1"

SCENARIO_ORDER = [
    "collecting_resources",
    "inventory_full_needs_service",
    "pathing_to_service",
    "service_ready_bank_closed",
    "bank_open_resources_held",
    "bank_open_after_deposit",
    "bank_closed_return_memory",
    "bank_closed_tree_visible",
    "bank_closed_no_memory_no_target",
    "bank_pin_blocked",
    "retained_booth_blocks_deposit",
    "remembered_return_cross_plane",
]


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def bool_label(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown"


def value_label(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def get_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def target(name: str = "Oak tree", class_id: str = "tree", *, world_x: int = 3219, world_y: int = 3206, plane: int = 0) -> dict[str, Any]:
    return {
        "targetName": name,
        "name": name,
        "targetType": "sceneObject",
        "classId": class_id,
        "id": 10820 if class_id in {"tree", "woodcutting_tree"} else 0,
        "worldX": world_x,
        "worldY": world_y,
        "plane": plane,
    }


def bank_booth() -> dict[str, Any]:
    return {
        "targetName": "Bank booth",
        "name": "Bank booth",
        "targetType": "sceneObject",
        "classId": "bank_booth",
        "objectKey": "booth-1",
        "worldX": 3208,
        "worldY": 3221,
        "plane": 2,
        "distanceTiles": 5,
        "pathLengthTiles": 5,
        "approachQuality": "side_access_unknown",
        "navigation": {"directReachability": "unknown"},
    }


def deposit_box() -> dict[str, Any]:
    return {
        "targetName": "Bank Deposit Box",
        "name": "Bank Deposit Box",
        "targetType": "sceneObject",
        "classId": "deposit_box",
        "objectKey": "deposit-1",
        "worldX": 3210,
        "worldY": 3217,
        "plane": 2,
        "distanceTiles": 2,
        "pathLengthTiles": 2,
        "approachQuality": "direct_side_access",
        "navigation": {"directReachability": "reachable"},
    }


def resource_return_target(*, plane: int = 0) -> dict[str, Any]:
    return target("Resource return", "resource_return", world_x=3219, world_y=3206, plane=plane)


def status_for(
    *,
    phase: str = "target_selected",
    active_intent: str = "continue_current_target",
    inventory_full: bool = False,
    free_slots: int = 23,
    resource_held: int = 5,
    active_target: dict[str, Any] | None = None,
    service: dict[str, Any] | None = None,
    pathing: dict[str, Any] | None = None,
    bank_ui: dict[str, Any] | None = None,
    bank_operation: dict[str, Any] | None = None,
    close_bank: dict[str, Any] | None = None,
    post_bank: dict[str, Any] | None = None,
    return_context: dict[str, Any] | None = None,
    resource_return: dict[str, Any] | None = None,
    blocking: list[str] | None = None,
    overlay_target: dict[str, Any] | None = None,
    missing_required: list[str] | None = None,
    optional_missing: list[str] | None = None,
) -> dict[str, Any]:
    if active_target is None and active_intent in {"continue_current_target", "target_selected"}:
        active_target = target()
    if overlay_target is None:
        overlay_target = active_target
    brain = {
        "task": "woodcutting",
        "genericTaskState": {
            "task": "woodcutting",
            "phase": phase,
            "activeIntent": active_intent,
            "activeIntentTarget": active_target,
            "blockingConditions": blocking or [],
        },
        "goalProgress": {
            "displayedGoalProgress": 2,
            "goalCount": 5,
            "currentHeldCount": resource_held,
            "resourceGroup": "logs",
            "baselineEstablished": True,
        },
        "inventoryContext": {
            "inventoryFull": inventory_full,
            "freeSlots": free_slots,
            "occupiedSlots": max(0, 28 - int(free_slots)),
            "progress": {"currentHeldCount": resource_held},
        },
        "serviceContext": service or {"serviceNeeded": inventory_full, "serviceReady": False},
        "pathingContext": pathing or {"pathingNeeded": False, "pathCompleted": False},
        "bankUiContext": bank_ui or {"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        "bankOperationContext": bank_operation or {"operationNeeded": False, "operationType": "none", "bankingComplete": False, "resourceItemsHeld": resource_held},
        "closeBankContext": close_bank or {"closeBankNeeded": False, "closeBankReady": False, "reason": "close_not_needed"},
        "postBankReacquisitionContext": post_bank or {"reason": "not_applicable", "resourceTargetReacquisitionAllowed": False},
        "returnToResourceContext": return_context or {"returnNeeded": False, "returnReady": False, "resourceTargetAvailable": False, "reason": "not_applicable"},
        "resourceReturnContext": resource_return or {
            "resourceMemoryValid": False,
            "returnDestinationNeeded": False,
            "returnDestinationAvailable": False,
            "reason": "not_applicable",
        },
        "requiredContextDomains": [],
        "missingRequiredContextDomains": missing_required or [],
        "optionalMissingContextDomains": optional_missing or [],
        "noActionEmitted": True,
        "warnings": [],
    }
    if overlay_target is not None:
        brain["intentOverlayContext"] = {
            "selectedMarker": overlay_target,
            "summary": {"intentMarkerCount": 1, "pathMarkersEmitted": 0},
        }
    return {
        "schema": "context_status.v1",
        "status": "ok",
        "brainTask": "woodcutting",
        "brainTaskPolicy": "woodcutting_bank",
        "activeMissionPreset": "woodcut_bank",
        "inventoryFull": inventory_full,
        "inventoryFreeSlots": free_slots,
        "currentCycleStageStableForTicks": 3,
        "lastCycleStage": None,
        "lastCycleTransitionReason": None,
        "brain": brain,
        "warnings": [],
    }


def resource_memory_payload(*, plane: int = 0, age_ticks: int = 45) -> dict[str, Any]:
    destination_tile = {"worldX": 3219, "worldY": 3206, "plane": plane}
    return {
        "lastResourceActivityTick": 10,
        "lastResourceTargetTile": destination_tile,
        "lastResourceTargetName": "Oak tree",
        "lastResourceTargetClass": "tree",
        "lastResourceClusterCenter": {"worldX": 3220, "worldY": 3207, "plane": plane},
        "lastResourcePlane": plane,
        "resourceMemoryValid": True,
        "resourceMemoryAgeTicks": age_ticks,
        "returnDestinationNeeded": True,
        "returnDestinationAvailable": True,
        "returnDestinationTile": destination_tile,
        "returnDestinationSource": "last_resource_target",
        "reason": "using_remembered_resource_area",
    }


def retained_booth_service_context() -> dict[str, Any]:
    state = service_analyzer.ServiceTargetState()
    service_analyzer.analyze_service_context(
        "woodcutting_bank",
        candidates=[bank_booth(), deposit_box()],
        source_tick=10,
        service_target_state=state,
        current_plane=2,
    )
    retained = service_analyzer.analyze_service_context(
        "woodcutting_bank",
        candidates=[deposit_box()],
        source_tick=11,
        service_target_state=state,
        current_plane=2,
    )
    return retained.to_dict()


def scenario_collecting_resources() -> dict[str, Any]:
    return status_for(
        phase="target_selected",
        active_intent="continue_current_target",
        inventory_full=False,
        free_slots=23,
        resource_held=5,
        active_target=target(),
        return_context={"returnNeeded": False, "returnReady": False, "resourceTargetAvailable": True, "bestResourceTarget": target(), "reason": "resource_target_visible"},
    )


def scenario_inventory_full_needs_service() -> dict[str, Any]:
    return status_for(
        phase="inventory_full",
        active_intent="needs_service",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=None,
        service={"serviceNeeded": True, "serviceReady": False, "candidateCount": 0},
    )


def scenario_pathing_to_service() -> dict[str, Any]:
    booth = bank_booth()
    return status_for(
        phase="needs_service",
        active_intent="navigate_to_service",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=booth,
        service={"serviceNeeded": True, "serviceReady": False, "bestServiceCandidate": booth, "selectedServiceGroup": "full_bank"},
        pathing={"pathingNeeded": True, "pathCompleted": False, "destination": booth, "pathLengthTiles": 12},
        overlay_target=booth,
    )


def scenario_service_ready_bank_closed() -> dict[str, Any]:
    booth = bank_booth()
    return status_for(
        phase="service_available",
        active_intent="service_available",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=booth,
        service={"serviceNeeded": True, "serviceReady": True, "serviceReadyReason": "arrived_at_service_target", "bestServiceCandidate": booth},
        pathing={"pathingNeeded": False, "pathCompleted": True, "serviceReady": True},
        bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        overlay_target=booth,
    )


def scenario_bank_open_resources_held() -> dict[str, Any]:
    return status_for(
        phase="service_open",
        active_intent="bank_operation_pending",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=None,
        bank_ui={"bankOpen": True, "bankReadable": True, "bankPinOpen": False},
        bank_operation={"operationNeeded": True, "operationType": "deposit_inventory", "resourceItemsHeld": 28, "resourceItemQuantity": 28, "bankingComplete": False},
    )


def scenario_bank_open_after_deposit() -> dict[str, Any]:
    return status_for(
        phase="waiting_for_world_view",
        active_intent="close_service_context",
        inventory_full=False,
        free_slots=28,
        resource_held=0,
        active_target=None,
        bank_ui={"bankOpen": True, "bankReadable": True, "bankPinOpen": False},
        bank_operation={"operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0, "resourceItemQuantity": 0, "bankingComplete": True},
        close_bank={"closeBankNeeded": True, "closeBankReady": True, "closeButtonAvailable": True, "reason": "close_button_available"},
        post_bank={"postBankReacquisitionNeeded": True, "bankUiStillOpen": True, "resourceTargetReacquisitionAllowed": False, "reason": "bank_ui_still_open"},
    )


def scenario_bank_closed_return_memory() -> dict[str, Any]:
    destination = resource_return_target()
    memory = resource_memory_payload()
    return status_for(
        phase="return_to_resource",
        active_intent="return_to_resource_area",
        inventory_full=False,
        free_slots=28,
        resource_held=0,
        active_target=destination,
        bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        bank_operation={"operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0, "resourceItemQuantity": 0, "bankingComplete": True},
        post_bank={"reason": "no_resource_target_observed", "resourceTargetReacquisitionAllowed": True, "resourceTargetAvailable": False},
        return_context={"returnNeeded": True, "returnReady": False, "resourceTargetAvailable": False, "resourcePathingNeeded": True, "reason": "no_resource_target_observed"},
        resource_return=memory,
        pathing={"pathingNeeded": True, "pathCompleted": False, "destination": destination, "destinationTile": memory["returnDestinationTile"], "pathLengthTiles": 18},
        overlay_target=destination,
        missing_required=["target.candidates", "target.freshness"],
    )


def scenario_bank_closed_tree_visible() -> dict[str, Any]:
    tree = target("Oak tree")
    return status_for(
        phase="target_selected",
        active_intent="select_target",
        inventory_full=False,
        free_slots=28,
        resource_held=0,
        active_target=tree,
        bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        bank_operation={"operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0, "resourceItemQuantity": 0, "bankingComplete": True},
        post_bank={"reason": "resource_target_visible", "resourceTargetReacquisitionAllowed": True, "resourceTargetAvailable": True},
        return_context={"returnNeeded": True, "returnReady": True, "resourceTargetAvailable": True, "bestResourceTarget": tree, "reason": "resource_target_visible"},
        resource_return={"resourceMemoryValid": True, "returnDestinationNeeded": False, "returnDestinationAvailable": False, "reason": "resource_target_visible"},
        overlay_target=tree,
    )


def scenario_bank_closed_no_memory_no_target() -> dict[str, Any]:
    return status_for(
        phase="needs_more_context",
        active_intent="select_target",
        inventory_full=False,
        free_slots=28,
        resource_held=0,
        active_target=None,
        bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        bank_operation={"operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0, "resourceItemQuantity": 0, "bankingComplete": True},
        post_bank={"reason": "no_resource_target_observed", "resourceTargetReacquisitionAllowed": True, "resourceTargetAvailable": False},
        return_context={"returnNeeded": True, "returnReady": False, "resourceTargetAvailable": False, "reason": "no_resource_target_observed"},
        resource_return={
            "status": "WARN",
            "resourceMemoryValid": False,
            "resourceMemoryInvalidReason": "no_resource_memory",
            "returnDestinationNeeded": True,
            "returnDestinationAvailable": False,
            "returnDestinationSource": "none",
            "reason": "no_resource_memory",
        },
        missing_required=["target.candidates", "target.freshness"],
    )


def scenario_bank_pin_blocked() -> dict[str, Any]:
    return status_for(
        phase="blocked",
        active_intent="needs_user_resolution",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=None,
        bank_ui={"bankOpen": True, "bankReadable": False, "bankPinOpen": True},
        bank_operation={"operationNeeded": False, "operationType": "unknown", "resourceItemsHeld": 28, "bankingComplete": False},
        blocking=["bank_pin_required"],
    )


def scenario_retained_booth_blocks_deposit() -> dict[str, Any]:
    service = retained_booth_service_context()
    booth = deepcopy(dict_value(service.get("bestServiceCandidate")))
    return status_for(
        phase="inventory_full",
        active_intent="needs_service",
        inventory_full=True,
        free_slots=0,
        resource_held=28,
        active_target=booth,
        service=service,
        pathing={"pathingNeeded": False, "pathCompleted": False, "destination": booth},
        overlay_target=booth,
    )


def scenario_remembered_return_cross_plane() -> dict[str, Any]:
    destination = resource_return_target(plane=0)
    memory = resource_memory_payload(plane=0)
    return status_for(
        phase="return_to_resource",
        active_intent="return_to_resource_area",
        inventory_full=False,
        free_slots=28,
        resource_held=0,
        active_target=destination,
        bank_ui={"bankOpen": False, "bankReadable": False, "bankPinOpen": False},
        bank_operation={"operationNeeded": False, "operationType": "none", "resourceItemsHeld": 0, "resourceItemQuantity": 0, "bankingComplete": True},
        post_bank={"reason": "no_resource_target_observed", "resourceTargetReacquisitionAllowed": True, "resourceTargetAvailable": False},
        return_context={"returnNeeded": True, "returnReady": False, "resourceTargetAvailable": False, "resourcePathingNeeded": True, "reason": "no_resource_target_observed"},
        resource_return={**memory, "playerPlane": 2, "pathingWarning": "cross_plane_return_destination"},
        pathing={
            "pathingNeeded": True,
            "pathCompleted": False,
            "destination": destination,
            "destinationTile": memory["returnDestinationTile"],
            "pathLengthTiles": None,
            "pathingWarning": "cross_plane_return_destination",
        },
        overlay_target=destination,
        missing_required=["target.candidates", "target.freshness"],
    )


SCENARIO_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "collecting_resources": scenario_collecting_resources,
    "inventory_full_needs_service": scenario_inventory_full_needs_service,
    "pathing_to_service": scenario_pathing_to_service,
    "service_ready_bank_closed": scenario_service_ready_bank_closed,
    "bank_open_resources_held": scenario_bank_open_resources_held,
    "bank_open_after_deposit": scenario_bank_open_after_deposit,
    "bank_closed_return_memory": scenario_bank_closed_return_memory,
    "bank_closed_tree_visible": scenario_bank_closed_tree_visible,
    "bank_closed_no_memory_no_target": scenario_bank_closed_no_memory_no_target,
    "bank_pin_blocked": scenario_bank_pin_blocked,
    "retained_booth_blocks_deposit": scenario_retained_booth_blocks_deposit,
    "remembered_return_cross_plane": scenario_remembered_return_cross_plane,
}


SCENARIO_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "collecting_resources": {
        "stages": ["collecting_resources", "resource_target_selected"],
        "phases": ["target_selected"],
        "activeIntents": ["continue_current_target"],
        "keys": {"cycle.inventory.inventoryFull": False, "cycle.returnToResource.resourceTargetAvailable": True},
    },
    "inventory_full_needs_service": {
        "stages": ["needs_service"],
        "phases": ["inventory_full"],
        "activeIntents": ["needs_service"],
        "keys": {"cycle.inventory.inventoryFull": True, "cycle.service.serviceNeeded": True},
    },
    "pathing_to_service": {
        "stages": ["pathing_to_service"],
        "phases": ["needs_service"],
        "activeIntents": ["navigate_to_service"],
        "keys": {"cycle.pathing.pathingNeeded": True, "cycle.service.targetName": "Bank booth"},
    },
    "service_ready_bank_closed": {
        "stages": ["service_ready"],
        "phases": ["service_available"],
        "activeIntents": ["service_available"],
        "keys": {"cycle.service.serviceReady": True, "cycle.bank.bankOpen": False},
    },
    "bank_open_resources_held": {
        "stages": ["bank_operation_pending"],
        "phases": ["service_open"],
        "activeIntents": ["bank_operation_pending"],
        "keys": {"cycle.bank.bankReadable": True, "cycle.bankOperation.operationNeeded": True, "cycle.bankOperation.resourceItemsHeld": 28},
    },
    "bank_open_after_deposit": {
        "stages": ["close_bank_needed", "waiting_for_world_view"],
        "phases": ["waiting_for_world_view"],
        "activeIntents": ["close_service_context"],
        "keys": {"cycle.bankOperation.bankingComplete": True, "cycle.closeBank.closeBankNeeded": True, "cycle.postBank.reason": "bank_ui_still_open"},
    },
    "bank_closed_return_memory": {
        "stages": ["return_to_resource"],
        "phases": ["return_to_resource"],
        "activeIntents": ["return_to_resource_area"],
        "keys": {"cycle.resourceReturn.returnDestinationAvailable": True, "cycle.resourceReturn.reason": "using_remembered_resource_area"},
    },
    "bank_closed_tree_visible": {
        "stages": ["resource_target_selected"],
        "phases": ["target_selected"],
        "activeIntents": ["select_target"],
        "keys": {"cycle.returnToResource.resourceTargetAvailable": True, "cycle.resourceReturn.returnDestinationNeeded": False},
    },
    "bank_closed_no_memory_no_target": {
        "stages": ["return_to_resource"],
        "phases": ["needs_more_context"],
        "activeIntents": ["select_target"],
        "reasons": ["no_resource_memory"],
        "keys": {"cycle.resourceReturn.returnDestinationAvailable": False, "cycle.resourceReturn.resourceMemoryValid": False},
    },
    "bank_pin_blocked": {
        "stages": ["blocked"],
        "phases": ["blocked"],
        "activeIntents": ["needs_user_resolution"],
        "reasons": ["bank_pin_required"],
        "keys": {"cycle.bank.bankPinOpen": True},
    },
    "retained_booth_blocks_deposit": {
        "stages": ["needs_service"],
        "phases": ["inventory_full"],
        "activeIntents": ["needs_service"],
        "keys": {
            "brain.serviceContext.selectedServiceGroup": "full_bank",
            "brain.serviceContext.depositFallbackAllowed": False,
            "brain.serviceContext.selectedServiceTargetSource": "retained_primary",
        },
    },
    "remembered_return_cross_plane": {
        "stages": ["return_to_resource"],
        "phases": ["return_to_resource"],
        "activeIntents": ["return_to_resource_area"],
        "keys": {
            "cycle.resourceReturn.returnDestinationAvailable": True,
            "cycle.resourceReturn.returnDestinationTile.plane": 0,
            "brain.resourceReturnContext.playerPlane": 2,
            "brain.resourceReturnContext.pathingWarning": "cross_plane_return_destination",
        },
    },
}


def key_fields_for(name: str, cycle: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    brain = dict_value(status.get("brain"))
    service = dict_value(brain.get("serviceContext"))
    pathing = dict_value(brain.get("pathingContext"))
    resource_return = dict_value(brain.get("resourceReturnContext"))
    fields: dict[str, Any] = {
        "inventoryFull": get_path(cycle, "inventory.inventoryFull"),
        "freeSlots": get_path(cycle, "inventory.freeSlots"),
        "resourceItemsHeld": get_path(cycle, "bankOperation.resourceItemsHeld"),
        "serviceReady": get_path(cycle, "service.serviceReady"),
        "serviceTarget": get_path(cycle, "service.targetName"),
        "pathingNeeded": get_path(cycle, "pathing.pathingNeeded"),
        "bankOpen": get_path(cycle, "bank.bankOpen"),
        "bankReadable": get_path(cycle, "bank.bankReadable"),
        "bankPinOpen": get_path(cycle, "bank.bankPinOpen"),
        "operationNeeded": get_path(cycle, "bankOperation.operationNeeded"),
        "operationType": get_path(cycle, "bankOperation.operationType"),
        "bankingComplete": get_path(cycle, "bankOperation.bankingComplete"),
        "closeBankNeeded": get_path(cycle, "closeBank.closeBankNeeded"),
        "postBankReason": get_path(cycle, "postBank.reason"),
        "returnDestinationAvailable": get_path(cycle, "resourceReturn.returnDestinationAvailable"),
        "resourceReturnReason": get_path(cycle, "resourceReturn.reason"),
        "overlaySelected": get_path(cycle, "overlay.selected.label"),
    }
    if name == "retained_booth_blocks_deposit":
        fields.update(
            {
                "selectedServiceGroup": service.get("selectedServiceGroup"),
                "selectedServiceTargetSource": service.get("selectedServiceTargetSource"),
                "depositFallbackAllowed": service.get("depositFallbackAllowed"),
                "serviceSwitchReason": service.get("serviceSwitchReason"),
                "retainedServiceTargetName": service.get("retainedServiceTargetName"),
                "depositCandidateIneligibleReason": next(
                    (
                        candidate.get("ineligibleReason")
                        for candidate in service.get("serviceCandidates", [])
                        if isinstance(candidate, dict) and candidate.get("classId") == "deposit_box"
                    ),
                    None,
                ),
            }
        )
    if name == "remembered_return_cross_plane":
        fields.update(
            {
                "returnDestinationTile": resource_return.get("returnDestinationTile"),
                "memoryPlane": resource_return.get("lastResourcePlane"),
                "playerPlane": resource_return.get("playerPlane"),
                "pathingWarning": pathing.get("pathingWarning") or resource_return.get("pathingWarning"),
            }
        )
    return {key: value for key, value in fields.items() if value is not None}


def evaluate_scenario(name: str, *, verbose: bool = False) -> dict[str, Any]:
    if name not in SCENARIO_BUILDERS:
        raise KeyError(name)
    status = SCENARIO_BUILDERS[name]()
    cycle = diagnose_woodcut_bank_cycle.build_from_daemon(status)
    expectation = SCENARIO_EXPECTATIONS[name]
    expected_stages = list_strings(expectation.get("stages"))
    expected_phases = list_strings(expectation.get("phases"))
    expected_intents = list_strings(expectation.get("activeIntents"))
    expected_reasons = list_strings(expectation.get("reasons"))

    failures: list[str] = []
    if cycle.get("cycleStage") not in expected_stages:
        failures.append(f"expected stage {'/'.join(expected_stages)}, got {cycle.get('cycleStage') or 'unknown'}")
    if expected_phases and cycle.get("phase") not in expected_phases:
        failures.append(f"expected phase {'/'.join(expected_phases)}, got {cycle.get('phase') or 'unknown'}")
    if expected_intents and cycle.get("activeIntent") not in expected_intents:
        failures.append(f"expected active intent {'/'.join(expected_intents)}, got {cycle.get('activeIntent') or 'unknown'}")
    if expected_reasons and cycle.get("reason") not in expected_reasons:
        failures.append(f"expected reason {'/'.join(expected_reasons)}, got {cycle.get('reason') or 'unknown'}")

    envelope = {"cycle": cycle, "status": status, "brain": dict_value(status.get("brain"))}
    for path, expected_value in dict_value(expectation.get("keys")).items():
        actual = get_path(envelope, path)
        if actual != expected_value:
            failures.append(f"expected {path}={expected_value!r}, got {actual!r}")

    warnings = list_strings(expectation.get("warnings"))
    result: dict[str, Any] = {
        "name": name,
        "expectedStages": expected_stages,
        "actualStage": cycle.get("cycleStage"),
        "expectedPhases": expected_phases,
        "actualPhase": cycle.get("phase"),
        "expectedActiveIntents": expected_intents,
        "actualActiveIntent": cycle.get("activeIntent"),
        "expectedReasons": expected_reasons,
        "actualReason": cycle.get("reason"),
        "status": "FAIL" if failures else "WARN" if warnings else "PASS",
        "reason": "; ".join(failures) if failures else cycle.get("reason") or "matched expected state",
        "keyFields": key_fields_for(name, cycle, status),
        "warnings": warnings,
        "failures": failures,
    }
    if verbose:
        result["cycle"] = cycle
    return result


def build_suite_report(scenario_names: list[str] | None = None, *, verbose: bool = False) -> dict[str, Any]:
    selected_names = scenario_names or SCENARIO_ORDER
    unknown = [name for name in selected_names if name not in SCENARIO_BUILDERS]
    if unknown:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "scenarioResults": [],
            "passCount": 0,
            "warnCount": 0,
            "failCount": len(unknown),
            "warnings": [],
            "failures": [f"unknown scenario: {name}" for name in unknown],
            "availableScenarios": SCENARIO_ORDER,
            "noActionEmitted": True,
        }
    results = [evaluate_scenario(name, verbose=verbose) for name in selected_names]
    pass_count = sum(1 for result in results if result.get("status") == "PASS")
    warn_count = sum(1 for result in results if result.get("status") == "WARN")
    fail_count = sum(1 for result in results if result.get("status") == "FAIL")
    failures = [failure for result in results for failure in list_strings(result.get("failures"))]
    warnings = [warning for result in results for warning in list_strings(result.get("warnings"))]
    return {
        "schema": SCHEMA,
        "status": "FAIL" if fail_count else "WARN" if warn_count else "PASS",
        "scenarioResults": results,
        "passCount": pass_count,
        "warnCount": warn_count,
        "failCount": fail_count,
        "warnings": warnings,
        "failures": failures,
        "noActionEmitted": True,
    }


def format_key_fields(fields: dict[str, Any], *, verbose: bool) -> str:
    if not fields:
        return "none"
    if not verbose:
        preferred = [
            "inventoryFull",
            "freeSlots",
            "resourceItemsHeld",
            "serviceTarget",
            "serviceReady",
            "pathingNeeded",
            "bankOpen",
            "bankReadable",
            "bankPinOpen",
            "bankingComplete",
            "closeBankNeeded",
            "postBankReason",
            "returnDestinationAvailable",
            "resourceReturnReason",
            "selectedServiceGroup",
            "depositFallbackAllowed",
            "pathingWarning",
        ]
        parts = [f"{key}={value_label(fields[key])}" for key in preferred if key in fields]
    else:
        parts = [f"{key}={value_label(value)}" for key, value in fields.items()]
    return ", ".join(parts) if parts else "none"


def format_human(report: dict[str, Any], *, verbose: bool = False) -> str:
    lines = [
        f"WOODCUT BANK SCENARIOS - {report.get('status') or 'UNKNOWN'}",
        "",
        "Summary:",
        f"  Pass: {report.get('passCount', 0)}",
        f"  Warn: {report.get('warnCount', 0)}",
        f"  Fail: {report.get('failCount', 0)}",
        "",
        "Scenarios:",
    ]
    for result in report.get("scenarioResults", []):
        if not isinstance(result, dict):
            continue
        lines.extend(
            [
                f"  {result.get('status') or 'UNKNOWN'} {result.get('name') or 'unknown'}",
                f"    Expected stage: {' / '.join(list_strings(result.get('expectedStages'))) or 'unknown'}",
                f"    Actual stage: {result.get('actualStage') or 'unknown'}",
                f"    Phase: {result.get('actualPhase') or 'unknown'}",
                f"    Active intent: {result.get('actualActiveIntent') or 'unknown'}",
                f"    Reason: {result.get('actualReason') or result.get('reason') or 'unknown'}",
                f"    Key fields: {format_key_fields(dict_value(result.get('keyFields')), verbose=verbose)}",
            ]
        )
        for failure in list_strings(result.get("failures")):
            lines.append(f"    FAIL: {failure}")
        for warning in list_strings(result.get("warnings")):
            lines.append(f"    WARN: {warning}")
    if not report.get("scenarioResults"):
        lines.append("  none")
    failures = list_strings(report.get("failures"))
    warnings = list_strings(report.get("warnings"))
    lines.extend(["", "Warnings:"])
    if not failures and not warnings:
        lines.append("  none")
    for failure in failures:
        lines.append(f"  FAIL: {failure}")
    for warning in warnings:
        lines.append(f"  WARN: {warning}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic in-memory woodcut_bank cycle scenarios. Prints to stdout only.")
    parser.add_argument("--json", action="store_true", help="Print woodcut_bank_scenario_suite.v1 JSON to stdout.")
    parser.add_argument("--scenario", action="append", default=[], help="Run only this scenario. May be provided more than once.")
    parser.add_argument("--list", action="store_true", help="List scenario names and exit.")
    parser.add_argument("--verbose", action="store_true", help="Show expanded key fields and include cycle payloads in JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        print("\n".join(SCENARIO_ORDER))
        return 0
    report = build_suite_report(args.scenario or None, verbose=bool(args.verbose))
    print(json.dumps(report, indent=2, sort_keys=False) if args.json else format_human(report, verbose=bool(args.verbose)), end="")
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

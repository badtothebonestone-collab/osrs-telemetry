from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Callable, Iterable

from .contract_limits import MAX_PRIORITY_OBJECT_IDS
from .definition import (
    DEFAULT_GATHERING_CAPABILITIES,
    RUNTIME_SUPPORTED_CAPABILITIES,
    BankDefinition,
    DefinitionProvenance,
    EquipmentPolicy,
    FixedRoute,
    FixedRouteStep,
    InventoryPredicate,
    LifecyclePolicy,
    NavigationPolicy,
    ObjectSelector,
    ProvenanceEvidence,
    RadialWorkArea,
    RecoveryPolicy,
    ResourceDefinition,
    RoutePointClassification,
    RouteStepKind,
    StopConditionKind,
    TargetPolicy,
    TargetSelectionMode,
    TaskCapability,
    TaskSiteDefinition,
    TaskType,
    VerificationExpectations,
    WithdrawalRule,
)
from .model import WorldPoint


TASK_DEFINITION_SCHEMA = "osrs_bot.task_definition.v1"
MAX_TASK_DEFINITION_BYTES = 1_048_576
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class TaskDefinitionAuthoringError(ValueError):
    """A path-qualified failure at the external task-definition boundary."""


def _fail(path: str, message: str) -> TaskDefinitionAuthoringError:
    return TaskDefinitionAuthoringError(f"{path}: {message}")


def _object(value: object, path: str, fields: Iterable[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(path, "must be a JSON object")
    if any(type(key) is not str for key in value):
        raise _fail(path, "field names must be strings")
    expected = frozenset(fields)
    actual = frozenset(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise _fail(path, f"unknown field(s): {', '.join(unknown)}")
    if missing:
        raise _fail(path, f"missing field(s): {', '.join(missing)}")
    return value


def _array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise _fail(path, "must be a JSON array; scalar-or-array forms are not accepted")
    return value


def _text(value: object, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail(path, "must be nonblank text")
    return value


def _identifier(value: object, path: str) -> str:
    text = _text(value, path)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise _fail(
            path,
            "must be a lowercase identifier ([a-z][a-z0-9_]{0,63})",
        )
    return text


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise _fail(path, "must be a JSON boolean")
    return value


def _integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise _fail(path, "must be a JSON integer (booleans are not integers)")
    return value


def _optional_integer(value: object, path: str) -> int | None:
    if value is None:
        return None
    return _integer(value, path)


def _number(value: object, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise _fail(path, "must be a finite JSON number")
    return float(value)


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _enum(value: object, path: str, enum_type):
    text = _text(value, path)
    try:
        return enum_type(text)
    except ValueError as error:
        choices = ", ".join(sorted(item.value for item in enum_type))
        raise _fail(path, f"must be one of: {choices}") from error


def _unique_array(
    value: object,
    path: str,
    decoder: Callable[[object, str], object],
) -> tuple[object, ...]:
    values = _array(value, path)
    decoded = tuple(decoder(item, f"{path}[{index}]") for index, item in enumerate(values))
    if len(decoded) != len(set(decoded)):
        raise _fail(path, "must not contain duplicates")
    return decoded


def _id_set(
    value: object,
    path: str,
    *,
    allow_empty: bool = False,
    maximum: int | None = None,
) -> frozenset[int]:
    decoded = _unique_array(value, path, _integer)
    if not allow_empty and not decoded:
        raise _fail(path, "must not be empty")
    if any(item <= 0 for item in decoded):
        raise _fail(path, "item and object IDs must be positive")
    if maximum is not None and len(decoded) > maximum:
        raise _fail(path, f"must contain at most {maximum} values")
    return frozenset(decoded)


def _text_tuple(value: object, path: str) -> tuple[str, ...]:
    return tuple(_unique_array(value, path, _text))


def _world_point(value: object, path: str) -> WorldPoint:
    item = _object(value, path, ("x", "y", "plane"))
    return WorldPoint(
        x=_integer(item["x"], f"{path}.x"),
        y=_integer(item["y"], f"{path}.y"),
        plane=_integer(item["plane"], f"{path}.plane"),
    )


def _selector(value: object, path: str) -> ObjectSelector:
    item = _object(value, path, ("object_ids", "name", "action"))
    return ObjectSelector(
        object_ids=_id_set(
            item["object_ids"],
            f"{path}.object_ids",
            maximum=MAX_PRIORITY_OBJECT_IDS,
        ),
        name=_text(item["name"], f"{path}.name"),
        action=_text(item["action"], f"{path}.action"),
    )


def _work_area(value: object, path: str) -> RadialWorkArea:
    item = _object(value, path, ("area_id", "anchor", "radius", "allowed_planes"))
    planes = _unique_array(item["allowed_planes"], f"{path}.allowed_planes", _integer)
    if not planes:
        raise _fail(f"{path}.allowed_planes", "must not be empty")
    return RadialWorkArea(
        anchor=_world_point(item["anchor"], f"{path}.anchor"),
        radius=_integer(item["radius"], f"{path}.radius"),
        area_id=_identifier(item["area_id"], f"{path}.area_id"),
        allowed_planes=frozenset(planes),
    )


def _resource(value: object, path: str) -> ResourceDefinition:
    item = _object(
        value,
        path,
        (
            "resource_id",
            "interaction_kind",
            "selector",
            "produced_item_ids",
            "work_area",
        ),
    )
    return ResourceDefinition(
        selector=_selector(item["selector"], f"{path}.selector"),
        produced_item_ids=_id_set(
            item["produced_item_ids"], f"{path}.produced_item_ids"
        ),
        work_area=_work_area(item["work_area"], f"{path}.work_area"),
        resource_id=_identifier(item["resource_id"], f"{path}.resource_id"),
        interaction_kind=_identifier(
            item["interaction_kind"], f"{path}.interaction_kind"
        ),
    )


def _bank(value: object, path: str) -> BankDefinition:
    item = _object(
        value,
        path,
        ("bank_id", "priority", "selector", "anchor", "interaction_radius"),
    )
    return BankDefinition(
        selector=_selector(item["selector"], f"{path}.selector"),
        anchor=_world_point(item["anchor"], f"{path}.anchor"),
        interaction_radius=_integer(
            item["interaction_radius"], f"{path}.interaction_radius"
        ),
        bank_id=_identifier(item["bank_id"], f"{path}.bank_id"),
        priority=_integer(item["priority"], f"{path}.priority"),
    )


def _route_step(value: object, path: str) -> FixedRouteStep:
    if type(value) is not dict:
        raise _fail(path, "must be a JSON object")
    if "kind" not in value:
        raise _fail(path, "missing field(s): kind")
    kind = _enum(value.get("kind"), f"{path}.kind", RouteStepKind)
    common = (
        "step_id",
        "kind",
        "location",
        "arrival_radius",
        "action",
        "classification",
    )
    fields = common if kind is RouteStepKind.WALK else (
        *common,
        "object_id",
        "object_name",
        "alternate_actions",
        "expected_plane",
    )
    item = _object(value, path, fields)
    return FixedRouteStep(
        step_id=_identifier(item["step_id"], f"{path}.step_id"),
        kind=kind,
        location=_world_point(item["location"], f"{path}.location"),
        arrival_radius=_integer(item["arrival_radius"], f"{path}.arrival_radius"),
        action=_text(item["action"], f"{path}.action"),
        classification=_enum(
            item["classification"],
            f"{path}.classification",
            RoutePointClassification,
        ),
        object_id=(
            None
            if kind is RouteStepKind.WALK
            else _optional_integer(item["object_id"], f"{path}.object_id")
        ),
        object_name=(
            None
            if kind is RouteStepKind.WALK
            else _optional_text(item["object_name"], f"{path}.object_name")
        ),
        alternate_actions=(
            ()
            if kind is RouteStepKind.WALK
            else _text_tuple(item["alternate_actions"], f"{path}.alternate_actions")
        ),
        expected_plane=(
            None
            if kind is RouteStepKind.WALK
            else _optional_integer(item["expected_plane"], f"{path}.expected_plane")
        ),
    )


def _route(value: object, path: str) -> FixedRoute:
    item = _object(
        value,
        path,
        ("route_id", "start_anchor", "destination_anchor", "steps"),
    )
    steps = _array(item["steps"], f"{path}.steps")
    return FixedRoute(
        route_id=_identifier(item["route_id"], f"{path}.route_id"),
        start_anchor=_world_point(item["start_anchor"], f"{path}.start_anchor"),
        destination_anchor=_world_point(
            item["destination_anchor"], f"{path}.destination_anchor"
        ),
        steps=tuple(
            _route_step(step, f"{path}.steps[{index}]")
            for index, step in enumerate(steps)
        ),
    )


def _withdrawal_rule(value: object, path: str) -> WithdrawalRule:
    item = _object(
        value,
        path,
        ("item_id", "minimum_quantity", "target_quantity"),
    )
    return WithdrawalRule(
        item_id=_integer(item["item_id"], f"{path}.item_id"),
        minimum_quantity=_integer(
            item["minimum_quantity"], f"{path}.minimum_quantity"
        ),
        target_quantity=_integer(item["target_quantity"], f"{path}.target_quantity"),
    )


def _inventory(value: object, path: str) -> InventoryPredicate:
    item = _object(
        value,
        path,
        (
            "allowed_item_ids",
            "deposit_item_ids",
            "retain_item_ids",
            "withdrawal_rules",
            "minimum_free_slots",
            "require_only_allowed_items",
            "require_nonempty_deposit",
            "require_produced_item_when_full",
        ),
    )
    rules = _array(item["withdrawal_rules"], f"{path}.withdrawal_rules")
    minimum_free_slots = _integer(
        item["minimum_free_slots"], f"{path}.minimum_free_slots"
    )
    require_only_allowed_items = _boolean(
        item["require_only_allowed_items"], f"{path}.require_only_allowed_items"
    )
    require_nonempty_deposit = _boolean(
        item["require_nonempty_deposit"], f"{path}.require_nonempty_deposit"
    )
    require_produced_item_when_full = _boolean(
        item["require_produced_item_when_full"],
        f"{path}.require_produced_item_when_full",
    )
    if minimum_free_slots < 1:
        raise _fail(
            f"{path}.minimum_free_slots",
            "must be at least 1 so a full inventory enters the bank route",
        )
    for field_name, enabled in (
        ("require_only_allowed_items", require_only_allowed_items),
        ("require_nonempty_deposit", require_nonempty_deposit),
        ("require_produced_item_when_full", require_produced_item_when_full),
    ):
        if not enabled:
            raise _fail(
                f"{path}.{field_name}",
                "must be true for the current fail-closed deposit-all runtime",
            )
    return InventoryPredicate(
        allowed_item_ids=_id_set(item["allowed_item_ids"], f"{path}.allowed_item_ids"),
        deposit_item_ids=_id_set(item["deposit_item_ids"], f"{path}.deposit_item_ids"),
        retain_item_ids=_id_set(
            item["retain_item_ids"], f"{path}.retain_item_ids", allow_empty=True
        ),
        withdrawal_rules=tuple(
            _withdrawal_rule(rule, f"{path}.withdrawal_rules[{index}]")
            for index, rule in enumerate(rules)
        ),
        minimum_free_slots=minimum_free_slots,
        require_only_allowed_items=require_only_allowed_items,
        require_nonempty_deposit=require_nonempty_deposit,
        require_produced_item_when_full=require_produced_item_when_full,
    )


def _equipment(value: object, path: str) -> EquipmentPolicy:
    item = _object(
        value,
        path,
        (
            "required_any_of_item_ids",
            "permitted_item_ids",
            "allow_inventory_fallback",
            "auto_equip",
        ),
    )
    return EquipmentPolicy(
        required_any_of_item_ids=_id_set(
            item["required_any_of_item_ids"],
            f"{path}.required_any_of_item_ids",
            allow_empty=True,
        ),
        permitted_item_ids=_id_set(
            item["permitted_item_ids"],
            f"{path}.permitted_item_ids",
            allow_empty=True,
        ),
        allow_inventory_fallback=_boolean(
            item["allow_inventory_fallback"], f"{path}.allow_inventory_fallback"
        ),
        auto_equip=_boolean(item["auto_equip"], f"{path}.auto_equip"),
    )


def _verification(value: object, path: str) -> VerificationExpectations:
    fields = (
        "action_deadline_ticks",
        "movement_deadline_ticks",
        "resource_deadline_ticks",
        "route_stable_ticks",
        "deposit_expected_quantity",
        "transition_dialogue_prompt_contains",
        "transition_up_option_contains",
        "transition_down_option_contains",
    )
    item = _object(value, path, fields)
    deposit_expected_quantity = _integer(
        item["deposit_expected_quantity"], f"{path}.deposit_expected_quantity"
    )
    if deposit_expected_quantity != 0:
        raise _fail(
            f"{path}.deposit_expected_quantity",
            "must be zero because the current runtime verifies deposit-all",
        )
    return VerificationExpectations(
        action_deadline_ticks=_integer(
            item["action_deadline_ticks"], f"{path}.action_deadline_ticks"
        ),
        movement_deadline_ticks=_integer(
            item["movement_deadline_ticks"], f"{path}.movement_deadline_ticks"
        ),
        resource_deadline_ticks=_integer(
            item["resource_deadline_ticks"], f"{path}.resource_deadline_ticks"
        ),
        route_stable_ticks=_integer(
            item["route_stable_ticks"], f"{path}.route_stable_ticks"
        ),
        deposit_expected_quantity=deposit_expected_quantity,
        transition_dialogue_prompt_contains=_text(
            item["transition_dialogue_prompt_contains"],
            f"{path}.transition_dialogue_prompt_contains",
        ),
        transition_up_option_contains=_text(
            item["transition_up_option_contains"],
            f"{path}.transition_up_option_contains",
        ),
        transition_down_option_contains=_text(
            item["transition_down_option_contains"],
            f"{path}.transition_down_option_contains",
        ),
    )


def _target_policy(value: object, path: str) -> TargetPolicy:
    item = _object(
        value,
        path,
        (
            "selection_mode",
            "max_candidates",
            "max_rejection_evidence",
            "incomplete_omission_wait_frames",
            "query_radius_tiles",
        ),
    )
    return TargetPolicy(
        selection_mode=_enum(
            item["selection_mode"], f"{path}.selection_mode", TargetSelectionMode
        ),
        max_candidates=_integer(item["max_candidates"], f"{path}.max_candidates"),
        max_rejection_evidence=_integer(
            item["max_rejection_evidence"], f"{path}.max_rejection_evidence"
        ),
        incomplete_omission_wait_frames=_integer(
            item["incomplete_omission_wait_frames"],
            f"{path}.incomplete_omission_wait_frames",
        ),
        query_radius_tiles=_integer(
            item["query_radius_tiles"], f"{path}.query_radius_tiles"
        ),
    )


def _lifecycle(value: object, path: str) -> LifecyclePolicy:
    item = _object(
        value,
        path,
        (
            "supported_stop_conditions",
            "maximum_cycles",
            "maximum_item_quantity",
            "maximum_duration_seconds",
            "maximum_actions",
        ),
    )
    conditions = _unique_array(
        item["supported_stop_conditions"],
        f"{path}.supported_stop_conditions",
        lambda candidate, item_path: _enum(candidate, item_path, StopConditionKind),
    )
    if not conditions:
        raise _fail(f"{path}.supported_stop_conditions", "must not be empty")
    return LifecyclePolicy(
        supported_stop_conditions=frozenset(conditions),
        maximum_cycles=_integer(item["maximum_cycles"], f"{path}.maximum_cycles"),
        maximum_item_quantity=_integer(
            item["maximum_item_quantity"], f"{path}.maximum_item_quantity"
        ),
        maximum_duration_seconds=_number(
            item["maximum_duration_seconds"], f"{path}.maximum_duration_seconds"
        ),
        maximum_actions=_integer(item["maximum_actions"], f"{path}.maximum_actions"),
    )


def _recovery(value: object, path: str) -> RecoveryPolicy:
    item = _object(
        value,
        path,
        (
            "reconcile_on_restart",
            "max_resource_no_yield_retries",
            "max_bank_unavailable_frames",
            "max_target_incomplete_frames",
        ),
    )
    return RecoveryPolicy(
        reconcile_on_restart=_boolean(
            item["reconcile_on_restart"], f"{path}.reconcile_on_restart"
        ),
        max_resource_no_yield_retries=_integer(
            item["max_resource_no_yield_retries"],
            f"{path}.max_resource_no_yield_retries",
        ),
        max_bank_unavailable_frames=_integer(
            item["max_bank_unavailable_frames"],
            f"{path}.max_bank_unavailable_frames",
        ),
        max_target_incomplete_frames=_integer(
            item["max_target_incomplete_frames"],
            f"{path}.max_target_incomplete_frames",
        ),
    )


def _navigation(value: object, path: str) -> NavigationPolicy:
    item = _object(
        value,
        path,
        (
            "intent",
            "allow_polyline_reconciliation",
            "require_mandatory_transitions",
        ),
    )
    allow_polyline_reconciliation = _boolean(
        item["allow_polyline_reconciliation"],
        f"{path}.allow_polyline_reconciliation",
    )
    require_mandatory_transitions = _boolean(
        item["require_mandatory_transitions"],
        f"{path}.require_mandatory_transitions",
    )
    if not allow_polyline_reconciliation:
        raise _fail(
            f"{path}.allow_polyline_reconciliation",
            "must be true for the current fixed-route runtime",
        )
    if not require_mandatory_transitions:
        raise _fail(
            f"{path}.require_mandatory_transitions",
            "must be true for the current fixed-route runtime",
        )
    return NavigationPolicy(
        intent=_identifier(item["intent"], f"{path}.intent"),
        allow_polyline_reconciliation=allow_polyline_reconciliation,
        require_mandatory_transitions=require_mandatory_transitions,
    )


def _provenance_evidence(value: object, path: str) -> ProvenanceEvidence:
    item = _object(value, path, ("path", "sha256", "proves"))
    return ProvenanceEvidence(
        path=_text(item["path"], f"{path}.path"),
        sha256=_text(item["sha256"], f"{path}.sha256"),
        proves=_text(item["proves"], f"{path}.proves"),
    )


def _provenance(value: object, path: str) -> DefinitionProvenance:
    item = _object(
        value,
        path,
        (
            "fixture_schema",
            "fixture_id",
            "description",
            "evidence_date",
            "baseline_parent",
            "proof_root",
            "evidence_kind",
            "limitations",
            "evidence",
        ),
    )
    evidence = _array(item["evidence"], f"{path}.evidence")
    return DefinitionProvenance(
        fixture_schema=_text(item["fixture_schema"], f"{path}.fixture_schema"),
        fixture_id=_text(item["fixture_id"], f"{path}.fixture_id"),
        description=_text(item["description"], f"{path}.description"),
        evidence_date=_text(item["evidence_date"], f"{path}.evidence_date"),
        baseline_parent=_text(item["baseline_parent"], f"{path}.baseline_parent"),
        proof_root=_text(item["proof_root"], f"{path}.proof_root"),
        evidence_kind=_text(item["evidence_kind"], f"{path}.evidence_kind"),
        limitations=_text(item["limitations"], f"{path}.limitations"),
        evidence=tuple(
            _provenance_evidence(entry, f"{path}.evidence[{index}]")
            for index, entry in enumerate(evidence)
        ),
    )


def _capabilities(value: object, path: str) -> frozenset[TaskCapability]:
    decoded = _unique_array(
        value,
        path,
        lambda candidate, item_path: _enum(candidate, item_path, TaskCapability),
    )
    if not decoded:
        raise _fail(path, "must not be empty")
    capabilities = frozenset(decoded)
    unsupported = capabilities - RUNTIME_SUPPORTED_CAPABILITIES
    if unsupported:
        names = ", ".join(sorted(item.value for item in unsupported))
        raise _fail(path, f"runtime does not support capability/capabilities: {names}")
    missing = DEFAULT_GATHERING_CAPABILITIES - capabilities
    if missing:
        names = ", ".join(sorted(item.value for item in missing))
        raise _fail(path, f"gathering definitions must declare capability/capabilities: {names}")
    return capabilities


def _validate_external_contract(definition: TaskSiteDefinition) -> None:
    if definition.task_type is not TaskType.GATHERING:
        raise _fail(
            "definition.task_type",
            "the current production runtime supports only gathering task definitions",
        )
    unsupported = definition.unsupported_capabilities
    if unsupported:
        names = ", ".join(sorted(item.value for item in unsupported))
        raise _fail(
            "definition.capabilities",
            f"runtime does not support capability/capabilities: {names}",
        )
    missing = DEFAULT_GATHERING_CAPABILITIES - definition.capabilities
    if definition.task_type is TaskType.GATHERING and missing:
        names = ", ".join(sorted(item.value for item in missing))
        raise _fail(
            "definition.capabilities",
            f"gathering definitions must declare capability/capabilities: {names}",
        )
    identifiers: list[tuple[str, str]] = [
        ("definition.definition_id", definition.definition_id),
        ("definition.resource.resource_id", definition.resource.resource_id),
        ("definition.resource.work_area.area_id", definition.resource.work_area.area_id),
        ("definition.bank.bank_id", definition.bank.bank_id),
        ("definition.route_to_bank.route_id", definition.route_to_bank.route_id),
        ("definition.route_to_resource.route_id", definition.route_to_resource.route_id),
    ]
    identifiers.extend(
        (f"definition.fallback_banks[{index}].bank_id", bank.bank_id)
        for index, bank in enumerate(definition.fallback_banks)
    )
    for route_name, route in (
        ("route_to_bank", definition.route_to_bank),
        ("route_to_resource", definition.route_to_resource),
    ):
        identifiers.extend(
            (f"definition.{route_name}.steps[{index}].step_id", step.step_id)
            for index, step in enumerate(route.steps)
        )
    by_identifier: dict[str, str] = {}
    for path, identifier in identifiers:
        previous = by_identifier.get(identifier)
        if previous is not None:
            raise _fail(path, f"duplicate identifier {identifier!r}; first used at {previous}")
        by_identifier[identifier] = path

    equipment_configured = bool(
        definition.equipment.required_any_of_item_ids
        or definition.equipment.permitted_item_ids
        or definition.equipment.allow_inventory_fallback
        or definition.equipment.auto_equip
    )
    if (
        equipment_configured
        and TaskCapability.EQUIPMENT_OBSERVATION not in definition.capabilities
    ):
        raise _fail(
            "definition.equipment",
            "configured equipment requires the equipment_observation capability",
        )
    if definition.equipment.allow_inventory_fallback:
        raise _fail(
            "definition.equipment.allow_inventory_fallback",
            "inventory fallback is unsafe with the current deposit-all bank policy",
        )
    if definition.inventory.retain_item_ids:
        raise _fail(
            "definition.inventory.retain_item_ids",
            "selective retention is not supported by the deposit-all runtime",
        )
    if definition.fallback_banks:
        raise _fail(
            "definition.fallback_banks",
            "fallback bank routing is not supported by the current runtime",
        )


def decode_task_definition(document: object) -> TaskSiteDefinition:
    """Decode one strict v1 JSON document into immutable runtime model objects."""

    envelope = _object(document, "$", ("schema_version", "runnable", "definition"))
    schema_version = _text(envelope["schema_version"], "$.schema_version")
    if schema_version != TASK_DEFINITION_SCHEMA:
        raise _fail(
            "$.schema_version",
            f"expected {TASK_DEFINITION_SCHEMA!r}, got {schema_version!r}",
        )
    runnable = _boolean(envelope["runnable"], "$.runnable")
    if not runnable:
        raise _fail(
            "$.runnable",
            "false marks a scaffold as intentionally non-runnable; replace every "
            "placeholder and set runnable to true before validation",
        )

    fields = (
        "definition_id",
        "display_name",
        "version",
        "task_type",
        "capabilities",
        "target_policy",
        "resource",
        "bank",
        "fallback_banks",
        "route_to_bank",
        "route_to_resource",
        "inventory",
        "equipment",
        "verification",
        "lifecycle",
        "recovery",
        "navigation",
        "provenance",
    )
    item = _object(envelope["definition"], "$.definition", fields)

    # Capability negotiation happens before model construction. Unsupported future
    # shapes (for example NPC fishing) therefore fail with an honest boundary error.
    task_type = _enum(item["task_type"], "$.definition.task_type", TaskType)
    if task_type is not TaskType.GATHERING:
        raise _fail(
            "$.definition.task_type",
            "the current production runtime supports only gathering task definitions",
        )
    capabilities = _capabilities(item["capabilities"], "$.definition.capabilities")
    fallback_values = _array(item["fallback_banks"], "$.definition.fallback_banks")
    try:
        inventory = _inventory(item["inventory"], "$.definition.inventory")
        equipment = _equipment(item["equipment"], "$.definition.equipment")
        equipment_configured = bool(
            equipment.required_any_of_item_ids
            or equipment.permitted_item_ids
            or equipment.allow_inventory_fallback
            or equipment.auto_equip
        )
        if (
            equipment_configured
            and TaskCapability.EQUIPMENT_OBSERVATION not in capabilities
        ):
            raise _fail(
                "$.definition.equipment",
                "configured equipment requires the equipment_observation capability",
            )
        if equipment.allow_inventory_fallback:
            raise _fail(
                "$.definition.equipment.allow_inventory_fallback",
                "inventory fallback is unsafe with the current deposit-all bank policy",
            )
        if inventory.retain_item_ids:
            raise _fail(
                "$.definition.inventory.retain_item_ids",
                "selective retention is not supported by the deposit-all runtime",
            )
        definition = TaskSiteDefinition(
            definition_id=_identifier(
                item["definition_id"], "$.definition.definition_id"
            ),
            display_name=_text(item["display_name"], "$.definition.display_name"),
            version=_integer(item["version"], "$.definition.version"),
            task_type=task_type,
            capabilities=capabilities,
            target_policy=_target_policy(
                item["target_policy"], "$.definition.target_policy"
            ),
            resource=_resource(item["resource"], "$.definition.resource"),
            bank=_bank(item["bank"], "$.definition.bank"),
            fallback_banks=tuple(
                _bank(bank, f"$.definition.fallback_banks[{index}]")
                for index, bank in enumerate(fallback_values)
            ),
            route_to_bank=_route(
                item["route_to_bank"], "$.definition.route_to_bank"
            ),
            route_to_resource=_route(
                item["route_to_resource"], "$.definition.route_to_resource"
            ),
            inventory=inventory,
            equipment=equipment,
            verification=_verification(
                item["verification"], "$.definition.verification"
            ),
            lifecycle=_lifecycle(item["lifecycle"], "$.definition.lifecycle"),
            recovery=_recovery(item["recovery"], "$.definition.recovery"),
            navigation=_navigation(item["navigation"], "$.definition.navigation"),
            provenance=_provenance(item["provenance"], "$.definition.provenance"),
        )
        _validate_external_contract(definition)
        return definition
    except TaskDefinitionAuthoringError:
        raise
    except (TypeError, ValueError) as error:
        raise _fail("$.definition", str(error)) from error


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskDefinitionAuthoringError(
                f"$: duplicate JSON object field is ambiguous: {key!r}"
            )
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> object:
    raise TaskDefinitionAuthoringError(
        f"$: non-standard JSON numeric constant is not accepted: {value}"
    )


def load_task_definition(path: str | Path) -> TaskSiteDefinition:
    source = Path(path)
    try:
        size = source.stat().st_size
        if size > MAX_TASK_DEFINITION_BYTES:
            raise TaskDefinitionAuthoringError(
                f"{source}: definition exceeds the {MAX_TASK_DEFINITION_BYTES}-byte limit"
            )
        document = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except json.JSONDecodeError as error:
        raise TaskDefinitionAuthoringError(
            f"{source}:{error.lineno}:{error.colno}: invalid JSON: {error.msg}"
        ) from error
    return decode_task_definition(document)


def _point_mapping(point: WorldPoint) -> dict[str, int]:
    return {"x": point.x, "y": point.y, "plane": point.plane}


def _selector_mapping(selector: ObjectSelector) -> dict[str, object]:
    return {
        "object_ids": sorted(selector.object_ids),
        "name": selector.name,
        "action": selector.action,
    }


def _bank_mapping(bank: BankDefinition) -> dict[str, object]:
    return {
        "bank_id": bank.bank_id,
        "priority": bank.priority,
        "selector": _selector_mapping(bank.selector),
        "anchor": _point_mapping(bank.anchor),
        "interaction_radius": bank.interaction_radius,
    }


def _route_step_mapping(step: FixedRouteStep) -> dict[str, object]:
    result: dict[str, object] = {
        "step_id": step.step_id,
        "kind": step.kind.value,
        "location": _point_mapping(step.location),
        "arrival_radius": step.arrival_radius,
        "action": step.action,
        "classification": step.classification.value,
    }
    if step.kind is RouteStepKind.OBJECT:
        result.update(
            {
                "object_id": step.object_id,
                "object_name": step.object_name,
                "alternate_actions": list(step.alternate_actions),
                "expected_plane": step.expected_plane,
            }
        )
    return result


def _route_mapping(route: FixedRoute) -> dict[str, object]:
    return {
        "route_id": route.route_id,
        "start_anchor": _point_mapping(route.start_anchor),
        "destination_anchor": _point_mapping(route.destination_anchor),
        "steps": [_route_step_mapping(step) for step in route.steps],
    }


def task_definition_document(definition: TaskSiteDefinition) -> dict[str, object]:
    """Return the complete canonical v1 JSON-compatible representation."""

    if not isinstance(definition, TaskSiteDefinition):
        raise TypeError("definition must be a TaskSiteDefinition")
    _validate_external_contract(definition)
    return {
        "schema_version": TASK_DEFINITION_SCHEMA,
        "runnable": True,
        "definition": {
            "definition_id": definition.definition_id,
            "display_name": definition.display_name,
            "version": definition.version,
            "task_type": definition.task_type.value,
            "capabilities": sorted(item.value for item in definition.capabilities),
            "target_policy": {
                "selection_mode": definition.target_policy.selection_mode.value,
                "max_candidates": definition.target_policy.max_candidates,
                "max_rejection_evidence": definition.target_policy.max_rejection_evidence,
                "incomplete_omission_wait_frames": (
                    definition.target_policy.incomplete_omission_wait_frames
                ),
                "query_radius_tiles": definition.target_policy.query_radius_tiles,
            },
            "resource": {
                "resource_id": definition.resource.resource_id,
                "interaction_kind": definition.resource.interaction_kind,
                "selector": _selector_mapping(definition.resource.selector),
                "produced_item_ids": sorted(definition.resource.produced_item_ids),
                "work_area": {
                    "area_id": definition.resource.work_area.area_id,
                    "anchor": _point_mapping(definition.resource.work_area.anchor),
                    "radius": definition.resource.work_area.radius,
                    "allowed_planes": sorted(
                        definition.resource.work_area.allowed_planes
                    ),
                },
            },
            "bank": _bank_mapping(definition.bank),
            "fallback_banks": [
                _bank_mapping(bank) for bank in definition.fallback_banks
            ],
            "route_to_bank": _route_mapping(definition.route_to_bank),
            "route_to_resource": _route_mapping(definition.route_to_resource),
            "inventory": {
                "allowed_item_ids": sorted(definition.inventory.allowed_item_ids),
                "deposit_item_ids": sorted(definition.inventory.deposit_item_ids),
                "retain_item_ids": sorted(definition.inventory.retain_item_ids),
                "withdrawal_rules": [
                    {
                        "item_id": rule.item_id,
                        "minimum_quantity": rule.minimum_quantity,
                        "target_quantity": rule.target_quantity,
                    }
                    for rule in definition.inventory.withdrawal_rules
                ],
                "minimum_free_slots": definition.inventory.minimum_free_slots,
                "require_only_allowed_items": (
                    definition.inventory.require_only_allowed_items
                ),
                "require_nonempty_deposit": (
                    definition.inventory.require_nonempty_deposit
                ),
                "require_produced_item_when_full": (
                    definition.inventory.require_produced_item_when_full
                ),
            },
            "equipment": {
                "required_any_of_item_ids": sorted(
                    definition.equipment.required_any_of_item_ids
                ),
                "permitted_item_ids": sorted(definition.equipment.permitted_item_ids),
                "allow_inventory_fallback": (
                    definition.equipment.allow_inventory_fallback
                ),
                "auto_equip": definition.equipment.auto_equip,
            },
            "verification": {
                "action_deadline_ticks": (
                    definition.verification.action_deadline_ticks
                ),
                "movement_deadline_ticks": (
                    definition.verification.movement_deadline_ticks
                ),
                "resource_deadline_ticks": (
                    definition.verification.resource_deadline_ticks
                ),
                "route_stable_ticks": definition.verification.route_stable_ticks,
                "deposit_expected_quantity": (
                    definition.verification.deposit_expected_quantity
                ),
                "transition_dialogue_prompt_contains": (
                    definition.verification.transition_dialogue_prompt_contains
                ),
                "transition_up_option_contains": (
                    definition.verification.transition_up_option_contains
                ),
                "transition_down_option_contains": (
                    definition.verification.transition_down_option_contains
                ),
            },
            "lifecycle": {
                "supported_stop_conditions": sorted(
                    item.value for item in definition.lifecycle.supported_stop_conditions
                ),
                "maximum_cycles": definition.lifecycle.maximum_cycles,
                "maximum_item_quantity": definition.lifecycle.maximum_item_quantity,
                "maximum_duration_seconds": float(
                    definition.lifecycle.maximum_duration_seconds
                ),
                "maximum_actions": definition.lifecycle.maximum_actions,
            },
            "recovery": {
                "reconcile_on_restart": definition.recovery.reconcile_on_restart,
                "max_resource_no_yield_retries": (
                    definition.recovery.max_resource_no_yield_retries
                ),
                "max_bank_unavailable_frames": (
                    definition.recovery.max_bank_unavailable_frames
                ),
                "max_target_incomplete_frames": (
                    definition.recovery.max_target_incomplete_frames
                ),
            },
            "navigation": {
                "intent": definition.navigation.intent,
                "allow_polyline_reconciliation": (
                    definition.navigation.allow_polyline_reconciliation
                ),
                "require_mandatory_transitions": (
                    definition.navigation.require_mandatory_transitions
                ),
            },
            "provenance": {
                "fixture_schema": definition.provenance.fixture_schema,
                "fixture_id": definition.provenance.fixture_id,
                "description": definition.provenance.description,
                "evidence_date": definition.provenance.evidence_date,
                "baseline_parent": definition.provenance.baseline_parent,
                "proof_root": definition.provenance.proof_root,
                "evidence_kind": definition.provenance.evidence_kind,
                "limitations": definition.provenance.limitations,
                "evidence": [
                    {
                        "path": evidence.path,
                        "sha256": evidence.sha256,
                        "proves": evidence.proves,
                    }
                    for evidence in definition.provenance.evidence
                ],
            },
        },
    }


def canonical_task_definition_json(definition: TaskSiteDefinition) -> str:
    return json.dumps(
        task_definition_document(definition),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def task_definition_summary(definition: TaskSiteDefinition) -> dict[str, object]:
    routes = (definition.route_to_bank, definition.route_to_resource)
    return {
        "definition_id": definition.definition_id,
        "display_name": definition.display_name,
        "version": definition.version,
        "task_type": definition.task_type.value,
        "runtime_supported": not definition.unsupported_capabilities,
        "capabilities": sorted(item.value for item in definition.capabilities),
        "resource": {
            "resource_id": definition.resource.resource_id,
            "interaction_kind": definition.resource.interaction_kind,
            "object_ids": sorted(definition.resource.selector.object_ids),
            "action": definition.resource.selector.action,
            "produced_item_ids": sorted(definition.resource.produced_item_ids),
            "area_id": definition.resource.work_area.area_id,
            "anchor": _point_mapping(definition.resource.work_area.anchor),
            "radius": definition.resource.work_area.radius,
        },
        "bank": {
            "bank_id": definition.bank.bank_id,
            "object_ids": sorted(definition.bank.selector.object_ids),
            "anchor": _point_mapping(definition.bank.anchor),
        },
        "routes": [
            {
                "route_id": route.route_id,
                "steps": len(route.steps),
                "object_transitions": sum(
                    step.kind is RouteStepKind.OBJECT for step in route.steps
                ),
            }
            for route in routes
        ],
        "equipment": {
            "required_any_of_item_ids": sorted(
                definition.equipment.required_any_of_item_ids
            ),
            "observation_required": (
                TaskCapability.EQUIPMENT_OBSERVATION in definition.capabilities
            ),
            "auto_equip": definition.equipment.auto_equip,
        },
        "provenance": {
            "fixture_id": definition.provenance.fixture_id,
            "evidence_count": len(definition.provenance.evidence),
            "limitations": definition.provenance.limitations,
        },
    }


def _human_summary(definition: TaskSiteDefinition) -> str:
    summary = task_definition_summary(definition)
    resource = summary["resource"]
    bank = summary["bank"]
    equipment = summary["equipment"]
    routes = summary["routes"]
    return "\n".join(
        (
            f"{definition.definition_id} v{definition.version}: {definition.display_name}",
            f"type: {definition.task_type.value}; runtime-supported: yes",
            (
                f"resource: {resource['resource_id']} / {resource['action']} "
                f"objects={resource['object_ids']} produces={resource['produced_item_ids']}"
            ),
            f"bank: {bank['bank_id']} objects={bank['object_ids']}",
            (
                "routes: "
                + ", ".join(
                    f"{route['route_id']} ({route['steps']} steps, "
                    f"{route['object_transitions']} transitions)"
                    for route in routes
                )
            ),
            (
                "equipment: "
                + (
                    f"one of {equipment['required_any_of_item_ids']} required"
                    if equipment["required_any_of_item_ids"]
                    else "none required"
                )
            ),
        )
    )


def _authoring_explanation(definition: TaskSiteDefinition | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": TASK_DEFINITION_SCHEMA,
        "contract": [
            "Every object has an exact field set; unknown and missing fields are rejected.",
            "Set-like arrays reject duplicates and decode into frozensets.",
            "Identifiers are lowercase and globally unique across task-owned objects.",
            "Routes are plane-consistent, end at their declared anchors, and cross-reference the resource and preferred bank.",
            "Only capabilities implemented by the one production runtime are accepted.",
            "Scaffolds remain blocked while runnable is false.",
        ],
        "runtime_supported_capabilities": sorted(
            item.value for item in RUNTIME_SUPPORTED_CAPABILITIES
        ),
        "known_but_unsupported_capabilities": sorted(
            item.value
            for item in TaskCapability
            if item not in RUNTIME_SUPPORTED_CAPABILITIES
        ),
    }
    if definition is not None:
        result["definition"] = task_definition_summary(definition)
    return result


def scaffold_document() -> dict[str, object]:
    """Return a complete-shape template that cannot be loaded until explicitly armed."""

    walk_to_bank = {
        "step_id": "replace_with_bank_arrival_step",
        "kind": "walk",
        "location": {"x": 0, "y": 0, "plane": 0},
        "arrival_radius": 1,
        "action": "Walk here",
        "classification": "arrival_point",
    }
    walk_to_resource = {
        "step_id": "replace_with_resource_arrival_step",
        "kind": "walk",
        "location": {"x": 0, "y": 0, "plane": 0},
        "arrival_radius": 1,
        "action": "Walk here",
        "classification": "arrival_point",
    }
    return {
        "schema_version": TASK_DEFINITION_SCHEMA,
        "runnable": False,
        "definition": {
            "definition_id": "replace_me",
            "display_name": "REPLACE ME with an evidence-backed task name",
            "version": 1,
            "task_type": "gathering",
            "capabilities": sorted(
                item.value for item in DEFAULT_GATHERING_CAPABILITIES
            ),
            "target_policy": {
                "selection_mode": "geometry_then_distance",
                "max_candidates": 64,
                "max_rejection_evidence": 32,
                "incomplete_omission_wait_frames": 2,
                "query_radius_tiles": 4,
            },
            "resource": {
                "resource_id": "replace_resource",
                "interaction_kind": "game_object",
                "selector": {
                    "object_ids": [0],
                    "name": "REPLACE ME",
                    "action": "REPLACE ME",
                },
                "produced_item_ids": [0],
                "work_area": {
                    "area_id": "replace_work_area",
                    "anchor": {"x": 0, "y": 0, "plane": 0},
                    "radius": 1,
                    "allowed_planes": [0],
                },
            },
            "bank": {
                "bank_id": "replace_bank",
                "priority": 0,
                "selector": {
                    "object_ids": [0],
                    "name": "REPLACE ME",
                    "action": "Bank",
                },
                "anchor": {"x": 0, "y": 0, "plane": 0},
                "interaction_radius": 1,
            },
            "fallback_banks": [],
            "route_to_bank": {
                "route_id": "replace_route_to_bank",
                "start_anchor": {"x": 0, "y": 0, "plane": 0},
                "destination_anchor": {"x": 0, "y": 0, "plane": 0},
                "steps": [walk_to_bank],
            },
            "route_to_resource": {
                "route_id": "replace_route_to_resource",
                "start_anchor": {"x": 0, "y": 0, "plane": 0},
                "destination_anchor": {"x": 0, "y": 0, "plane": 0},
                "steps": [walk_to_resource],
            },
            "inventory": {
                "allowed_item_ids": [0],
                "deposit_item_ids": [0],
                "retain_item_ids": [],
                "withdrawal_rules": [],
                "minimum_free_slots": 1,
                "require_only_allowed_items": True,
                "require_nonempty_deposit": True,
                "require_produced_item_when_full": True,
            },
            "equipment": {
                "required_any_of_item_ids": [],
                "permitted_item_ids": [],
                "allow_inventory_fallback": False,
                "auto_equip": False,
            },
            "verification": {
                "action_deadline_ticks": 8,
                "movement_deadline_ticks": 20,
                "resource_deadline_ticks": 100,
                "route_stable_ticks": 4,
                "deposit_expected_quantity": 0,
                "transition_dialogue_prompt_contains": "climb",
                "transition_up_option_contains": "climb up",
                "transition_down_option_contains": "climb down",
            },
            "lifecycle": {
                "supported_stop_conditions": sorted(
                    item.value for item in StopConditionKind
                ),
                "maximum_cycles": 100,
                "maximum_item_quantity": 100000,
                "maximum_duration_seconds": 86400.0,
                "maximum_actions": 500,
            },
            "recovery": {
                "reconcile_on_restart": True,
                "max_resource_no_yield_retries": 1,
                "max_bank_unavailable_frames": 2,
                "max_target_incomplete_frames": 2,
            },
            "navigation": {
                "intent": "fixed_route",
                "allow_polyline_reconciliation": True,
                "require_mandatory_transitions": True,
            },
            "provenance": {
                "fixture_schema": "REPLACE ME",
                "fixture_id": "REPLACE ME",
                "description": "REPLACE ME with what the evidence proves",
                "evidence_date": "REPLACE ME with YYYY-MM-DD",
                "baseline_parent": "0000000000000000000000000000000000000000",
                "proof_root": "REPLACE ME",
                "evidence_kind": "REPLACE ME",
                "limitations": "REPLACE ME; explicitly state what is not proven",
                "evidence": [
                    {
                        "path": "REPLACE ME",
                        "sha256": "0" * 64,
                        "proves": "REPLACE ME",
                    }
                ],
            },
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot.task_authoring",
        description="Strict authoring tools for immutable task definitions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate one definition JSON file")
    validate.add_argument("path", type=Path)
    validate.add_argument("--json", action="store_true", dest="as_json")

    inspect = commands.add_parser("inspect", help="show a concise validated summary")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--json", action="store_true", dest="as_json")

    explain = commands.add_parser("explain", help="explain the format and safety boundary")
    explain.add_argument("path", type=Path, nargs="?")
    explain.add_argument("--json", action="store_true", dest="as_json")

    scaffold = commands.add_parser(
        "scaffold", help="emit an intentionally non-runnable complete-shape template"
    )
    scaffold.add_argument("--output", type=Path)
    scaffold.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output path",
    )
    return parser


def _error_payload(error: Exception) -> dict[str, object]:
    return {"valid": False, "error": str(error)}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "scaffold":
        rendered = json.dumps(scaffold_document(), indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(rendered, end="")
            return 0
        if args.output.exists() and not args.force:
            print(
                f"refusing to replace existing scaffold output: {args.output}",
                file=sys.stderr,
            )
            return 2
        try:
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
        except OSError as error:
            print(f"unable to write {args.output}: {error}", file=sys.stderr)
            return 2
        print(f"wrote intentionally non-runnable scaffold: {args.output}")
        return 0

    try:
        definition = load_task_definition(args.path) if args.path is not None else None
    except (OSError, TaskDefinitionAuthoringError, TypeError, ValueError) as error:
        if getattr(args, "as_json", False):
            print(json.dumps(_error_payload(error), indent=2, sort_keys=True))
        else:
            print(f"invalid task definition: {error}", file=sys.stderr)
        return 2

    if args.command == "explain":
        explanation = _authoring_explanation(definition)
        if args.as_json:
            print(json.dumps(explanation, indent=2, sort_keys=True))
        else:
            print(f"schema: {TASK_DEFINITION_SCHEMA}")
            for rule in explanation["contract"]:
                print(f"- {rule}")
            print(
                "unsupported boundary: "
                + ", ".join(explanation["known_but_unsupported_capabilities"])
            )
            if definition is not None:
                print("\n" + _human_summary(definition))
        return 0

    assert definition is not None
    if args.command == "inspect":
        if args.as_json:
            print(json.dumps(task_definition_summary(definition), indent=2, sort_keys=True))
        else:
            print(_human_summary(definition))
        return 0

    canonical = canonical_task_definition_json(definition)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload = {
        "valid": True,
        "schema_version": TASK_DEFINITION_SCHEMA,
        "definition_id": definition.definition_id,
        "canonical_sha256": digest,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"valid {TASK_DEFINITION_SCHEMA}: {definition.definition_id} "
            f"(canonical sha256 {digest})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Mapping

from .contract_limits import MAX_PRIORITY_OBJECT_IDS
from .model import WorldPoint


_MIN_PLANE = 0
_MAX_PLANE = 3
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _require_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank text")


def _require_identifier(field_name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a lowercase identifier of at most 64 characters"
        )


def _require_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_nonnegative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_point(field_name: str, value: object) -> None:
    if not isinstance(value, WorldPoint):
        raise ValueError(f"{field_name} must be a WorldPoint")
    _require_nonnegative_int(f"{field_name}.x", value.x)
    _require_nonnegative_int(f"{field_name}.y", value.y)
    if (
        isinstance(value.plane, bool)
        or not isinstance(value.plane, int)
        or not _MIN_PLANE <= value.plane <= _MAX_PLANE
    ):
        raise ValueError(
            f"{field_name}.plane must be an integer from {_MIN_PLANE} to {_MAX_PLANE}"
        )


def _require_bool(field_name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")


def _require_id_set(field_name: str, value: object) -> None:
    if type(value) is not frozenset or not value:
        raise ValueError(f"{field_name} must be a nonempty frozenset")
    for item_id in value:
        _require_positive_int(f"{field_name} member", item_id)


def _require_optional_id_set(field_name: str, value: object) -> None:
    if type(value) is not frozenset:
        raise ValueError(f"{field_name} must be a frozenset")
    for item_id in value:
        _require_positive_int(f"{field_name} member", item_id)


def _require_text_tuple(field_name: str, value: object) -> None:
    if type(value) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    seen: set[str] = set()
    for item in value:
        _require_text(f"{field_name} member", item)
        if item in seen:
            raise ValueError(f"{field_name} must not contain duplicates")
        seen.add(item)


class TaskType(str, Enum):
    GATHERING = "gathering"
    COMBAT = "combat"
    QUEST = "quest"


class TaskCapability(str, Enum):
    """Definition requirements negotiated against the one production runtime."""

    GAME_OBJECT_INTERACTION = "game_object_interaction"
    FIXED_ROUTE_NAVIGATION = "fixed_route_navigation"
    OBJECT_ROUTE_TRANSITIONS = "object_route_transitions"
    BANK_DEPOSIT_ALL = "bank_deposit_all"
    TARGET_CONTINUITY = "target_continuity"
    CAMERA_ACQUISITION = "camera_acquisition"
    EQUIPMENT_OBSERVATION = "equipment_observation"
    SCHEDULED_START = "scheduled_start"
    COMPOSABLE_STOP_CONDITIONS = "composable_stop_conditions"
    RESTART_RECONCILIATION = "restart_reconciliation"
    FALLBACK_BANKS = "fallback_banks"
    BANK_WITHDRAWAL = "bank_withdrawal"
    EQUIPMENT_MANAGEMENT = "equipment_management"
    NPC_INTERACTION_GEOMETRY = "npc_interaction_geometry"
    MULTI_ITEM_YIELD_VERIFICATION = "multi_item_yield_verification"
    COMBAT_STATE_OBSERVATION = "combat_state_observation"
    HEALTH_POLICY = "health_policy"
    FOOD_POLICY = "food_policy"
    PRAYER_POLICY = "prayer_policy"
    COMBAT_TARGETING = "combat_targeting"
    LOOT_POLICY = "loot_policy"
    ESCAPE_POLICY = "escape_policy"
    RESUPPLY_POLICY = "resupply_policy"
    QUEST_STATE_PROVIDER = "quest_state_provider"
    QUEST_STEP_PRECONDITIONS = "quest_step_preconditions"
    QUEST_ITEM_ORCHESTRATION = "quest_item_orchestration"
    DIALOGUE_ORCHESTRATION = "dialogue_orchestration"
    TRAVEL_ORCHESTRATION = "travel_orchestration"
    QUESTHELPER_INTEGRATION = "questhelper_integration"
    VERSIONED_WIKI_KNOWLEDGE = "versioned_wiki_knowledge"


RUNTIME_SUPPORTED_CAPABILITIES = frozenset(
    {
        TaskCapability.GAME_OBJECT_INTERACTION,
        TaskCapability.FIXED_ROUTE_NAVIGATION,
        TaskCapability.OBJECT_ROUTE_TRANSITIONS,
        TaskCapability.BANK_DEPOSIT_ALL,
        TaskCapability.TARGET_CONTINUITY,
        TaskCapability.CAMERA_ACQUISITION,
        TaskCapability.EQUIPMENT_OBSERVATION,
        TaskCapability.SCHEDULED_START,
        TaskCapability.COMPOSABLE_STOP_CONDITIONS,
        TaskCapability.RESTART_RECONCILIATION,
    }
)


DEFAULT_GATHERING_CAPABILITIES = frozenset(
    {
        TaskCapability.GAME_OBJECT_INTERACTION,
        TaskCapability.FIXED_ROUTE_NAVIGATION,
        TaskCapability.OBJECT_ROUTE_TRANSITIONS,
        TaskCapability.BANK_DEPOSIT_ALL,
        TaskCapability.TARGET_CONTINUITY,
        TaskCapability.CAMERA_ACQUISITION,
        TaskCapability.SCHEDULED_START,
        TaskCapability.COMPOSABLE_STOP_CONDITIONS,
        TaskCapability.RESTART_RECONCILIATION,
    }
)


class TargetSelectionMode(str, Enum):
    GEOMETRY_THEN_DISTANCE = "geometry_then_distance"
    NEAREST = "nearest"


@dataclass(frozen=True, slots=True)
class TargetPolicy:
    selection_mode: TargetSelectionMode = TargetSelectionMode.GEOMETRY_THEN_DISTANCE
    max_candidates: int = 64
    max_rejection_evidence: int = 32
    incomplete_omission_wait_frames: int = 2
    query_radius_tiles: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.selection_mode, TargetSelectionMode):
            raise ValueError("selection_mode must be a TargetSelectionMode")
        for field_name, maximum in (
            ("max_candidates", 64),
            ("max_rejection_evidence", 64),
            ("incomplete_omission_wait_frames", 8),
            ("query_radius_tiles", 32),
        ):
            value = getattr(self, field_name)
            _require_positive_int(field_name, value)
            if value > maximum:
                raise ValueError(f"{field_name} must be at most {maximum}")


class StopConditionKind(str, Enum):
    CYCLES = "cycles"
    ITEM_QUANTITY = "item_quantity"
    INVENTORIES_BANKED = "inventories_banked"
    INVENTORY_FULL = "inventory_full"
    DURATION = "duration"
    ABSOLUTE_TIME = "absolute_time"


@dataclass(frozen=True, slots=True)
class LifecyclePolicy:
    supported_stop_conditions: frozenset[StopConditionKind] = frozenset(
        {
            StopConditionKind.CYCLES,
            StopConditionKind.ITEM_QUANTITY,
            StopConditionKind.INVENTORIES_BANKED,
            StopConditionKind.INVENTORY_FULL,
            StopConditionKind.DURATION,
            StopConditionKind.ABSOLUTE_TIME,
        }
    )
    maximum_cycles: int = 100
    maximum_item_quantity: int = 100_000
    maximum_duration_seconds: float = 86_400.0
    maximum_actions: int = 500

    def __post_init__(self) -> None:
        if type(self.supported_stop_conditions) is not frozenset or not self.supported_stop_conditions:
            raise ValueError("supported_stop_conditions must be a nonempty frozenset")
        if any(
            not isinstance(item, StopConditionKind)
            for item in self.supported_stop_conditions
        ):
            raise ValueError("supported_stop_conditions contains an unknown value")
        _require_positive_int("maximum_cycles", self.maximum_cycles)
        _require_positive_int("maximum_item_quantity", self.maximum_item_quantity)
        _require_positive_int("maximum_actions", self.maximum_actions)
        if (
            isinstance(self.maximum_duration_seconds, bool)
            or not isinstance(self.maximum_duration_seconds, (int, float))
            or not 0 < float(self.maximum_duration_seconds) <= 86_400.0
        ):
            raise ValueError(
                "maximum_duration_seconds must be positive and no more than 86400"
            )


@dataclass(frozen=True, slots=True)
class EquipmentPolicy:
    required_any_of_item_ids: frozenset[int] = frozenset()
    permitted_item_ids: frozenset[int] = frozenset()
    allow_inventory_fallback: bool = False
    auto_equip: bool = False

    def __post_init__(self) -> None:
        _require_optional_id_set(
            "required_any_of_item_ids", self.required_any_of_item_ids
        )
        _require_optional_id_set("permitted_item_ids", self.permitted_item_ids)
        if not self.required_any_of_item_ids.issubset(self.permitted_item_ids):
            raise ValueError(
                "required equipment items must be included in permitted_item_ids"
            )
        _require_bool("allow_inventory_fallback", self.allow_inventory_fallback)
        _require_bool("auto_equip", self.auto_equip)


@dataclass(frozen=True, slots=True)
class WithdrawalRule:
    item_id: int
    minimum_quantity: int
    target_quantity: int

    def __post_init__(self) -> None:
        _require_positive_int("item_id", self.item_id)
        _require_nonnegative_int("minimum_quantity", self.minimum_quantity)
        _require_positive_int("target_quantity", self.target_quantity)
        if self.minimum_quantity > self.target_quantity:
            raise ValueError("minimum_quantity cannot exceed target_quantity")


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    reconcile_on_restart: bool = True
    max_resource_no_yield_retries: int = 1
    max_bank_unavailable_frames: int = 2
    max_target_incomplete_frames: int = 2

    def __post_init__(self) -> None:
        _require_bool("reconcile_on_restart", self.reconcile_on_restart)
        for field_name, maximum in (
            ("max_resource_no_yield_retries", 4),
            ("max_bank_unavailable_frames", 8),
            ("max_target_incomplete_frames", 8),
        ):
            value = getattr(self, field_name)
            _require_nonnegative_int(field_name, value)
            if value > maximum:
                raise ValueError(f"{field_name} must be at most {maximum}")


@dataclass(frozen=True, slots=True)
class NavigationPolicy:
    intent: str = "fixed_route"
    allow_polyline_reconciliation: bool = True
    require_mandatory_transitions: bool = True

    def __post_init__(self) -> None:
        _require_identifier("intent", self.intent)
        if self.intent != "fixed_route":
            raise ValueError("only fixed_route navigation is currently supported")
        _require_bool(
            "allow_polyline_reconciliation", self.allow_polyline_reconciliation
        )
        _require_bool(
            "require_mandatory_transitions", self.require_mandatory_transitions
        )
        if not self.allow_polyline_reconciliation:
            raise ValueError(
                "the current fixed-route runtime requires polyline reconciliation"
            )
        if not self.require_mandatory_transitions:
            raise ValueError(
                "the current fixed-route runtime requires mandatory transitions"
            )


@dataclass(frozen=True, slots=True)
class ProvenanceEvidence:
    path: str
    sha256: str
    proves: str

    def __post_init__(self) -> None:
        _require_text("path", self.path)
        _require_text("proves", self.proves)
        if not isinstance(self.sha256, str) or _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True, slots=True)
class DefinitionProvenance:
    fixture_schema: str
    fixture_id: str
    description: str
    evidence_date: str
    baseline_parent: str
    proof_root: str
    evidence_kind: str
    limitations: str
    evidence: tuple[ProvenanceEvidence, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "fixture_schema",
            "fixture_id",
            "description",
            "evidence_date",
            "proof_root",
            "evidence_kind",
            "limitations",
        ):
            _require_text(field_name, getattr(self, field_name))
        if (
            not isinstance(self.baseline_parent, str)
            or _GIT_SHA_RE.fullmatch(self.baseline_parent) is None
        ):
            raise ValueError("baseline_parent must be a 40-character lowercase Git SHA")
        if type(self.evidence) is not tuple or not self.evidence:
            raise ValueError("evidence must be a nonempty tuple")
        if any(not isinstance(item, ProvenanceEvidence) for item in self.evidence):
            raise ValueError("evidence must contain only ProvenanceEvidence values")
        paths = tuple(item.path for item in self.evidence)
        if len(set(paths)) != len(paths):
            raise ValueError("evidence paths must be unique")


@dataclass(frozen=True, slots=True)
class ObjectSelector:
    object_ids: frozenset[int]
    name: str
    action: str

    def __post_init__(self) -> None:
        _require_id_set("object_ids", self.object_ids)
        if len(self.object_ids) > MAX_PRIORITY_OBJECT_IDS:
            raise ValueError(
                "object_ids must contain at most "
                f"{MAX_PRIORITY_OBJECT_IDS} priority object IDs"
            )
        _require_text("name", self.name)
        _require_text("action", self.action)


@dataclass(frozen=True, slots=True)
class RadialWorkArea:
    anchor: WorldPoint
    radius: int
    area_id: str = "work_area"
    allowed_planes: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        _require_point("anchor", self.anchor)
        _require_positive_int("radius", self.radius)
        _require_identifier("area_id", self.area_id)
        planes = self.allowed_planes or frozenset({self.anchor.plane})
        if type(planes) is not frozenset or not planes:
            raise ValueError("allowed_planes must be a nonempty frozenset")
        for plane in planes:
            if (
                isinstance(plane, bool)
                or not isinstance(plane, int)
                or not _MIN_PLANE <= plane <= _MAX_PLANE
            ):
                raise ValueError(
                    f"allowed_planes members must be integers from {_MIN_PLANE} to {_MAX_PLANE}"
                )
        if self.anchor.plane not in planes:
            raise ValueError("allowed_planes must include the anchor plane")
        object.__setattr__(self, "allowed_planes", planes)


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    selector: ObjectSelector
    produced_item_ids: frozenset[int]
    work_area: RadialWorkArea
    resource_id: str = "resource"
    interaction_kind: str = "game_object"

    def __post_init__(self) -> None:
        if not isinstance(self.selector, ObjectSelector):
            raise ValueError("selector must be an ObjectSelector")
        _require_id_set("produced_item_ids", self.produced_item_ids)
        if not isinstance(self.work_area, RadialWorkArea):
            raise ValueError("work_area must be a RadialWorkArea")
        _require_identifier("resource_id", self.resource_id)
        _require_identifier("interaction_kind", self.interaction_kind)
        if self.interaction_kind != "game_object":
            raise ValueError(
                "only game_object resources have production targeting geometry"
            )


@dataclass(frozen=True, slots=True)
class BankDefinition:
    selector: ObjectSelector
    anchor: WorldPoint
    interaction_radius: int
    bank_id: str = "preferred_bank"
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.selector, ObjectSelector):
            raise ValueError("selector must be an ObjectSelector")
        _require_point("anchor", self.anchor)
        _require_positive_int("interaction_radius", self.interaction_radius)
        _require_identifier("bank_id", self.bank_id)
        _require_nonnegative_int("priority", self.priority)


class RouteStepKind(str, Enum):
    WALK = "walk"
    OBJECT = "object"


class RoutePointClassification(str, Enum):
    MANDATORY_TRANSITION = "mandatory_transition"
    MANDATORY_TURN = "mandatory_turn"
    ARRIVAL_POINT = "arrival_point"
    NORMAL_GUIDANCE = "normal_guidance"


@dataclass(frozen=True, slots=True)
class FixedRouteStep:
    step_id: str
    kind: RouteStepKind
    location: WorldPoint
    arrival_radius: int
    action: str
    classification: RoutePointClassification = RoutePointClassification.NORMAL_GUIDANCE
    object_id: int | None = None
    object_name: str | None = None
    alternate_actions: tuple[str, ...] = ()
    expected_plane: int | None = None

    def __post_init__(self) -> None:
        _require_text("step_id", self.step_id)
        if not isinstance(self.kind, RouteStepKind):
            raise ValueError("kind must be a RouteStepKind")
        _require_point("location", self.location)
        _require_positive_int("arrival_radius", self.arrival_radius)
        _require_text("action", self.action)
        _require_text_tuple("alternate_actions", self.alternate_actions)
        if not isinstance(self.classification, RoutePointClassification):
            raise ValueError("classification must be a RoutePointClassification")

        if self.kind is RouteStepKind.WALK:
            if self.action != "Walk here":
                raise ValueError("walk route steps must use the exact Walk here action")
            if (
                self.object_id is not None
                or self.object_name is not None
                or self.alternate_actions
                or self.expected_plane is not None
            ):
                raise ValueError("walk route steps cannot carry object transition facts")
            if self.classification is RoutePointClassification.MANDATORY_TRANSITION:
                raise ValueError("walk route steps cannot be mandatory transitions")
            return

        if self.classification is not RoutePointClassification.MANDATORY_TRANSITION:
            raise ValueError("object route steps must be mandatory transitions")

        _require_positive_int("object_id", self.object_id)
        _require_text("object_name", self.object_name)
        if (
            isinstance(self.expected_plane, bool)
            or not isinstance(self.expected_plane, int)
            or not _MIN_PLANE <= self.expected_plane <= _MAX_PLANE
        ):
            raise ValueError(
                f"expected_plane must be an integer from {_MIN_PLANE} to {_MAX_PLANE}"
            )
        if self.expected_plane == self.location.plane:
            raise ValueError("object transition must change planes")

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return (self.action, *self.alternate_actions)

    @property
    def is_walk(self) -> bool:
        return self.kind is RouteStepKind.WALK

    @property
    def target_key(self) -> str:
        return f"route:{self.step_id}"


@dataclass(frozen=True, slots=True)
class FixedRoute:
    route_id: str
    start_anchor: WorldPoint
    destination_anchor: WorldPoint
    steps: tuple[FixedRouteStep, ...]

    def __post_init__(self) -> None:
        _require_text("route_id", self.route_id)
        _require_point("start_anchor", self.start_anchor)
        _require_point("destination_anchor", self.destination_anchor)
        if type(self.steps) is not tuple or not self.steps:
            raise ValueError("steps must be a nonempty tuple")
        if any(not isinstance(step, FixedRouteStep) for step in self.steps):
            raise ValueError("steps must contain only FixedRouteStep values")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("route step IDs must be unique")

        current_plane = self.start_anchor.plane
        for step in self.steps:
            if step.location.plane != current_plane:
                raise ValueError(
                    f"route step {step.step_id} starts on plane {step.location.plane}, "
                    f"but the preceding route state is plane {current_plane}"
                )
            if step.kind is RouteStepKind.OBJECT:
                assert step.expected_plane is not None
                current_plane = step.expected_plane

        if self.steps[-1].location != self.destination_anchor:
            raise ValueError("the final route step must equal the destination anchor")
        if self.steps[-1].classification is not RoutePointClassification.ARRIVAL_POINT:
            raise ValueError("the final route step must be classified as an arrival point")
        if any(
            step.classification is RoutePointClassification.ARRIVAL_POINT
            for step in self.steps[:-1]
        ):
            raise ValueError("only the final route step may be an arrival point")
        if current_plane != self.destination_anchor.plane:
            raise ValueError("the route's final transition plane must match its destination")


@dataclass(frozen=True, slots=True)
class InventoryPredicate:
    allowed_item_ids: frozenset[int]
    deposit_item_ids: frozenset[int]
    require_only_allowed_items: bool
    require_nonempty_deposit: bool
    require_produced_item_when_full: bool
    retain_item_ids: frozenset[int] = frozenset()
    withdrawal_rules: tuple[WithdrawalRule, ...] = ()
    minimum_free_slots: int = 1

    def __post_init__(self) -> None:
        _require_id_set("allowed_item_ids", self.allowed_item_ids)
        _require_id_set("deposit_item_ids", self.deposit_item_ids)
        if not self.deposit_item_ids.issubset(self.allowed_item_ids):
            raise ValueError("deposit_item_ids must be allowed by the inventory predicate")
        _require_optional_id_set("retain_item_ids", self.retain_item_ids)
        if not self.retain_item_ids.issubset(self.allowed_item_ids):
            raise ValueError("retain_item_ids must be allowed by the inventory predicate")
        if self.deposit_item_ids & self.retain_item_ids:
            raise ValueError("deposit_item_ids and retain_item_ids must be disjoint")
        if self.allowed_item_ids != self.deposit_item_ids | self.retain_item_ids:
            raise ValueError(
                "allowed_item_ids must equal the deposited and retained item sets"
            )
        if type(self.withdrawal_rules) is not tuple or any(
            not isinstance(rule, WithdrawalRule) for rule in self.withdrawal_rules
        ):
            raise ValueError("withdrawal_rules must contain WithdrawalRule values")
        withdrawal_ids = tuple(rule.item_id for rule in self.withdrawal_rules)
        if len(withdrawal_ids) != len(set(withdrawal_ids)):
            raise ValueError("withdrawal_rules item IDs must be unique")
        if not set(withdrawal_ids).issubset(self.retain_item_ids):
            raise ValueError(
                "withdrawal_rules items must be retained by the inventory predicate"
            )
        _require_positive_int("minimum_free_slots", self.minimum_free_slots)
        if self.minimum_free_slots > 28:
            raise ValueError("minimum_free_slots must be at most 28")
        for field_name in (
            "require_only_allowed_items",
            "require_nonempty_deposit",
            "require_produced_item_when_full",
        ):
            _require_bool(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class VerificationExpectations:
    action_deadline_ticks: int
    movement_deadline_ticks: int
    resource_deadline_ticks: int
    route_stable_ticks: int
    deposit_expected_quantity: int
    transition_dialogue_prompt_contains: str
    transition_up_option_contains: str
    transition_down_option_contains: str

    def __post_init__(self) -> None:
        _require_positive_int("action_deadline_ticks", self.action_deadline_ticks)
        _require_positive_int("movement_deadline_ticks", self.movement_deadline_ticks)
        _require_positive_int("resource_deadline_ticks", self.resource_deadline_ticks)
        _require_positive_int("route_stable_ticks", self.route_stable_ticks)
        _require_nonnegative_int("deposit_expected_quantity", self.deposit_expected_quantity)
        for field_name in (
            "transition_dialogue_prompt_contains",
            "transition_up_option_contains",
            "transition_down_option_contains",
        ):
            _require_text(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class TaskSiteDefinition:
    definition_id: str
    display_name: str
    version: int
    resource: ResourceDefinition
    bank: BankDefinition
    route_to_bank: FixedRoute
    route_to_resource: FixedRoute
    inventory: InventoryPredicate
    verification: VerificationExpectations
    provenance: DefinitionProvenance
    task_type: TaskType = TaskType.GATHERING
    capabilities: frozenset[TaskCapability] = DEFAULT_GATHERING_CAPABILITIES
    target_policy: TargetPolicy = TargetPolicy()
    fallback_banks: tuple[BankDefinition, ...] = ()
    equipment: EquipmentPolicy = EquipmentPolicy()
    lifecycle: LifecyclePolicy = LifecyclePolicy()
    recovery: RecoveryPolicy = RecoveryPolicy()
    navigation: NavigationPolicy = NavigationPolicy()

    def __post_init__(self) -> None:
        _require_text("definition_id", self.definition_id)
        _require_text("display_name", self.display_name)
        _require_positive_int("version", self.version)
        required_types = (
            ("resource", self.resource, ResourceDefinition),
            ("bank", self.bank, BankDefinition),
            ("route_to_bank", self.route_to_bank, FixedRoute),
            ("route_to_resource", self.route_to_resource, FixedRoute),
            ("inventory", self.inventory, InventoryPredicate),
            ("verification", self.verification, VerificationExpectations),
            ("provenance", self.provenance, DefinitionProvenance),
        )
        for field_name, value, expected_type in required_types:
            if not isinstance(value, expected_type):
                raise ValueError(f"{field_name} must be a {expected_type.__name__}")

        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        if type(self.capabilities) is not frozenset or not self.capabilities:
            raise ValueError("capabilities must be a nonempty frozenset")
        if any(
            not isinstance(capability, TaskCapability)
            for capability in self.capabilities
        ):
            raise ValueError("capabilities contains an unknown value")
        for field_name, value, expected_type in (
            ("target_policy", self.target_policy, TargetPolicy),
            ("equipment", self.equipment, EquipmentPolicy),
            ("lifecycle", self.lifecycle, LifecyclePolicy),
            ("recovery", self.recovery, RecoveryPolicy),
            ("navigation", self.navigation, NavigationPolicy),
        ):
            if not isinstance(value, expected_type):
                raise ValueError(f"{field_name} must be a {expected_type.__name__}")
        if type(self.fallback_banks) is not tuple or any(
            not isinstance(bank, BankDefinition) for bank in self.fallback_banks
        ):
            raise ValueError("fallback_banks must contain BankDefinition values")
        bank_ids = (self.bank.bank_id, *(bank.bank_id for bank in self.fallback_banks))
        if len(bank_ids) != len(set(bank_ids)):
            raise ValueError("preferred and fallback bank IDs must be unique")
        bank_priorities = (
            self.bank.priority,
            *(bank.priority for bank in self.fallback_banks),
        )
        if len(bank_priorities) != len(set(bank_priorities)):
            raise ValueError("preferred and fallback bank priorities must be unique")
        if any(
            fallback.priority <= self.bank.priority
            for fallback in self.fallback_banks
        ):
            raise ValueError("fallback bank priorities must follow the preferred bank")
        if self.fallback_banks and TaskCapability.FALLBACK_BANKS not in self.capabilities:
            raise ValueError("fallback_banks requires the fallback_banks capability")
        required_gathering_capabilities = frozenset(
            {
                TaskCapability.GAME_OBJECT_INTERACTION,
                TaskCapability.FIXED_ROUTE_NAVIGATION,
                TaskCapability.BANK_DEPOSIT_ALL,
                TaskCapability.TARGET_CONTINUITY,
                TaskCapability.CAMERA_ACQUISITION,
            }
        )
        missing_gathering_capabilities = (
            required_gathering_capabilities - self.capabilities
        )
        if self.task_type is TaskType.GATHERING and missing_gathering_capabilities:
            names = sorted(item.value for item in missing_gathering_capabilities)
            raise ValueError(
                f"gathering definition is missing required capabilities: {names}"
            )
        if any(
            step.kind is RouteStepKind.OBJECT
            for route in (self.route_to_bank, self.route_to_resource)
            for step in route.steps
        ) and TaskCapability.OBJECT_ROUTE_TRANSITIONS not in self.capabilities:
            raise ValueError(
                "object route steps require the object_route_transitions capability"
            )
        if (
            self.inventory.withdrawal_rules
            and TaskCapability.BANK_WITHDRAWAL not in self.capabilities
        ):
            raise ValueError("withdrawal_rules requires the bank_withdrawal capability")
        if self.inventory.retain_item_ids and not self.inventory.withdrawal_rules:
            raise ValueError(
                "retained inventory items require explicit withdrawal rules after deposit-all"
            )
        if (
            self.equipment.required_any_of_item_ids
            and TaskCapability.EQUIPMENT_OBSERVATION not in self.capabilities
        ):
            raise ValueError(
                "required equipment requires the equipment_observation capability"
            )
        if (
            self.equipment.auto_equip
            and TaskCapability.EQUIPMENT_MANAGEMENT not in self.capabilities
        ):
            raise ValueError("auto_equip requires the equipment_management capability")
        if (
            self.equipment.allow_inventory_fallback
            and not self.equipment.required_any_of_item_ids.issubset(
                self.inventory.retain_item_ids
            )
        ):
            raise ValueError(
                "inventory equipment fallback items must be retained and resupplied"
            )

        produced = self.resource.produced_item_ids
        if (
            len(produced) != 1
            and TaskCapability.MULTI_ITEM_YIELD_VERIFICATION
            not in self.capabilities
        ):
            raise ValueError(
                "multiple produced items require the multi_item_yield_verification capability"
            )
        if not produced.issubset(self.inventory.allowed_item_ids):
            raise ValueError("every produced item must be allowed by the inventory predicate")
        if not produced.issubset(self.inventory.deposit_item_ids):
            raise ValueError("every produced item must be included in the deposit predicate")
        if self.inventory.deposit_item_ids != produced:
            raise ValueError(
                "the current gathering verifier requires deposit_item_ids to equal produced_item_ids"
            )
        if not self.inventory.require_only_allowed_items:
            raise ValueError(
                "the current deposit-all runtime requires only allowed inventory items"
            )
        if not self.inventory.require_nonempty_deposit:
            raise ValueError(
                "the current deposit verifier requires a nonempty deposit"
            )
        if not self.inventory.require_produced_item_when_full:
            raise ValueError(
                "the current gathering runtime requires produced-item evidence at the bank threshold"
            )
        if self.verification.deposit_expected_quantity != 0:
            raise ValueError(
                "the current deposit-all verifier requires deposit_expected_quantity to be zero"
            )
        if self.route_to_bank.start_anchor != self.resource.work_area.anchor:
            raise ValueError("the bank route must start at the resource anchor")
        if self.route_to_bank.destination_anchor != self.bank.anchor:
            raise ValueError("the bank route must end at the bank anchor")
        if self.route_to_resource.start_anchor != self.bank.anchor:
            raise ValueError("the resource route must start at the bank anchor")
        if self.route_to_resource.destination_anchor != self.resource.work_area.anchor:
            raise ValueError("the resource route must end at the resource anchor")
        if self.route_to_bank.route_id == self.route_to_resource.route_id:
            raise ValueError("route IDs must be unique")
        all_step_ids = tuple(
            step.step_id
            for route in (self.route_to_bank, self.route_to_resource)
            for step in route.steps
        )
        if len(set(all_step_ids)) != len(all_step_ids):
            raise ValueError("route step IDs must be unique across the definition")

    @property
    def unsupported_capabilities(self) -> frozenset[TaskCapability]:
        return self.capabilities - RUNTIME_SUPPORTED_CAPABILITIES

    @property
    def operating_areas(self) -> tuple[RadialWorkArea, ...]:
        return (
            self.resource.work_area,
            RadialWorkArea(
                self.bank.anchor,
                self.bank.interaction_radius,
                area_id=self.bank.bank_id,
            ),
            *(
                RadialWorkArea(
                    bank.anchor,
                    bank.interaction_radius,
                    area_id=bank.bank_id,
                )
                for bank in self.fallback_banks
            ),
        )


def _walk(
    step_id: str,
    x: int,
    y: int,
    plane: int,
    arrival_radius: int,
    classification: RoutePointClassification = RoutePointClassification.NORMAL_GUIDANCE,
) -> FixedRouteStep:
    return FixedRouteStep(
        step_id=step_id,
        kind=RouteStepKind.WALK,
        location=WorldPoint(x, y, plane),
        arrival_radius=arrival_radius,
        action="Walk here",
        classification=classification,
    )


def _transition(
    step_id: str,
    x: int,
    y: int,
    plane: int,
    arrival_radius: int,
    object_id: int,
    action: str,
    expected_plane: int,
) -> FixedRouteStep:
    return FixedRouteStep(
        step_id=step_id,
        kind=RouteStepKind.OBJECT,
        location=WorldPoint(x, y, plane),
        arrival_radius=arrival_radius,
        action=action,
        classification=RoutePointClassification.MANDATORY_TRANSITION,
        object_id=object_id,
        object_name="Staircase",
        alternate_actions=("Climb",),
        expected_plane=expected_plane,
    )


_TREE_ANCHOR = WorldPoint(3196, 3244, 0)
_BANK_ANCHOR = WorldPoint(3208, 3221, 2)


LUMBRIDGE_WEST_TREES_V1 = TaskSiteDefinition(
    definition_id="lumbridge_west_trees_v1",
    display_name="Lumbridge West ordinary Trees to Lumbridge Castle bank",
    version=1,
    resource=ResourceDefinition(
        selector=ObjectSelector(frozenset({1276}), "Tree", "Chop down"),
        produced_item_ids=frozenset({1511}),
        work_area=RadialWorkArea(
            _TREE_ANCHOR,
            16,
            area_id="lumbridge_west_tree_area",
        ),
        resource_id="ordinary_tree",
    ),
    bank=BankDefinition(
        selector=ObjectSelector(frozenset({18491}), "Bank booth", "Bank"),
        anchor=_BANK_ANCHOR,
        interaction_radius=6,
        bank_id="lumbridge_castle_bank",
    ),
    route_to_bank=FixedRoute(
        route_id="lumbridge_west_trees_to_castle_bank",
        start_anchor=_TREE_ANCHOR,
        destination_anchor=_BANK_ANCHOR,
        steps=(
            _walk("tree_lane_exit", 3196, 3244, 0, 1),
            _walk(
                "west_approach_bridge",
                3200,
                3238,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("west_corridor_north", 3196, 3237, 0, 2),
            _walk(
                "west_wall_corner",
                3196,
                3234,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("west_wall_descent_1", 3197, 3231, 0, 1),
            _walk("west_wall_descent_2", 3198, 3228, 0, 1),
            _walk("west_wall_descent_3", 3197, 3225, 0, 1),
            _walk("west_wall_descent_4", 3197, 3222, 0, 1),
            _walk("west_corridor_south", 3197, 3221, 0, 2),
            _walk(
                "south_corridor_entry",
                3199,
                3218,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("south_corridor_west", 3202, 3215, 0, 2),
            _walk("south_corridor_bridge", 3205, 3214, 0, 1),
            _walk(
                "south_corridor_safe",
                3208,
                3212,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk(
                "south_stairs_approach",
                3205,
                3209,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _transition("ground_floor_stairs_up", 3204, 3207, 0, 4, 56230, "Climb-up", 1),
            _transition("first_floor_stairs_up", 3204, 3207, 1, 4, 16672, "Climb-up", 2),
            _walk("bank_floor_south_1", 3205, 3211, 2, 2),
            _walk("bank_floor_south_2", 3205, 3215, 2, 2),
            _walk("bank_floor_north", 3207, 3218, 2, 2),
            _walk(
                "bank_booth_approach",
                3208,
                3221,
                2,
                2,
                RoutePointClassification.ARRIVAL_POINT,
            ),
        ),
    ),
    route_to_resource=FixedRoute(
        route_id="lumbridge_castle_bank_to_west_trees",
        start_anchor=_BANK_ANCHOR,
        destination_anchor=_TREE_ANCHOR,
        steps=(
            _walk(
                "bank_booth_exit_turn",
                3206,
                3222,
                2,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("bank_floor_return_1", 3206, 3226, 2, 2),
            _walk("bank_floor_return_2", 3206, 3228, 2, 1),
            _transition("bank_floor_bottom", 3205, 3229, 2, 3, 56231, "Bottom-floor", 0),
            _walk("ground_corridor_east_1", 3210, 3228, 0, 2),
            _walk("ground_corridor_east_mid", 3213, 3228, 0, 1),
            _walk(
                "ground_corridor_east_2",
                3215,
                3228,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("ground_corridor_south_1", 3215, 3225, 0, 2),
            _walk("ground_corridor_south_2", 3215, 3222, 0, 2),
            _walk(
                "ground_corridor_south_3",
                3215,
                3219,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("ground_corridor_west_mid", 3213, 3219, 0, 1),
            _walk("ground_corridor_west", 3211, 3219, 0, 2),
            _walk(
                "ground_corridor_west_turn",
                3210,
                3216,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("ground_corridor_west_2", 3207, 3214, 0, 2),
            _walk(
                "south_corridor_return",
                3203,
                3214,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk(
                "south_corridor_entry_return",
                3199,
                3218,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk(
                "west_corridor_return_1",
                3197,
                3221,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("west_wall_ascent_1", 3197, 3225, 0, 1),
            _walk("west_wall_ascent_2", 3198, 3228, 0, 1),
            _walk("west_wall_ascent_3", 3197, 3231, 0, 1),
            _walk(
                "west_wall_corner_return",
                3196,
                3234,
                0,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("west_corridor_north_return", 3196, 3237, 0, 2),
            _walk("tree_lane_return", 3196, 3240, 0, 2),
            _walk(
                "west_trees",
                3196,
                3244,
                0,
                2,
                RoutePointClassification.ARRIVAL_POINT,
            ),
        ),
    ),
    inventory=InventoryPredicate(
        allowed_item_ids=frozenset({1511}),
        deposit_item_ids=frozenset({1511}),
        require_only_allowed_items=True,
        require_nonempty_deposit=True,
        require_produced_item_when_full=True,
    ),
    verification=VerificationExpectations(
        action_deadline_ticks=8,
        movement_deadline_ticks=20,
        resource_deadline_ticks=100,
        route_stable_ticks=4,
        deposit_expected_quantity=0,
        transition_dialogue_prompt_contains="climb",
        transition_up_option_contains="climb up",
        transition_down_option_contains="climb down",
    ),
    provenance=DefinitionProvenance(
        fixture_schema="osrs_bot.golden_cycle.v1",
        fixture_id="woodcut-lumbridge-west-castle-bank-return",
        description=(
            "Sanitized deterministic semantic replay of the proven ordinary-log cycle "
            "with a live-evidenced tree-lane route anchor."
        ),
        evidence_date="2026-07-13",
        baseline_parent="a843915c68b1c8ab2bcc0661ad467ec7bfa231b1",
        proof_root="_run_proofs",
        evidence_kind=(
            "stitched bounded live component traces plus current live "
            "route-recovery evidence"
        ),
        limitations=(
            "The earlier ignored live traces were resumed across bounded runs while source "
            "was changing. They prove the component interactions and terminal COMPLETE state, "
            "but are not one uninterrupted raw Observation or SafetyGate replay. The "
            "2026-07-13 production receipt additionally proves a full inventory at "
            "(3194,3248,0), a safe terminal cleanup, and the missing tree-lane reentry that "
            "motivated the corrected anchor; its stopped run did not reach the bank. This "
            "committed fixture freezes the semantic FSM, corrected definition facts, and "
            "typed verification sequence without retaining process IDs, session IDs, or "
            "screen coordinates."
        ),
        evidence=(
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_152906/trace.jsonl",
                "8f9ee6680b1c1b7ff0e27068b252f8ceea2a041bfcba4449fbf2c4250c99d32f",
                "final log gain to a 28-log inventory and bank-route selection",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_161528/trace.jsonl",
                "059a5d6de13619a0f4bc3151f4539f89b5bf56a81dd0cf564cf787148a50f33b",
                "ground-floor staircase transition to plane 1",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_163433/trace.jsonl",
                "5c3c824c2d80e423bd65dabefcafcd76a857dc66aaf97cc36d291c9fe14d6dd2",
                "first-floor staircase transition to plane 2",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_163648/trace.jsonl",
                "6268071f25971427cad9be9f319bf6f89e96a7012fa83a698b20ff4105fe6707",
                "exact bank open and verified 28-to-0 log deposit",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_165547/trace.jsonl",
                "560dc54a66b0c72f39447e2c76d42eb9bdbcadc69bea636edcd5a3f8745c55c7",
                "verified Escape bank close and return-route start",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_170631/trace.jsonl",
                "acfa9336e5e278ed0828a076e047efd6acb6445fa2825fd603dfe9362167ebf0",
                "bounded return-route continuation",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_170659/trace.jsonl",
                "5b9a32e6f0734eb7e9042137c92fbfa03dec69068be03aaba12a68dea1839906",
                "bounded return-route continuation",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_170818/trace.jsonl",
                "acaccd0ced677d42f0f3a1a098b9c28a6b2b569e753152d29886f7b42c681530",
                "arrival at the final tree-lane waypoint",
            ),
            ProvenanceEvidence(
                "_run_proofs/vertical_slice/20260710_170849/trace.jsonl",
                "86df453ec2470cb8acf744ea87b7c9f7cabb99309e4846d4397e7177bd7a7f9a",
                "terminal COMPLETE state and acknowledged STOP_ALL/DISARM cleanup",
            ),
            ProvenanceEvidence(
                "_run_proofs/movement_targeting_quality/"
                "20260713T055558.877889Z/run_receipt.json",
                "4f04d7b784321d51d43c8dc913e3cf48bc4990f5faeab0d155a201f465c0326f",
                "full inventory north of the historical tree anchor, missing route "
                "reentry, and acknowledged safe-stop cleanup",
            ),
        ),
    ),
)


_COPPER_ANCHOR = WorldPoint(3226, 3146, 0)
_PICKAXE_ITEM_IDS = frozenset(
    {
        1265,   # Bronze pickaxe
        1267,   # Iron pickaxe
        1269,   # Steel pickaxe
        1271,   # Adamant pickaxe
        1273,   # Mithril pickaxe
        1275,   # Rune pickaxe
        11920,  # Dragon pickaxe
        12297,  # Black pickaxe
        13243,  # Infernal pickaxe
        23680,  # Crystal pickaxe
    }
)


LUMBRIDGE_SWAMP_COPPER_V1 = TaskSiteDefinition(
    definition_id="lumbridge_swamp_copper_v1",
    display_name="Lumbridge Swamp East copper to Lumbridge Castle bank",
    version=1,
    resource=ResourceDefinition(
        selector=ObjectSelector(frozenset({10943, 11161}), "Rocks", "Mine"),
        produced_item_ids=frozenset({436}),
        work_area=RadialWorkArea(
            _COPPER_ANCHOR,
            12,
            area_id="lumbridge_swamp_east_copper_area",
        ),
        resource_id="copper_rock",
    ),
    bank=BankDefinition(
        selector=ObjectSelector(frozenset({18491}), "Bank booth", "Bank"),
        anchor=_BANK_ANCHOR,
        interaction_radius=6,
        bank_id="lumbridge_castle_bank",
    ),
    route_to_bank=FixedRoute(
        route_id="lumbridge_swamp_copper_to_castle_bank",
        start_anchor=_COPPER_ANCHOR,
        destination_anchor=_BANK_ANCHOR,
        steps=(
            _walk("copper_mine_depart", 3226, 3146, 0, 2),
            _walk("swamp_north_1", 3225, 3156, 0, 2),
            _walk("swamp_north_2", 3223, 3167, 0, 2),
            _walk("swamp_north_3", 3222, 3180, 0, 2),
            _walk("swamp_north_4", 3220, 3192, 0, 2),
            _walk("swamp_north_5", 3218, 3203, 0, 2),
            _walk(
                "castle_south_entry",
                3215,
                3211,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("castle_south_corridor", 3208, 3212, 0, 2),
            _walk(
                "copper_south_stairs_approach",
                3205,
                3209,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _transition(
                "copper_ground_floor_stairs_up",
                3204,
                3207,
                0,
                4,
                56230,
                "Climb-up",
                1,
            ),
            _transition(
                "copper_first_floor_stairs_up",
                3204,
                3207,
                1,
                4,
                16672,
                "Climb-up",
                2,
            ),
            _walk("copper_bank_floor_south_1", 3205, 3211, 2, 2),
            _walk("copper_bank_floor_south_2", 3205, 3215, 2, 2),
            _walk("copper_bank_floor_north", 3207, 3218, 2, 2),
            _walk(
                "copper_bank_booth_approach",
                3208,
                3221,
                2,
                2,
                RoutePointClassification.ARRIVAL_POINT,
            ),
        ),
    ),
    route_to_resource=FixedRoute(
        route_id="lumbridge_castle_bank_to_swamp_copper",
        start_anchor=_BANK_ANCHOR,
        destination_anchor=_COPPER_ANCHOR,
        steps=(
            _walk(
                "copper_bank_booth_exit_turn",
                3206,
                3222,
                2,
                1,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("copper_bank_floor_return_1", 3206, 3226, 2, 2),
            _walk("copper_bank_floor_return_2", 3206, 3228, 2, 1),
            _transition(
                "copper_bank_floor_bottom",
                3205,
                3229,
                2,
                3,
                56231,
                "Bottom-floor",
                0,
            ),
            _walk("copper_ground_corridor_east_1", 3210, 3228, 0, 2),
            _walk(
                "copper_ground_corridor_east_2",
                3215,
                3228,
                0,
                2,
                RoutePointClassification.MANDATORY_TURN,
            ),
            _walk("copper_castle_exit", 3215, 3219, 0, 2),
            _walk("copper_swamp_return_1", 3217, 3208, 0, 2),
            _walk("copper_swamp_return_2", 3219, 3196, 0, 2),
            _walk("copper_swamp_return_3", 3221, 3183, 0, 2),
            _walk("copper_swamp_return_4", 3223, 3168, 0, 2),
            _walk("copper_swamp_return_5", 3225, 3157, 0, 2),
            _walk(
                "copper_mine_arrival",
                3226,
                3146,
                0,
                2,
                RoutePointClassification.ARRIVAL_POINT,
            ),
        ),
    ),
    inventory=InventoryPredicate(
        allowed_item_ids=frozenset({436}),
        deposit_item_ids=frozenset({436}),
        require_only_allowed_items=True,
        require_nonempty_deposit=True,
        require_produced_item_when_full=True,
    ),
    verification=VerificationExpectations(
        action_deadline_ticks=8,
        movement_deadline_ticks=20,
        resource_deadline_ticks=100,
        route_stable_ticks=4,
        deposit_expected_quantity=0,
        transition_dialogue_prompt_contains="climb",
        transition_up_option_contains="climb up",
        transition_down_option_contains="climb down",
    ),
    provenance=DefinitionProvenance(
        fixture_schema="osrs_bot.synthetic_gathering.v1",
        fixture_id="mining-lumbridge-swamp-east-castle-bank-return",
        description=(
            "A deterministic copper-mining definition using RuneLite generated object "
            "and item constants plus the RuneLite mining-site anchor."
        ),
        evidence_date="2026-07-16",
        baseline_parent="ac9e3ba7a92f152ac1ea214b21a8a830dd198753",
        proof_root="_run_proofs/task_platform",
        evidence_kind="upstream generated constants and authored deterministic route",
        limitations=(
            "The copper object IDs, ore item ID, pickaxe IDs, and mine anchor are "
            "upstream-derived. The fixed route reuses the existing proven Lumbridge "
            "Castle staircase transitions, but the swamp surface waypoints have not "
            "been live-replayed in this checkout. They are authored configuration, not "
            "current live proof; production-use claims require a loaded-scene rehearsal."
        ),
        evidence=(
            ProvenanceEvidence(
                "https://raw.githubusercontent.com/runelite/runelite/master/"
                "runelite-api/src/main/java/net/runelite/api/gameval/ObjectID.java",
                "1eb19a73b335c7f3b4e470ad38eed2232684f13fcf3172f46d79dc2223a9c321",
                "generated Copper rocks object IDs 10943 and 11161",
            ),
            ProvenanceEvidence(
                "https://raw.githubusercontent.com/runelite/runelite/master/"
                "runelite-api/src/main/java/net/runelite/api/gameval/ItemID.java",
                "d524a9e2e7ca4255b4499484d1e4bc8be637c79e9494b464c5cb16e01c358c6e",
                "generated copper ore and supported pickaxe item IDs",
            ),
            ProvenanceEvidence(
                "https://raw.githubusercontent.com/runelite/runelite/master/"
                "runelite-client/src/main/java/net/runelite/client/plugins/mining/"
                "MiningSiteLocation.java",
                "fb3748f83b6a98bdc6fbaa917e256a82c9ce1f39588acd79f01eb598e021fa2d",
                "Lumbridge Swamp East mine anchor and copper-rock count",
            ),
        ),
    ),
    capabilities=DEFAULT_GATHERING_CAPABILITIES
    | frozenset({TaskCapability.EQUIPMENT_OBSERVATION}),
    equipment=EquipmentPolicy(
        required_any_of_item_ids=_PICKAXE_ITEM_IDS,
        permitted_item_ids=_PICKAXE_ITEM_IDS,
    ),
)


BUILTIN_DEFINITIONS = (
    LUMBRIDGE_WEST_TREES_V1,
    LUMBRIDGE_SWAMP_COPPER_V1,
)
BUILTIN_DEFINITIONS_BY_ID: Mapping[str, TaskSiteDefinition] = MappingProxyType(
    {definition.definition_id: definition for definition in BUILTIN_DEFINITIONS}
)


def list_builtin_definitions() -> tuple[TaskSiteDefinition, ...]:
    return BUILTIN_DEFINITIONS


def get_builtin_definition(definition_id: str) -> TaskSiteDefinition:
    _require_identifier("definition_id", definition_id)
    try:
        return BUILTIN_DEFINITIONS_BY_ID[definition_id]
    except KeyError as error:
        raise ValueError(f"unsupported definition_id: {definition_id!r}") from error

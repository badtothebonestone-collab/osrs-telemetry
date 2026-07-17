from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any

from .definition import (
    BUILTIN_DEFINITIONS,
    LUMBRIDGE_WEST_TREES_V1,
    StopConditionKind,
    TaskCapability,
    TaskSiteDefinition,
    TaskType,
    get_builtin_definition,
)


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
DEFAULT_PROFILE_ID = "default_lumbridge_west_trees_v1"
PROFILE_CONTRACT_SCHEMA = "osrs_profile_contract.v2"


def _require_identifier(field_name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a lowercase identifier of at most 64 characters"
        )


def _require_optional_positive_int(field_name: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be null or a positive integer")


def _require_optional_positive_number(field_name: str, value: object) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ValueError(f"{field_name} must be null or a positive number")


def _require_optional_utc_datetime(field_name: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be null or a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _parse_utc_datetime(field_name: str, value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be null or an ISO-8601 UTC string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be null or an ISO-8601 UTC string"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Profile:
    """Strict user choices; engine safety invariants are intentionally absent."""

    profile_id: str
    definition_id: str
    cycle_goal: int | None
    item_quantity_goal: int | None = None
    inventories_banked_goal: int | None = None
    duration_seconds: float | None = None
    start_at_utc: datetime | None = None
    stop_at_utc: datetime | None = None
    stop_when_inventory_full: bool = False
    max_actions: int | None = None
    reconcile_on_start: bool = True

    def __post_init__(self) -> None:
        _require_identifier("profile_id", self.profile_id)
        _require_identifier("definition_id", self.definition_id)
        _require_optional_positive_int("cycle_goal", self.cycle_goal)
        _require_optional_positive_int(
            "item_quantity_goal", self.item_quantity_goal
        )
        _require_optional_positive_int(
            "inventories_banked_goal", self.inventories_banked_goal
        )
        _require_optional_positive_number("duration_seconds", self.duration_seconds)
        _require_optional_positive_int("max_actions", self.max_actions)
        _require_optional_utc_datetime("start_at_utc", self.start_at_utc)
        _require_optional_utc_datetime("stop_at_utc", self.stop_at_utc)
        if not isinstance(self.stop_when_inventory_full, bool):
            raise ValueError("stop_when_inventory_full must be a boolean")
        if not isinstance(self.reconcile_on_start, bool):
            raise ValueError("reconcile_on_start must be a boolean")
        if (
            self.start_at_utc is not None
            and self.stop_at_utc is not None
            and self.start_at_utc >= self.stop_at_utc
        ):
            raise ValueError("start_at_utc must be earlier than stop_at_utc")
        if not self.stop_conditions:
            raise ValueError("at least one stop condition must be configured")

    @property
    def stop_conditions(self) -> frozenset[StopConditionKind]:
        conditions: set[StopConditionKind] = set()
        if self.cycle_goal is not None:
            conditions.add(StopConditionKind.CYCLES)
        if self.item_quantity_goal is not None:
            conditions.add(StopConditionKind.ITEM_QUANTITY)
        if self.inventories_banked_goal is not None:
            conditions.add(StopConditionKind.INVENTORIES_BANKED)
        if self.stop_when_inventory_full:
            conditions.add(StopConditionKind.INVENTORY_FULL)
        if self.duration_seconds is not None:
            conditions.add(StopConditionKind.DURATION)
        if self.stop_at_utc is not None:
            conditions.add(StopConditionKind.ABSOLUTE_TIME)
        return frozenset(conditions)


@dataclass(frozen=True, slots=True)
class BoundProfile:
    """A profile paired with one validated immutable task definition."""

    profile: Profile
    definition: TaskSiteDefinition

    def __post_init__(self) -> None:
        if not isinstance(self.profile, Profile):
            raise TypeError("profile must be a Profile")
        if not isinstance(self.definition, TaskSiteDefinition):
            raise TypeError("definition must be a TaskSiteDefinition")
        if self.profile.definition_id != self.definition.definition_id:
            raise ValueError("profile definition_id does not match the bound definition")
        _validate_runtime_definition(self.definition)
        _validate_profile_for_definition(self.profile, self.definition)


DEFAULT_PROFILE = Profile(
    profile_id=DEFAULT_PROFILE_ID,
    definition_id=LUMBRIDGE_WEST_TREES_V1.definition_id,
    cycle_goal=1,
)


def _validate_runtime_definition(definition: TaskSiteDefinition) -> None:
    if definition.task_type is not TaskType.GATHERING:
        raise ValueError(
            "runtime supports only gathering task definitions; "
            f"got task_type {definition.task_type.value!r}"
        )
    if definition.unsupported_capabilities:
        names = sorted(item.value for item in definition.unsupported_capabilities)
        raise ValueError(f"definition requires unsupported capabilities: {names}")


def bind_profile(profile: Profile, definition: TaskSiteDefinition) -> BoundProfile:
    """Validate an already-decoded profile against an immutable definition."""

    if not isinstance(profile, Profile):
        raise TypeError("profile must be a Profile")
    if not isinstance(definition, TaskSiteDefinition):
        raise TypeError("definition must be a TaskSiteDefinition")
    return BoundProfile(profile=profile, definition=definition)


def bind_builtin_profile(profile: Profile) -> BoundProfile:
    """Resolve and validate a profile through the immutable built-in registry."""

    if not isinstance(profile, Profile):
        raise TypeError("profile must be a Profile")
    return bind_profile(profile, get_builtin_definition(profile.definition_id))


def _field(
    name: str,
    field_type: str,
    *,
    required: bool,
    default: object,
    constraints: str,
) -> dict[str, object]:
    return {
        "name": name,
        "type": field_type,
        "required": required,
        "default": default,
        "constraints": constraints,
    }


def profile_contract(
    definition: TaskSiteDefinition = LUMBRIDGE_WEST_TREES_V1,
    *,
    allowed_definition_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a fresh frontend-safe contract for one selected definition."""

    if not isinstance(definition, TaskSiteDefinition):
        raise TypeError("definition must be a TaskSiteDefinition")
    _validate_runtime_definition(definition)
    lifecycle = definition.lifecycle
    default_cycle_goal = (
        1
        if StopConditionKind.CYCLES in lifecycle.supported_stop_conditions
        else None
    )
    if allowed_definition_ids is None:
        builtin_definition_ids = tuple(
            item.definition_id for item in BUILTIN_DEFINITIONS
        )
        definition_ids = (
            builtin_definition_ids
            if definition in BUILTIN_DEFINITIONS
            else (definition.definition_id,)
        )
    else:
        if definition.definition_id not in allowed_definition_ids:
            raise ValueError(
                "allowed_definition_ids must include the selected definition_id"
            )
        definition_ids = allowed_definition_ids
    fields: list[dict[str, object]] = [
        _field(
            "profileId",
            "identifier",
            required=True,
            default=DEFAULT_PROFILE.profile_id,
            constraints="lowercase identifier, at most 64 characters",
        ),
        {
            "name": "definitionId",
            "type": "enum",
            "required": True,
            "default": definition.definition_id,
            "allowedValues": list(definition_ids),
        },
        _field(
            "cycleGoal",
            "integer|null",
            required=True,
            default=default_cycle_goal,
            constraints=f"null or integer from 1 to {lifecycle.maximum_cycles}",
        ),
        _field(
            "itemQuantityGoal",
            "integer|null",
            required=False,
            default=None,
            constraints=(
                f"null or integer from 1 to {lifecycle.maximum_item_quantity}"
            ),
        ),
        _field(
            "inventoriesBankedGoal",
            "integer|null",
            required=False,
            default=None,
            constraints=f"null or integer from 1 to {lifecycle.maximum_cycles}",
        ),
        _field(
            "durationSeconds",
            "number|null",
            required=False,
            default=None,
            constraints=(
                "null or positive seconds no greater than "
                f"{lifecycle.maximum_duration_seconds:g}"
            ),
        ),
        _field(
            "startAtUtc",
            "datetime|null",
            required=False,
            default=None,
            constraints="null or ISO-8601 datetime with a UTC offset",
        ),
        _field(
            "stopAtUtc",
            "datetime|null",
            required=False,
            default=None,
            constraints="null or ISO-8601 datetime with a UTC offset",
        ),
        _field(
            "stopWhenInventoryFull",
            "boolean",
            required=False,
            default=False,
            constraints="boolean; completion is evaluated before another action",
        ),
        _field(
            "maxActions",
            "integer|null",
            required=False,
            default=None,
            constraints=f"null or integer from 1 to {lifecycle.maximum_actions}",
        ),
        _field(
            "reconcileOnStart",
            "boolean",
            required=False,
            default=True,
            constraints="boolean; reconciliation always uses a fresh observation",
        ),
    ]
    return {
        "schema": PROFILE_CONTRACT_SCHEMA,
        "additionalProperties": False,
        "fields": fields,
        "stopConditionComposition": "any",
        "profileMayOverrideEngineInvariants": False,
    }


_REQUIRED_PROFILE_FIELDS = frozenset({"profileId", "definitionId", "cycleGoal"})
_OPTIONAL_PROFILE_FIELDS = frozenset(
    {
        "itemQuantityGoal",
        "inventoriesBankedGoal",
        "durationSeconds",
        "startAtUtc",
        "stopAtUtc",
        "stopWhenInventoryFull",
        "maxActions",
        "reconcileOnStart",
    }
)


def validate_profile_values(
    values: Mapping[str, object],
    definition: TaskSiteDefinition | None = None,
) -> BoundProfile:
    """Strictly decode and bind the frontend profile contract.

    With no explicit definition, resolution stays confined to the immutable
    built-in registry. An explicit immutable definition is instead bound
    directly, while retaining the same identifier, capability, lifecycle, and
    task-type checks.
    """

    if not isinstance(values, Mapping):
        raise TypeError("profile values must be a mapping")
    if any(not isinstance(key, str) for key in values):
        raise ValueError("profile field names must be strings")
    actual = set(values)
    missing = sorted(_REQUIRED_PROFILE_FIELDS - actual)
    unknown = sorted(actual - _REQUIRED_PROFILE_FIELDS - _OPTIONAL_PROFILE_FIELDS)
    if missing:
        raise ValueError(f"profile values are missing fields: {missing}")
    if unknown:
        raise ValueError(f"profile values contain unknown fields: {unknown}")
    for field_name in ("stopWhenInventoryFull", "reconcileOnStart"):
        value = values.get(
            field_name,
            False if field_name == "stopWhenInventoryFull" else True,
        )
        if not isinstance(value, bool):
            raise ValueError(f"{field_name} must be a boolean")
    profile = Profile(
        profile_id=values["profileId"],  # type: ignore[arg-type]
        definition_id=values["definitionId"],  # type: ignore[arg-type]
        cycle_goal=values["cycleGoal"],  # type: ignore[arg-type]
        item_quantity_goal=values.get("itemQuantityGoal"),  # type: ignore[arg-type]
        inventories_banked_goal=values.get("inventoriesBankedGoal"),  # type: ignore[arg-type]
        duration_seconds=values.get("durationSeconds"),  # type: ignore[arg-type]
        start_at_utc=_parse_utc_datetime("startAtUtc", values.get("startAtUtc")),
        stop_at_utc=_parse_utc_datetime("stopAtUtc", values.get("stopAtUtc")),
        stop_when_inventory_full=values.get("stopWhenInventoryFull", False),  # type: ignore[arg-type]
        max_actions=values.get("maxActions"),  # type: ignore[arg-type]
        reconcile_on_start=values.get("reconcileOnStart", True),  # type: ignore[arg-type]
    )
    if definition is None:
        return bind_builtin_profile(profile)
    return bind_profile(profile, definition)


def _validate_profile_for_definition(
    profile: Profile,
    definition: TaskSiteDefinition,
) -> None:
    lifecycle = definition.lifecycle
    unsupported_conditions = profile.stop_conditions - lifecycle.supported_stop_conditions
    if unsupported_conditions:
        names = sorted(item.value for item in unsupported_conditions)
        raise ValueError(f"profile requires unsupported stop conditions: {names}")
    if (
        (profile.start_at_utc is not None or profile.stop_at_utc is not None)
        and TaskCapability.SCHEDULED_START not in definition.capabilities
    ):
        raise ValueError("profile scheduling requires the scheduled_start capability")
    if (
        len(profile.stop_conditions) > 1
        and TaskCapability.COMPOSABLE_STOP_CONDITIONS
        not in definition.capabilities
    ):
        raise ValueError(
            "multiple stop conditions require the composable_stop_conditions capability"
        )
    if (
        profile.reconcile_on_start
        and TaskCapability.RESTART_RECONCILIATION not in definition.capabilities
    ):
        raise ValueError(
            "restart reconciliation requires the restart_reconciliation capability"
        )
    bounds = (
        ("cycle_goal", profile.cycle_goal, lifecycle.maximum_cycles),
        (
            "inventories_banked_goal",
            profile.inventories_banked_goal,
            lifecycle.maximum_cycles,
        ),
        (
            "item_quantity_goal",
            profile.item_quantity_goal,
            lifecycle.maximum_item_quantity,
        ),
        ("max_actions", profile.max_actions, lifecycle.maximum_actions),
    )
    for field_name, value, maximum in bounds:
        if value is not None and value > maximum:
            raise ValueError(f"{field_name} must be no greater than {maximum}")
    if (
        profile.duration_seconds is not None
        and profile.duration_seconds > lifecycle.maximum_duration_seconds
    ):
        raise ValueError(
            "duration_seconds must be no greater than "
            f"{lifecycle.maximum_duration_seconds:g}"
        )
    if profile.reconcile_on_start and not definition.recovery.reconcile_on_restart:
        raise ValueError("definition does not permit restart reconciliation")
    if (
        profile.start_at_utc is not None
        and profile.stop_at_utc is not None
        and (
            profile.stop_at_utc - profile.start_at_utc
        ).total_seconds()
        > lifecycle.maximum_duration_seconds
    ):
        raise ValueError(
            "scheduled run window exceeds the definition's maximum duration"
        )


DEFAULT_BINDING = bind_builtin_profile(DEFAULT_PROFILE)

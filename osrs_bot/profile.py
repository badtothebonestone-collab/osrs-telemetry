from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping
from typing import Any

from .definition import LUMBRIDGE_WEST_TREES_V1, TaskSiteDefinition


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
DEFAULT_PROFILE_ID = "default_lumbridge_west_trees_v1"
PROFILE_CONTRACT_SCHEMA = "osrs_profile_contract.v1"


def _require_identifier(field_name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a lowercase identifier of at most 64 characters"
        )


@dataclass(frozen=True, slots=True)
class Profile:
    """The deliberately small user-selectable contract for the built-in task."""

    profile_id: str
    definition_id: str
    cycle_goal: int

    def __post_init__(self) -> None:
        _require_identifier("profile_id", self.profile_id)
        _require_identifier("definition_id", self.definition_id)
        if (
            not isinstance(self.cycle_goal, int)
            or isinstance(self.cycle_goal, bool)
            or self.cycle_goal <= 0
        ):
            raise ValueError("cycle_goal must be a positive integer")


@dataclass(frozen=True, slots=True)
class BoundProfile:
    """A validated profile paired with its immutable built-in definition."""

    profile: Profile
    definition: TaskSiteDefinition

    def __post_init__(self) -> None:
        if not isinstance(self.profile, Profile):
            raise TypeError("profile must be a Profile")
        if not isinstance(self.definition, TaskSiteDefinition):
            raise TypeError("definition must be a TaskSiteDefinition")
        if self.definition is not LUMBRIDGE_WEST_TREES_V1:
            raise ValueError("definition is not the supported built-in definition")
        if self.profile.definition_id != self.definition.definition_id:
            raise ValueError("profile definition_id does not match the bound definition")
        _validate_supported_profile(self.profile)


DEFAULT_PROFILE = Profile(
    profile_id=DEFAULT_PROFILE_ID,
    definition_id=LUMBRIDGE_WEST_TREES_V1.definition_id,
    cycle_goal=1,
)


def bind_builtin_profile(profile: Profile) -> BoundProfile:
    """Bind the sole supported profile; unsupported choices fail closed."""

    if not isinstance(profile, Profile):
        raise TypeError("profile must be a Profile")
    _validate_supported_profile(profile)
    return BoundProfile(profile=profile, definition=LUMBRIDGE_WEST_TREES_V1)


def profile_contract() -> dict[str, Any]:
    """Return the sole frontend-safe profile shape and its exact allowed values."""

    return {
        "schema": PROFILE_CONTRACT_SCHEMA,
        "additionalProperties": False,
        "fields": [
            {
                "name": "profileId",
                "type": "identifier",
                "required": True,
                "default": DEFAULT_PROFILE.profile_id,
                "constraints": "lowercase identifier, at most 64 characters",
            },
            {
                "name": "definitionId",
                "type": "enum",
                "required": True,
                "default": DEFAULT_PROFILE.definition_id,
                "allowedValues": [LUMBRIDGE_WEST_TREES_V1.definition_id],
            },
            {
                "name": "cycleGoal",
                "type": "integer",
                "required": True,
                "default": DEFAULT_PROFILE.cycle_goal,
                "allowedValues": [1],
            },
        ],
        "profileMayOverrideEngineInvariants": False,
    }


def validate_profile_values(values: Mapping[str, object]) -> BoundProfile:
    """Validate the in-memory frontend contract; this is not a profile loader."""

    if not isinstance(values, Mapping):
        raise TypeError("profile values must be a mapping")
    if any(not isinstance(key, str) for key in values):
        raise ValueError("profile field names must be strings")
    required = {"profileId", "definitionId", "cycleGoal"}
    actual = set(values)
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing:
        raise ValueError(f"profile values are missing fields: {missing}")
    if unknown:
        raise ValueError(f"profile values contain unknown fields: {unknown}")
    return bind_builtin_profile(
        Profile(
            profile_id=values["profileId"],  # type: ignore[arg-type]
            definition_id=values["definitionId"],  # type: ignore[arg-type]
            cycle_goal=values["cycleGoal"],  # type: ignore[arg-type]
        )
    )


def _validate_supported_profile(profile: Profile) -> None:
    if profile.definition_id != LUMBRIDGE_WEST_TREES_V1.definition_id:
        raise ValueError(f"unsupported definition_id: {profile.definition_id!r}")
    if profile.cycle_goal != 1:
        raise ValueError("unsupported cycle_goal: the built-in profile requires exactly 1")


DEFAULT_BINDING = bind_builtin_profile(DEFAULT_PROFILE)

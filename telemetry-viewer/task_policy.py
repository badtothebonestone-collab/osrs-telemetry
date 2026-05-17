from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import capabilities


TASK_POLICIES_SCHEMA = "task_policies.v1"
POLICY_PATH = Path(__file__).resolve().with_name("task_policies.json")
_POLICY_CACHE: dict[Path, dict[str, "TaskPolicy"]] = {}


class InventoryFullStrategy(str, Enum):
    CONTINUE_TASK = "continue_task"
    NEEDS_SERVICE = "needs_service"
    PROCESS_INVENTORY = "process_inventory"
    STOP = "stop"
    OBSERVE_ONLY = "observe_only"
    UNKNOWN = "unknown"


class ResourceDisposition(str, Enum):
    BANK = "bank"
    BURN = "burn"
    DROP = "drop"
    KEEP = "keep"
    CONSUME = "consume"
    NONE = "none"
    UNKNOWN = "unknown"


class InventoryExpectation(str, Enum):
    MAY_START_FULL = "may_start_full"
    SHOULD_HAVE_SPACE = "should_have_space"
    MUST_HAVE_SPACE = "must_have_space"
    UNKNOWN = "unknown"


def enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def normalize_enum(enum_cls: type[Enum], value: Any, fallback: Enum) -> Enum:
    if isinstance(value, enum_cls):
        return value
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        return enum_cls(text)
    except ValueError:
        return fallback


@dataclass
class TaskPolicy:
    name: str
    task: str = ""
    profile: str | None = None
    inventoryExpectation: InventoryExpectation | str = InventoryExpectation.UNKNOWN
    fullInventoryStrategy: InventoryFullStrategy | str = InventoryFullStrategy.UNKNOWN
    resourceDisposition: ResourceDisposition | str = ResourceDisposition.UNKNOWN
    serviceTypeNeeded: str | None = None
    processTypeNeeded: str | None = None
    minFreeSlotsPreferred: int | None = None
    requiredCapabilities: list[str] = field(default_factory=list)
    optionalCapabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.inventoryExpectation = normalize_enum(
            InventoryExpectation,
            self.inventoryExpectation,
            InventoryExpectation.UNKNOWN,
        )
        self.fullInventoryStrategy = normalize_enum(
            InventoryFullStrategy,
            self.fullInventoryStrategy,
            InventoryFullStrategy.UNKNOWN,
        )
        self.resourceDisposition = normalize_enum(
            ResourceDisposition,
            self.resourceDisposition,
            ResourceDisposition.UNKNOWN,
        )
        self.requiredCapabilities = capabilities.normalize_capability_names(self.requiredCapabilities)
        self.optionalCapabilities = capabilities.normalize_capability_names(self.optionalCapabilities)

    @classmethod
    def from_dict(cls, name: str, payload: dict[str, Any]) -> "TaskPolicy":
        data = dict(payload) if isinstance(payload, dict) else {}
        return cls(
            name=str(data.get("name") or name),
            task=str(data.get("task") or ""),
            profile=data.get("profile"),
            inventoryExpectation=data.get("inventoryExpectation", InventoryExpectation.UNKNOWN),
            fullInventoryStrategy=data.get("fullInventoryStrategy", InventoryFullStrategy.UNKNOWN),
            resourceDisposition=data.get("resourceDisposition", ResourceDisposition.UNKNOWN),
            serviceTypeNeeded=data.get("serviceTypeNeeded"),
            processTypeNeeded=data.get("processTypeNeeded"),
            minFreeSlotsPreferred=data.get("minFreeSlotsPreferred"),
            requiredCapabilities=list(data.get("requiredCapabilities") or []),
            optionalCapabilities=list(data.get("optionalCapabilities") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task": self.task,
            "profile": self.profile,
            "inventoryExpectation": enum_value(self.inventoryExpectation),
            "fullInventoryStrategy": enum_value(self.fullInventoryStrategy),
            "resourceDisposition": enum_value(self.resourceDisposition),
            "serviceTypeNeeded": self.serviceTypeNeeded,
            "processTypeNeeded": self.processTypeNeeded,
            "minFreeSlotsPreferred": self.minFreeSlotsPreferred,
            "requiredCapabilities": capabilities.normalize_capability_names(self.requiredCapabilities),
            "optionalCapabilities": capabilities.normalize_capability_names(self.optionalCapabilities),
        }


BUILTIN_POLICIES: dict[str, dict[str, Any]] = {
    "woodcutting_bank": {
        "task": "woodcutting",
        "profile": "woodcutting",
        "inventoryExpectation": "must_have_space",
        "fullInventoryStrategy": "needs_service",
        "resourceDisposition": "bank",
        "serviceTypeNeeded": "bank_full",
        "minFreeSlotsPreferred": 1,
        "requiredCapabilities": ["inventory.items"],
        "optionalCapabilities": ["target.candidates", "navigation.local_collision_window"],
    },
    "woodcutting_deposit": {
        "task": "woodcutting",
        "profile": "woodcutting",
        "inventoryExpectation": "must_have_space",
        "fullInventoryStrategy": "needs_service",
        "resourceDisposition": "bank",
        "serviceTypeNeeded": "bank_deposit",
        "minFreeSlotsPreferred": 1,
        "requiredCapabilities": ["inventory.items"],
        "optionalCapabilities": ["target.candidates", "navigation.local_collision_window"],
    },
    "woodcutting_firemake": {
        "task": "woodcutting",
        "profile": "woodcutting",
        "inventoryExpectation": "must_have_space",
        "fullInventoryStrategy": "process_inventory",
        "resourceDisposition": "burn",
        "processTypeNeeded": "firemaking",
        "minFreeSlotsPreferred": 1,
        "requiredCapabilities": ["inventory.items"],
    },
    "woodcutting_drop": {
        "task": "woodcutting",
        "profile": "woodcutting",
        "inventoryExpectation": "must_have_space",
        "fullInventoryStrategy": "process_inventory",
        "resourceDisposition": "drop",
        "processTypeNeeded": "drop",
        "minFreeSlotsPreferred": 1,
        "requiredCapabilities": ["inventory.items"],
    },
    "combat_default": {
        "task": "combat",
        "profile": "combat",
        "inventoryExpectation": "may_start_full",
        "fullInventoryStrategy": "continue_task",
        "resourceDisposition": "keep",
    },
    "observe_only": {
        "task": "observe",
        "profile": "observe",
        "inventoryExpectation": "unknown",
        "fullInventoryStrategy": "observe_only",
        "resourceDisposition": "none",
    },
}

DEFAULT_POLICY_BY_TASK = {
    "woodcutting": "woodcutting_bank",
    "combat": "combat_default",
    "observe": "observe_only",
    "observe_only": "observe_only",
}


def load_task_policies(path: Path | str | None = None, *, reload: bool = False) -> dict[str, TaskPolicy]:
    policy_path = (Path(path) if path is not None else POLICY_PATH).resolve()
    if not reload and policy_path in _POLICY_CACHE:
        return _POLICY_CACHE[policy_path]
    raw: dict[str, Any] = {}
    if policy_path.exists():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            raw = loaded.get("policies", loaded) if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
    merged = {name: dict(payload) for name, payload in BUILTIN_POLICIES.items()}
    for name, payload in raw.items():
        if isinstance(payload, dict):
            merged[str(name)] = dict(payload)
    policies = {name: TaskPolicy.from_dict(name, payload) for name, payload in merged.items()}
    _POLICY_CACHE[policy_path] = policies
    return policies


def clear_task_policy_cache() -> None:
    _POLICY_CACHE.clear()


def default_policy_name(task: str | None = None, profile: str | None = None) -> str:
    profile_text = str(profile or "").strip().lower()
    task_text = str(task or "").strip().lower()
    if profile_text in DEFAULT_POLICY_BY_TASK:
        return DEFAULT_POLICY_BY_TASK[profile_text]
    if task_text in DEFAULT_POLICY_BY_TASK:
        return DEFAULT_POLICY_BY_TASK[task_text]
    return "observe_only"


def resolve_task_policy(policy: TaskPolicy | dict[str, Any] | str | None, *, task: str | None = None, profile: str | None = None) -> TaskPolicy:
    if isinstance(policy, TaskPolicy):
        return policy
    if isinstance(policy, dict):
        return TaskPolicy.from_dict(str(policy.get("name") or "inline_policy"), policy)
    policies = load_task_policies()
    name = str(policy or "").strip() or default_policy_name(task, profile)
    if name not in policies:
        name = default_policy_name(task, profile)
    return policies.get(name) or TaskPolicy.from_dict("observe_only", BUILTIN_POLICIES["observe_only"])


def policy_names() -> list[str]:
    return sorted(load_task_policies().keys())

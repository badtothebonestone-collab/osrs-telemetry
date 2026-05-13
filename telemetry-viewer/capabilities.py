from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


STATUS_AVAILABLE = "available"
STATUS_UNAVAILABLE = "unavailable"
STATUS_OPTIONAL = "optional"
STATUS_EXPERIMENTAL = "experimental"
STATUS_DEBUG_ONLY = "debug_only"
STATUS_UNSUPPORTED = "unsupported"

VALID_STATUSES = {
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE,
    STATUS_OPTIONAL,
    STATUS_EXPERIMENTAL,
    STATUS_DEBUG_ONLY,
    STATUS_UNSUPPORTED,
}

CAPABILITY_ALIASES = {
    "inventoryDeltas": "inventory.deltas",
    "inventory_delta": "inventory.deltas",
    "inventory.deltas": "inventory.deltas",
    "animationFrame": "activity.animation_frame",
    "activity.animation_frame": "activity.animation_frame",
    "explicitMovementState": "activity.explicit_movement_state",
    "activity.explicit_movement_state": "activity.explicit_movement_state",
    "fullPathfinding": "navigation.full_pathfinding",
    "navigation.full_pathfinding": "navigation.full_pathfinding",
    "globalPathfinding": "navigation.global_pathfinding",
    "navigation.global_pathfinding": "navigation.global_pathfinding",
    "interactionTile": "navigation.interaction_tile",
    "navigation.interaction_tile": "navigation.interaction_tile",
    "movement.run_state": "movement.run_state",
    "watch_values": "plugin_snapshot.watch_values",
    "watch_values.java_runtime": "plugin_snapshot.watch_values",
    "plugin_snapshot.watch_values": "plugin_snapshot.watch_values",
    "service.actions": "service.actions",
}

KNOWN_CAPABILITIES = {
    "inventory.items": STATUS_AVAILABLE,
    "inventory.resource_counts": STATUS_AVAILABLE,
    "inventory.deltas": STATUS_OPTIONAL,
    "target.candidates": STATUS_AVAILABLE,
    "target.best": STATUS_AVAILABLE,
    "target.intent": STATUS_AVAILABLE,
    "navigation.local_collision_window": STATUS_AVAILABLE,
    "navigation.full_pathfinding": STATUS_OPTIONAL,
    "navigation.global_pathfinding": STATUS_UNSUPPORTED,
    "navigation.interaction_tile": STATUS_OPTIONAL,
    "activity.animation": STATUS_OPTIONAL,
    "activity.animation_frame": STATUS_OPTIONAL,
    "activity.explicit_movement_state": STATUS_OPTIONAL,
    "overlay.intent_markers": STATUS_AVAILABLE,
    "plugin_snapshot.watch_values": STATUS_EXPERIMENTAL,
    "movement.run_state": STATUS_OPTIONAL,
    "service.actions": STATUS_OPTIONAL,
}


@dataclass
class CapabilityRecord:
    name: str
    status: str = STATUS_UNAVAILABLE
    reason: str | None = None
    optional: bool = False

    def normalized(self) -> "CapabilityRecord":
        name = normalize_capability_name(self.name)
        status = self.status if self.status in VALID_STATUSES else STATUS_UNAVAILABLE
        return CapabilityRecord(name=name, status=status, reason=self.reason, optional=self.optional)


def normalize_capability_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("capability:"):
        text = text.split(":", 1)[1]
    return CAPABILITY_ALIASES.get(text, text)


def normalize_capability_names(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = normalize_capability_name(value)
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def default_capability_status(name: Any) -> str:
    return KNOWN_CAPABILITIES.get(normalize_capability_name(name), STATUS_UNAVAILABLE)


def capability_record(name: Any, *, status: str | None = None, reason: str | None = None, optional: bool | None = None) -> CapabilityRecord:
    normalized = normalize_capability_name(name)
    resolved_status = status or default_capability_status(normalized)
    if resolved_status not in VALID_STATUSES:
        resolved_status = STATUS_UNAVAILABLE
    return CapabilityRecord(
        name=normalized,
        status=resolved_status,
        reason=reason,
        optional=bool(optional) if optional is not None else resolved_status == STATUS_OPTIONAL,
    )

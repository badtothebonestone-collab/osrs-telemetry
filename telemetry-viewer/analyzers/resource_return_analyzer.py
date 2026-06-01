from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import BankOperationContext, BankUiContext, InventoryContext, PlayerContext, ResourceReturnContext, TargetContext


DEFAULT_MEMORY_MAX_AGE_TICKS = 1200
RESOURCE_AREA_REACHED_DISTANCE_TILES = 8
RESOURCE_MEMORY_DRIFT_THRESHOLD_TILES = 18
RESOURCE_CLASSES = {"tree", "woodcutting_tree"}
SERVICE_CLASSES = {"bank_booth", "banker", "bank_chest", "deposit_box", "deposit_chest", "bank_related", "bank_service"}
SERVICE_INTENTS = {"needs_service", "navigate_to_service", "service_available", "service_open", "bank_operation_pending", "close_service_context", "wait_for_world_view"}
PROFILE_RESOURCE_ANCHORS = {
    "woodcutting_bank": {
        "anchorId": "lumbridge_west_tree_area",
        "type": "profile_anchor",
        "worldLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
        "confidence": 0.45,
        "source": "route_profile",
    },
    "woodcut_bank": {
        "anchorId": "lumbridge_west_tree_area",
        "type": "profile_anchor",
        "worldLocation": {"worldX": 3196, "worldY": 3248, "plane": 0},
        "confidence": 0.45,
        "source": "route_profile",
    },
}


@dataclass
class ResourceAreaMemoryState:
    last_resource_activity_tick: int | None = None
    last_resource_player_tile: dict[str, Any] | None = None
    last_resource_target_tile: dict[str, Any] | None = None
    last_resource_target_name: str | None = None
    last_resource_target_id: int | None = None
    last_resource_target_class: str | None = None
    last_resource_cluster_center: dict[str, Any] | None = None
    last_resource_plane: int | None = None
    last_resource_profile: str | None = None
    resource_memory_invalid_reason: str | None = "no_resource_memory"
    last_resource_target: dict[str, Any] = field(default_factory=dict)

    def age_ticks(self, source_tick: int | None = None) -> int | None:
        if self.last_resource_activity_tick is None or source_tick is None:
            return None
        try:
            return max(0, int(source_tick) - int(self.last_resource_activity_tick))
        except (TypeError, ValueError):
            return None

    def is_valid(
        self,
        *,
        source_tick: int | None = None,
        current_plane: int | None = None,
        max_age_ticks: int = DEFAULT_MEMORY_MAX_AGE_TICKS,
        require_current_plane_match: bool = False,
    ) -> tuple[bool, str | None]:
        if self.last_resource_activity_tick is None:
            return False, "no_resource_memory"
        if not any(isinstance(tile, dict) and tile for tile in (self.last_resource_target_tile, self.last_resource_cluster_center, self.last_resource_player_tile)):
            return False, "no_resource_memory"
        age = self.age_ticks(source_tick)
        if age is not None and age > max(0, int(max_age_ticks)):
            return False, "memory_expired"
        if require_current_plane_match and current_plane is not None and self.last_resource_plane is not None and int(current_plane) != int(self.last_resource_plane):
            return False, "wrong_plane"
        return True, None

    def destination_tile(self) -> tuple[dict[str, Any] | None, str]:
        for tile, source in (
            (self.last_resource_target_tile, "last_resource_target"),
            (self.last_resource_cluster_center, "last_resource_cluster"),
            (self.last_resource_player_tile, "last_resource_player_tile"),
        ):
            if isinstance(tile, dict) and tile.get("worldX") is not None and tile.get("worldY") is not None and tile.get("plane") is not None:
                return dict(tile), source
        return None, "none"

    def to_dict(self, *, source_tick: int | None = None, current_plane: int | None = None) -> dict[str, Any]:
        valid, reason = self.is_valid(source_tick=source_tick, current_plane=current_plane)
        return {
            "lastResourceActivityTick": self.last_resource_activity_tick,
            "lastResourcePlayerTile": dict(self.last_resource_player_tile) if isinstance(self.last_resource_player_tile, dict) else None,
            "lastResourceTargetTile": dict(self.last_resource_target_tile) if isinstance(self.last_resource_target_tile, dict) else None,
            "lastResourceTargetName": self.last_resource_target_name,
            "lastResourceTargetId": self.last_resource_target_id,
            "lastResourceTargetClass": self.last_resource_target_class,
            "lastResourceClusterCenter": dict(self.last_resource_cluster_center) if isinstance(self.last_resource_cluster_center, dict) else None,
            "lastResourcePlane": self.last_resource_plane,
            "lastResourceProfile": self.last_resource_profile,
            "resourceMemoryAgeTicks": self.age_ticks(source_tick),
            "resourceMemoryValid": valid,
            "resourceMemoryInvalidReason": reason or self.resource_memory_invalid_reason,
            "lastResourceTarget": dict(self.last_resource_target) if self.last_resource_target else {},
        }


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "open", "visible", "ready", "available", "complete"}:
            return True
        if text in {"false", "no", "0", "closed", "hidden", "not_ready", "unavailable", "incomplete"}:
            return False
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _context_value(context: Any, snake_key: str, camel_key: str | None = None, default: Any = None) -> Any:
    if context is None:
        return default
    camel_key = camel_key or snake_key
    if isinstance(context, dict):
        if snake_key in context:
            return context.get(snake_key)
        return context.get(camel_key, default)
    if hasattr(context, snake_key):
        return getattr(context, snake_key)
    if hasattr(context, camel_key):
        return getattr(context, camel_key)
    return default


def _source_tick(source_tick: int | None, *contexts: Any) -> int | None:
    if source_tick is not None:
        return source_tick
    for context in contexts:
        tick = _as_int(_context_value(context, "source_tick", "sourceTick"))
        if tick is not None:
            return tick
    return None


def _tile_from_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    world_x = _as_int(payload.get("worldX"))
    world_y = _as_int(payload.get("worldY"))
    plane = _as_int(payload.get("plane"))
    if world_x is None or world_y is None or plane is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": plane}


def _tile_distance_tiles(left: dict[str, Any] | None, right: dict[str, Any] | None) -> int | None:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_x = _as_int(left.get("worldX"))
    left_y = _as_int(left.get("worldY"))
    right_x = _as_int(right.get("worldX"))
    right_y = _as_int(right.get("worldY"))
    left_plane = _as_int(left.get("plane"))
    right_plane = _as_int(right.get("plane"))
    if left_x is None or left_y is None or right_x is None or right_y is None:
        return None
    if left_plane is not None and right_plane is not None and left_plane != right_plane:
        return None
    return max(abs(left_x - right_x), abs(left_y - right_y))


def _near_tile(left: dict[str, Any] | None, right: dict[str, Any] | None, *, threshold: int = RESOURCE_AREA_REACHED_DISTANCE_TILES) -> bool:
    distance = _tile_distance_tiles(left, right)
    return distance is not None and distance <= max(0, int(threshold))


def _player_tile(player_context: PlayerContext | dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(player_context, PlayerContext):
        return _tile_from_payload({"worldX": player_context.world_x, "worldY": player_context.world_y, "plane": player_context.plane})
    raw = _context_value(player_context, "raw")
    return _tile_from_payload(player_context if isinstance(player_context, dict) else raw if isinstance(raw, dict) else None)


def _player_plane(player_context: PlayerContext | dict[str, Any] | None) -> int | None:
    tile = _player_tile(player_context)
    return _as_int(tile.get("plane")) if isinstance(tile, dict) else None


def _inventory_payload(context: InventoryContext | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(context, InventoryContext):
        return context.inventory if isinstance(context.inventory, dict) else {}
    if isinstance(context, dict):
        nested = context.get("inventory")
        return nested if isinstance(nested, dict) else context
    return {}


def _inventory_full(context: InventoryContext | dict[str, Any] | None) -> bool | None:
    inventory = _inventory_payload(context)
    full = _as_bool(inventory.get("inventoryFull"))
    if full is not None:
        return full
    free_slots = _as_int(inventory.get("freeSlots"))
    return free_slots <= 0 if free_slots is not None else None


def _candidate_class(candidate: dict[str, Any] | None) -> str:
    return str((candidate or {}).get("classId") or (candidate or {}).get("class_id") or "").lower()


def _is_resource_target(candidate: dict[str, Any] | None) -> bool:
    if not isinstance(candidate, dict) or not candidate:
        return False
    class_id = _candidate_class(candidate)
    if class_id in RESOURCE_CLASSES:
        return True
    target_type = str(candidate.get("resourceType") or candidate.get("resourceGroup") or candidate.get("profile") or "").lower()
    return "woodcut" in target_type or "tree" in str(candidate.get("targetName") or candidate.get("name") or "").lower()


def _is_service_target(candidate: dict[str, Any] | None) -> bool:
    if not isinstance(candidate, dict) or not candidate:
        return False
    class_id = _candidate_class(candidate)
    if class_id in SERVICE_CLASSES:
        return True
    name = str(candidate.get("targetName") or candidate.get("name") or "").lower()
    return "bank" in name or "deposit" in name


def _candidate_lists(target_context: TargetContext | dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key_pair in (
        ("raw_best_target", "rawBestTarget"),
        ("nearest_target", "nearestTarget"),
    ):
        target = _context_value(target_context, key_pair[0], key_pair[1])
        if isinstance(target, dict) and target:
            candidates.append(target)
    for key in ("top_candidates", "topCandidates", "profile_candidates", "profileCandidates", "candidates"):
        value = _context_value(target_context, key, key)
        if isinstance(value, list):
            candidates.extend(candidate for candidate in value if isinstance(candidate, dict) and candidate)
    return candidates


def best_resource_target(target_context: TargetContext | dict[str, Any] | None) -> dict[str, Any] | None:
    for candidate in _candidate_lists(target_context):
        if _is_resource_target(candidate) and not _is_service_target(candidate):
            return dict(candidate)
    return None


def _resource_cluster_center(candidates: list[dict[str, Any]], fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    tiles = [_tile_from_payload(candidate) for candidate in candidates if _is_resource_target(candidate) and not _is_service_target(candidate)]
    clean_tiles = [tile for tile in tiles if tile is not None]
    if not clean_tiles:
        return dict(fallback) if isinstance(fallback, dict) else None
    plane = clean_tiles[0]["plane"]
    same_plane = [tile for tile in clean_tiles if tile.get("plane") == plane]
    if not same_plane:
        return dict(fallback) if isinstance(fallback, dict) else None
    return {
        "worldX": round(sum(int(tile["worldX"]) for tile in same_plane) / len(same_plane)),
        "worldY": round(sum(int(tile["worldY"]) for tile in same_plane) / len(same_plane)),
        "plane": plane,
    }


def _policy_applies(policy: task_policy_module.TaskPolicy) -> bool:
    return (
        policy.profile == "woodcutting"
        and policy.fullInventoryStrategy == task_policy_module.InventoryFullStrategy.NEEDS_SERVICE
        and policy.resourceDisposition == task_policy_module.ResourceDisposition.BANK
    )


def _profile_resource_anchor(policy: task_policy_module.TaskPolicy) -> dict[str, Any] | None:
    for key in (policy.name, policy.task, policy.profile):
        anchor = PROFILE_RESOURCE_ANCHORS.get(str(key or ""))
        if isinstance(anchor, dict):
            return dict(anchor)
    if _policy_applies(policy):
        return dict(PROFILE_RESOURCE_ANCHORS["woodcutting_bank"])
    return None


def update_resource_area_memory(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    memory_state: ResourceAreaMemoryState | None,
    *,
    inventory_context: InventoryContext | dict[str, Any] | None,
    target_context: TargetContext | dict[str, Any] | None,
    bank_ui_context: BankUiContext | dict[str, Any] | None,
    current_task_state: dict[str, Any] | None = None,
    player_context: PlayerContext | dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> ResourceAreaMemoryState:
    memory = memory_state if isinstance(memory_state, ResourceAreaMemoryState) else ResourceAreaMemoryState()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    if not _policy_applies(resolved_policy):
        return memory
    if _inventory_full(inventory_context) is True:
        return memory
    if _as_bool(_context_value(bank_ui_context, "bank_open", "bankOpen")) is True:
        return memory
    active_intent = str((current_task_state or {}).get("activeIntent") or "")
    active_target = (current_task_state or {}).get("activeIntentTarget")
    if active_intent in SERVICE_INTENTS or _is_service_target(active_target if isinstance(active_target, dict) else None):
        return memory

    target = best_resource_target(target_context)
    if target is None and isinstance(active_target, dict) and _is_resource_target(active_target) and not _is_service_target(active_target):
        target = dict(active_target)
    if not target:
        return memory

    tick = _source_tick(source_tick, target_context, inventory_context, bank_ui_context)
    target_tile = _tile_from_payload(target)
    player_tile = _player_tile(player_context)
    cluster = _resource_cluster_center(_candidate_lists(target_context), target_tile)
    previous_destination, _previous_source = memory.destination_tile()
    if (
        isinstance(previous_destination, dict)
        and isinstance(target_tile, dict)
        and not _near_tile(target_tile, previous_destination, threshold=RESOURCE_MEMORY_DRIFT_THRESHOLD_TILES)
    ):
        return memory
    if (
        isinstance(previous_destination, dict)
        and isinstance(cluster, dict)
        and not _near_tile(cluster, previous_destination, threshold=RESOURCE_MEMORY_DRIFT_THRESHOLD_TILES)
    ):
        return memory
    memory.last_resource_activity_tick = tick
    memory.last_resource_player_tile = player_tile
    memory.last_resource_target_tile = target_tile
    memory.last_resource_target_name = str(target.get("targetName") or target.get("name") or target.get("classId") or "resource")
    memory.last_resource_target_id = _as_int(target.get("rawId") if target.get("rawId") is not None else target.get("id"))
    memory.last_resource_target_class = str(target.get("classId") or "tree")
    memory.last_resource_cluster_center = cluster
    memory.last_resource_plane = _as_int((target_tile or cluster or player_tile or {}).get("plane"))
    memory.last_resource_profile = resolved_policy.profile or resolved_policy.task or "woodcutting"
    memory.last_resource_target = dict(target)
    memory.resource_memory_invalid_reason = None
    return memory


def _destination_target(memory: ResourceAreaMemoryState, tile: dict[str, Any], source: str) -> dict[str, Any]:
    target = dict(memory.last_resource_target) if isinstance(memory.last_resource_target, dict) else {}
    target.update(
        {
            "targetType": "tile",
            "classId": "resource_return",
            "targetName": "Resource return",
            "name": "Resource return",
            "worldX": tile.get("worldX"),
            "worldY": tile.get("worldY"),
            "plane": tile.get("plane"),
            "objectKey": f"resource-return-{tile.get('worldX')}-{tile.get('worldY')}-{tile.get('plane')}",
            "returnDestinationSource": source,
            "resourceMemoryTargetName": memory.last_resource_target_name,
            "navigation": {"directReachability": "unknown"},
        }
    )
    return target


def analyze_resource_return_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    bank_operation_context: BankOperationContext | dict[str, Any] | None,
    bank_ui_context: BankUiContext | dict[str, Any] | None,
    target_context: TargetContext | dict[str, Any] | None,
    resource_memory_state: ResourceAreaMemoryState | None,
    player_context: PlayerContext | dict[str, Any] | None = None,
    current_task_state: dict[str, Any] | None = None,
    source_tick: int | None = None,
) -> ResourceReturnContext:
    started = time.perf_counter()
    resolved_policy = task_policy_module.resolve_task_policy(policy)
    tick = _source_tick(source_tick, bank_operation_context, bank_ui_context, target_context)
    timing = lambda: (time.perf_counter() - started) * 1000.0
    bank_open = _as_bool(_context_value(bank_ui_context, "bank_open", "bankOpen"))
    banking_complete = _as_bool(_context_value(bank_operation_context, "banking_complete", "bankingComplete", False)) is True
    memory = resource_memory_state if isinstance(resource_memory_state, ResourceAreaMemoryState) else ResourceAreaMemoryState()
    current_plane = _player_plane(player_context)
    memory_valid, invalid_reason = memory.is_valid(source_tick=tick, current_plane=current_plane)
    memory_age = memory.age_ticks(tick)
    visible_target = best_resource_target(target_context)
    target_visible = bool(visible_target)
    player_tile = _player_tile(player_context)
    destination_tile, destination_source = memory.destination_tile() if memory_valid else (None, "none")
    profile_anchor = _profile_resource_anchor(resolved_policy)
    profile_tile = _tile_from_payload(_context_value(profile_anchor, "worldLocation")) if isinstance(profile_anchor, dict) else None
    base_kwargs = {
        "source_tick": tick,
        "timing_millis": timing(),
        "banking_complete": banking_complete,
        "bank_open": bank_open,
        "resource_memory_valid": memory_valid,
        "resource_memory_age_ticks": memory_age,
        "resource_memory_invalid_reason": invalid_reason,
        "resource_target_currently_visible": target_visible,
    }

    if not _policy_applies(resolved_policy):
        return ResourceReturnContext(reason="not_applicable", **base_kwargs)
    if not banking_complete:
        return ResourceReturnContext(reason="not_applicable", **base_kwargs)
    if bank_open is True:
        return ResourceReturnContext(reason="not_applicable", **base_kwargs)
    if target_visible:
        visible_tile = _tile_from_payload(visible_target)
        destination_for_reached_check = destination_tile or profile_tile
        if (
            destination_for_reached_check is None
            or _near_tile(player_tile, destination_for_reached_check)
            or _near_tile(visible_tile, destination_for_reached_check)
        ):
            return ResourceReturnContext(reason="resource_target_visible", **base_kwargs)

    if memory_valid and destination_tile:
        destination = _destination_target(memory, destination_tile, destination_source)
        return ResourceReturnContext(
            return_destination_needed=True,
            return_destination_available=True,
            return_destination_tile=destination_tile,
            return_destination_source=destination_source,
            destination_target=destination,
            reason="using_remembered_resource_area",
            **base_kwargs,
        )

    if profile_tile:
        destination = {
            "targetType": "tile",
            "classId": "resource_return",
            "targetName": "Resource return",
            "name": "Resource return",
            "worldX": profile_tile.get("worldX"),
            "worldY": profile_tile.get("worldY"),
            "plane": profile_tile.get("plane"),
            "objectKey": f"resource-return-{profile_tile.get('worldX')}-{profile_tile.get('worldY')}-{profile_tile.get('plane')}",
            "returnDestinationSource": "profile_anchor",
            "resourceAnchor": profile_anchor,
            "navigation": {"directReachability": "unknown"},
        }
        return ResourceReturnContext(
            status="WARN",
            warnings=["using profile resource anchor because no live resource memory is available"],
            return_destination_needed=True,
            return_destination_available=True,
            return_destination_tile=profile_tile,
            return_destination_source="profile_anchor",
            destination_target=destination,
            reason="using_profile_resource_anchor",
            **base_kwargs,
        )

    missing = ["resource.memory"]
    warnings = ["no remembered resource area is available after banking complete"]
    reason = invalid_reason or "no_resource_memory"
    return ResourceReturnContext(
        status="WARN",
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        return_destination_needed=True,
        return_destination_available=False,
        return_destination_source="none",
        reason=reason,
        **base_kwargs,
    )

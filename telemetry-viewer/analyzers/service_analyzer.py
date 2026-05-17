from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import ServiceContext


SERVICE_CLASS_IDS = {
    "bank": {"bank", "banker", "bank_booth", "bank_chest", "deposit_box", "deposit_chest", "deposit", "bank_service", "bank_related"},
    "bank_any": {"bank", "banker", "bank_booth", "bank_chest", "deposit_box", "deposit_chest", "deposit", "bank_service", "bank_related"},
    "bank_full": {"bank", "banker", "bank_booth", "bank_chest", "deposit_box", "deposit_chest", "deposit", "bank_service", "bank_related"},
    "bank_deposit": {"deposit_box", "deposit_chest", "deposit", "banker", "bank_booth", "bank_chest", "bank_service", "bank_related"},
    "deposit": {"deposit_box", "deposit_chest", "deposit", "bank_booth", "bank_chest", "bank_service", "bank_related"},
}
SERVICE_CANDIDATE_TYPES = {
    "bank_service",
    "banker",
    "bank_booth",
    "bank_chest",
    "deposit_box",
    "deposit_chest",
}
SERVICE_INTENT_BANK_FULL = "bank_full"
SERVICE_INTENT_BANK_DEPOSIT = "bank_deposit"
SERVICE_INTENT_BANK_ANY = "bank_any"
SERVICE_GROUP_FULL_BANK = "full_bank"
SERVICE_GROUP_DEPOSIT_ONLY = "deposit_only"
SERVICE_GROUP_GENERIC = "generic_bank"
SOURCE_STAGE_CLASSES = ("bank_booth", "banker", "bank_chest", "deposit_box", "bank_table")
SERVICE_TYPE_PRIORITY = {
    "bank_booth": 0,
    "banker": 1,
    "bank_chest": 2,
    "deposit_box": 3,
    "deposit_chest": 4,
    "bank_service": 5,
}
PRIMARY_SERVICE_TYPES = {"bank_booth", "banker", "bank_chest"}
SECONDARY_SERVICE_TYPES = {"deposit_box", "deposit_chest"}
APPROACH_QUALITY_RANK = {
    "direct_side_access": 0,
    "line_of_sight_access": 0,
    "side_access_unknown": 2,
    "unknown": 2,
    "suspect_outside_wall": 4,
    "invalid_no_side_access": 8,
    "invalid_no_line_of_sight": 8,
}
SERVICE_NAME_TOKENS = {
    "bank": ("banker", "bank booth", "bank chest", "bank deposit box", "deposit box", "deposit chest", "bank service"),
    "deposit": ("bank deposit box", "deposit box", "deposit chest", "bank chest"),
}
SERVICE_TYPE_NAME_TOKENS = (
    ("deposit_chest", ("deposit chest",)),
    ("deposit_box", ("bank deposit box", "deposit box")),
    ("bank_chest", ("bank chest",)),
    ("bank_booth", ("bank booth", "booth")),
    ("banker", ("banker",)),
    ("bank_service", ("bank service", "bank")),
)
SOURCE_STAGE_NAME_TOKENS = (
    ("bank_table", ("bank table",)),
    *SERVICE_TYPE_NAME_TOKENS,
)


@dataclass
class ServiceTargetState:
    active_service_target_key: str | None = None
    active_policy_name: str | None = None
    active_service_type: str | None = None
    retained_candidate: dict[str, Any] | None = None
    retained_candidate_type: str | None = None
    retained_missing_ticks: int = 0
    last_seen_tick: int | None = None
    stable_for_ticks: int = 0
    service_switch_reason: str | None = None
    service_candidate_dropped_reason: str | None = None
    missing_grace_ticks: int = 10
    primary_bank_grace_ticks: int = 50
    deposit_grace_ticks: int = 10
    memory_max_distance_tiles: float | None = None
    memory_plane_policy: str = "same_plane"
    recent_service_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_recent_service_candidates: int = 24
    memory_eviction_reasons_this_tick: list[dict[str, Any]] = field(default_factory=list)

    def clear(self, *, reason: str | None = None) -> None:
        self.active_service_target_key = None
        self.active_policy_name = None
        self.active_service_type = None
        self.retained_candidate = None
        self.retained_candidate_type = None
        self.retained_missing_ticks = 0
        self.last_seen_tick = None
        self.stable_for_ticks = 0
        self.recent_service_candidates.clear()
        self.memory_eviction_reasons_this_tick.clear()
        self.service_switch_reason = reason
        self.service_candidate_dropped_reason = None


def normalized_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def display_text(candidate: dict[str, Any]) -> str:
    return str(candidate.get("name") or candidate.get("targetName") or "").strip().lower()


def service_candidate_type(candidate: dict[str, Any]) -> str | None:
    if not isinstance(candidate, dict):
        return None
    explicit = normalized_text(candidate.get("serviceCandidateType") or candidate.get("serviceClassId"))
    if explicit in SERVICE_CANDIDATE_TYPES:
        return explicit
    class_id = normalized_text(candidate.get("classId") or candidate.get("targetClass"))
    if class_id in SERVICE_CANDIDATE_TYPES:
        return class_id
    name = display_text(candidate)
    for candidate_type, tokens in SERVICE_TYPE_NAME_TOKENS:
        if any(token in name for token in tokens):
            return candidate_type
    raw_actions = candidate.get("actions") or candidate.get("menuActions") or candidate.get("actionNames")
    if isinstance(raw_actions, list):
        action_text = " ".join(str(item).lower() for item in raw_actions)
        if "deposit" in action_text:
            return "deposit_box"
        if "bank" in action_text or "collect" in action_text:
            return "bank_service"
    if class_id in {"bank_related", "bank", "deposit"}:
        return "bank_service"
    return None


def source_stage_class(candidate: dict[str, Any]) -> str | None:
    if not isinstance(candidate, dict):
        return None
    inferred = service_candidate_type(candidate)
    if inferred in {"bank_booth", "banker", "bank_chest", "deposit_box"}:
        return inferred
    name = display_text(candidate)
    for candidate_type, tokens in SOURCE_STAGE_NAME_TOKENS:
        if any(token in name for token in tokens):
            return candidate_type
    class_id = normalized_text(candidate.get("classId") or candidate.get("targetClass"))
    if class_id in SOURCE_STAGE_CLASSES:
        return class_id
    return None


def service_candidate_key(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    for key in ("objectKey", "targetKey", "candidateKey"):
        value = candidate.get(key)
        if value:
            return f"{key}:{value}"
    if candidate.get("hash") is not None:
        return f"hash:{candidate.get('hash')}"
    object_id = candidate.get("id")
    world_x = candidate.get("worldX")
    world_y = candidate.get("worldY")
    plane = candidate.get("plane")
    candidate_type = service_candidate_type(candidate) or candidate.get("classId") or ""
    target_type = candidate.get("targetType") or ""
    if object_id is not None and world_x is not None and world_y is not None and plane is not None:
        return f"id-world:{object_id}:{world_x}:{world_y}:{plane}:{target_type}:{candidate_type}"
    scene_x = candidate.get("sceneX")
    scene_y = candidate.get("sceneY")
    if object_id is not None and scene_x is not None and scene_y is not None and plane is not None:
        return f"id-scene:{object_id}:{scene_x}:{scene_y}:{plane}:{candidate_type}"
    if world_x is not None and world_y is not None and plane is not None:
        return f"world:{world_x}:{world_y}:{plane}:{target_type}:{candidate_type}:{display_text(candidate)}"
    return None


def is_primary_service_type(candidate_type: str | None) -> bool:
    return str(candidate_type or "") in PRIMARY_SERVICE_TYPES


def is_secondary_service_type(candidate_type: str | None) -> bool:
    return str(candidate_type or "") in SECONDARY_SERVICE_TYPES


def normalize_service_type_needed(service_type: str | None) -> str | None:
    text = normalized_text(service_type)
    if text in {"bank_full", "full_bank", "bank_full_service"}:
        return SERVICE_INTENT_BANK_FULL
    if text in {"bank_deposit", "deposit_bank", "deposit_only", "deposit"}:
        return SERVICE_INTENT_BANK_DEPOSIT
    if text in {"bank_any", "any_bank", "bank", "bank_service"}:
        return SERVICE_INTENT_BANK_ANY
    return text or None


def service_group(candidate: dict[str, Any] | None) -> str:
    candidate_type = service_candidate_type(candidate or {})
    if is_primary_service_type(candidate_type):
        return SERVICE_GROUP_FULL_BANK
    if is_secondary_service_type(candidate_type):
        return SERVICE_GROUP_DEPOSIT_ONLY
    return SERVICE_GROUP_GENERIC


def service_group_rank(candidate: dict[str, Any], service_type: str | None) -> int:
    intent = normalize_service_type_needed(service_type)
    group = service_group(candidate)
    if intent == SERVICE_INTENT_BANK_FULL:
        return {
            SERVICE_GROUP_FULL_BANK: 0,
            SERVICE_GROUP_DEPOSIT_ONLY: 10,
            SERVICE_GROUP_GENERIC: 20,
        }.get(group, 30)
    if intent == SERVICE_INTENT_BANK_DEPOSIT:
        return {
            SERVICE_GROUP_DEPOSIT_ONLY: 0,
            SERVICE_GROUP_FULL_BANK: 10,
            SERVICE_GROUP_GENERIC: 20,
        }.get(group, 30)
    return 0


def deposit_fallback_allowed_for_service(
    service_type: str | None,
    *,
    primary_visible: bool,
    primary_retained: bool,
) -> bool:
    intent = normalize_service_type_needed(service_type)
    if intent == SERVICE_INTENT_BANK_FULL:
        return not (primary_visible or primary_retained)
    return True


def candidate_service_match(candidate: dict[str, Any], service_type: str | None) -> bool:
    if not isinstance(candidate, dict) or not service_type:
        return False
    normalized_service_type = normalize_service_type_needed(service_type)
    wanted = SERVICE_CLASS_IDS.get(str(normalized_service_type), {str(normalized_service_type)})
    name_tokens = SERVICE_NAME_TOKENS.get(str(service_type), ())
    inferred_type = service_candidate_type(candidate)
    if inferred_type and inferred_type in wanted:
        return True
    fields = (
        normalized_text(candidate.get("classId")),
        normalized_text(candidate.get("targetType")),
        normalized_text(candidate.get("name") or candidate.get("targetName")),
    )
    if any(field in wanted or any(token in field for token in wanted) for field in fields):
        return True
    if any(token in display_text(candidate) for token in name_tokens):
        return True
    raw_actions = candidate.get("actions") or candidate.get("menuActions") or candidate.get("actionNames")
    if isinstance(raw_actions, list):
        action_text = " ".join(str(item).lower() for item in raw_actions)
        return any(token in action_text for token in ("bank", "deposit", "collect"))
    return False


def candidate_has_optional_service_detail(candidate: dict[str, Any]) -> bool:
    for key in ("actions", "menuActions", "actionNames"):
        value = candidate.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def service_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = dict(candidate)
    navigation = payload.get("navigation") if isinstance(payload.get("navigation"), dict) else {}
    if "directReachability" not in payload and navigation.get("directReachability") is not None:
        payload["directReachability"] = navigation.get("directReachability")
    if "name" not in payload and payload.get("targetName") is not None:
        payload["name"] = payload.get("targetName")
    if "targetName" not in payload and payload.get("name") is not None:
        payload["targetName"] = payload.get("name")
    candidate_type = service_candidate_type(candidate)
    if candidate_type:
        payload["serviceCandidateType"] = candidate_type
    if payload.get("serviceType") is None:
        payload["serviceType"] = "bank"
    payload["serviceTypePriority"] = service_type_priority(payload)
    payload["serviceGroup"] = service_group(payload)
    payload["serviceCandidatePolicyGroupRank"] = service_group_rank(payload, payload.get("serviceType"))
    payload["serviceReachabilityContribution"] = reachability_rank(payload)
    payload["serviceDistanceContribution"] = finite_metric_or_none(candidate_distance(payload))
    payload["servicePathingContribution"] = finite_metric_or_none(candidate_path_length(payload))
    payload["serviceApproachQualityContribution"] = approach_quality_rank(payload)
    payload["serviceRetentionContribution"] = 0
    payload["serviceQualityContribution"] = candidate_quality(payload)
    payload["serviceScore"] = service_score(payload)
    payload["serviceSelectionTentative"] = is_tentative_service_candidate(payload)
    payload["serviceRankReason"] = service_rank_reason(payload)
    payload["serviceCandidateKey"] = service_candidate_key(payload)
    return payload


def apply_service_policy_fields(candidate: dict[str, Any], service_type: str | None) -> dict[str, Any]:
    normalized_service_type = normalize_service_type_needed(service_type)
    candidate["serviceType"] = normalized_service_type or service_type
    candidate["serviceGroup"] = service_group(candidate)
    candidate["serviceCandidatePolicyGroupRank"] = service_group_rank(candidate, normalized_service_type)
    candidate["depositFallbackEligible"] = candidate["serviceGroup"] == SERVICE_GROUP_DEPOSIT_ONLY
    candidate["serviceScore"] = service_score(candidate)
    candidate["serviceRankReason"] = service_rank_reason(candidate)
    return candidate


def reachability_value(candidate: dict[str, Any]) -> str | None:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    value = navigation.get("directReachability") or candidate.get("directReachability") or candidate.get("reachability")
    return str(value).lower() if value is not None else None


def is_reachable(candidate: dict[str, Any]) -> bool:
    value = reachability_value(candidate)
    return value in {"reachable", "direct", "yes", "true", "1"}


def is_unknown_reachability(candidate: dict[str, Any]) -> bool:
    value = reachability_value(candidate)
    return value in {None, "", "unknown", "unavailable", "none"}


def reachability_rank(candidate: dict[str, Any]) -> int:
    return 0 if is_reachable(candidate) else (1 if is_unknown_reachability(candidate) else 2)


def candidate_distance(candidate: dict[str, Any]) -> float:
    value = candidate.get("distanceTiles")
    if isinstance(value, (int, float)):
        return float(value)
    return float("inf")


def candidate_path_length(candidate: dict[str, Any]) -> float:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    for value in (candidate.get("pathLengthTiles"), navigation.get("pathLengthTiles")):
        if isinstance(value, (int, float)):
            return float(value)
    return float("inf")


def approach_quality(candidate: dict[str, Any]) -> str:
    value = candidate.get("approachQuality")
    if value is None and isinstance(candidate.get("pathing"), dict):
        value = candidate["pathing"].get("approachQuality")
    text = normalized_text(value)
    return text if text else "unknown"


def approach_quality_rank(candidate: dict[str, Any]) -> int:
    return APPROACH_QUALITY_RANK.get(approach_quality(candidate), APPROACH_QUALITY_RANK["unknown"])


def is_tentative_service_candidate(candidate: dict[str, Any]) -> bool:
    candidate_type = service_candidate_type(candidate)
    return is_secondary_service_type(candidate_type) and approach_quality_rank(candidate) >= APPROACH_QUALITY_RANK["side_access_unknown"]


def finite_metric_or_none(value: float) -> float | None:
    return None if value == float("inf") else value


def candidate_quality(candidate: dict[str, Any]) -> float:
    value = candidate.get("qualityScore") or candidate.get("score") or candidate.get("candidateScore")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def service_type_priority(candidate: dict[str, Any]) -> int:
    candidate_type = service_candidate_type(candidate) or str(candidate.get("serviceCandidateType") or "bank_service")
    return SERVICE_TYPE_PRIORITY.get(candidate_type, SERVICE_TYPE_PRIORITY["bank_service"])


def service_rank(candidate: dict[str, Any]) -> tuple[int, int, int, int, float, float, float]:
    return (
        int(candidate.get("serviceCandidatePolicyGroupRank") or 0),
        service_type_priority(candidate),
        approach_quality_rank(candidate),
        reachability_rank(candidate),
        candidate_path_length(candidate),
        candidate_distance(candidate),
        -candidate_quality(candidate),
    )


def service_score(candidate: dict[str, Any]) -> float:
    path_length = candidate_path_length(candidate)
    distance = candidate_distance(candidate)
    path_cost = min(path_length, 50.0) if path_length != float("inf") else 25.0
    distance_cost = min(distance, 50.0) if distance != float("inf") else 25.0
    return round(
        1000.0
        - (float(candidate.get("serviceCandidatePolicyGroupRank") or 0) * 1000.0)
        - (service_type_priority(candidate) * 120.0)
        - (approach_quality_rank(candidate) * 40.0)
        - (reachability_rank(candidate) * 30.0)
        - path_cost
        - (distance_cost * 0.25)
        + min(candidate_quality(candidate), 100.0) * 0.05,
        3,
    )


def readable_contribution(value: float) -> str:
    if value == float("inf"):
        return "unknown"
    if value.is_integer():
        return str(int(value))
    return str(value)


def service_rank_reason(candidate: dict[str, Any]) -> str:
    candidate_type = service_candidate_type(candidate) or str(candidate.get("serviceCandidateType") or "bank_service")
    return (
        f"group {candidate.get('serviceGroup') or service_group(candidate)} rank {candidate.get('serviceCandidatePolicyGroupRank')}; "
        f"type priority {service_type_priority(candidate)} ({candidate_type}); "
        f"approach quality {approach_quality(candidate)} rank {approach_quality_rank(candidate)}; "
        f"reachability rank {reachability_rank(candidate)}; "
        f"path {readable_contribution(candidate_path_length(candidate))}; "
        f"distance {readable_contribution(candidate_distance(candidate))}"
    )


def group_candidates_by_type(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        candidate_type = str(candidate.get("serviceCandidateType") or "bank_service")
        grouped.setdefault(candidate_type, []).append(candidate)
    return grouped


def count_candidates_by_service_group(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        group = str(candidate.get("serviceGroup") or service_group(candidate))
        counts[group] = counts.get(group, 0) + 1
    return counts


def candidate_name(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    value = candidate.get("targetName") or candidate.get("name") or candidate.get("classId")
    return str(value) if value is not None else None


def retained_candidate_still_plausible(candidate: dict[str, Any], *, current_plane: int | None) -> bool:
    if current_plane is None:
        return True
    plane = candidate.get("plane")
    return not isinstance(plane, int) or plane == current_plane


def service_candidate_age_ticks(candidate: dict[str, Any], source_tick: int | None) -> int | None:
    last_seen = candidate.get("lastSeenTick")
    if isinstance(source_tick, int) and isinstance(last_seen, int):
        return max(0, source_tick - last_seen)
    return candidate.get("retainedServiceAgeTicks") if isinstance(candidate.get("retainedServiceAgeTicks"), int) else None


def service_memory_grace_ticks(state: ServiceTargetState, candidate: dict[str, Any] | None = None) -> int:
    candidate_type = service_candidate_type(candidate or {})
    if is_primary_service_type(candidate_type):
        return max(0, int(state.primary_bank_grace_ticks))
    if is_secondary_service_type(candidate_type):
        return max(0, int(state.deposit_grace_ticks))
    return max(0, int(state.missing_grace_ticks))


def record_memory_eviction(state: ServiceTargetState, candidate: dict[str, Any], reason: str, *, source_tick: int | None) -> None:
    state.memory_eviction_reasons_this_tick.append(
        {
            "candidateKey": service_candidate_key(candidate),
            "targetName": candidate_name(candidate),
            "serviceGroup": service_group(candidate),
            "worldX": candidate.get("worldX"),
            "worldY": candidate.get("worldY"),
            "plane": candidate.get("plane"),
            "ageTicks": service_candidate_age_ticks(candidate, source_tick),
            "reason": reason,
        }
    )


def prune_recent_service_candidates(
    state: ServiceTargetState,
    *,
    source_tick: int | None,
    current_plane: int | None,
    visible_keys: set[str] | None = None,
) -> None:
    visible = visible_keys or set()
    for key, candidate in list(state.recent_service_candidates.items()):
        if key in visible:
            continue
        if not retained_candidate_still_plausible(candidate, current_plane=current_plane):
            record_memory_eviction(state, candidate, "plane_mismatch", source_tick=source_tick)
            state.recent_service_candidates.pop(key, None)
            continue
        distance = candidate_distance(candidate)
        if state.memory_max_distance_tiles is not None and distance != float("inf") and distance > float(state.memory_max_distance_tiles):
            record_memory_eviction(state, candidate, "too_far", source_tick=source_tick)
            state.recent_service_candidates.pop(key, None)
            continue
        grace = service_memory_grace_ticks(state, candidate)
        age = service_candidate_age_ticks(candidate, source_tick)
        if age is not None and age > grace:
            record_memory_eviction(state, candidate, "stale_beyond_grace", source_tick=source_tick)
            state.recent_service_candidates.pop(key, None)
    if len(state.recent_service_candidates) > state.max_recent_service_candidates:
        ranked = sorted(
            state.recent_service_candidates.items(),
            key=lambda item: (
                service_type_priority(item[1]),
                -(item[1].get("lastSeenTick") if isinstance(item[1].get("lastSeenTick"), int) else -1),
            ),
        )
        state.recent_service_candidates = dict(ranked[: state.max_recent_service_candidates])


def remember_recent_service_candidates(
    state: ServiceTargetState,
    service_candidates: list[dict[str, Any]],
    *,
    source_tick: int | None,
    current_plane: int | None,
) -> None:
    visible_keys: set[str] = set()
    for candidate in service_candidates:
        key = service_candidate_key(candidate)
        if not key:
            continue
        visible_keys.add(key)
        payload = deepcopy(candidate)
        payload["lastSeenTick"] = source_tick
        payload["lastSeenDistance"] = finite_metric_or_none(candidate_distance(candidate))
        payload["lastSeenReachability"] = reachability_value(candidate)
        payload["lastSeenApproachQuality"] = approach_quality(candidate)
        payload["lastSeenSourceLane"] = candidate.get("_serviceSourceLane") or candidate.get("lastSeenSourceLane") or "serviceCandidates"
        payload["serviceTypePriority"] = service_type_priority(candidate)
        payload["serviceCandidateType"] = service_candidate_type(candidate) or payload.get("serviceCandidateType") or "bank_service"
        payload["serviceCandidateKey"] = key
        state.recent_service_candidates[key] = payload
    prune_recent_service_candidates(state, source_tick=source_tick, current_plane=current_plane, visible_keys=visible_keys)


def recent_preferred_service_candidates(
    state: ServiceTargetState | None,
    *,
    visible_keys: set[str],
    source_tick: int | None,
    current_plane: int | None,
) -> list[dict[str, Any]]:
    if state is None:
        return []
    retained: list[dict[str, Any]] = []
    prune_recent_service_candidates(state, source_tick=source_tick, current_plane=current_plane, visible_keys=visible_keys)
    for key, candidate in state.recent_service_candidates.items():
        if key in visible_keys:
            continue
        candidate_type = service_candidate_type(candidate)
        if not is_primary_service_type(candidate_type):
            continue
        if not retained_candidate_still_plausible(candidate, current_plane=current_plane):
            continue
        grace = service_memory_grace_ticks(state, candidate)
        age = service_candidate_age_ticks(candidate, source_tick)
        if age is not None and age > grace:
            continue
        payload = deepcopy(candidate)
        payload["retainedFromPrevious"] = True
        payload["serviceTargetRetained"] = True
        payload["retainedServiceAgeTicks"] = age
        payload["retainedServiceMissingTicks"] = age
        payload["serviceRetentionContribution"] = -20
        payload["serviceScore"] = service_score(payload) + 20
        payload["serviceSelectedReason"] = "retained preferred service target; current candidates omitted it transiently"
        payload["selectedServiceTargetSource"] = "retained_primary"
        retained.append(payload)
    return sorted(retained, key=service_rank)


def preferred_service_types(candidates: list[dict[str, Any]]) -> list[str]:
    types: set[str] = set()
    for candidate in candidates:
        candidate_type = service_candidate_type(candidate) or candidate.get("serviceCandidateType")
        if is_primary_service_type(candidate_type):
            types.add(str(candidate_type))
    return sorted(types)


def missing_preferred_service_reason(
    *,
    preferred_seen: list[str],
    preferred_recent: list[str],
    service_candidate_count: int,
    visibility: str | None = None,
) -> str | None:
    if preferred_seen:
        return None
    if preferred_recent:
        return "preferred_service_missing_from_current_candidates"
    if visibility == "possibly_capped_or_filtered":
        return "capped_or_filtered"
    if service_candidate_count:
        return "preferred_service_not_observed_current_tick"
    return "no_service_candidates_observed"


def selected_service_target_source(candidate: dict[str, Any] | None, *, retained: bool) -> str | None:
    if not isinstance(candidate, dict):
        return None
    if retained or candidate.get("retainedFromPrevious"):
        return "retained_primary" if service_group(candidate) == SERVICE_GROUP_FULL_BANK else "retained_recent"
    candidate_type = service_candidate_type(candidate)
    if is_primary_service_type(candidate_type):
        return "current_visible"
    if candidate_type == "deposit_box" or candidate_type == "deposit_chest":
        return "fallback_deposit"
    return "fallback_generic"


def candidate_policy_ineligible_reason(
    candidate: dict[str, Any],
    service_type: str | None,
    *,
    primary_visible: bool,
    primary_retained: bool,
    deposit_visible: bool,
) -> str | None:
    intent = normalize_service_type_needed(service_type)
    group = service_group(candidate)
    if intent == SERVICE_INTENT_BANK_FULL:
        if group == SERVICE_GROUP_DEPOSIT_ONLY:
            if primary_retained:
                return "deposit_fallback_blocked_by_retained_primary"
            if primary_visible:
                return "deposit_fallback_blocked_by_visible_primary"
        if group == SERVICE_GROUP_GENERIC:
            if primary_retained:
                return "generic_fallback_blocked_by_retained_primary"
            if primary_visible:
                return "generic_fallback_blocked_by_visible_primary"
            if deposit_visible:
                return "generic_fallback_blocked_by_deposit_candidate"
    return None


def apply_policy_eligibility_fields(
    candidates: list[dict[str, Any]],
    service_type: str | None,
    *,
    primary_visible: bool,
    primary_retained: bool,
) -> None:
    deposit_visible = any(service_group(candidate) == SERVICE_GROUP_DEPOSIT_ONLY for candidate in candidates)
    for candidate in candidates:
        reason = candidate_policy_ineligible_reason(
            candidate,
            service_type,
            primary_visible=primary_visible,
            primary_retained=primary_retained,
            deposit_visible=deposit_visible,
        )
        candidate["policyEligible"] = reason is None
        if reason:
            candidate["ineligibleReason"] = reason
        else:
            candidate.pop("ineligibleReason", None)


def memory_lifecycle_payload(state: ServiceTargetState | None, *, source_tick: int | None) -> dict[str, Any]:
    if state is None:
        return {
            "serviceMemoryGraceTicks": None,
            "primaryBankGraceTicks": None,
            "depositGraceTicks": None,
            "memoryMaxDistanceTiles": None,
            "memoryPlanePolicy": "same_plane",
            "memorySize": 0,
            "memoryEvictionReasons": [],
            "retainedCandidates": [],
        }
    retained = []
    for candidate in sorted(state.recent_service_candidates.values(), key=lambda item: (service_type_priority(item), service_candidate_age_ticks(item, source_tick) or 0)):
        age = service_candidate_age_ticks(candidate, source_tick)
        retained.append(
            {
                "candidateKey": service_candidate_key(candidate),
                "targetName": candidate_name(candidate),
                "serviceGroup": service_group(candidate),
                "serviceCandidateType": service_candidate_type(candidate),
                "worldX": candidate.get("worldX"),
                "worldY": candidate.get("worldY"),
                "plane": candidate.get("plane"),
                "ageTicks": age,
                "missingTicks": age,
                "lastSeenTick": candidate.get("lastSeenTick"),
                "lastSeenSourceLane": candidate.get("lastSeenSourceLane"),
                "lastSeenDistance": candidate.get("lastSeenDistance"),
                "lastSeenReachability": candidate.get("lastSeenReachability"),
                "lastSeenApproachQuality": candidate.get("lastSeenApproachQuality"),
            }
        )
    return {
        "serviceMemoryGraceTicks": state.missing_grace_ticks,
        "primaryBankGraceTicks": state.primary_bank_grace_ticks,
        "depositGraceTicks": state.deposit_grace_ticks,
        "memoryMaxDistanceTiles": state.memory_max_distance_tiles,
        "memoryPlanePolicy": state.memory_plane_policy,
        "memorySize": len(state.recent_service_candidates),
        "memoryEvictionReasons": list(state.memory_eviction_reasons_this_tick),
        "retainedCandidates": retained,
    }


def source_stage_counts(
    *,
    source_tick: int | None,
    profile_candidates: list[dict[str, Any]] | None = None,
    broad_candidates: list[dict[str, Any]] | None = None,
    loaded_service_scene: list[dict[str, Any]] | None = None,
    service_input_candidates: list[dict[str, Any]] | None = None,
    service_candidates: list[dict[str, Any]] | None = None,
    memory_state: ServiceTargetState | None = None,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {
        key: {
            "rawProjection": None,
            "profileCandidates": 0,
            "broadCandidates": 0,
            "loadedServiceScene": 0,
            "serviceCandidateInputs": 0,
            "serviceCandidates": 0,
            "retainedMemory": 0,
            "lastSeenTick": None,
            "ageTicks": None,
            "missingReason": "not_observed",
        }
        for key in SOURCE_STAGE_CLASSES
    }
    lanes = (
        ("profileCandidates", profile_candidates or []),
        ("broadCandidates", broad_candidates or []),
        ("loadedServiceScene", loaded_service_scene or []),
        ("serviceCandidateInputs", service_input_candidates or []),
        ("serviceCandidates", service_candidates or []),
    )
    for lane, candidates in lanes:
        for candidate in candidates:
            key = source_stage_class(candidate)
            if key in counts:
                counts[key][lane] += 1
                counts[key]["missingReason"] = None
    if memory_state is not None:
        for candidate in memory_state.recent_service_candidates.values():
            key = source_stage_class(candidate)
            if key in counts:
                counts[key]["retainedMemory"] += 1
                last_seen = candidate.get("lastSeenTick")
                age = service_candidate_age_ticks(candidate, source_tick)
                previous_age = counts[key].get("ageTicks")
                if previous_age is None or (age is not None and age < previous_age):
                    counts[key]["lastSeenTick"] = last_seen
                    counts[key]["ageTicks"] = age
                counts[key]["missingReason"] = None
    for key, payload in counts.items():
        if payload["missingReason"] is None:
            continue
        if key in {"bank_booth", "banker", "bank_chest"}:
            payload["missingReason"] = "preferred_service_not_observed"
        elif key == "deposit_box":
            payload["missingReason"] = "deposit_not_observed"
        else:
            payload["missingReason"] = "not_observed"
    return counts


def service_selected_reason_prefix(candidate: dict[str, Any], service_type: str | None) -> str:
    intent = normalize_service_type_needed(service_type)
    group = service_group(candidate)
    if intent == SERVICE_INTENT_BANK_FULL and group == SERVICE_GROUP_FULL_BANK:
        return "full bank target required by policy"
    if intent == SERVICE_INTENT_BANK_DEPOSIT and group == SERVICE_GROUP_DEPOSIT_ONLY:
        return "deposit service target requested by policy"
    if intent == SERVICE_INTENT_BANK_FULL and group == SERVICE_GROUP_DEPOSIT_ONLY:
        return "deposit fallback allowed because no full bank target is visible or retained"
    return "selected by policy ranking"


def store_service_target_state(
    state: ServiceTargetState,
    candidate: dict[str, Any] | None,
    *,
    policy_name: str,
    service_type: str | None,
    source_tick: int | None,
    switch_reason: str | None,
) -> None:
    key = service_candidate_key(candidate)
    if not candidate or not key:
        state.clear(reason=switch_reason)
        return
    if state.active_service_target_key == key:
        state.stable_for_ticks = max(1, state.stable_for_ticks + 1)
    else:
        state.stable_for_ticks = 1
    state.active_service_target_key = key
    state.active_policy_name = policy_name
    state.active_service_type = service_type
    retained_payload = deepcopy(candidate)
    retained_payload["lastSeenTick"] = source_tick
    retained_payload["lastSeenSourceLane"] = retained_payload.get("lastSeenSourceLane") or retained_payload.get("_serviceSourceLane") or "serviceCandidates"
    state.retained_candidate = retained_payload
    state.retained_candidate_type = service_candidate_type(candidate)
    state.retained_missing_ticks = 0
    state.last_seen_tick = source_tick
    state.service_switch_reason = switch_reason
    state.service_candidate_dropped_reason = None


def choose_service_candidate_with_retention(
    ranked: list[dict[str, Any]],
    *,
    state: ServiceTargetState | None,
    policy_name: str,
    service_type: str | None,
    source_tick: int | None,
    current_plane: int | None,
    memory_candidates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, bool, int | None, str | None, str | None]:
    current_best = ranked[0] if ranked else None
    if state is None:
        if current_best:
            current_best["selectedServiceTargetSource"] = selected_service_target_source(current_best, retained=False)
        return current_best, False, None, "selected_current_best" if current_best else "no_service_candidate", None
    if state.active_policy_name and state.active_policy_name != policy_name:
        state.clear(reason="policy_changed")
    remember_recent_service_candidates(
        state,
        [*(memory_candidates or []), *ranked],
        source_tick=source_tick,
        current_plane=current_plane,
    )
    retained = state.retained_candidate
    retained_key = state.active_service_target_key
    current_by_key = {service_candidate_key(candidate): candidate for candidate in ranked if service_candidate_key(candidate)}
    visible_keys = set(current_by_key)
    recent_preferred = recent_preferred_service_candidates(
        state,
        visible_keys=visible_keys,
        source_tick=source_tick,
        current_plane=current_plane,
    )
    current_type = service_candidate_type(current_best) if isinstance(current_best, dict) else None
    if (
        normalize_service_type_needed(service_type) == SERVICE_INTENT_BANK_FULL
        and recent_preferred
        and (current_best is None or not is_primary_service_type(current_type))
    ):
        retained_payload = deepcopy(recent_preferred[0])
        missing_ticks = retained_payload.get("retainedServiceMissingTicks")
        if not isinstance(missing_ticks, int):
            missing_ticks = max(1, state.retained_missing_ticks + 1)
            retained_payload["retainedServiceMissingTicks"] = missing_ticks
        retained_payload["selectedServiceTargetSource"] = "retained_primary"
        retained_payload["serviceSelectedReason"] = "retained full bank target; deposit fallback blocked by bank_full policy"
        state.retained_missing_ticks = max(state.retained_missing_ticks, missing_ticks)
        state.service_switch_reason = "retained_primary_blocks_deposit_fallback"
        state.service_candidate_dropped_reason = "preferred_service_missing_from_current_candidates"
        return (
            retained_payload,
            True,
            missing_ticks,
            state.service_switch_reason,
            state.service_candidate_dropped_reason,
        )
    if retained_key and retained_key in current_by_key:
        visible = current_by_key[retained_key]
        store_service_target_state(
            state,
            visible,
            policy_name=policy_name,
            service_type=service_type,
            source_tick=source_tick,
            switch_reason="retained_service_target_visible",
        )
        if current_best:
            current_best["selectedServiceTargetSource"] = selected_service_target_source(current_best, retained=False)
        return current_best, False, 0, state.service_switch_reason, None
    retained_type = service_candidate_type(retained) if isinstance(retained, dict) else None
    can_retain_primary = bool(
        retained
        and retained_key
        and is_primary_service_type(retained_type)
        and retained_candidate_still_plausible(retained, current_plane=current_plane)
        and (
            service_candidate_age_ticks(retained, source_tick) is None
            or service_candidate_age_ticks(retained, source_tick) <= service_memory_grace_ticks(state, retained)
        )
        and (current_best is None or not is_primary_service_type(current_type))
    )
    if can_retain_primary:
        state.retained_missing_ticks += 1
        if state.retained_missing_ticks <= max(0, int(state.missing_grace_ticks)):
            retained_payload = deepcopy(recent_preferred[0] if recent_preferred else retained)
            retained_payload["retainedFromPrevious"] = True
            retained_payload["serviceTargetRetained"] = True
            retained_payload["retainedServiceMissingTicks"] = state.retained_missing_ticks
            retained_payload["retainedServiceAgeTicks"] = service_candidate_age_ticks(retained_payload, source_tick) or state.retained_missing_ticks
            retained_payload["serviceRetentionContribution"] = -20
            retained_payload["serviceScore"] = service_score(retained_payload) + 20
            retained_payload["serviceSelectedReason"] = "retained preferred service target; current candidates omitted it transiently"
            retained_payload["selectedServiceTargetSource"] = "retained_primary"
            state.service_switch_reason = "retained_preferred_service_target_transient_missing"
            state.service_candidate_dropped_reason = "preferred_service_missing_from_current_candidates"
            return (
                retained_payload,
                True,
                state.retained_missing_ticks,
                state.service_switch_reason,
                state.service_candidate_dropped_reason,
            )
        state.service_switch_reason = "retained_service_missing_grace_expired"
        state.service_candidate_dropped_reason = "preferred_service_missing_too_long"
    elif (
        retained
        and retained_key
        and is_primary_service_type(retained_type)
        and retained_candidate_still_plausible(retained, current_plane=current_plane)
        and service_candidate_age_ticks(retained, source_tick) is not None
        and service_candidate_age_ticks(retained, source_tick) > service_memory_grace_ticks(state, retained)
    ):
        state.service_switch_reason = "retained_service_missing_grace_expired"
        state.service_candidate_dropped_reason = "preferred_service_missing_too_long"
    elif recent_preferred and (current_best is None or not is_primary_service_type(current_type)):
        retained_payload = deepcopy(recent_preferred[0])
        missing_ticks = retained_payload.get("retainedServiceMissingTicks")
        if not isinstance(missing_ticks, int):
            missing_ticks = max(1, state.retained_missing_ticks + 1)
            retained_payload["retainedServiceMissingTicks"] = missing_ticks
        state.retained_missing_ticks = max(state.retained_missing_ticks, missing_ticks)
        state.service_switch_reason = "retained_recent_preferred_service_target"
        state.service_candidate_dropped_reason = "preferred_service_missing_from_current_candidates"
        return (
            retained_payload,
            True,
            missing_ticks,
            state.service_switch_reason,
            state.service_candidate_dropped_reason,
        )
    elif retained and not retained_candidate_still_plausible(retained, current_plane=current_plane):
        state.clear(reason="retained_service_off_plane")
    switch_reason = state.service_switch_reason or ("selected_current_best" if current_best else "no_service_candidate")
    if current_best:
        current_best["selectedServiceTargetSource"] = selected_service_target_source(current_best, retained=False)
        store_service_target_state(
            state,
            current_best,
            policy_name=policy_name,
            service_type=service_type,
            source_tick=source_tick,
            switch_reason=switch_reason,
        )
    else:
        state.clear(reason=switch_reason)
    return current_best, False, state.retained_missing_ticks if retained else None, switch_reason, state.service_candidate_dropped_reason


def analyze_service_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    memory_candidates: list[dict[str, Any]] | None = None,
    profile_candidates: list[dict[str, Any]] | None = None,
    broad_candidates: list[dict[str, Any]] | None = None,
    loaded_service_scene: list[dict[str, Any]] | None = None,
    source_tick: int | None = None,
    service_target_state: ServiceTargetState | None = None,
    current_plane: int | None = None,
) -> ServiceContext:
    started = time.perf_counter()
    resolved = task_policy_module.resolve_task_policy(policy)
    if service_target_state is not None:
        service_target_state.memory_eviction_reasons_this_tick.clear()
    if resolved.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE:
        if service_target_state is not None:
            service_target_state.clear(reason="task_policy_does_not_require_service")
        return ServiceContext(
            status="PASS",
            source_tick=source_tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            service_required=False,
            reason="task policy does not require service",
        )

    service_type = normalize_service_type_needed(resolved.serviceTypeNeeded)
    raw_input_candidates = list(candidates or [])
    raw_service_candidates = [
        candidate
        for candidate in raw_input_candidates
        if candidate_service_match(candidate, service_type)
    ]
    service_candidates = [service_candidate_payload(candidate) for candidate in raw_service_candidates]
    for candidate in service_candidates:
        apply_service_policy_fields(candidate, service_type)
        candidate["_serviceSourceLane"] = candidate.get("_serviceSourceLane") or "serviceCandidateInputs"
    memory_service_candidates: list[dict[str, Any]] = []
    for candidate in memory_candidates or []:
        if candidate_service_match(candidate, service_type):
            payload = service_candidate_payload(candidate)
            apply_service_policy_fields(payload, service_type)
            payload["_serviceSourceLane"] = candidate.get("_serviceSourceLane") or payload.get("_serviceSourceLane") or "memoryCandidates"
            memory_service_candidates.append(payload)
    ranked = sorted(service_candidates, key=service_rank)
    best, retained, missing_ticks, switch_reason, dropped_reason = choose_service_candidate_with_retention(
        ranked,
        state=service_target_state,
        policy_name=resolved.name,
        service_type=service_type,
        source_tick=source_tick,
        current_plane=current_plane,
        memory_candidates=memory_service_candidates,
    )
    if best:
        best["selectedServiceTargetSource"] = selected_service_target_source(best, retained=retained)
        prefix = "tentative selected by" if best.get("serviceSelectionTentative") else service_selected_reason_prefix(best, service_type)
        if best.get("serviceSelectedReason") is None:
            best["serviceSelectedReason"] = f"{prefix} {best.get('serviceRankReason')}"
    visible_keys = {service_candidate_key(candidate) for candidate in service_candidates if service_candidate_key(candidate)}
    retained_recent = recent_preferred_service_candidates(
        service_target_state,
        visible_keys=visible_keys,
        source_tick=source_tick,
        current_plane=current_plane,
    )
    if retained and best:
        retained_key = service_candidate_key(best)
        if retained_key and not any(service_candidate_key(candidate) == retained_key for candidate in retained_recent):
            retained_recent.insert(0, best)
    retained_best = retained_recent[0] if retained_recent else None
    retained_age = None
    if isinstance(retained_best, dict):
        retained_age = retained_best.get("retainedServiceAgeTicks")
        if not isinstance(retained_age, int):
            retained_age = service_candidate_age_ticks(retained_best, source_tick)
    preferred_seen = preferred_service_types(service_candidates)
    preferred_recent = preferred_service_types(retained_recent)
    primary_visible = bool(preferred_seen)
    primary_retained = bool(preferred_recent)
    deposit_fallback_allowed = deposit_fallback_allowed_for_service(
        service_type,
        primary_visible=primary_visible,
        primary_retained=primary_retained,
    )
    apply_policy_eligibility_fields(
        service_candidates,
        service_type,
        primary_visible=primary_visible,
        primary_retained=primary_retained,
    )
    selected_group = service_group(best) if isinstance(best, dict) else None
    logic_error = bool(selected_group == SERVICE_GROUP_DEPOSIT_ONLY and not deposit_fallback_allowed)
    if isinstance(best, dict):
        best["policyEligible"] = not logic_error
        if logic_error:
            best["ineligibleReason"] = "selected_deposit_when_deposit_fallback_blocked"
        else:
            best.pop("ineligibleReason", None)
    missing_preferred = missing_preferred_service_reason(
        preferred_seen=preferred_seen,
        preferred_recent=preferred_recent,
        service_candidate_count=len(service_candidates),
    )
    nearest = min(service_candidates, key=candidate_distance, default=None)
    reachable_count = sum(1 for candidate in service_candidates if is_reachable(candidate))
    unknown_count = sum(1 for candidate in service_candidates if is_unknown_reachability(candidate))
    candidates_by_type = group_candidates_by_type(service_candidates)
    candidate_counts_by_type = {key: len(value) for key, value in candidates_by_type.items()}
    candidate_counts_by_group = count_candidates_by_service_group(service_candidates)
    visible_primary_count = candidate_counts_by_group.get(SERVICE_GROUP_FULL_BANK, 0)
    visible_deposit_count = candidate_counts_by_group.get(SERVICE_GROUP_DEPOSIT_ONLY, 0)
    source_counts = source_stage_counts(
        source_tick=source_tick,
        profile_candidates=profile_candidates,
        broad_candidates=broad_candidates if broad_candidates is not None else memory_candidates,
        loaded_service_scene=loaded_service_scene,
        service_input_candidates=raw_input_candidates,
        service_candidates=service_candidates,
        memory_state=service_target_state,
    )
    lifecycle = memory_lifecycle_payload(service_target_state, source_tick=source_tick)
    missing = []
    if raw_service_candidates and not any(candidate_has_optional_service_detail(candidate) for candidate in raw_service_candidates):
        missing.append("service.actions")
    return ServiceContext(
        status="PASS" if service_candidates else "WARN",
        warnings=[] if service_candidates else [f"no {service_type or 'service'} bank_service candidate available in current context"],
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        service_required=True,
        service_type_needed=service_type,
        best_service_candidate=best,
        nearest_service_candidate=nearest,
        service_candidates=([best] + [candidate for candidate in service_candidates if service_candidate_key(candidate) != service_candidate_key(best)] if retained and best else service_candidates),
        candidates_by_type=candidates_by_type,
        candidate_counts_by_type=candidate_counts_by_type,
        candidate_counts_by_service_group=candidate_counts_by_group,
        candidate_count=len(service_candidates),
        visible_primary_service_target_count=visible_primary_count,
        visible_deposit_service_target_count=visible_deposit_count,
        source_stage_counts=source_counts,
        memory_lifecycle=lifecycle,
        reachable_count=reachable_count,
        unknown_reachability_count=unknown_count,
        reason=best.get("serviceSelectedReason") if best else "task policy requires service context",
        service_target_retained=retained,
        retained_service_target_name=candidate_name(best) if retained else None,
        retained_service_missing_ticks=missing_ticks,
        retained_service_candidate_count=len(retained_recent),
        retained_best_service_candidate=retained_best,
        retained_service_age_ticks=retained_age,
        preferred_service_types_seen=preferred_seen,
        preferred_service_types_recently_seen=preferred_recent,
        missing_preferred_reason=missing_preferred,
        selected_service_target_source=best.get("selectedServiceTargetSource") if isinstance(best, dict) else None,
        primary_service_visible=primary_visible,
        primary_service_retained=primary_retained,
        deposit_fallback_allowed=deposit_fallback_allowed,
        selected_service_group=selected_group,
        logic_error=logic_error,
        service_switch_reason=switch_reason,
        service_candidate_dropped_reason=dropped_reason,
    )

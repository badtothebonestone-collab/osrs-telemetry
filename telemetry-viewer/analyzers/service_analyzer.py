from __future__ import annotations

import time
from typing import Any

import capabilities
import task_policy as task_policy_module

from analyzers.live_state import ServiceContext


SERVICE_CLASS_IDS = {
    "bank": {"bank", "banker", "bank_booth", "bank_chest", "deposit_box", "deposit_chest", "deposit", "bank_service", "bank_related"},
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
SERVICE_TYPE_PRIORITY = {
    "bank_booth": 0,
    "banker": 1,
    "bank_chest": 2,
    "deposit_box": 3,
    "deposit_chest": 4,
    "bank_service": 5,
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


def candidate_service_match(candidate: dict[str, Any], service_type: str | None) -> bool:
    if not isinstance(candidate, dict) or not service_type:
        return False
    wanted = SERVICE_CLASS_IDS.get(str(service_type), {str(service_type)})
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
    payload["serviceReachabilityContribution"] = reachability_rank(payload)
    payload["serviceDistanceContribution"] = finite_metric_or_none(candidate_distance(payload))
    payload["servicePathingContribution"] = finite_metric_or_none(candidate_path_length(payload))
    payload["serviceQualityContribution"] = candidate_quality(payload)
    payload["serviceScore"] = service_score(payload)
    payload["serviceRankReason"] = service_rank_reason(payload)
    return payload


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


def service_rank(candidate: dict[str, Any]) -> tuple[int, int, float, float, float]:
    return (
        service_type_priority(candidate),
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
        - (service_type_priority(candidate) * 120.0)
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
        f"type priority {service_type_priority(candidate)} ({candidate_type}); "
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


def analyze_service_context(
    policy: task_policy_module.TaskPolicy | dict[str, Any] | str | None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    source_tick: int | None = None,
) -> ServiceContext:
    started = time.perf_counter()
    resolved = task_policy_module.resolve_task_policy(policy)
    if resolved.fullInventoryStrategy != task_policy_module.InventoryFullStrategy.NEEDS_SERVICE:
        return ServiceContext(
            status="PASS",
            source_tick=source_tick,
            timing_millis=(time.perf_counter() - started) * 1000.0,
            service_required=False,
            reason="task policy does not require service",
        )

    service_type = resolved.serviceTypeNeeded
    raw_service_candidates = [
        candidate
        for candidate in candidates or []
        if candidate_service_match(candidate, service_type)
    ]
    service_candidates = [service_candidate_payload(candidate) for candidate in raw_service_candidates]
    for candidate in service_candidates:
        candidate["serviceType"] = service_type
    ranked = sorted(service_candidates, key=service_rank)
    if ranked:
        ranked[0]["serviceSelectedReason"] = f"selected by {ranked[0].get('serviceRankReason')}"
    nearest = min(service_candidates, key=candidate_distance, default=None)
    reachable_count = sum(1 for candidate in service_candidates if is_reachable(candidate))
    unknown_count = sum(1 for candidate in service_candidates if is_unknown_reachability(candidate))
    candidates_by_type = group_candidates_by_type(service_candidates)
    candidate_counts_by_type = {key: len(value) for key, value in candidates_by_type.items()}
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
        best_service_candidate=ranked[0] if ranked else None,
        nearest_service_candidate=nearest,
        service_candidates=service_candidates,
        candidates_by_type=candidates_by_type,
        candidate_counts_by_type=candidate_counts_by_type,
        candidate_count=len(service_candidates),
        reachable_count=reachable_count,
        unknown_reachability_count=unknown_count,
        reason=ranked[0].get("serviceSelectedReason") if ranked else "task policy requires service context",
    )

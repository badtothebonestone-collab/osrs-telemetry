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
SERVICE_NAME_TOKENS = {
    "bank": ("banker", "bank booth", "bank chest", "bank deposit box", "deposit box", "deposit chest", "bank service"),
    "deposit": ("bank deposit box", "deposit box", "deposit chest", "bank chest"),
}
SERVICE_OUTPUT_FIELDS = (
    "targetKey",
    "objectKey",
    "candidateKey",
    "targetType",
    "classId",
    "name",
    "targetName",
    "id",
    "rawId",
    "hash",
    "worldX",
    "worldY",
    "plane",
    "sceneX",
    "sceneY",
    "localX",
    "localY",
    "distanceTiles",
    "qualityScore",
    "qualityTier",
    "targetLiveState",
    "liveness",
    "directReachability",
    "navigation",
    "aimPoint",
    "geometrySource",
    "clickableHull",
    "clickboxPolygon",
    "convexHull",
    "canvasTilePolygon",
    "bounds",
    "onScreen",
    "geometryAvailable",
)


def candidate_service_match(candidate: dict[str, Any], service_type: str | None) -> bool:
    if not isinstance(candidate, dict) or not service_type:
        return False
    wanted = SERVICE_CLASS_IDS.get(str(service_type), {str(service_type)})
    name_tokens = SERVICE_NAME_TOKENS.get(str(service_type), ())
    fields = (
        str(candidate.get("classId") or "").lower(),
        str(candidate.get("targetType") or "").lower(),
        str(candidate.get("name") or candidate.get("targetName") or "").lower().replace(" ", "_"),
    )
    if any(field in wanted or any(token in field for token in wanted) for field in fields):
        return True
    display_name = str(candidate.get("name") or candidate.get("targetName") or "").lower()
    if any(token in display_name for token in name_tokens):
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


def sanitized_service_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in SERVICE_OUTPUT_FIELDS:
        value = candidate.get(key)
        if value is not None:
            sanitized[key] = value
    navigation = sanitized.get("navigation") if isinstance(sanitized.get("navigation"), dict) else {}
    if "directReachability" not in sanitized and navigation.get("directReachability") is not None:
        sanitized["directReachability"] = navigation.get("directReachability")
    if "name" not in sanitized and sanitized.get("targetName") is not None:
        sanitized["name"] = sanitized.get("targetName")
    if "targetName" not in sanitized and sanitized.get("name") is not None:
        sanitized["targetName"] = sanitized.get("name")
    return sanitized


def reachability_value(candidate: dict[str, Any]) -> str | None:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    value = navigation.get("directReachability") or candidate.get("directReachability") or candidate.get("reachability")
    return str(value).lower() if value is not None else None


def is_reachable(candidate: dict[str, Any]) -> bool:
    value = reachability_value(candidate)
    return value in {"reachable", "direct", "yes", "true", "1"}


def candidate_distance(candidate: dict[str, Any]) -> float:
    value = candidate.get("distanceTiles")
    if isinstance(value, (int, float)):
        return float(value)
    return float("inf")


def candidate_quality(candidate: dict[str, Any]) -> float:
    value = candidate.get("qualityScore") or candidate.get("score") or candidate.get("candidateScore")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


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
    service_candidates = [sanitized_service_candidate(candidate) for candidate in raw_service_candidates]
    ranked = sorted(service_candidates, key=lambda candidate: (-candidate_quality(candidate), candidate_distance(candidate)))
    nearest = min(service_candidates, key=candidate_distance, default=None)
    reachable_count = sum(1 for candidate in service_candidates if is_reachable(candidate))
    missing = []
    if raw_service_candidates and not any(candidate_has_optional_service_detail(candidate) for candidate in raw_service_candidates):
        missing.append("service.actions")
    return ServiceContext(
        status="PASS" if service_candidates else "WARN",
        warnings=[] if service_candidates else [f"no {service_type or 'service'} candidate available in current context"],
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        service_required=True,
        service_type_needed=service_type,
        best_service_candidate=ranked[0] if ranked else None,
        nearest_service_candidate=nearest,
        service_candidates=service_candidates,
        candidate_count=len(service_candidates),
        reachable_count=reachable_count,
        reason="task policy requires service context",
    )

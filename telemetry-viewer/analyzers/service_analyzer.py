from __future__ import annotations

import time
from typing import Any

import task_policy as task_policy_module

from analyzers.live_state import ServiceContext


SERVICE_CLASS_IDS = {
    "bank": {"bank", "banker", "bank_booth", "deposit_box", "deposit"},
    "deposit": {"deposit_box", "deposit", "bank_booth"},
}


def candidate_service_match(candidate: dict[str, Any], service_type: str | None) -> bool:
    if not isinstance(candidate, dict) or not service_type:
        return False
    wanted = SERVICE_CLASS_IDS.get(str(service_type), {str(service_type)})
    fields = (
        str(candidate.get("classId") or "").lower(),
        str(candidate.get("targetType") or "").lower(),
        str(candidate.get("name") or candidate.get("targetName") or "").lower().replace(" ", "_"),
    )
    return any(field in wanted or any(token in field for token in wanted) for field in fields)


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
    service_candidates = [
        candidate
        for candidate in candidates or []
        if candidate_service_match(candidate, service_type)
    ]
    return ServiceContext(
        status="PASS" if service_candidates else "WARN",
        warnings=[] if service_candidates else [f"no {service_type or 'service'} candidate available in current context"],
        missing_capabilities=[],
        source_tick=source_tick,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        service_required=True,
        service_type_needed=service_type,
        best_service_candidate=service_candidates[0] if service_candidates else None,
        service_candidates=service_candidates,
        reason="task policy requires service context",
    )

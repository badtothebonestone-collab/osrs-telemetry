from __future__ import annotations

import time
from typing import Any

import capabilities
from analyzers import service_analyzer
from analyzers.live_state import TargetContext


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _class_matches(candidate: dict[str, Any], class_id: str | None) -> bool:
    if not class_id:
        return True
    wanted = class_id.lower()
    actual = str(candidate.get("classId") or candidate.get("targetType") or "").lower()
    name = str(candidate.get("targetName") or candidate.get("name") or "").lower()
    return wanted in actual or wanted in name


def _score(candidate: dict[str, Any]) -> float:
    for key in ("qualityScore", "score", "candidateScore", "rankScore"):
        value = _number(candidate.get(key))
        if value is not None:
            return value
    return 0.0


def _distance(candidate: dict[str, Any]) -> float:
    value = _number(candidate.get("distanceTiles"))
    return value if value is not None else 1_000_000.0


def _tick(candidate: dict[str, Any]) -> int | None:
    for key in ("tick", "lastSeenTick", "lastUpdatedTick"):
        value = _number(candidate.get(key))
        if value is not None:
            return int(value)
    return None


def _is_service_candidate(candidate: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return False
    return bool(
        service_analyzer.service_candidate_type(candidate)
        or service_analyzer.candidate_service_match(candidate, "bank")
        or service_analyzer.candidate_service_match(candidate, "deposit")
    )


def analyze_targets(
    candidates: list[dict[str, Any]] | None,
    *,
    class_id: str | None = None,
    max_candidates: int = 10,
) -> TargetContext:
    started = time.perf_counter()
    candidates = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    broad_candidates = list(candidates)
    scoped = [candidate for candidate in candidates if _class_matches(candidate, class_id)]
    service_inputs = [candidate for candidate in broad_candidates if _is_service_candidate(candidate)]
    raw_best = scoped[0] if scoped else (candidates[0] if candidates and class_id is None else None)
    if scoped:
        raw_best = max(scoped, key=_score)
    nearest = min(scoped, key=_distance) if scoped else None
    missing: list[str] = []
    warnings: list[str] = []
    if not candidates:
        missing.append("target.candidates")
        warnings.append("no target candidates in current analysis")
    if raw_best is None:
        missing.append("target.best")
    source_ticks = [_tick(candidate) for candidate in candidates]
    source_ticks = [tick for tick in source_ticks if tick is not None]
    return TargetContext(
        status="WARN" if warnings or missing else "PASS",
        warnings=warnings,
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=max(source_ticks) if source_ticks else None,
        retained_from_previous=False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        candidates=candidates,
        profile_candidates=scoped,
        broad_candidates=broad_candidates,
        service_candidate_inputs=service_inputs,
        raw_best_target=raw_best,
        nearest_target=nearest,
        top_candidates=scoped[: max(0, max_candidates)],
        candidate_count=len(candidates),
        profile_candidate_count=len(scoped),
        broad_candidate_count=len(broad_candidates),
        service_candidate_input_count=len(service_inputs),
        service_candidate_visibility="available" if service_inputs else "not_observed",
    )

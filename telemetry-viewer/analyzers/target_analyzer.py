from __future__ import annotations

import time
from typing import Any

import capabilities
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


def analyze_targets(
    candidates: list[dict[str, Any]] | None,
    *,
    class_id: str | None = None,
    max_candidates: int = 10,
) -> TargetContext:
    started = time.perf_counter()
    candidates = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    scoped = [candidate for candidate in candidates if _class_matches(candidate, class_id)]
    raw_best = scoped[0] if scoped else (candidates[0] if candidates else None)
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
        raw_best_target=raw_best,
        nearest_target=nearest,
        top_candidates=scoped[: max(0, max_candidates)],
        candidate_count=len(candidates),
    )
